"""Streaming HTTP request limits for the existing import endpoints."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_IMPORT_PATHS = {
    "/api/v1/import-export/import/inspect",
    "/api/v1/import-export/import/apply",
}


class _ImportBodyTooLarge(Exception):
    pass


class ImportBodyLimit:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _IMPORT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        lengths = [
            value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
        ]
        if lengths and (
            len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > self.max_body_bytes
        ):
            await self._reject(scope, receive, send)
            return

        total = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_body_bytes:
                    raise _ImportBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except _ImportBodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "IMPORT_TOO_LARGE",
                    "message": "The import request body is too large.",
                }
            },
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        await response(scope, receive, send)
