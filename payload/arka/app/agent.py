from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from . import tools
from .sessions import save_session

STOP = set()


def request_stop(sid: str) -> None:
    STOP.add(sid)


def _agents_md(workspace: str) -> str:
    p = Path(workspace) / "AGENTS.md"
    if p.exists() and p.is_file() and p.stat().st_size < 80_000:
        return p.read_text(encoding="utf-8", errors="replace")[:8000]
    return ""


def normalize_mode(mode: str) -> str:
    m = (mode or "build").strip().lower()
    if m in {"plan", "agent", "build"}:
        return m
    return "build"


def system_prompt(mode: str, workspace: str) -> str:
    agents = _agents_md(workspace)
    extra = f"\n\n# AGENTS.md\n{agents}" if agents else ""
    mode = normalize_mode(mode)
    if mode == "plan":
        return (
            "You are Arka, an AI coding agent in PLAN mode. "
            "Read-only tools: list_dir, read_file, glob, grep, todo_write, web_fetch, web_search. "
            "Do not modify files or run shell commands that change state. "
            "You MAY look up docs on the internet. "
            "Explore the repo, then produce a clear implementation plan with files, steps, and risks. "
            f"Workspace: {workspace}."
            f"{extra}"
        )
    talk = (
        "While working, call tools immediately. Do not narrate plans. "
        "Forbidden filler: repeating 'Baik, saya akan…', 'Mari saya…', 'Let me continue…'. "
        "At most ONE short status line between tool batches (example: 'Menulis login_view.py'). "
        "Never concatenate many 'Baik' sentences. Put a blank line before any user-facing paragraph. "
        "After the task is done, write a short readable summary of what changed — not a replay of the plan. "
    )
    if mode == "agent":
        return (
            "You are Arka in AGENT mode — like a full desktop coding agent. "
            "You have tools: list_dir, read_file, write_file, edit_file, delete_path, glob, grep, "
            "bash, web_fetch, web_search, download_url, todo_write. "
            "Be autonomous: explore the repo first, then do the work. Do not only describe steps. "
            "If the request is ambiguous, ask at most 1–2 short questions OR pick a reasonable default and continue. "
            "Search the web when you need docs or current facts. "
            "Write and edit files, run commands, install packages, and verify the result. "
            "Use todo_write for multi-step tasks and finish every item. "
            "Stay inside the workspace. Do not destroy the OS. "
            "Never stop mid-task with 'I will continue later' — continue now until it actually works. "
            + talk
            + "Reply in the user's language when they write in Indonesian. "
            f"Workspace: {workspace}."
            f"{extra}"
        )
    return (
        "You are Arka, an AI coding agent in BUILD mode with full project access. "
        "You MUST use tools instead of only describing what you would do. "
        "Capabilities: write_file / edit_file to write code; delete_path to remove files; "
        "bash to run commands that change the project (pip/npm install, tests, git, scripts); "
        "web_fetch / web_search / download_url for the public internet. "
        "Prefer edit_file for small patches and write_file for new files. "
        "Stay inside the workspace. Do not attempt to destroy the OS. "
        "Do not stop early. If the user asked for an app or many files, keep writing files "
        "until the project actually runs (HTML/CSS/JS or equivalent all present). "
        "Use todo_write for a checklist, then complete every item before you finish. "
        "Never end with 'I will continue later' — continue now. "
        + talk
        + "Reply in the user's language when they write in Indonesian. "
        f"Workspace: {workspace}."
        f"{extra}"
    )


def _maybe_title(session: dict[str, Any], user_text: str) -> None:
    if session.get("title") in (None, "", "Sesi baru"):
        session["title"] = user_text.strip().splitlines()[0][:48] or "Sesi"


def sanitize_model(name: str) -> str:
    s = (name or "").strip().replace("\u200b", "").replace("\ufeff", "")
    s = s.strip("\"'`“”")
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    low = s.lower()
    if low.startswith("model:") or low.startswith("model "):
        s = s.split(":", 1)[-1].strip().strip("\"'`")
    if " (" in s:
        head = s.split(" (", 1)[0].strip()
        if "/" in head or head.startswith(("auto", "cx/", "cc/", "gg/")):
            s = head
    return s[:240]


def _api_headers(settings: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (settings.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["x-api-key"] = key
    return headers


def _normalize_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    return u


def parse_model_catalog(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or payload.get("combos") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        mid = label = kind = owned = ""
        if isinstance(row, str):
            mid = row
        elif isinstance(row, dict):
            mid = str(row.get("id") or row.get("name") or row.get("model") or "").strip()
            label = str(row.get("name") or row.get("display_name") or row.get("title") or "").strip()
            kind = str(row.get("type") or row.get("object") or "").strip().lower()
            owned = str(row.get("owned_by") or row.get("owner") or "").strip().lower()
        mid = sanitize_model(mid)
        if not mid or mid in seen:
            continue
        if owned in {"combo", "combos"} or kind in {"combo", "combos"} or mid.startswith("combo/"):
            kind = "combo"
        seen.add(mid)
        out.append({"id": mid, "name": label if label != mid else "", "type": kind})
        if len(out) >= 8000:
            break
    return out


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any | None:
    try:
        r = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def fetch_model_catalog(settings: dict[str, Any]) -> list[dict[str, str]]:
    base = _normalize_base(settings.get("api_base") or "")
    if not base:
        return []
    origin = base[:-3] if base.endswith("/v1") else base
    headers = _api_headers(settings)
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_rows(rows: list[dict[str, str]]) -> None:
        for m in rows:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            catalog.append(m)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for url in (f"{base}/models", f"{origin}/v1/models"):
            data = await _get_json(client, url, headers)
            if data is not None:
                add_rows(parse_model_catalog(data))
                break
        for url in (f"{base}/combos", f"{origin}/v1/combos", f"{origin}/api/combos"):
            data = await _get_json(client, url, headers)
            if data is None:
                continue
            rows = parse_model_catalog(data)
            for m in rows:
                m["type"] = "combo"
                add_rows([m])
            break
    combos = [m for m in catalog if m.get("type") == "combo"]
    rest = [m for m in catalog if m.get("type") != "combo"]
    return combos + rest


def resolve_model_id(requested: str, catalog: list[dict[str, str]]) -> str:
    req = sanitize_model(requested)
    if not req:
        return req
    ids = [m["id"] for m in catalog]
    if req in ids:
        return req
    low = req.lower()
    for i in ids:
        if i.lower() == low:
            return i
    if not req.startswith(("combo/", "auto/")) and req != "auto":
        prefixed = "combo/" + req
        for i in ids:
            if i.lower() == prefixed.lower():
                return i
    return req


def _parse_api_err(err: str) -> str:
    raw = (err or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        e = data.get("error") if isinstance(data.get("error"), (dict, str)) else data
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or raw)
        if isinstance(e, str):
            return e
    return raw


def _is_credential_error(err: str) -> bool:
    t = (err or "").lower()
    return any(
        p in t
        for p in (
            "no active credentials",
            "credentials for provider",
            "missing api key",
            "invalid api key",
            "no credentials",
            "provider not configured",
            "not authenticated",
        )
    )


def format_api_error(status: int, err: str, model: str) -> str:
    msg = _parse_api_err(err)
    if _is_credential_error(err):
        prov = ""
        m = re.search(r"provider:\s*([A-Za-z0-9._-]+)", msg, flags=re.I)
        if m:
            prov = m.group(1)
        who = f"provider `{prov}`" if prov else "salah satu provider di combo"
        return (
            f"OmniRoute (bukan Arka) mencoba provider `{prov or '?'}` padahal kamu tidak memilihnya. "
            f"Itu biasanya karena combo `{model}` strategy-nya **fusion** (Arka kirim tools, jadi OmniRoute hanya memakai **judge**/panel[0], bukan semua anggota) "
            f"atau **auto**. Judge/panel[0] kemungkinan `agy/` / antigravity. "
            f"Di OmniRoute: Combos → `{combo_bare_name(model)}` → set **judgeModel** ke model yang sudah jalan, "
            f"atau ganti strategy ke **priority** dan hapus Antigravity/agy.\nDetail: {msg}"
        )
    return f"API {status}: {msg[:800]}"


def combo_bare_name(model: str) -> str:
    s = sanitize_model(model)
    if s.lower().startswith("combo/"):
        return s[6:].strip()
    return s


def looks_like_combo_id(model: str) -> bool:
    s = sanitize_model(model)
    if not s:
        return False
    if s == "auto" or s.startswith(("auto/", "combo/")):
        return True
    if " " in s or "/" not in s:
        return True
    return False


def _mid_prov(mid: str, prov: str = "") -> tuple[str, str] | None:
    mid = (mid or "").strip()
    if not mid or mid.startswith("combo/"):
        return None
    if not prov and "/" in mid:
        prov = mid.split("/", 1)[0]
    return mid, prov.lower()


def combo_member_ids(combo: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    cfg = combo.get("config") if isinstance(combo.get("config"), dict) else {}
    judge = str(cfg.get("judgeModel") or combo.get("judgeModel") or "").strip()
    jp = _mid_prov(judge)
    if jp:
        out.append(jp)
        seen.add(jp[0])
    rows = combo.get("models") or combo.get("targets") or combo.get("members") or combo.get("steps") or []
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("items") or []
    for step in rows:
        mid = prov = ""
        if isinstance(step, str):
            mid = step.strip()
        elif isinstance(step, dict):
            if str(step.get("kind") or "") == "combo-ref":
                continue
            mid = str(step.get("model") or step.get("modelId") or step.get("id") or "").strip()
            prov = str(step.get("providerId") or step.get("provider") or "").strip()
        if not mid or mid.startswith("combo/"):
            continue
        if not prov and "/" in mid:
            prov = mid.split("/", 1)[0]
        out.append((mid, prov.lower()))
    return out


async def fetch_combo(settings: dict[str, Any], model: str) -> dict[str, Any] | None:
    from urllib.parse import quote

    want = combo_bare_name(model).lower()
    if not want:
        return None
    base = _normalize_base(settings.get("api_base") or "")
    origin = base[:-3] if base.endswith("/v1") else base
    headers = _api_headers(settings)
    enc = quote(want, safe="")
    urls = [
        f"{base}/combos",
        f"{origin}/v1/combos",
        f"{origin}/api/combos",
        f"{base}/combos/{enc}",
        f"{origin}/v1/combos/{enc}",
        f"{origin}/api/combos/{enc}",
    ]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url in urls:
            data = await _get_json(client, url, headers)
            if data is None:
                continue
            if isinstance(data, dict) and (data.get("name") or data.get("models") or data.get("targets")) and not data.get("data"):
                return data
            rows: list[Any]
            if isinstance(data, dict):
                rows = data.get("data") or data.get("combos") or data.get("items") or []
            elif isinstance(data, list):
                rows = data
            else:
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                names = [str(row.get("name") or ""), str(row.get("id") or ""), str(row.get("slug") or "")]
                if any(n.lower() == want or n.lower() == "combo/" + want for n in names if n):
                    return row
    return None


def _provider_from_err(err: str) -> str:
    m = re.search(r"provider:\s*([A-Za-z0-9._-]+)", err or "", flags=re.I)
    return (m.group(1) if m else "").lower()


def _is_model_missing(err: str) -> bool:
    if _is_credential_error(err):
        return False
    t = (err or "").lower()
    return any(
        p in t
        for p in (
            "model_not_found",
            "model not found",
            "does not exist",
            "does not have access to model",
            "invalid model",
            "unknown model",
            "not a valid model",
            "no such model",
        )
    )


def _messages_have_images(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False


def _strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            out.append(m)
            continue
        texts = [str(p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
        nm = dict(m)
        nm["content"] = "\n".join(t for t in texts if t).strip() or "(gambar terlampir di inbox)"
        out.append(nm)
    return out


def user_message_content(text: str, images: list[dict[str, Any]] | None) -> Any:
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    n = 0
    for im in images:
        raw = (im.get("data") or "").strip()
        if not raw:
            continue
        mime = (im.get("mime") or "image/png").split(";")[0].strip() or "image/png"
        if not mime.startswith("image/"):
            continue
        if raw.startswith("data:"):
            url = raw
        else:
            url = f"data:{mime};base64,{raw}"
        if len(url) > 6_000_000:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
        n += 1
        if n >= 4:
            break
    return parts if n else text


def _is_tool_error(err: str) -> bool:
    t = (err or "").lower()
    return any(
        p in t
        for p in (
            "tool_choice",
            "does not support tool",
            "does not support function",
            "tools are not supported",
            "function calling is not",
            "tool use is not",
            "unknown parameter: tools",
            "unexpected field `tools`",
            "invalid parameter: tools",
        )
    )


async def _openai_chat(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    base = _normalize_base(settings.get("api_base") or "")
    if not base:
        raise RuntimeError("API base URL kosong. Isi OmniRoute, contoh http://127.0.0.1:20128/v1")
    headers = _api_headers(settings)
    payload = dict(payload)
    model = sanitize_model(str(payload.get("model") or settings.get("model") or ""))
    payload["model"] = model
    is_combo = model == "auto" or model.startswith(("auto/", "combo/")) or "/" not in model
    timeout = 180.0 if is_combo else 120.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def post(body: dict[str, Any]) -> httpx.Response:
            return await client.post(f"{base}/chat/completions", headers=headers, json=body)

        names: list[str] = []
        if looks_like_combo_id(model):
            bare = combo_bare_name(model)
            for cand in (bare, "combo/" + bare, model):
                if cand and cand not in names:
                    names.append(cand)
        else:
            names.append(model)

        skip_prov = {"antigravity", "agy"}
        r = None
        err = ""
        for cand in names:
            body = dict(payload)
            body["model"] = cand
            r = await post(body)
            if r.status_code < 400:
                return r.json()
            err = r.text[:1200]
            if (
                r.status_code in {400, 415, 422}
                and _messages_have_images(body.get("messages") or [])
            ):
                body2 = dict(body)
                body2["messages"] = _strip_images(body.get("messages") or [])
                r_img = await post(body2)
                if r_img.status_code < 400:
                    return r_img.json()
                err = r_img.text[:1200]
            if _is_credential_error(err):
                p = _provider_from_err(err)
                if p:
                    skip_prov.add(p)
                continue
            if not _is_model_missing(err):
                break

        assert r is not None

        if _is_credential_error(err) or _is_model_missing(err):
            combo = await fetch_combo(settings, model)
            members = combo_member_ids(combo) if combo else []
            tried = set(names)
            for mid, prov in members:
                if mid in tried:
                    continue
                if prov in skip_prov or mid.lower().startswith(("antigravity/", "agy/")):
                    continue
                tried.add(mid)
                body = dict(payload)
                body["model"] = mid
                r2 = await post(body)
                if r2.status_code < 400:
                    return r2.json()
                err = r2.text[:1200]
                if _is_credential_error(err):
                    nxt = _provider_from_err(err)
                    if nxt:
                        skip_prov.add(nxt)

        if r.status_code in {400, 404, 422} and _is_tool_error(err) and payload.get("tools"):
            body = dict(payload)
            body["model"] = names[0]
            body.pop("tool_choice", None)
            r2 = await post(body)
            if r2.status_code < 400:
                return r2.json()
            body.pop("tools", None)
            r3 = await post(body)
            if r3.status_code < 400:
                data = r3.json()
                note = (
                    "\n\n_(Combo/model ini tidak mendukung tools. Agen tidak bisa edit file sampai "
                    "kamu pilih yang support function calling.)_"
                )
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                msg["content"] = (msg.get("content") or "") + note
                choice["message"] = msg
                if data.get("choices"):
                    data["choices"][0] = choice
                return data
            err = r3.text[:1200]

        raise RuntimeError(format_api_error(r.status_code, err, model))


def _demo_reply(user_text: str, workspace: str, mode: str) -> list[dict[str, Any]]:
    """Deterministic local agent so the UI works without an API key."""
    listing = tools.list_dir(workspace, ".")
    steps: list[dict[str, Any]] = [
        {"tool": "list_dir", "args": {"path": "."}, "result": listing},
    ]
    text = user_text.lower()
    if mode == "plan":
        reply = (
            "Mode **Plan** (demo, tanpa API key).\n\n"
            "Saya sudah melihat isi folder proyek. Rencana:\n"
            "1. Petakan file yang relevan\n"
            "2. Tulis perubahan kecil dan terukur\n"
            "3. Jalankan cek cepat lewat terminal\n\n"
            "Isi folder saat ini:\n```\n"
            + listing[:1200]
            + "\n```\n\nIsi kunci API di Pengaturan untuk agen model sungguhan, lalu pindah ke **Build**."
        )
        return steps + [{"text": reply}]

    if any(k in text for k in ("hapus", "delete", "remove")):
        path = "ARKA.md"
        msg, snaps = tools.delete_path(workspace, path)
        steps.append({"tool": "delete_path", "args": {"path": path}, "result": msg, "snaps": snaps})
        return steps + [{"text": f"Mode demo: `{path}` — {msg}. Undo di toolbar jika salah hapus."}]

    if any(k in text for k in ("http://", "https://", "fetch", "unduh", "download", "cari ", "search")):
        if "http://" in user_text or "https://" in user_text:
            m = re.search(r"https?://\S+", user_text)
            url = m.group(0).rstrip(").,") if m else "https://example.com"
            result = tools.web_fetch(url)
            steps.append({"tool": "web_fetch", "args": {"url": url}, "result": result[:2000]})
            return steps + [{"text": f"Mode demo: hasil fetch `{url}` ada di kartu tool di atas."}]
        result = tools.web_search(user_text)
        steps.append({"tool": "web_search", "args": {"query": user_text}, "result": result[:2000]})
        return steps + [{"text": "Mode demo: hasil pencarian web di kartu tool."}]

    if any(k in text for k in ("pip ", "npm ", "jalankan", "run ", "install", "perintah", "command")):
        cmd = "python --version && pip --version"
        if "npm" in text:
            cmd = "npm --version"
        result = tools.run_bash(workspace, cmd, timeout=30)
        steps.append({"tool": "bash", "args": {"command": cmd}, "result": result})
        return steps + [{"text": f"Mode demo menjalankan `{cmd}`. Isi API key agar model memilih perintah sendiri."}]

    if any(k in text for k in ("buat", "create", "tulis", "hello", "halo", "kode", "code")):
        path = "ARKA.md"
        content = (
            "# Arka\n\nFile ini ditulis oleh agen (mode demo).\n\n"
            f"Permintaan: {user_text.strip()[:200]}\n"
        )
        msg, snap = tools.write_file(workspace, path, content)
        steps.append({"tool": "write_file", "args": {"path": path, "content": content}, "result": msg, "snaps": [snap]})
        reply = (
            f"Mode demo: kode/file `{path}` sudah ditulis ke folder proyek. "
            "Sambungkan API key di Pengaturan untuk agen model sungguhan."
        )
        return steps + [{"text": reply}]

    reply = (
        "Ini **mode demo** (belum ada API key), tapi tool lokal tetap jalan.\n\n"
        "Di **Build** saya bisa: menulis/mengubah/menghapus file, menjalankan perintah "
        "(pip/npm/git), fetch URL, dan unduh ke proyek — di dalam folder workspace.\n\n"
        "Cuplikan proyek:\n```\n"
        + listing[:1200]
        + "\n```"
    )
    return steps + [{"text": reply}]


async def run_agent(
    session: dict[str, Any],
    user_text: str,
    settings: dict[str, Any],
    mode: str,
    images: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    try:
        workspace = str(
            tools.pick_workspace(
                settings.get("workspace"),
                session.get("workspace"),
            )
        )
    except ValueError as e:
        yield {"type": "error", "data": {"message": str(e)}}
        return

    mode = normalize_mode(mode)
    session["mode"] = mode
    session["workspace"] = workspace
    run_model = sanitize_model(str(session.get("model") or settings.get("model") or ""))
    if run_model:
        session["model"] = run_model
    _maybe_title(session, user_text)
    session["messages"].append({"role": "user", "content": user_message_content(user_text, images)})
    ui_text = user_text
    if images:
        names = ", ".join((im.get("name") or "gambar") for im in images[:4])
        if names and names not in ui_text:
            ui_text = (ui_text + f"\n📎 {names}").strip()
    session["ui"].append({"role": "user", "text": ui_text})
    assistant_ui: dict[str, Any] = {"role": "assistant", "text": "", "tools": []}
    session["ui"].append(assistant_ui)
    save_session(session)

    sid = session["id"]
    STOP.discard(sid)
    turn_snaps: list[dict[str, Any]] = []
    files_changed: list[dict[str, str]] = []

    def note_file(name: str, args: dict[str, Any]) -> None:
        path = str(args.get("path") or "").strip()
        if name in {"write_file", "edit_file", "download_url"} and path:
            files_changed.append({"path": path, "action": name})
        elif name == "delete_path" and path:
            files_changed.append({"path": path, "action": "delete"})

    if not settings.get("api_key"):
        yield {"type": "status", "data": {"phase": "demo"}}
        for step in _demo_reply(user_text, workspace, mode):
            if sid in STOP:
                break
            if "tool" in step:
                assistant_ui["tools"].append(
                    {"name": step["tool"], "args": step.get("args") or {}, "result": step.get("result", "")}
                )
                turn_snaps.extend(step.get("snaps") or [])
                note_file(step["tool"], step.get("args") or {})
                yield {
                    "type": "tool",
                    "data": {
                        "name": step["tool"],
                        "args": step.get("args") or {},
                        "result": step.get("result", ""),
                        "status": "done",
                    },
                }
            if "text" in step:
                assistant_ui["text"] = step["text"]
                yield {"type": "text", "data": {"delta": step["text"]}}
        if turn_snaps:
            session.setdefault("undo", []).append(turn_snaps)
        save_session(session)
        session["last_files"] = files_changed
        save_session(session)
        yield {
            "type": "done",
            "data": {"title": session["title"], "todos": session.get("todos", []), "files_changed": files_changed},
        }
        return

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(mode, workspace)}]
    messages.extend(session["messages"])
    schemas = tools.schemas_for(mode)
    max_iter = int(settings.get("max_iterations") or 40)
    if mode == "agent":
        max_iter = max(max_iter, 56)
    continues = 0
    auto_limit = 4 if mode == "agent" else 2 if mode == "build" else 0
    _nudge = (
        "Jangan menulis pengantar. Jangan ulangi 'Baik, saya akan'. "
        "Langsung panggil tools (write_file/edit_file/bash/read_file). "
        "Satu kalimat status saja, lalu tools."
    )

    def looks_unfinished(text: str) -> bool:
        t = (text or "").lower()
        hints = (
            "nanti saya lanjutkan", "akan saya lanjutkan nanti", "to be continued",
            "i'll continue later", "i will continue later", "continue in the next",
            "belum selesai", "tahap berikutnya", "coming next", "lanjut di sesi",
        )
        if any(h in t for h in hints):
            return True
        return bool(re.search(r"saya akan (buat|tulis|lanjut|selesai|lengkapi|kerjakan)", t)) and len(t) < 900

    def is_work_narration(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        if "```" in t or t.startswith("#"):
            return False
        low = t.lower()
        if low.count("baik") >= 2 and ("saya akan" in low or "mari saya" in low):
            return True
        cues = (
            "saya akan", "mari saya", "saya lihat sudah", "let me ", "i'll ", "i will ",
            "melanjutkan", "menyelesaikan semua file", "lengkapi semua file",
        )
        if any(c in low for c in cues) and not re.search(r"(^|\n)\s*(- |\d+\.|## )", t):
            return True
        return False

    def emit_text(delta: str) -> dict[str, Any] | None:
        if not delta:
            return None
        cur = assistant_ui.get("text") or ""
        if cur and not cur.endswith("\n") and not delta.startswith("\n"):
            delta = "\n\n" + delta
        assistant_ui["text"] = cur + delta
        return {"type": "text", "data": {"delta": delta}}

    try:
        while True:
            stopped_for_limit = True
            for _ in range(max_iter):
                if sid in STOP:
                    assistant_ui["text"] = (assistant_ui.get("text") or "") + "\n\n*(dihentikan)*"
                    stopped_for_limit = False
                    break
                yield {"type": "status", "data": {"phase": "model"}}
                payload = {
                    "model": run_model,
                    "messages": messages,
                    "tools": schemas,
                    "tool_choice": "auto",
                }
                data = await _openai_chat(settings, payload)
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content") or ""

                if tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": tool_calls,
                        }
                    )
                    if content and not is_work_narration(content):
                        ev = emit_text(content)
                        if ev:
                            yield ev

                    for call in tool_calls:
                        if sid in STOP:
                            break
                        fn = call.get("function") or {}
                        name = fn.get("name") or ""
                        raw_args = fn.get("arguments") or "{}"
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except json.JSONDecodeError:
                            args = {}
                        yield {"type": "tool", "data": {"name": name, "args": args, "status": "running"}}
                        if mode == "plan" and name not in tools.PLAN_TOOLS:
                            result = f"Ditolak: tool `{name}` tidak tersedia di mode Plan."
                            snaps: list[dict[str, Any]] = []
                        else:
                            result, snaps = tools.execute(name, args, workspace, session)
                        turn_snaps.extend(snaps)
                        assistant_ui["tools"].append({"name": name, "args": args, "result": result})
                        note_file(name, args)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id", "tool"),
                                "content": result[:12000],
                            }
                        )
                        yield {"type": "tool", "data": {"name": name, "args": args, "result": result, "status": "done"}}
                    continue

                session["messages"].append({"role": "assistant", "content": content})
                chatter = mode != "plan" and (is_work_narration(content) or looks_unfinished(content))
                if content and not chatter:
                    ev = emit_text(content)
                    if ev:
                        yield ev
                stopped_for_limit = False
                if auto_limit and continues < auto_limit and chatter:
                    continues += 1
                    messages.append({"role": "user", "content": _nudge})
                    stopped_for_limit = True
                    break
                if content and chatter:
                    ev = emit_text(content.strip().splitlines()[0][:180])
                    if ev:
                        yield ev
                break
            else:
                if auto_limit and continues < auto_limit and sid not in STOP:
                    continues += 1
                    messages.append({"role": "user", "content": _nudge})
                    continue
                note = "\n\nBatas iterasi tool tercapai. Klik Lanjutkan jika belum selesai."
                ev = emit_text(note)
                if ev:
                    yield ev
            if not stopped_for_limit or sid in STOP:
                break
            if continues >= auto_limit:
                break
    except Exception as e:
        yield {"type": "error", "data": {"message": str(e)}}
        assistant_ui["text"] = assistant_ui.get("text") or f"Error: {e}"

    if turn_snaps:
        session.setdefault("undo", []).append(turn_snaps)
        if len(session["undo"]) > 20:
            session["undo"] = session["undo"][-20:]
    session["last_files"] = files_changed
    save_session(session)
    yield {
        "type": "done",
        "data": {"title": session["title"], "todos": session.get("todos", []), "files_changed": files_changed},
    }
