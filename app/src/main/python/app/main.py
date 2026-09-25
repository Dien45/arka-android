from __future__ import annotations

import json
import re
import base64
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import mimetypes

from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tools
from .agent import request_stop, run_agent, sanitize_model, is_image_model
from .paths import APP_DIR
from .sessions import delete_session, list_sessions, load_session, new_session, save_session, update_session, rename_session
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
    github_username: str | None = None
    github_token: str | None = None
    github_repo: str | None = None


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


class GithubPushIn(BaseModel):
    message: str | None = None
    workspace: str | None = None


class ImageGenIn(BaseModel):
    prompt: str
    model: str | None = None
    size: str | None = "1024x1024"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"ok": "arka"}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    s = load_settings()
    key = s.get("api_key") or ""
    # JANGAN isi api_key="Tutup" — ensure empty if placeholder
    if str(key).lower() == "tutup":
        key = ""
        s["api_key"] = ""
    s["api_key_set"] = bool(key)
    s["api_key"] = key
    # don't expose full token in list? but UI needs to show masked? We return token as is for settings modal, but github token separate
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
    # ensure github defaults
    if not s.get("github_repo"):
        s["github_repo"] = "Dien45/arka-android"
    return s


def _body_dict(body: Any) -> dict[str, Any]:
    if hasattr(body, "model_dump"):
        return body.model_dump()
    if hasattr(body, "dict"):
        return body.dict()
    return {}


@app.api_route("/api/settings", methods=["PUT", "POST"])
def put_settings(body: SettingsIn) -> dict[str, Any]:
    patch = {k: v for k, v in _body_dict(body).items() if v is not None}
    if "model" in patch:
        patch["model"] = sanitize_model(str(patch["model"] or ""))
    if "api_base" in patch:
        b = str(patch.get("api_base") or "").strip()
        if b and "://" not in b:
            b = "http://" + b
        patch["api_base"] = b.rstrip("/")
    if "api_key" in patch:
        k = str(patch.get("api_key") or "").strip()
        if k.lower() == "tutup":
            k = ""
        patch["api_key"] = k
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
    if "github_repo" in patch:
        repo = str(patch.get("github_repo") or "").strip()
        if "github.com/" in repo:
            repo = repo.split("github.com/")[-1].strip().strip("/").replace(".git", "")
        patch["github_repo"] = repo[:200] or "Dien45/arka-android"
    # github_username and token are free form
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
    """Fix bug Internal Server Error: handle both title and model via PATCH."""
    session = load_session(sid)
    if not session:
        raise HTTPException(404, "Sesi tidak ada")
    # update title if provided
    title = body.title
    model = body.model
    # Use update_session which handles both
    updated = update_session(sid, title=title, model=model)
    if not updated:
        raise HTTPException(404, "Sesi tidak ada")
    return updated


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
    from .agent import _normalize_base as norm

    return norm(url)


SEED_MODELS = [
    {"id": "auto", "name": "OmniRoute auto", "type": ""},
    {"id": "auto/coding", "name": "OmniRoute coding", "type": ""},
    {"id": "auto/fast", "name": "OmniRoute fast", "type": ""},
    {"id": "auto/cheap", "name": "OmniRoute cheap", "type": ""},
    {"id": "auto/quality", "name": "OmniRoute quality", "type": ""},
    {"id": "sdxl", "name": "SDXL (image)", "type": "image"},
    {"id": "flux", "name": "Flux (image)", "type": "image"},
    {"id": "flux-lightning", "name": "Flux Lightning (image)", "type": "image"},
]


@app.post("/api/models")
async def list_models(body: ModelsIn | None = None) -> dict[str, Any]:
    settings = load_settings()
    if body:
        if body.api_base:
            settings = {**settings, "api_base": body.api_base}
        if body.api_key not in (None, ""):
            k = str(body.api_key).strip()
            if k.lower() != "tutup":
                settings = {**settings, "api_key": k}
    catalog: list[dict[str, Any]] = []
    err = ""
    try:
        from .agent import fetch_models_with_status

        catalog, err = await fetch_models_with_status(settings)
    except Exception as e:
        err = str(e)
        try:
            from .agent import fetch_model_catalog

            catalog = await fetch_model_catalog(settings)
            if catalog:
                err = ""
        except Exception as e2:
            err = str(e2)
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for m in list(SEED_MODELS) + list(catalog or []):
        mid = str(m.get("id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        merged.append(m)
    ids = [m["id"] for m in merged]
    current = sanitize_model(settings.get("model") or "")
    return {
        "models": ids,
        "items": merged,
        "base": _normalize_base(settings.get("api_base") or ""),
        "count": len(ids),
        "current": current,
        "error": err,
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


# ---- GitHub endpoints ----
@app.get("/api/github/status")
def github_status_api(workspace: str | None = None) -> dict[str, Any]:
    from .github_sync import github_status

    settings = load_settings()
    ws = workspace or settings.get("workspace") or str(APP_DIR)
    try:
        return github_status(ws, settings)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/github/status")
def github_status_post(body: GithubPushIn | None = None) -> dict[str, Any]:
    from .github_sync import github_status

    settings = load_settings()
    ws = (body.workspace if body else None) or settings.get("workspace") or str(APP_DIR)
    try:
        return github_status(ws, settings)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/github/push")
def github_push_api(body: GithubPushIn | None = None) -> dict[str, Any]:
    from .github_sync import github_push

    settings = load_settings()
    ws = (body.workspace if body else None) or settings.get("workspace") or str(APP_DIR)
    msg = (body.message if body else None) or ""
    try:
        res = github_push(ws, settings, message=msg)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "Push gagal")
    return res


# ---- Image generation endpoint ----
@app.post("/api/images/generations")
@app.post("/api/generate-image")
async def generate_image_api(body: ImageGenIn) -> dict[str, Any]:
    import httpx

    settings = load_settings()
    api_base = (settings.get("api_base") or "").strip()
    api_key = (settings.get("api_key") or "").strip()
    if api_key.lower() == "tutup":
        api_key = ""
    if not api_base:
        raise HTTPException(400, "API base kosong")
    if not api_key:
        raise HTTPException(400, "API key kosong")
    if not body.prompt.strip():
        raise HTTPException(400, "Prompt kosong")

    # normalize base
    def norm(u: str) -> str:
        u = u.strip().strip("<>").rstrip("/")
        for suffix in ("/chat/completions", "/completions"):
            if u.endswith(suffix):
                u = u[: -len(suffix)]
        return u

    base = norm(api_base)
    url = f"{base}/images/generations"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    model_id = (body.model or settings.get("model") or "sdxl").strip()
    size = (body.size or "1024x1024").strip()

    payload: dict[str, Any] = {"prompt": body.prompt, "n": 1, "size": size, "response_format": "b64_json"}
    if model_id:
        payload["model"] = model_id

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code >= 400 and "model" in payload:
                # retry without model for providers that don't need it
                payload2 = {k: v for k, v in payload.items() if k != "model"}
                r2 = await client.post(url, headers=headers, json=payload2)
                if r2.status_code < 400:
                    r = r2
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"{r.text[:800]}")
            data = r.json()
            # optionally save to workspace
            try:
                ws = str(tools.pick_workspace(settings.get("workspace")))
                items = data.get("data") or []
                if items:
                    b64 = items[0].get("b64_json")
                    if b64:
                        gen_dir = Path(ws) / "generated_images"
                        gen_dir.mkdir(parents=True, exist_ok=True)
                        fname = f"img_{int(time.time())}.png"
                        target = gen_dir / fname
                        target.write_bytes(base64.b64decode(b64))
                        rel = str(target.relative_to(Path(ws))).replace("\\", "/")
                        data["saved_path"] = rel
            except Exception:
                pass
            return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
