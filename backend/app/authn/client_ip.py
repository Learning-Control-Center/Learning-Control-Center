from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from fastapi import Request

from app.config import Settings
from app.errors import AppError

IPAddress = IPv4Address | IPv6Address


def _literal(value: str) -> IPAddress:
    candidate = value.strip()
    if not candidate or candidate.startswith("[") or "%" in candidate:
        raise ValueError("Client addresses must be unadorned IP literals.")
    return ip_address(candidate)


def resolve_client_ip(request: Request, settings: Settings) -> str:
    """Resolve X-Forwarded-For right-to-left, trusting only configured proxy hops."""
    peer_text = request.client.host if request.client else "0.0.0.0"
    try:
        peer = _literal(peer_text)
    except ValueError as exc:
        raise AppError(400, "CLIENT_ADDRESS_INVALID", "The client address is invalid.") from exc
    trusted = [ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs]
    if not any(peer in network for network in trusted):
        return peer.compressed

    forwarded_values = request.headers.getlist("x-forwarded-for")
    if not forwarded_values:
        return peer.compressed
    if len(forwarded_values) != 1:
        raise AppError(400, "FORWARDED_FOR_INVALID", "Forwarded client data is ambiguous.")
    try:
        chain = [_literal(item) for item in forwarded_values[0].split(",")]
    except ValueError as exc:
        raise AppError(400, "FORWARDED_FOR_INVALID", "Forwarded client data is malformed.") from exc
    if not chain:
        raise AppError(400, "FORWARDED_FOR_INVALID", "Forwarded client data is malformed.")
    current = peer
    for candidate in reversed(chain):
        if not any(current in network for network in trusted):
            break
        current = candidate
    return current.compressed
