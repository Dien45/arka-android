from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import mimetypes

from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tools
from .agent import request_stop, run_agent
from .paths import APP_DIR
from .sessions import delete_session, list_sessions, load_session, new_session, save_session, update_session
from .settings_store import load_settings, save_settings

WEB = APP_DIR / "web"

app = FastAPI(title="Arka")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsIn(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    workspace: str | None = None
    max_iterations: int | None = None


class SessionIn(BaseModel):
    workspace: str | None = None
    model: str | None = None


class ChatImage(BaseModel):
    name: str = ""
    mime: str = "image/png"
    data: str = ""


class ChatIn(BaseModel):
    text: str = ""
    mode: str = "build"
    images: list[ChatImage] | None = None


class WorkspaceIn(BaseModel):
    path: str


class SessionPatch(BaseModel):
    title: str | None = None
    model: str | None = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"ok": "arka"}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    s = load_settings()
    key = s.get("api_key") or ""
    s["api_key_set"] = bool(key)
    s["api_key"] = key
    try:
        resolved = str(tools.pick_workspace(s.get("workspace")))
        if resolved != s.get("workspace"):
            save_settings({"workspace": resolved})
        s["workspace"] = resolved
        s["workspace_ok"] = True
    except ValueError as e:
        s["workspace_ok"] = False
        s["workspace_error"] = str(e)
        s["workspace"] = str(APP_DIR)
    s["suggested_workspace"] = str(APP_DIR)
    return s


def _body_dict(body: Any) -> dict[str, Any]:
    if hasattr(body, "model_dump"):
        return body.model_dump()
    if hasattr(body, "dict"):
        return body.dict()
    return {}


@app.api_route("/api/settings", methods=["PUT", "POST"])
def put_settings(body: SettingsIn) -> dict[str, Any]:
    from .agent import sanitize_model

    patch = {k: v for k, v in _body_dict(body).items() if v is not None}
    if "model" in patch:
        patch["model"] = sanitize_model(str(patch["model"] or ""))
    if "workspace" in patch:
        raw = str(patch.get("workspace") or "").strip()
        if not raw:
            patch.pop("workspace", None)
        else:
            p = Path(raw).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            try:
                patch["workspace"] = str(tools.resolve_workspace(str(p)))
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
    s = save_settings(patch)
    s["api_key_set"] = bool(s.get("api_key"))
    return s


@app.get("/api/sessions")
def get_sessions() -> list[dict[str, Any]]:
    return list_sessions()


@app.post("/api/sessions")
def create_session(body: SessionIn | None = None) -> dict[str, Any]:
    settings = load_settings()
    raw = (body.workspace if body else None) or settings.get("workspace") or str(APP_DIR)
    try:
        ws = str(tools.pick_workspace(raw))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    save_settings({"workspace": ws})
    from .agent import sanitize_model

    model = sanitize_model((body.model if body else None) or settings.get("model") or "")
    return new_session(ws, model=model)


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    s = load_session(sid)
    if not s:
        raise HTTPException(404, "Sesi tidak ada")
    return s


@app.patch("/api/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch) -> dict[str, Any]:
    s = rename_session(sid, body.title)
    if not s:
        raise HTTPException(404, "Sesi tidak ada")
    return s


@app.delete("/api/sessions/{sid}")
def remove_session(sid: str) -> dict[str, bool]:
    ok = delete_session(sid)
    if not ok:
        raise HTTPException(404, "Sesi tidak ada")
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop")
def stop_session(sid: str) -> dict[str, bool]:
    request_stop(sid)
    return {"ok": True}


@app.post("/api/sessions/{sid}/undo")
def undo_session(sid: str) -> dict[str, Any]:
    s = load_session(sid)
    if not s:
        raise HTTPException(404, "Sesi tidak ada")
    stack = s.get("undo") or []
    if not stack:
        return {"ok": False, "message": "Tidak ada perubahan untuk di-undo"}
    snaps = stack.pop()
    n = tools.restore_snapshots(snaps)
    s["undo"] = stack
    s["ui"].append({"role": "system", "text": f"Undo: {n} file dikembalikan."})
    save_session(s)
    return {"ok": True, "restored": n, "session": s}


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: str, body: ChatIn) -> StreamingResponse:
    session = load_session(sid)
    if not session:
        raise HTTPException(404, "Sesi tidak ada")
    settings = load_settings()
    from .agent import normalize_mode

    mode = normalize_mode(body.mode)
    session["mode"] = mode
    text = (body.text or "").strip() or "Lihat file terlampir."
    images = [
        {"name": im.name, "mime": im.mime or "image/png", "data": im.data or ""}
        for im in (body.images or [])
        if (im.data or "").strip()
    ]

    async def gen():
        async for event in run_agent(session, text, settings, mode, images=images):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class ModelsIn(BaseModel):
    api_base: str | None = None
    api_key: str | None = None


def _normalize_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    return u


@app.post("/api/models")
async def list_models(body: ModelsIn | None = None) -> dict[str, Any]:
    from .agent import fetch_model_catalog, sanitize_model

    settings = load_settings()
    if body:
        if body.api_base:
            settings = {**settings, "api_base": body.api_base}
        if body.api_key is not None:
            settings = {**settings, "api_key": body.api_key}
    catalog = await fetch_model_catalog(settings)
    ids = [m["id"] for m in catalog]
    current = sanitize_model(settings.get("model") or "")
    return {
        "models": ids,
        "items": catalog,
        "base": _normalize_base(settings.get("api_base") or ""),
        "count": len(ids),
        "current": current,
    }


@app.post("/api/clipboard-image")
def clipboard_image() -> dict[str, Any]:
    data = tools.read_clipboard_image()
    if not data:
        return {"ok": False}
    return {"ok": True, **data}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    settings = load_settings()
    try:
        ws = str(tools.pick_workspace(settings.get("workspace")))
        dest = tools.safe_path(ws, "inbox")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise HTTPException(400, tools.permission_hint(str(dest))) from e
    saved: list[dict[str, Any]] = []
    for item in files[:12]:
        raw_name = Path(item.filename or "file").name
        name = re.sub(r"[^\w.\-() +]", "_", raw_name).strip("._") or "file"
        data = await item.read()
        if len(data) > 8_000_000:
            saved.append({"name": name, "error": "lebih dari 8MB, dilewati"})
            continue
        target = dest / name
        target.write_bytes(data)
        rel = str(target.relative_to(ws)).replace("\\", "/")
        text = ""
        if len(data) < 250_000:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
        saved.append({"path": rel, "name": name, "size": len(data), "content": text[:60000]})
    return {"files": saved}


@app.get("/api/tree")
def get_tree(path: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    ws = path or settings.get("workspace") or str(APP_DIR)
    try:
        return {"workspace": str(Path(ws).expanduser().resolve()), "nodes": tools.tree(ws)}
    except PermissionError as e:
        raise HTTPException(400, tools.permission_hint(ws)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class ReadIn(BaseModel):
    rel: str
    workspace: str | None = None


def _file_payload(rel: str, workspace: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    ws = workspace or settings.get("workspace") or str(APP_DIR)
    ws = str(tools.pick_workspace(ws))
    target = tools.safe_path(ws, rel.replace("\\", "/"))
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File tidak ada: {rel}")
    mime, _ = mimetypes.guess_type(str(target))
    mime = mime or "application/octet-stream"
    kind = "code"
    suffix = target.suffix.lower()
    if suffix in {".html", ".htm"}:
        kind = "html"
    elif suffix in {".md", ".markdown"}:
        kind = "markdown"
    elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}:
        kind = "image"
    elif suffix == ".pdf":
        kind = "pdf"
    elif suffix in {".mp3", ".wav", ".ogg"}:
        kind = "audio"
    elif suffix in {".mp4", ".webm"}:
        kind = "video"
    text = ""
    if kind in {"code", "html", "markdown"} and target.stat().st_size < 1_500_000:
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            text = f"(gagal baca: {e})"
    return {
        "path": rel.replace("\\", "/"),
        "name": target.name,
        "mime": mime,
        "kind": kind,
        "size": int(target.stat().st_size),
        "content": text,
        "workspace": ws,
    }


@app.get("/api/file")
def get_file(rel: str = "", path: str = "", workspace: str | None = None) -> dict[str, Any]:
    name = rel or path
    if not name:
        raise HTTPException(400, "rel/path kosong")
    try:
        return _file_payload(name, workspace)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@app.post("/api/read-file")
def read_file_post(body: ReadIn) -> dict[str, Any]:
    try:
        return _file_payload(body.rel, body.workspace)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@app.get("/api/preview-file")
def preview_file(path: str, workspace: str | None = None):
    settings = load_settings()
    ws = workspace or settings.get("workspace") or str(APP_DIR)
    try:
        ws = str(tools.pick_workspace(ws))
        target = tools.safe_path(ws, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File tidak ada")
    mime, _ = mimetypes.guess_type(str(target))
    if target.suffix.lower() in {".html", ".htm"}:
        mime = "text/html; charset=utf-8"
    return FileResponse(
        target,
        media_type=mime or "application/octet-stream",
        content_disposition_type="inline",
    )


@app.put("/api/workspace")
def set_workspace(body: WorkspaceIn) -> dict[str, Any]:
    p = Path(body.path).expanduser()
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, "Folder tidak ditemukan")
    resolved = str(p.resolve())
    save_settings({"workspace": resolved})
    return {"workspace": resolved}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
