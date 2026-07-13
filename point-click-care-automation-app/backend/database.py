"""MongoDB storage layer with field-level encryption for secrets.

Collections
-----------
users       : login accounts (username, bcrypt-style password hash, role).
clients     : top-level organisations, scoped to an owner (user id).
facilities  : individual sites under a client. Each facility carries its own
              login URL, credentials and an optional TOTP secret (imported from
              a QR code or pasted as a hash).
settings    : single document of key/value pairs (proxy / FoxyProxy config).
counters    : auto-increment sequence per collection, so documents keep small
              integer ``id`` values (the API/URLs use ``<int:id>`` routes).

Passwords and TOTP secrets are encrypted at rest with a Fernet key taken from
``SECRET_KEY`` in ``.env``. The connection string comes from ``MONGODB_URI``.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet, InvalidToken
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from werkzeug.security import check_password_hash, generate_password_hash

from . import config


def normalize_totp_secret(raw: Optional[str]) -> Optional[str]:
    """Clean a pasted TOTP secret into bare base32.

    Accepts a raw secret, a spaced/hyphenated secret, or a full
    ``otpauth://...?secret=XXXX`` URI, and returns uppercase base32 (A-Z2-7).
    """
    if not raw:
        return raw
    s = raw.strip()
    if s.lower().startswith("otpauth://"):
        secret = parse_qs(urlparse(s).query).get("secret", [s])[0]
        s = secret or s
    return re.sub(r"[^A-Za-z2-7]", "", s).upper()


# --------------------------------------------------------------------------
# Encryption helpers
# --------------------------------------------------------------------------
def _load_key() -> bytes:
    """Fernet key for at-rest secret encryption, from SECRET_KEY in .env.

    If unset, a per-process key is generated as a fallback so the app still
    runs in dev — but stored secrets won't decrypt after a restart, so set
    SECRET_KEY in .env for any real use.
    """
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode("utf-8")
    return Fernet.generate_key()


_fernet = Fernet(_load_key())


def encrypt(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    try:
        return _fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Value was stored unencrypted (e.g. legacy/manual edit) -> return as-is.
        return value


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
_client: Optional[MongoClient] = None


def _db():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGODB_URI)
    return _client[config.MONGODB_DB]


def _next_id(name: str) -> int:
    """Atomically increment and return the next integer id for a collection."""
    doc = _db().counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return int(doc["seq"])


def init_db() -> None:
    """Create indexes, seed default settings, and bootstrap the .env admin."""
    db = _db()
    db.users.create_index([("username", ASCENDING)], unique=True)
    db.clients.create_index([("owner_id", ASCENDING)])
    db.facilities.create_index([("owner_id", ASCENDING)])
    db.facilities.create_index([("client_id", ASCENDING)])

    # Seed default proxy / FoxyProxy settings once (never overwrite existing).
    defaults = {
        "site_url": "https://pointclickcare.com/login/",
        "login_url": "https://login.pointclickcare.com/home/userLogin.xhtml",
        "proxy_enabled": "0",
        "proxy_scheme": "http",
        "proxy_host": "",
        "proxy_port": "",
        "proxy_username": "",
        "proxy_password": "",
        "proxy_socks_remote_dns": "1",
        "foxyproxy_xpi": "",
        "remember_device": "1",
        "otp_wait_seconds": "30",
        "logout_menu_selector": ".evergreen-emar-text-body2",
        "logout_selector": "",
        "logout_arrow_down_count": "4",
        "logout_step_delay": "4",
        "logout_url": "",
        "headless": "0",
    }
    for key, value in defaults.items():
        db.settings.update_one(
            {"key": key}, {"$setOnInsert": {"value": value}}, upsert=True
        )

    _bootstrap_admin()


def _bootstrap_admin() -> None:
    """Upsert the admin defined in .env (ADMIN_USERNAME/ADMIN_PASSWORD).

    The password is stored hashed. Re-running keeps the same id and refreshes
    the hash so changing .env + restart rotates the admin login.
    """
    username = (config.ADMIN_USERNAME or "").strip()
    password = config.ADMIN_PASSWORD or ""
    if not username or not password:
        return
    db = _db()
    existing = db.users.find_one({"username": username})
    if existing is None:
        db.users.insert_one(
            {
                "id": _next_id("users"),
                "username": username,
                "password_hash": generate_password_hash(password),
                "role": "admin",
                "created": _now(),
            }
        )
    else:
        db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"password_hash": generate_password_hash(password), "role": "admin"}},
        )


def _now() -> str:
    """UTC timestamp string (avoids importing datetime everywhere)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Users / auth
# --------------------------------------------------------------------------
def _user_public(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
    }


def create_user(username: str, password: str, role: str = "user") -> dict[str, Any]:
    role = "admin" if role == "admin" else "user"
    username = username.strip()
    new_id = _next_id("users")
    _db().users.insert_one(
        {
            "id": new_id,
            "username": username,
            "password_hash": generate_password_hash(password),
            "role": role,
            "created": _now(),
        }
    )
    return {"id": new_id, "username": username, "role": role}


def verify_user(username: str, password: str) -> Optional[dict[str, Any]]:
    row = _db().users.find_one({"username": username.strip()})
    if row and check_password_hash(row["password_hash"], password):
        return _user_public(row)
    return None


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    row = _db().users.find_one({"id": user_id})
    return _user_public(row) if row else None


def list_users() -> list[dict[str, Any]]:
    rows = _db().users.find().sort("username", ASCENDING)
    return [_user_public(r) for r in rows]


def delete_user(user_id: int) -> None:
    _db().users.delete_one({"id": user_id})


def set_password(user_id: int, password: str) -> None:
    _db().users.update_one(
        {"id": user_id},
        {"$set": {"password_hash": generate_password_hash(password)}},
    )


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def list_clients(owner_id: int) -> list[dict[str, Any]]:
    rows = _db().clients.find({"owner_id": owner_id}).sort("name", ASCENDING)
    return [{"id": r["id"], "name": r["name"], "owner_id": r["owner_id"]} for r in rows]


def create_client(owner_id: int, name: str) -> dict[str, Any]:
    new_id = _next_id("clients")
    _db().clients.insert_one({"id": new_id, "name": name, "owner_id": owner_id})
    return {"id": new_id, "name": name}


def client_owner(client_id: int) -> Optional[int]:
    row = _db().clients.find_one({"id": client_id})
    return row["owner_id"] if row else None


def delete_client(client_id: int) -> None:
    # Mirror the old SQLite ON DELETE CASCADE: remove the client's facilities too.
    _db().facilities.delete_many({"client_id": client_id})
    _db().clients.delete_one({"id": client_id})


# --------------------------------------------------------------------------
# Facilities
# --------------------------------------------------------------------------
_FACILITY_FIELDS = (
    "id", "client_id", "owner_id", "name", "location", "site_url", "username",
    "username_selector", "password_selector", "submit_selector", "totp_selector",
)


def _facility_to_public(row: dict) -> dict[str, Any]:
    """Shape a doc for the API. Secrets are flagged, never exposed."""
    d = {k: row.get(k) for k in _FACILITY_FIELDS}
    d["has_password"] = bool(row.get("password_enc"))
    d["has_totp"] = bool(row.get("totp_secret_enc"))
    return d


def list_facilities(owner_id: int, client_id: Optional[int] = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"owner_id": owner_id}
    if client_id is not None:
        query["client_id"] = client_id
    rows = _db().facilities.find(query).sort("name", ASCENDING)
    return [_facility_to_public(r) for r in rows]


def get_facility_secrets(facility_id: int) -> Optional[dict[str, Any]]:
    """Full record including decrypted secrets — used by the automation layer."""
    row = _db().facilities.find_one({"id": facility_id})
    if row is None:
        return None
    d = {k: row.get(k) for k in _FACILITY_FIELDS}
    d["password"] = decrypt(row.get("password_enc"))
    d["totp_secret"] = decrypt(row.get("totp_secret_enc"))
    return d


_EDITABLE = (
    "name", "location", "site_url", "username",
    "username_selector", "password_selector", "submit_selector", "totp_selector",
)


def create_facility(owner_id: int, client_id: int, data: dict[str, Any]) -> int:
    new_id = _next_id("facilities")
    _db().facilities.insert_one(
        {
            "id": new_id,
            "owner_id": owner_id,
            "client_id": client_id,
            "name": data.get("name", ""),
            "location": data.get("location", ""),
            "site_url": data.get("site_url", ""),
            "username": data.get("username", ""),
            "password_enc": encrypt(data.get("password")),
            "totp_secret_enc": encrypt(normalize_totp_secret(data.get("totp_secret"))),
            "username_selector": data.get("username_selector", ""),
            "password_selector": data.get("password_selector", ""),
            "submit_selector": data.get("submit_selector", ""),
            "totp_selector": data.get("totp_selector", ""),
        }
    )
    return new_id


def update_facility(facility_id: int, data: dict[str, Any]) -> None:
    updates: dict[str, Any] = {}
    for field in _EDITABLE:
        if field in data:
            updates[field] = data[field]
    # Only overwrite secrets when a non-empty value is supplied.
    if data.get("password"):
        updates["password_enc"] = encrypt(data["password"])
    if data.get("totp_secret"):
        updates["totp_secret_enc"] = encrypt(normalize_totp_secret(data["totp_secret"]))
    if not updates:
        return
    _db().facilities.update_one({"id": facility_id}, {"$set": updates})


def delete_facility(facility_id: int) -> None:
    _db().facilities.delete_one({"id": facility_id})


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def get_settings() -> dict[str, str]:
    rows = _db().settings.find()
    return {r["key"]: r["value"] for r in rows}


def update_settings(data: dict[str, Any]) -> dict[str, str]:
    coll: Collection = _db().settings
    for key, value in data.items():
        # The proxy password is sensitive: encrypt it, and never overwrite a
        # stored password with a blank (lets the UI omit it on resave).
        if key == "proxy_password":
            if not value:
                continue
            value = encrypt(str(value))
        coll.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)
    return get_settings()


def get_proxy_password() -> str:
    """Decrypted proxy password for the automation layer."""
    row = _db().settings.find_one({"key": "proxy_password"})
    return decrypt(row["value"]) if row and row.get("value") else ""
