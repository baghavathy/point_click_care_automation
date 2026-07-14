"""Local storage for generated report PDFs + their metadata (DESKTOP mode only).

Reports are generated and downloaded entirely on this machine (the cloud never
sees them) — a small JSON index lives next to the saved PDFs under the same
local data dir as the token store / automation log (see ``config.DESKTOP_DATA_DIR``).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config

REPORTS_DIR = config.DESKTOP_DATA_DIR / "reports"
FILES_DIR = REPORTS_DIR / "files"
INDEX_PATH = REPORTS_DIR / "index.json"


def _ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict[str, Any]]:
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_index(entries: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    INDEX_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def add_result(*, owner_id: Any, facility_id: int, facility_name: str, report_name: str,
               period_label: str, generated_at: str, file_bytes: bytes,
               kind: str = "pdf") -> dict[str, Any]:
    """Save ``file_bytes`` to disk and record it in the index. Returns the new
    entry. ``kind`` is "pdf" (the normal case) or "html" — a fallback capture
    used when we couldn't fetch real PDF bytes and saved the report page's raw
    HTML instead, so it isn't lost outright (needs manual PDF conversion)."""
    _ensure_dirs()
    result_id = uuid.uuid4().hex[:16]
    ext = "html" if kind == "html" else "pdf"
    filename = f"{result_id}.{ext}"
    (FILES_DIR / filename).write_bytes(file_bytes)
    entry = {
        "id": result_id,
        "owner_id": owner_id,
        "facility_id": facility_id,
        "facility_name": facility_name,
        "report_name": report_name,
        "period_label": period_label,
        "generated_at": generated_at,
        "filename": filename,
        "kind": kind,
        "size_bytes": len(file_bytes),
    }
    entries = _load_index()
    entries.append(entry)
    _save_index(entries)
    return entry


def list_results(owner_id: Any = None) -> list[dict[str, Any]]:
    """Newest first. ``owner_id=None`` returns everyone's (admin view)."""
    entries = _load_index()
    if owner_id is not None:
        entries = [e for e in entries if e.get("owner_id") == owner_id]
    return sorted(entries, key=lambda e: e.get("generated_at", ""), reverse=True)


def get_result(result_id: str) -> Optional[dict[str, Any]]:
    for e in _load_index():
        if e.get("id") == result_id:
            return e
    return None


def get_file_path(result_id: str) -> Optional[Path]:
    entry = get_result(result_id)
    if not entry:
        return None
    path = FILES_DIR / entry["filename"]
    return path if path.exists() else None
