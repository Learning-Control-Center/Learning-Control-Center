from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings_dependency
from app.database import get_db
from app.errors import AppError
from app.models import AuthSession, User
from app.schemas import AuthCredentials, AuthResponse, BootstrapRequest
from app.security import (
    RateLimitRule,
    decode_session_cookie,
    encode_session_cookie,
    hash_password,
    hash_secret,
    new_secret,
    rate_limiter,
    secure_compare,
    verify_password,
)
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    user: User
    session: AuthSession
    csrf_token: str


def _request_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _create_session(db: Session, user: User, settings: Settings) -> tuple[AuthSession, str, str]:
    now = utc_now_ms()
    session_token = new_secret()
    csrf_token = new_secret()
    auth_session = AuthSession(
        user_id=user.id,
        token_lookup_hash=hash_secret(session_token),
        csrf_secret_hash=hash_secret(csrf_token),
        created_at=now,
        last_seen_at=now,
        absolute_expires_at=now + settings.session_absolute_timeout_ms,
    )
    db.add(auth_session)
    db.commit()
    return auth_session, session_token, csrf_token


def _set_session_cookie(
    response: Response, settings: Settings, session_token: str, csrf_token: str
) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        encode_session_cookie(session_token, csrf_token),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_absolute_timeout_ms // 1000,
        path="/",
    )


async def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthContext:
    decoded = decode_session_cookie(request.cookies.get(settings.session_cookie_name))
    if decoded is None:
        raise AppError(401, "AUTH_REQUIRED", "Authentication is required.")
    session_token, csrf_token = decoded
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_lookup_hash == hash_secret(session_token))
    )
    now = utc_now_ms()
    if auth_session is None or auth_session.revoked_at is not None:
        raise AppError(401, "SESSION_INVALID", "The session is invalid.")
    if auth_session.absolute_expires_at <= now or (
        auth_session.last_seen_at + settings.session_idle_timeout_ms <= now
    ):
        auth_session.revoked_at = now
        db.commit()
        raise AppError(401, "SESSION_EXPIRED", "The session has expired.")
    if not secure_compare(auth_session.csrf_secret_hash, hash_secret(csrf_token)):
        raise AppError(401, "SESSION_INVALID", "The session is invalid.")
    user = db.get(User, auth_session.user_id)
    if user is None:
        raise AppError(401, "SESSION_INVALID", "The session is invalid.")
    auth_session.last_seen_at = now
    db.commit()
    return AuthContext(user=user, session=auth_session, csrf_token=csrf_token)


async def require_csrf(
    request: Request, auth: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secure_compare(hash_secret(supplied), auth.session.csrf_secret_hash):
        raise AppError(403, "CSRF_INVALID", "The CSRF token is missing or invalid.")
    return auth


@router.get("/bootstrap-status")
async def bootstrap_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"bootstrapAvailable": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/bootstrap", response_model=AuthResponse, status_code=201)
async def bootstrap(
    payload: BootstrapRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthResponse:
    if (db.scalar(select(func.count(User.id))) or 0) != 0:
        raise AppError(409, "BOOTSTRAP_UNAVAILABLE", "Initial setup is no longer available.")
    if not settings.bootstrap_token or not secure_compare(
        payload.bootstrap_token, settings.bootstrap_token
    ):
        raise AppError(403, "BOOTSTRAP_TOKEN_INVALID", "The bootstrap token is invalid.")
    user = User(username=payload.username.strip(), password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "BOOTSTRAP_UNAVAILABLE", "Initial setup is no longer available."
        ) from exc
    logger.info("Initial user bootstrap completed")
    auth_session, session_token, csrf_token = _create_session(db, user, settings)
    _set_session_cookie(response, settings, session_token, csrf_token)
    return AuthResponse(
        user_id=user.id,
        username=user.username,
        csrf_token=csrf_token,
        absolute_expires_at=epoch_ms_to_rfc3339(auth_session.absolute_expires_at),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: AuthCredentials,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthResponse:
    ip = _request_ip(request)
    login_rule = RateLimitRule(
        attempts=settings.login_rate_limit_attempts,
        window_ms=settings.login_rate_limit_window_ms,
    )
    if not rate_limiter.check("login", ip, login_rule):
        raise AppError(429, "LOGIN_RATE_LIMITED", "Too many login attempts. Try again later.")
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if user is None or not verify_password(user.password_hash, payload.password):
        rate_limiter.add("login", ip)
        logger.warning("Login failed")
        raise AppError(401, "LOGIN_FAILED", "The username or password is incorrect.")
    rate_limiter.clear("login", ip)
    logger.info("Login succeeded")
    auth_session, session_token, csrf_token = _create_session(db, user, settings)
    _set_session_cookie(response, settings, session_token, csrf_token)
    return AuthResponse(
        user_id=user.id,
        username=user.username,
        csrf_token=csrf_token,
        absolute_expires_at=epoch_ms_to_rfc3339(auth_session.absolute_expires_at),
    )


@router.get("/session", response_model=AuthResponse)
async def current_session(auth: AuthContext = Depends(get_auth_context)) -> AuthResponse:
    return AuthResponse(
        user_id=auth.user.id,
        username=auth.user.username,
        csrf_token=auth.csrf_token,
        absolute_expires_at=epoch_ms_to_rfc3339(auth.session.absolute_expires_at),
    )


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> Response:
    auth.session.revoked_at = utc_now_ms()
    db.commit()
    logger.info("Session logout completed")
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = 204
    return response
