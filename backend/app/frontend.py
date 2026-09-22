"""Serve the verified release frontend through the application origin."""

from __future__ import annotations

import re
from mimetypes import guess_type
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

FRONTEND_CSP = (
    "default-src 'self'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
_HASHED_ASSET = re.compile(r"^.+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")
_PUBLIC_ROOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp", ".txt", ".webmanifest"}


def install_frontend_routes(app: FastAPI, release_root: Path) -> None:
    """Register the final GET/HEAD route after every API router."""
    dist = release_root / "frontend" / "dist"

    @app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def frontend(request: Request, frontend_path: str) -> Response:
        if frontend_path == "api" or frontend_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        if not frontend_path:
            requested = "index.html"
        else:
            relative = PurePosixPath(frontend_path)
            if (
                frontend_path.startswith("/")
                or "\\" in frontend_path
                or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts)
                or relative.parts[0] == "api"
            ):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            requested = frontend_path

        if requested != "index.html":
            relative = PurePosixPath(requested)
            if relative.parts[0] == "assets":
                if len(relative.parts) < 2:
                    return JSONResponse({"detail": "Not Found"}, status_code=404)
            elif len(relative.parts) != 1 or (
                relative.suffix.lower() not in _PUBLIC_ROOT_SUFFIXES
                and requested != "IMPORT_EXPORT_FORMAT.md"
            ):
                if relative.suffix or relative.parts[0] == "assets":
                    return JSONResponse({"detail": "Not Found"}, status_code=404)
                requested = "index.html"

        target = dist / requested
        if (
            not target.is_file()
            or target.is_symlink()
            or not target.resolve().is_relative_to(dist.resolve())
        ):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # The build verifier rejects symlinks. Keep the runtime boundary explicit as well.
        if any(
            parent.is_symlink()
            for parent in target.parents
            if parent != dist and dist in parent.parents
        ):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        request.scope["lcc_frontend"] = True
        headers = {"Cache-Control": "no-store"}
        if requested.startswith("assets/") and _HASHED_ASSET.fullmatch(target.name):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        media_type = guess_type(target.name)[0] or "application/octet-stream"
        if request.method == "HEAD":
            headers["Content-Length"] = str(target.stat().st_size)
            return Response(content=b"", media_type=media_type, headers=headers)
        return Response(content=target.read_bytes(), media_type=media_type, headers=headers)
