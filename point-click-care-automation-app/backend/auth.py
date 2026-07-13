"""Stateless API tokens for the desktop agent.

The website (browser) authenticates with a normal Flask **session cookie**.
The desktop ``.exe`` cannot rely on cookies across restarts, so it authenticates
with a signed **bearer token** instead. Both are accepted by the API.

Tokens are signed (not encrypted) with the server secret using ``itsdangerous``
— a dependency Flask already pulls in, so nothing new to install. A token simply
carries the user's id/username/role and an expiry; revoking is done by changing
the server secret or letting it lapse.
"""
from __future__ import annotations

from typing import Any, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Tokens are valid for 30 days; the desktop silently re-logs in when one lapses.
TOKEN_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_SALT = "gateway-pcc-desktop-token"


def _serializer(secret: bytes) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def make_token(secret: bytes, user: dict[str, Any]) -> str:
    """Sign a token for ``user`` (expects keys: id, username, role)."""
    return _serializer(secret).dumps(
        {"id": user["id"], "username": user["username"], "role": user["role"]}
    )


def read_token(secret: bytes, token: str) -> Optional[dict[str, Any]]:
    """Return the user payload if the token is valid and unexpired, else None."""
    try:
        return _serializer(secret).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired, Exception):  # noqa: BLE001
        return None
