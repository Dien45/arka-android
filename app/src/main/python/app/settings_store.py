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
}


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not SETTINGS_FILE.exists():
        out = dict(DEFAULTS)
        out["workspace"] = str(APP_DIR)
        return out
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        out = dict(DEFAULTS)
        out.update({k: v for k, v in data.items() if k in DEFAULTS})
        if not out.get("workspace"):
            out["workspace"] = str(APP_DIR)
        try:
            if int(out.get("max_iterations") or 0) <= 18:
                out["max_iterations"] = 40
        except (TypeError, ValueError):
            out["max_iterations"] = 40
        return out
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for key, value in patch.items():
        if key in DEFAULTS:
            current[key] = value
    ensure_dirs()
    SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current
