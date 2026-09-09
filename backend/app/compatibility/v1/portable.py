from __future__ import annotations

from typing import Any


class UnsupportedV1PortableSchema(ValueError):
    pass


def read_v1_portable_package(package: dict[str, Any]) -> dict[str, Any]:
    """Validate only the frozen V1 envelope before handing it to the upgrader."""
    if package.get("schemaVersion") != 1:
        raise UnsupportedV1PortableSchema("The V1 reader only accepts schemaVersion 1.")
    if package.get("packageType") not in {"portable_logical_backup", "restore"}:
        raise UnsupportedV1PortableSchema("The package is not a V1 portable backup.")
    payload = package.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise UnsupportedV1PortableSchema("The V1 portable payload is malformed.")
    return package
