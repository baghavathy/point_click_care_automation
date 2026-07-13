"""Persist the desktop's chosen server URL + login token between restarts.

Stored as a small JSON file under the user's local data dir so the user does not
have to re-enter the server or sign in on every launch.
"""
from __future__ import annotations

import json
from typing import Optional

from . import config


def save(server_url: str, token: Optional[str], user: Optional[dict]) -> None:
    config.ensure_desktop_dirs()
    config.TOKEN_PATH.write_text(
        json.dumps({"server_url": server_url, "token": token, "user": user}),
        encoding="utf-8",
    )


def load() -> Optional[dict]:
    try:
        data = json.loads(config.TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_token() -> None:
    """Forget the login but keep the chosen server URL."""
    data = load() or {}
    save(data.get("server_url", ""), None, None)
