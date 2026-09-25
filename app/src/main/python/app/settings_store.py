from __future__ import annotations

import json
from typing import Any

from .paths import APP_DIR, SETTINGS_FILE, ensure_dirs

DEFAULTS: dict[str, Any] = {
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "workspace": "",
    "max_iterations": 40,
    "github_username": "",
    "github_token": "",
    "github_repo": "Dien45/arka-android",
    "model_list": [
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
        {"id": "gpt-3.5", "name": "GPT-3.5"},
    ],
}


def _clean_api_key(v: Any) -> str:
    s = str(v or "").strip()
    # JANGAN isi api_key="Tutup" — treat legacy placeholder as empty
    if s.lower() == "tutup":
        return ""
    return s


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not SETTINGS_FILE.exists():
        out = dict(DEFAULTS)
        out["workspace"] = str(APP_DIR)
        return out
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        out = dict(DEFAULTS)
        # only allow known keys
        for k in DEFAULTS:
            if k in data:
                out[k] = data[k]
        # legacy api_key="Tutup" -> empty
        out["api_key"] = _clean_api_key(out.get("api_key"))
        if not out.get("workspace"):
            out["workspace"] = str(APP_DIR)
        # also load github fields if stored (already in DEFAULTS)
        # normalize repo
        repo = str(out.get("github_repo") or "").strip()
        if repo:
            # allow full URL, extract user/repo
            if "github.com/" in repo:
                repo = repo.split("github.com/")[-1].strip().strip("/").replace(".git", "")
            out["github_repo"] = repo[:200]
        try:
            if int(out.get("max_iterations") or 0) <= 18:
                out["max_iterations"] = 40
        except (TypeError, ValueError):
            out["max_iterations"] = 40
        return out
    except (OSError, json.JSONDecodeError):
        out = dict(DEFAULTS)
        out["workspace"] = str(APP_DIR)
        return out


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for key, value in patch.items():
        if key in DEFAULTS:
            if key == "api_key":
                current[key] = _clean_api_key(value)
            elif key == "github_repo":
                s = str(value or "").strip()
                if "github.com/" in s:
                    s = s.split("github.com/")[-1].strip().strip("/").replace(".git", "")
                current[key] = s[:200]
            else:
                current[key] = value
    ensure_dirs()
    SETTINGS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current
