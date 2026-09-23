"""Frozen mi-canon-v1 JSON and domain-separated digest contract.

This encoder is deliberately independent of Pydantic and json.dumps serialization.
Changing it requires a new canonicalization version, never an in-place adjustment.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from app.errors import AppError

SAFE_INTEGER = 9_007_199_254_740_991
MAX_JSON_NODES = 500_000
MAX_CONTAINER_ITEMS = 100_000
MAX_STRING_CHARACTERS = 16_384
CONTENT_PREFIX = b"LCC-MASTER-CONTENT-v1\x00"
PACKAGE_PREFIX = b"LCC-MASTER-PACKAGE-v1\x00"


def _invalid(reason: str) -> AppError:
    return AppError(422, "MASTER_IMPORT_JSON_INVALID", reason)


def _integer(token: str) -> int:
    if token == "-0":
        raise _invalid("Negative zero is prohibited in Master Import JSON.")
    value = int(token)
    if abs(value) > SAFE_INTEGER:
        raise _invalid("Master Import integers must be within the signed safe integer range.")
    return value


def _float(_token: str) -> None:
    raise _invalid("Master Import JSON does not permit floating-point or exponent numbers.")


def _constant(_token: str) -> None:
    raise _invalid("Master Import JSON does not permit nonstandard numeric constants.")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("Master Import JSON contains a duplicate object key.")
        result[key] = value
    return result


def _validate(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is not None:
        budget[0] += 1
        if budget[0] > MAX_JSON_NODES:
            raise _invalid("Master Import JSON has too many nodes.")
    if depth > 80:
        raise _invalid("Master Import JSON nesting is too deep.")
    if isinstance(value, str):
        if budget is not None and len(value) > MAX_STRING_CHARACTERS:
            raise _invalid("A Master Import string exceeds the authored field limit.")
        if unicodedata.normalize("NFC", value) != value or any(
            0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            raise _invalid("Master Import strings must be NFC Unicode scalar sequences.")
    elif isinstance(value, dict):
        if budget is not None and len(value) > MAX_CONTAINER_ITEMS:
            raise _invalid("A Master Import object has too many fields.")
        for key, item in value.items():
            if not isinstance(key, str):
                raise _invalid("Master Import object keys must be strings.")
            _validate(key, depth + 1, budget)
            _validate(item, depth + 1, budget)
    elif isinstance(value, list):
        if budget is not None and len(value) > MAX_CONTAINER_ITEMS:
            raise _invalid("A Master Import array has too many items.")
        for item in value:
            _validate(item, depth + 1, budget)
    elif type(value) is int:
        if abs(value) > SAFE_INTEGER:
            raise _invalid("Master Import integers must be within the signed safe integer range.")
    elif value is not None and type(value) is not bool:
        raise _invalid("Master Import JSON contains an unsupported value type.")


def parse_transport_text(raw_text: str, max_bytes: int) -> dict[str, Any]:
    if not isinstance(raw_text, str) or raw_text.startswith("\ufeff"):
        raise _invalid("Master Import JSON must be UTF-8 text without a BOM.")
    try:
        encoded = raw_text.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise _invalid("Master Import JSON contains invalid Unicode.") from exc
    if len(encoded) > max_bytes:
        raise AppError(413, "IMPORT_TOO_LARGE", "The import package is too large.")
    try:
        result = json.loads(
            raw_text,
            parse_int=_integer,
            parse_float=_float,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
    except AppError:
        raise
    except (ValueError, RecursionError) as exc:
        raise _invalid("Master Import JSON is malformed or exceeds parser limits.") from exc
    if not isinstance(result, dict):
        raise _invalid("Master Import JSON must have an object at its root.")
    _validate(result, budget=[0])
    return result


_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _string(value: str) -> str:
    pieces = ['"']
    for char in value:
        if char == '"' or char == "\\":
            pieces.append("\\" + char)
        elif char in _ESCAPES:
            pieces.append(_ESCAPES[char])
        elif ord(char) < 0x20:
            pieces.append(f"\\u00{ord(char):02x}")
        else:
            pieces.append(char)
    pieces.append('"')
    return "".join(pieces)


def canonical_bytes(value: Any) -> bytes:
    _validate(value)

    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if type(item) is int:
            return str(item)
        if isinstance(item, str):
            return _string(item)
        if isinstance(item, list):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, dict):
            keys = sorted(item, key=lambda key: key.encode("utf-8"))
            return "{" + ",".join(_string(key) + ":" + encode(item[key]) for key in keys) + "}"
        raise _invalid("Master Import JSON contains an unsupported value type.")

    return encode(value).encode("utf-8")


def transport_text_digest(raw_text: str) -> str:
    return "mi-text-v1:sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def package_digest(package: dict[str, Any]) -> str:
    return (
        "mi-package-v1:sha256:"
        + hashlib.sha256(PACKAGE_PREFIX + canonical_bytes(package)).hexdigest()
    )


def semantic_content(package: dict[str, Any]) -> dict[str, Any]:
    payload = package["payload"]
    return {
        "packageType": package["packageType"],
        "schemaVersion": package["schemaVersion"],
        **{
            key: payload[key]
            for key in (
                "canonicalizationVersion",
                "lineageKey",
                "ownerKey",
                "effectiveAt",
                "activationIntent",
                "competencies",
                "targetProfile",
                "curriculum",
                "learningGraph",
                "initialSpine",
                "removedFromActiveVersion",
            )
        },
    }


def content_digest(package: dict[str, Any]) -> str:
    return (
        "mi-content-v1:sha256:"
        + hashlib.sha256(CONTENT_PREFIX + canonical_bytes(semantic_content(package))).hexdigest()
    )
