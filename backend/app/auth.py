from __future__ import annotations

import logging
from dataclasses import dataclass
from json import JSONDecodeError
from typing import overload

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.authn.client_ip import resolve_client_ip
from app.authn.service import (
    active_sessions,
    arm_generation_guard,
    audit,
    change_password_atomically,
    consume_failure_budget,
    disarm_generation_guard,
    release_attempt,
    revoke_all_sessions,
    revoke_other_sessions,
)
from app.config import Settings, get_settings_dependency
from app.database import get_db
from app.errors import AppError
from app.models import AuthSession, User
from app.schemas import (
    AuthCredentials,
    AuthResponse,
    AuthSessionResponse,
    BootstrapRequest,
    PasswordChangeRequest,
)
from app.security import (
    RateLimitRule,
    decode_session_cookie,
    dummy_password_hash,
    encode_session_cookie,
    hash_password,
    hash_secret,
    new_secret,
    password_needs_rehash,
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


@overload
async def _parse_auth_payload(
    request: Request, model: type[BootstrapRequest]
) -> BootstrapRequest: ...


@overload
async def _parse_auth_payload(
    request: Request, model: type[AuthCredentials]
) -> AuthCredentials: ...


async def _parse_auth_payload(
    request: Request, model: type[AuthCredentials] | type[BootstrapRequest]
) -> AuthCredentials | BootstrapRequest:
    try:
        return model.model_validate(await request.json())
    except (JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise AppError(422, "AUTH_PAYLOAD_INVALID", "The credential payload is invalid.") from exc


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
        credential_generation=user.credential_generation,
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
    if auth_session.credential_generation != user.credential_generation:
        auth_session.revoked_at = now
        db.commit()
        raise AppError(401, "SESSION_INVALID", "The session is invalid.")
    auth_session.last_seen_at = now
    db.commit()
    return AuthContext(user=user, session=auth_session, csrf_token=csrf_token)


async def require_csrf(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> AuthContext:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secure_compare(hash_secret(supplied), auth.session.csrf_secret_hash):
        raise AppError(403, "CSRF_INVALID", "The CSRF token is missing or invalid.")
    arm_generation_guard(db, auth.session)
    return auth


@router.get("/bootstrap-status")
async def bootstrap_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"bootstrapAvailable": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/bootstrap", response_model=AuthResponse, status_code=201)
async def bootstrap(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthResponse:
    client_ip = resolve_client_ip(request, settings)
    rule = RateLimitRule(
        attempts=settings.login_rate_limit_attempts,
        window_ms=settings.login_rate_limit_window_ms,
    )
    budgets = (("bootstrap-client", client_ip), ("bootstrap-global", "global"))
    for namespace, key in budgets:
        if not consume_failure_budget(db, settings, namespace, key, rule):
            raise AppError(429, "BOOTSTRAP_RATE_LIMITED", "Too many setup attempts.")
    payload = await _parse_auth_payload(request, BootstrapRequest)
    if (db.scalar(select(func.count(User.id))) or 0) != 0:
        for namespace, key in budgets:
            release_attempt(db, settings, namespace, key, rule)
        raise AppError(409, "BOOTSTRAP_UNAVAILABLE", "Initial setup is no longer available.")
    if not settings.bootstrap_token or not secure_compare(
        payload.bootstrap_token, settings.bootstrap_token
    ):
        raise AppError(403, "BOOTSTRAP_TOKEN_INVALID", "The bootstrap token is invalid.")
    for namespace, key in budgets:
        release_attempt(db, settings, namespace, key, rule)
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
    audit(
        db,
        "bootstrap_completed",
        user_id=user.id,
        session_id=auth_session.id,
        client_key=client_ip,
        settings=settings,
    )
    db.commit()
    _set_session_cookie(response, settings, session_token, csrf_token)
    return AuthResponse(
        user_id=user.id,
        username=user.username,
        csrf_token=csrf_token,
        absolute_expires_at=epoch_ms_to_rfc3339(auth_session.absolute_expires_at),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthResponse:
    ip = resolve_client_ip(request, settings)
    login_rule = RateLimitRule(
        attempts=settings.login_rate_limit_attempts,
        window_ms=settings.login_rate_limit_window_ms,
    )
    preliminary_budgets = (("login-client", ip), ("login-global", "global"))
    for namespace, key in preliminary_budgets:
        if not consume_failure_budget(db, settings, namespace, key, login_rule):
            raise AppError(429, "LOGIN_RATE_LIMITED", "Too many login attempts. Try again later.")
    payload = await _parse_auth_payload(request, AuthCredentials)
    account_key = payload.username.strip().casefold()
    account_budget = ("login-account", account_key)
    if not consume_failure_budget(db, settings, *account_budget, login_rule):
        raise AppError(429, "LOGIN_RATE_LIMITED", "Too many login attempts. Try again later.")
    budgets = (*preliminary_budgets, account_budget)
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    password_hash = user.password_hash if user is not None else dummy_password_hash
    if not verify_password(password_hash, payload.password) or user is None:
        logger.warning("Login failed")
        raise AppError(401, "LOGIN_FAILED", "The username or password is incorrect.")
    for namespace, key in budgets:
        release_attempt(db, settings, namespace, key, login_rule)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    logger.info("Login succeeded")
    auth_session, session_token, csrf_token = _create_session(db, user, settings)
    audit(
        db,
        "login_succeeded",
        user_id=user.id,
        session_id=auth_session.id,
        client_key=ip,
        settings=settings,
    )
    db.commit()
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
    disarm_generation_guard(db)
    auth.session.revoked_at = utc_now_ms()
    db.commit()
    logger.info("Session logout completed")
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response


@router.post("/password/change", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> Response:
    if not verify_password(auth.user.password_hash, payload.current_password):
        raise AppError(401, "CURRENT_PASSWORD_INVALID", "The current password is incorrect.")
    disarm_generation_guard(db)
    change_password_atomically(
        db,
        auth.user,
        auth.session.credential_generation,
        auth.user.password_hash,
        hash_password(payload.new_password),
    )
    audit(db, "password_changed", user_id=auth.user.id, session_id=auth.session.id)
    db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response


@router.get("/sessions", response_model=list[AuthSessionResponse])
async def list_sessions(
    auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[AuthSessionResponse]:
    return [
        AuthSessionResponse(
            id=item.id,
            created_at=epoch_ms_to_rfc3339(item.created_at),
            last_seen_at=epoch_ms_to_rfc3339(item.last_seen_at),
            absolute_expires_at=epoch_ms_to_rfc3339(item.absolute_expires_at),
            current=item.id == auth.session.id,
            revoked=item.revoked_at is not None,
        )
        for item in active_sessions(db, auth.user.id)
    ]


@router.post("/sessions/revoke-others", status_code=204)
async def revoke_others(
    auth: AuthContext = Depends(require_csrf), db: Session = Depends(get_db)
) -> Response:
    count = revoke_other_sessions(db, auth.user.id, auth.session.id)
    audit(
        db,
        "sessions_revoked",
        user_id=auth.user.id,
        session_id=auth.session.id,
        details={"count": count},
    )
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/sessions/revoke-all", status_code=204)
async def revoke_all(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> Response:
    disarm_generation_guard(db)
    revoke_all_sessions(db, auth.user, auth.session.credential_generation)
    audit(db, "sessions_revoked_all", user_id=auth.user.id, session_id=auth.session.id)
    db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response
