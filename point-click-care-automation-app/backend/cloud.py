"""Tiny HTTP client for the Gateway PCC server API (used by DESKTOP mode).

Standard-library only (``urllib``) so the packaged ``.exe`` stays small. Holds
the bearer token in memory and persists the chosen server URL + token via
``tokenstore`` so the user stays pointed at the right server and signed in across
restarts. The server URL is editable in the app (login screen) — no rebuild.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import config, tokenstore


class CloudError(Exception):
    """Raised when the server returns an error; carries an HTTP-ish status code."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CloudClient:
    def __init__(self):
        saved = tokenstore.load() or {}
        self.base_url = (saved.get("server_url") or config.DEFAULT_SERVER_URL).rstrip("/")
        self.token: Optional[str] = saved.get("token")
        self.user: Optional[dict] = saved.get("user")

    # -- server selection -------------------------------------------------
    def set_server(self, url: str) -> None:
        """Point at a different server. Changing it drops any existing login."""
        url = (url or "").strip().rstrip("/")
        if url and url != self.base_url:
            self.base_url = url
            self.token = None
            self.user = None
        tokenstore.save(self.base_url, self.token, self.user)

    # -- low-level request ------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        # Cloudflare's bot protection blocks Python's default "Python-urllib/x.y"
        # User-Agent outright (403 "browser_signature_banned"). A custom UA sidesteps
        # that without touching any Cloudflare-side config.
        req.add_header("User-Agent", "GatewayPCC-Desktop/1.0")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:  # noqa: BLE001
                pass
            raise CloudError(detail or f"Server returned {exc.code}.", exc.code)
        except urllib.error.URLError as exc:
            raise CloudError(
                f"Can't reach the Gateway server ({self.base_url}). {exc.reason}", 0
            )

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._request("POST", path, body=body)

    # -- auth -------------------------------------------------------------
    def _store_login(self, data: dict) -> dict:
        """Persist a completed login response (must contain a token)."""
        self.token = data.get("token")
        self.user = {"id": data.get("id"), "username": data.get("username"),
                     "role": data.get("role")}
        tokenstore.save(self.base_url, self.token, self.user)
        return self.user

    def login(self, username: str, password: str) -> dict:
        """Verify username + password against the server and complete the login."""
        data = self.post("/api/auth/login", {"username": username, "password": password})
        return self._store_login(data)

    def me(self) -> Optional[dict]:
        """Validate the stored token against the server; refresh cached user."""
        if not self.token:
            return None
        try:
            data = self.get("/api/auth/me")
        except CloudError:
            return None
        if not data.get("authenticated"):
            return None
        self.user = {"id": data.get("id"), "username": data.get("username"),
                     "role": data.get("role")}
        return self.user

    def logout(self) -> None:
        self.token = None
        self.user = None
        tokenstore.clear_token()

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.get("role") == "admin")
