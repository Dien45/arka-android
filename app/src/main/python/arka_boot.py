from __future__ import annotations

import os
from pathlib import Path


def start_server(home: str | None = None, workspace: str | None = None) -> None:
    if home:
        os.environ["ARKA_HOME"] = home
    if workspace:
        os.environ["ARKA_WORKSPACE"] = workspace
    home_p = Path(os.environ.get("ARKA_HOME") or (Path.home() / ".arka"))
    home_p.mkdir(parents=True, exist_ok=True)
    ws = os.environ.get("ARKA_WORKSPACE")
    if ws:
        Path(ws).mkdir(parents=True, exist_ok=True)

    from app.paths import ensure_dirs
    from app.settings_store import load_settings, save_settings

    ensure_dirs()
    s = load_settings()
    if ws and (not s.get("workspace") or s.get("workspace") == str(Path(__file__).resolve().parent)):
        save_settings({"workspace": ws})

    import uvicorn
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
