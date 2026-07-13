"""Configuration for Gateway PCC — one codebase, two run modes.

  * SERVER mode  (``python -m backend.server``)  — the website + encrypted vault.
  * DESKTOP mode (``python -m backend.desktop``) — the local launcher/agent.

The two modes keep their state in different places, so the helpers below are
split into ``*_server`` and ``*_desktop`` variants. A machine only ever runs one
mode at a time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _app_dir() -> Path:
    """Folder the app runs from — works from source and from a packaged .exe."""
    if getattr(sys, "frozen", False):  # PyInstaller one-file exe
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


BASE_DIR = _app_dir()

# Load .env from the app root (next to pyproject.toml) before reading any vars.
# Real values live in .env (gitignored); .env.example documents the shape.
load_dotenv(BASE_DIR / ".env")
FRONTEND_DIR = BASE_DIR / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

# --- SERVER state (the website): MongoDB + encryption keys -------------------
# Data lives in MongoDB; only the encryption/signing keys come from .env.
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "gateway_pcc")

# Bootstrap admin — upserted into Mongo on startup (password stored hashed).
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin@gatewaypcc.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Keys (required in production via .env). Empty -> auto-generated per process,
# which signs out users on restart and can't decrypt prior data: set them in .env.
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# --- DESKTOP state: saved login token + chosen server URL + automation log ---
DESKTOP_DATA_DIR = Path(
    os.environ.get("GATEWAY_DATA_DIR", "") or (Path.home() / ".gateway-pcc")
)
TOKEN_PATH = DESKTOP_DATA_DIR / "token.json"

# Default server the desktop points at. The user can change this in the app's
# login screen (it's saved), so the exe never needs rebuilding to switch servers.
DEFAULT_SERVER_URL = os.environ.get(
    "GATEWAY_CLOUD_URL", "https://pcc.arithmed.com"
).rstrip("/")

# --- Network ----------------------------------------------------------------
# The two modes use different default ports so they can run side by side on one
# machine (server = 5000, desktop = 5050). GATEWAY_PORT overrides either.
HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("GATEWAY_PORT") or 5000)
DESKTOP_PORT = int(os.environ.get("GATEWAY_PORT") or 5050)


def ensure_server_dirs() -> None:
    # Server state now lives in MongoDB; no local data dir to create. Kept as a
    # no-op so existing callers (database/server bootstrap) stay unchanged.
    return None


def ensure_desktop_dirs() -> None:
    DESKTOP_DATA_DIR.mkdir(parents=True, exist_ok=True)
