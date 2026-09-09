from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy import delete, event, select, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.models import AuthRateLimitBucket, AuthSession, SecurityAuditEvent, User
from app.security import RateLimitRule
from app.time_utils import utc_now_ms


@event.listens_for(Session, "before_commit")
def _revalidate_authenticated_mutation(db: Session) -> None:
    guard = db.info.get("auth_generation_guard")
    if not guard:
        return
    session_id, user_id, generation = guard
    locked = db.execute(
        text(
            "UPDATE auth_sessions SET last_seen_at=last_seen_at WHERE id=:session_id "
            "AND user_id=:user_id AND revoked_at IS NULL AND credential_generation=:generation "
            "AND EXISTS (SELECT 1 FROM users WHERE id=:user_id "
            "AND credential_generation=:generation)"
        ),
        {"session_id": session_id, "user_id": user_id, "generation": generation},
    )
    if getattr(locked, "rowcount", 0) != 1:
        raise AppError(401, "SESSION_INVALID", "The session became invalid.")


def arm_generation_guard(db: Session, auth_session: AuthSession) -> None:
    db.info["auth_generation_guard"] = (
        auth_session.id,
        auth_session.user_id,
        auth_session.credential_generation,
    )


def disarm_generation_guard(db: Session) -> None:
    db.info.pop("auth_generation_guard", None)


def _client_hash(settings: Settings, value: str) -> str:
    secret = settings.security_secret or "lcc-non-production-rate-limit-key"
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def consume_failure_budget(
    db: Session,
    settings: Settings,
    namespace: str,
    client_key: str,
    rule: RateLimitRule,
    *,
    now_ms: int | None = None,
) -> bool:
    """Atomically record a failed attempt and report whether that attempt is permitted."""
    now = utc_now_ms() if now_ms is None else now_ms
    window_start = now - (now % rule.window_ms)
    key_hash = _client_hash(settings, client_key)
    statement = (
        insert(AuthRateLimitBucket)
        .values(
            namespace=namespace,
            client_key_hash=key_hash,
            key_id="v1",
            window_started_at=window_start,
            failure_count=1,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["namespace", "client_key_hash", "window_started_at"],
            set_={
                "failure_count": AuthRateLimitBucket.failure_count + 1,
                "updated_at": now,
            },
        )
        .returning(AuthRateLimitBucket.failure_count)
    )
    try:
        count = db.execute(statement).scalar_one()
        db.execute(
            delete(AuthRateLimitBucket).where(
                AuthRateLimitBucket.updated_at < now - max(rule.window_ms * 4, 86_400_000)
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise AppError(
            503, "AUTH_RATE_LIMIT_UNAVAILABLE", "Authentication is unavailable."
        ) from exc
    return int(count) <= rule.attempts


def release_attempt(
    db: Session,
    settings: Settings,
    namespace: str,
    client_key: str,
    rule: RateLimitRule,
    *,
    now_ms: int | None = None,
) -> None:
    now = utc_now_ms() if now_ms is None else now_ms
    window_start = now - (now % rule.window_ms)
    key_hash = _client_hash(settings, client_key)
    db.execute(
        update(AuthRateLimitBucket)
        .where(
            AuthRateLimitBucket.namespace == namespace,
            AuthRateLimitBucket.client_key_hash == key_hash,
            AuthRateLimitBucket.window_started_at == window_start,
            AuthRateLimitBucket.failure_count > 1,
        )
        .values(failure_count=AuthRateLimitBucket.failure_count - 1, updated_at=now)
    )
    db.execute(
        delete(AuthRateLimitBucket).where(
            AuthRateLimitBucket.namespace == namespace,
            AuthRateLimitBucket.client_key_hash == key_hash,
            AuthRateLimitBucket.window_started_at == window_start,
            AuthRateLimitBucket.failure_count <= 1,
        )
    )
    db.commit()


def clear_failure_budget(db: Session, settings: Settings, namespace: str, client_key: str) -> None:
    db.execute(
        delete(AuthRateLimitBucket).where(
            AuthRateLimitBucket.namespace == namespace,
            AuthRateLimitBucket.client_key_hash == _client_hash(settings, client_key),
        )
    )
    db.commit()


def audit(
    db: Session,
    event_type: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    actor_kind: str = "user",
    client_key: str | None = None,
    settings: Settings | None = None,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        SecurityAuditEvent(
            event_type=event_type,
            user_id=user_id,
            auth_session_id=session_id,
            actor_kind=actor_kind,
            client_key_hash=(
                _client_hash(settings, client_key) if settings and client_key else None
            ),
            details_json=json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
        )
    )


def revoke_all_sessions(
    db: Session, user: User, expected_generation: int, *, now_ms: int | None = None
) -> None:
    now = utc_now_ms() if now_ms is None else now_ms
    changed = db.execute(
        update(User)
        .where(User.id == user.id, User.credential_generation == expected_generation)
        .values(credential_generation=User.credential_generation + 1)
    )
    if getattr(changed, "rowcount", 0) != 1:
        raise AppError(409, "CREDENTIAL_STATE_CHANGED", "Credential state changed; try again.")
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.expire(user)


def change_password_atomically(
    db: Session,
    user: User,
    expected_generation: int,
    expected_password_hash: str,
    new_password_hash: str,
    *,
    now_ms: int | None = None,
) -> None:
    now = utc_now_ms() if now_ms is None else now_ms
    changed = db.execute(
        update(User)
        .where(
            User.id == user.id,
            User.credential_generation == expected_generation,
            User.password_hash == expected_password_hash,
        )
        .values(
            password_hash=new_password_hash,
            password_changed_at=now,
            credential_generation=User.credential_generation + 1,
            updated_at=now,
        )
    )
    if getattr(changed, "rowcount", 0) != 1:
        raise AppError(409, "CREDENTIAL_STATE_CHANGED", "Credential state changed; try again.")
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.expire(user)


def revoke_other_sessions(db: Session, user_id: str, current_session_id: str) -> int:
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.id != current_session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now_ms())
    )
    return int(getattr(result, "rowcount", 0))


def active_sessions(db: Session, user_id: str) -> list[AuthSession]:
    return list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user_id)
            .order_by(AuthSession.created_at.desc())
        )
    )
