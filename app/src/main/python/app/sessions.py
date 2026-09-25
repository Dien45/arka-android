from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import SESSIONS_DIR, ensure_dirs


def _path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def new_session(workspace: str, model: str = "") -> dict[str, Any]:
    ensure_dirs()
    sid = uuid.uuid4().hex[:12]
    session = {
        "id": sid,
        "title": "Sesi baru",
        "created": time.time(),
        "updated": time.time(),
        "workspace": workspace,
        "mode": "build",
        "model": model or "",
        "messages": [],
        "ui": [],
        "todos": [],
        "undo": [],
    }
    save_session(session)
    return session


def save_session(session: dict[str, Any]) -> None:
    ensure_dirs()
    session["updated"] = time.time()
    _path(session["id"]).write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(sid: str) -> dict[str, Any] | None:
    p = _path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_sessions() -> list[dict[str, Any]]:
    ensure_dirs()
    items = []
    for file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            items.append(
                {
                    "id": data.get("id", file.stem),
                    "title": data.get("title", "Sesi"),
                    "updated": data.get("updated", 0),
                    "mode": data.get("mode", "build"),
                    "model": data.get("model", ""),
                    "workspace": data.get("workspace", ""),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def delete_session(sid: str) -> bool:
    p = _path(sid)
    if p.exists():
        p.unlink()
        return True
    return False


def rename_session(sid: str, title: str | None) -> dict[str, Any] | None:
    # legacy wrapper, but now also supports model via update_session
    return update_session(sid, title=title)


def update_session(sid: str, title: str | None = None, model: str | None = None) -> dict[str, Any] | None:
    session = load_session(sid)
    if not session:
        return None
    if title is not None:
        session["title"] = (title or "").strip()[:80] or "Sesi"
    if model is not None:
        try:
            from .agent import sanitize_model

            session["model"] = sanitize_model(model)
        except Exception:
            session["model"] = (model or "").strip()[:240]
    save_session(session)
    return session
