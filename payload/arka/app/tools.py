from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def read_clipboard_image() -> dict[str, Any] | None:
    """Gambar di clipboard Windows (Win+Shift+S, Copy). WebView2 sering tidak mengirim file ke JS."""
    if os.name != "nt":
        return None
    import base64
    import time

    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "if ([Windows.Forms.Clipboard]::ContainsImage()) { "
        "$img = [Windows.Forms.Clipboard]::GetImage(); "
        "$path = Join-Path $env:TEMP 'arka-clipboard.png'; "
        "$img.Save($path, [Drawing.Imaging.ImageFormat]::Png); "
        "Write-Output ('IMG|' + $path); exit 0 }; "
        "if ([Windows.Forms.Clipboard]::ContainsFileDropList()) { "
        "foreach ($f in [Windows.Forms.Clipboard]::GetFileDropList()) { "
        "$e = [IO.Path]::GetExtension([string]$f).ToLowerInvariant(); "
        "if (@('.png','.jpg','.jpeg','.gif','.webp','.bmp') -contains $e) { "
        "Write-Output ('FILE|' + $f); exit 0 } } }; "
        "exit 3"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    line = lines[-1] if lines else ""
    if "|" not in line:
        return None
    kind, raw_path = line.split("|", 1)
    target = Path(raw_path.strip().strip('"'))
    if not target.is_file():
        return None
    try:
        data = target.read_bytes()
    except OSError:
        return None
    if not data or len(data) > 8_000_000:
        return None
    suf = target.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suf, "image/png")
    name = target.name if kind.upper() == "FILE" else f"paste-{int(time.time())}.png"
    return {"name": name, "mime": mime, "data": base64.b64encode(data).decode("ascii"), "size": len(data)}


def looks_like_windows_path(path: str) -> bool:
    return bool(_WIN_DRIVE.match((path or "").strip()))


def permission_hint(path: str) -> str:
    raw = (path or "").strip()
    return (
        f"Izin ditolak (Permission denied): {raw}\n\n"
        "Kalau path itu folder (bukan file), AI salah nulis ke folder — coba lagi ke nama file di dalamnya.\n"
        "Kalau file biasa juga gagal: Windows kadang mengunci Desktop (OneDrive / Controlled Folder Access).\n"
        "Tes: di Explorer buat file teks di folder itu. Kalau Explorer juga gagal, pindah ke Documents, "
        "atau Windows Security → Ransomware protection → Allow an app → py.exe / Arka.exe."
    )


def workspace_error(path: str) -> str:
    raw = (path or "").strip()
    if looks_like_windows_path(raw) and os.name != "nt":
        return (
            f"Folder tidak ada di server preview (Linux): {raw}\n\n"
            "Path Windows hanya jalan jika Arka dijalankan di Windows (Arka.vbs). "
            "Di preview ini isi Folder proyek dengan `/home/user/arka`."
        )
    if raw and not looks_like_windows_path(raw) and os.name == "nt" and raw.startswith("/"):
        return (
            f"Ini path Linux: {raw}\n"
            "Di Windows, isi folder lokal, misalnya Documents\\proyek-arka — jangan Desktop."
        )
    return f"Workspace tidak valid (folder tidak ditemukan): {raw or '(kosong)'}"


def resolve_workspace(workspace: str) -> Path:
    raw = (workspace or "").strip().strip('"')
    if not raw:
        raise ValueError(workspace_error(raw))
    root = Path(raw).expanduser()
    try:
        root = root.resolve()
    except OSError as e:
        raise ValueError(workspace_error(raw) + f" ({e})") from e
    if not root.exists() or not root.is_dir():
        raise ValueError(workspace_error(raw))
    return root


def pick_workspace(*candidates: str | None) -> Path:
    from .paths import APP_DIR

    ordered: list[str] = []
    for c in candidates:
        if c and str(c).strip() and str(c).strip() not in ordered:
            ordered.append(str(c).strip())
    for fallback in (str(APP_DIR), os.getcwd()):
        if fallback not in ordered:
            ordered.append(fallback)
    last_err = "Workspace tidak valid"
    for item in ordered:
        try:
            return resolve_workspace(item)
        except ValueError as e:
            last_err = str(e)
            continue
    raise ValueError(last_err)


def safe_path(workspace: str, rel: str) -> Path:
    root = resolve_workspace(workspace)
    raw = Path(rel).expanduser()
    target = raw.resolve() if raw.is_absolute() else (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Path di luar folder proyek")
    return target


def snapshot_file(path: Path) -> dict[str, Any]:
    if path.exists() and path.is_file():
        return {"path": str(path), "existed": True, "content": path.read_text(encoding="utf-8", errors="replace")}
    return {"path": str(path), "existed": False, "content": ""}


def restore_snapshots(snaps: list[dict[str, Any]]) -> int:
    n = 0
    for snap in reversed(snaps):
        p = Path(snap["path"])
        if snap.get("existed"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(snap.get("content", ""), encoding="utf-8")
            n += 1
        elif p.exists() and p.is_file():
            p.unlink()
            n += 1
    return n


def list_dir(workspace: str, path: str = ".", max_entries: int = 400) -> str:
    target = safe_path(workspace, path)
    if not target.exists():
        return f"Tidak ditemukan: {path}"
    if target.is_file():
        return f"FILE {target.name} ({target.stat().st_size} bytes)"
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".next", ".arka"}
    lines = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return str(e)
    for i, item in enumerate(entries):
        if i >= max_entries:
            lines.append("… (terpotong)")
            break
        if item.name in skip:
            continue
        mark = "DIR " if item.is_dir() else "FILE"
        lines.append(f"{mark} {item.name}")
    return "\n".join(lines) or "(kosong)"


def read_file(workspace: str, path: str, offset: int = 1, limit: int = 250) -> str:
    target = safe_path(workspace, path)
    if not target.exists() or not target.is_file():
        return f"File tidak ada: {path}"
    if target.stat().st_size > 1_500_000:
        return "File terlalu besar untuk dibaca."
    text = target.read_text(encoding="utf-8", errors="replace")
    rows = text.splitlines()
    start = max(1, int(offset))
    end = min(len(rows), start + max(1, int(limit)) - 1)
    chunk = rows[start - 1 : end]
    numbered = [f"{i + start:>4}| {line}" for i, line in enumerate(chunk)]
    header = f"{path} baris {start}-{end} dari {len(rows)}"
    return header + "\n" + "\n".join(numbered)


def _writable_file_path(workspace: str, path: str) -> Path | str:
    rel = (path or "").strip().replace("\\", "/")
    if not rel or rel in {".", "..", "/"}:
        return "Path kosong atau folder. Tulis ke nama file, misalnya README.md — bukan ke folder proyek."
    target = safe_path(workspace, rel)
    root = resolve_workspace(workspace)
    if target == root or target.is_dir():
        return f"Itu folder, bukan file: {rel}. Tulis ke file di dalamnya, misalnya {rel.rstrip('/')}/nama.py"
    return target


def write_file(workspace: str, path: str, content: str) -> tuple[str, dict[str, Any]]:
    target = _writable_file_path(workspace, path)
    if isinstance(target, str):
        return target, {"path": "", "existed": False, "content": ""}
    snap = snapshot_file(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except PermissionError:
        return permission_hint(str(target)), snap
    except OSError as e:
        if getattr(e, "errno", None) == 13 or "Permission denied" in str(e):
            return permission_hint(str(target)), snap
        return f"Gagal tulis {path}: {e}", snap
    return f"Ditulis {path} ({len(content)} karakter)", snap


def edit_file(workspace: str, path: str, old_text: str, new_text: str) -> tuple[str, dict[str, Any] | None]:
    target = _writable_file_path(workspace, path)
    if isinstance(target, str):
        return target, None
    if not target.exists():
        return f"File tidak ada: {path}", None
    if not target.is_file():
        return f"Itu bukan file: {path}", None
    original = target.read_text(encoding="utf-8", errors="replace")
    count = original.count(old_text)
    if count == 0:
        return "Teks lama tidak ditemukan. Baca file lagi lalu coba edit yang lebih unik.", None
    if count > 1:
        return f"Teks lama muncul {count} kali. Buat old_text lebih spesifik.", None
    snap = snapshot_file(target)
    try:
        target.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
    except PermissionError:
        return permission_hint(str(target)), snap
    except OSError as e:
        if getattr(e, "errno", None) == 13 or "Permission denied" in str(e):
            return permission_hint(str(target)), snap
        return f"Gagal edit {path}: {e}", snap
    return f"Diedit {path}", snap


def glob_files(workspace: str, pattern: str, max_results: int = 200) -> str:
    root = resolve_workspace(workspace)
    matches = []
    for p in root.glob(pattern):
        if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in p.parts):
            continue
        matches.append(str(p.relative_to(root)).replace("\\", "/"))
        if len(matches) >= max_results:
            break
    return "\n".join(matches) or "(tidak ada hasil)"


def grep_files(workspace: str, pattern: str, path: str = ".", glob: str | None = None, max_hits: int = 80) -> str:
    root = safe_path(workspace, path)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Regex tidak valid: {e}"
    hits: list[str] = []
    files: list[Path] = []
    if root.is_file():
        files = [root]
    else:
        iterator = root.rglob(glob or "*")
        for p in iterator:
            if not p.is_file():
                continue
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "dist"} for part in p.parts):
                continue
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".exe", ".dll", ".zip", ".pdf"}:
                continue
            files.append(p)
            if len(files) > 800:
                break
    for p in files:
        try:
            if p.stat().st_size > 400_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(resolve_workspace(workspace))).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(hits) >= max_hits:
                    return "\n".join(hits)
    return "\n".join(hits) or "(tidak ada hasil)"


def run_bash(workspace: str, command: str, timeout: int = 60) -> str:
    root = resolve_workspace(workspace)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"(timeout {timeout}s) perintah dihentikan"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    out = out[-12000:]
    code = proc.returncode
    return f"exit {code}\n{out}".strip()


def delete_path(workspace: str, path: str) -> tuple[str, list[dict[str, Any]]]:
    target = safe_path(workspace, path)
    root = resolve_workspace(workspace)
    if target == root:
        return "Tidak boleh menghapus folder proyek itu sendiri.", []
    if not target.exists():
        return f"Tidak ada: {path}", []
    snaps: list[dict[str, Any]] = []
    if target.is_file():
        snaps.append(snapshot_file(target))
        target.unlink()
        return f"Dihapus file {path}", snaps
    for p in target.rglob("*"):
        if p.is_file():
            try:
                snaps.append(snapshot_file(p))
            except OSError:
                snaps.append({"path": str(p), "existed": True, "content": ""})
    shutil.rmtree(target)
    return f"Dihapus folder {path} ({len(snaps)} file)", snaps


def web_fetch(url: str, max_chars: int = 12000) -> str:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return "Hanya URL http:// atau https://"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            r = client.get(u, headers={"User-Agent": "Arka/1.0"})
    except httpx.HTTPError as e:
        return f"Gagal fetch: {e}"
    ctype = r.headers.get("content-type", "")
    text = r.text if "image" not in ctype and "octet-stream" not in ctype else f"(binari {ctype}, {len(r.content)} bytes)"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (terpotong)"
    return f"HTTP {r.status_code} {u}\n{text}"


def web_search(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "Query kosong"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            r = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": q},
                headers={"User-Agent": "Arka/1.0"},
            )
    except httpx.HTTPError as e:
        return f"Gagal search: {e}"
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, flags=re.I | re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', r.text, flags=re.I | re.S)
    clean = lambda s: re.sub(r"<[^>]+>", "", s).replace("\n", " ").strip()
    lines = []
    for i, t in enumerate(titles[:8]):
        sn = clean(snippets[i]) if i < len(snippets) else ""
        lines.append(f"{i + 1}. {clean(t)}" + (f" — {sn[:180]}" if sn else ""))
    return "\n".join(lines) or f"HTTP {r.status_code}; tidak ada hasil terurai.\n{r.text[:1500]}"


def download_url(workspace: str, url: str, path: str) -> tuple[str, list[dict[str, Any]]]:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return "Hanya URL http:// atau https://", []
    target = safe_path(workspace, path)
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            r = client.get(u, headers={"User-Agent": "Arka/1.0"})
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Gagal unduh: {e}", []
    if len(r.content) > 8_000_000:
        return "File lebih dari 8MB, dibatalkan.", []
    if target.exists() and target.is_dir():
        return f"Itu folder, bukan file: {path}", []
    snap = snapshot_file(target) if target.exists() else {"path": str(target), "existed": False, "content": ""}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
    except PermissionError:
        return permission_hint(str(target)), [snap]
    return f"Diunduh {url} → {path} ({len(r.content)} bytes)", [snap]


def tree(workspace: str, max_nodes: int = 800) -> list[dict[str, Any]]:
    root = resolve_workspace(workspace)
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".next"}
    nodes: list[dict[str, Any]] = []

    def walk(folder: Path, depth: int) -> None:
        nonlocal nodes
        if len(nodes) >= max_nodes or depth > 6:
            return
        try:
            children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        except OSError:
            return
        for item in children:
            if item.name.startswith(".") and item.name not in {".env.example"}:
                if item.name in skip or item.name.startswith("."):
                    if item.name in skip or item.name in {".git"}:
                        continue
            if item.name in skip:
                continue
            rel = str(item.relative_to(root)).replace("\\", "/")
            nodes.append({"path": rel, "name": item.name, "dir": item.is_dir(), "depth": depth})
            if item.is_dir():
                walk(item, depth + 1)
            if len(nodes) >= max_nodes:
                return

    walk(root, 0)
    return nodes


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders in a directory relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative directory, default ."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file with line numbers. Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exactly one occurrence of old_text with new_text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files with a glob pattern relative to workspace, e.g. **/*.py",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_path",
            "description": "Delete a file or folder inside the workspace. Use for cleanup. Undo can restore text files.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace (cmd on Windows, bash on Unix). Can install packages (pip, npm), run tests, start scripts, git, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds, default 60, max 180"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch an http(s) URL and return text content (docs, APIs, pages).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web and return top result titles/snippets.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_url",
            "description": "Download an http(s) URL into a file in the workspace (not for huge binaries).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "path": {"type": "string"}},
                "required": ["url", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Replace the current todo list. Use during multi-step work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
]

PLAN_TOOLS = {"list_dir", "read_file", "glob", "grep", "todo_write", "web_fetch", "web_search"}
BUILD_TOOLS = {t["function"]["name"] for t in TOOL_SCHEMAS}


def schemas_for(mode: str) -> list[dict[str, Any]]:
    allowed = PLAN_TOOLS if (mode or "").lower() == "plan" else BUILD_TOOLS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]


def execute(name: str, args: dict[str, Any], workspace: str, session: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    snaps: list[dict[str, Any]] = []
    if name == "list_dir":
        return list_dir(workspace, args.get("path") or "."), snaps
    if name == "read_file":
        return (
            read_file(
                workspace,
                args.get("path") or "",
                int(args.get("offset") or 1),
                int(args.get("limit") or 250),
            ),
            snaps,
        )
    if name == "write_file":
        msg, snap = write_file(workspace, args.get("path") or "", args.get("content") or "")
        return msg, [snap]
    if name == "edit_file":
        msg, snap = edit_file(
            workspace,
            args.get("path") or "",
            args.get("old_text") or "",
            args.get("new_text") or "",
        )
        return msg, ([snap] if snap else [])
    if name == "glob":
        return glob_files(workspace, args.get("pattern") or "*"), snaps
    if name == "grep":
        return grep_files(workspace, args.get("pattern") or "", args.get("path") or ".", args.get("glob")), snaps
    if name == "delete_path":
        msg, dsnaps = delete_path(workspace, args.get("path") or "")
        return msg, dsnaps
    if name == "bash":
        timeout = min(180, max(5, int(args.get("timeout") or 60)))
        return run_bash(workspace, args.get("command") or "", timeout=timeout), snaps
    if name == "web_fetch":
        return web_fetch(args.get("url") or ""), snaps
    if name == "web_search":
        return web_search(args.get("query") or ""), snaps
    if name == "download_url":
        msg, dsnaps = download_url(workspace, args.get("url") or "", args.get("path") or "")
        return msg, dsnaps
    if name == "todo_write":
        items = args.get("items") or []
        session["todos"] = items
        return f"Todo: {len(items)} item", snaps
    return f"Tool tidak dikenal: {name}", snaps
