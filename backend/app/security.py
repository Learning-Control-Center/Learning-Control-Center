from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.time_utils import utc_now_ms

password_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4)
dummy_password_hash = password_hasher.hash("lcc-constant-time-invalid-credential")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def encode_session_cookie(session_token: str, csrf_token: str) -> str:
    return f"{session_token}.{csrf_token}"


def decode_session_cookie(value: str | None) -> tuple[str, str] | None:
    if not value or "." not in value:
        return None
    session_token, csrf_token = value.split(".", 1)
    if not session_token or not csrf_token:
        return None
    return session_token, csrf_token


def secure_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


@dataclass(frozen=True)
class RateLimitRule:
    attempts: int
    window_ms: int


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[int]] = defaultdict(deque)

    def check(
        self, namespace: str, key: str, rule: RateLimitRule, now_ms: int | None = None
    ) -> bool:
        now = now_ms if now_ms is not None else utc_now_ms()
        events = self._events[(namespace, key)]
        cutoff = now - rule.window_ms
        while events and events[0] <= cutoff:
            events.popleft()
        return len(events) < rule.attempts

    def add(self, namespace: str, key: str, now_ms: int | None = None) -> None:
        self._events[(namespace, key)].append(now_ms if now_ms is not None else utc_now_ms())

    def clear(self, namespace: str, key: str) -> None:
        self._events.pop((namespace, key), None)


rate_limiter = InMemoryRateLimiter()
