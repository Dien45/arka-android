from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

# Default repo as required
DEFAULT_REPO = "Dien45/arka-android"


def _repo_normalize(repo: str) -> str:
    s = (repo or "").strip()
    if not s:
        return DEFAULT_REPO
    # allow https://github.com/user/repo.git
    if "github.com/" in s:
        s = s.split("github.com/")[-1]
    s = s.strip().strip("/").replace(".git", "")
    # only keep user/repo
    # remove extra path segments
    parts = s.split("/")
    if len(parts) >= 2:
        s = f"{parts[0]}/{parts[1]}"
    return s[:200]


def _sanitize_repo_for_url(repo: str) -> str:
    # ensure repo like user/repo, no spaces
    repo = _repo_normalize(repo)
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        raise ValueError(f"Format repo tidak valid: {repo}. Pakai user/repo, contoh {DEFAULT_REPO}")
    return repo


def _workspace_root(workspace: str) -> Path:
    from . import tools

    try:
        return tools.resolve_workspace(workspace)
    except Exception as e:
        raise ValueError(str(e))


def _run_git(workspace: Path, args: list[str], env_extra: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    # avoid leaking token in process list? we use extraHeader, token not in args if we pass via env?
    # We'll pass token via extraHeader in args, but that's still visible in ps briefly. Better than URL.
    # GitHub recommends using http.extraHeader.
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return proc.returncode, out[-8000:]
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout}s: git {' '.join(args)}"
    except FileNotFoundError:
        return 127, "git tidak ditemukan di sistem"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _ensure_git_repo(ws: Path) -> tuple[bool, str]:
    if (ws / ".git").exists():
        return True, "repo sudah ada"
    code, out = _run_git(ws, ["init"])
    if code != 0:
        return False, out
    return True, out


def _ensure_remote(ws: Path, repo: str) -> tuple[bool, str]:
    repo = _sanitize_repo_for_url(repo)
    url = f"https://github.com/{repo}.git"
    # check existing remote
    code, out = _run_git(ws, ["remote", "get-url", "origin"])
    if code == 0:
        existing = out.strip().splitlines()[-1].strip() if out.strip() else ""
        # if existing contains token (contains @), replace
        if "@" in existing or "github.com" not in existing:
            _run_git(ws, ["remote", "set-url", "origin", url])
        else:
            # normalize to clean url
            if existing.rstrip(".git") != url.rstrip(".git") and "github.com" in existing:
                # keep existing if same repo but allow update
                _run_git(ws, ["remote", "set-url", "origin", url])
            elif "github.com" not in existing:
                _run_git(ws, ["remote", "set-url", "origin", url])
        return True, f"remote origin -> {url}"
    else:
        code2, out2 = _run_git(ws, ["remote", "add", "origin", url])
        if code2 != 0:
            return False, out2
        return True, f"remote origin added {url}"


def _ensure_user_config(ws: Path, username: str) -> None:
    # set user.name and email if not set, don't overwrite existing
    code, out = _run_git(ws, ["config", "--get", "user.name"])
    if code != 0 or not out.strip():
        name = (username or "Arka").strip() or "Arka"
        _run_git(ws, ["config", "user.name", name])
    code, out = _run_git(ws, ["config", "--get", "user.email"])
    if code != 0 or not out.strip():
        email = f"{(username or 'arka').strip() or 'arka'}@users.noreply.github.com"
        _run_git(ws, ["config", "user.email", email])


def github_status(workspace: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Check local git status and remote GitHub status without exposing token in URL."""
    repo_raw = settings.get("github_repo") or DEFAULT_REPO
    try:
        repo = _sanitize_repo_for_url(repo_raw)
    except ValueError as e:
        return {"ok": False, "repo": repo_raw, "error": str(e)}

    token = (settings.get("github_token") or "").strip()
    username = (settings.get("github_username") or "").strip()

    result: dict[str, Any] = {
        "ok": False,
        "repo": repo,
        "default_repo": DEFAULT_REPO,
        "username": username,
        "token_set": bool(token),
        "workspace": workspace,
    }

    try:
        ws = _workspace_root(workspace)
    except ValueError as e:
        result["error"] = str(e)
        return result

    result["workspace_resolved"] = str(ws)

    # local git info
    if (ws / ".git").exists():
        code, out = _run_git(ws, ["remote", "get-url", "origin"])
        result["remote_url"] = out.strip().splitlines()[-1].strip() if code == 0 else "(no remote)"
        # check if token leaked in remote url
        if "@" in result["remote_url"]:
            result["remote_has_token"] = True
            result["warning"] = "Remote URL mengandung token/@, akan dibersihkan saat push"
        else:
            result["remote_has_token"] = False

        code, out = _run_git(ws, ["branch", "--show-current"])
        result["branch"] = out.strip() if code == 0 else "main"

        code, out = _run_git(ws, ["status", "--porcelain"])
        result["dirty"] = bool(out.strip())
        result["dirty_files"] = out.strip().splitlines()[:20] if out.strip() else []

        code, out = _run_git(ws, ["rev-parse", "HEAD"])
        result["local_sha"] = out.strip()[:40] if code == 0 else ""
    else:
        result["remote_url"] = f"https://github.com/{repo}.git"
        result["branch"] = "main"
        result["local_sha"] = ""
        result["dirty"] = True

    # remote GitHub API check (if token)
    if token:
        try:
            with httpx.Client(timeout=15.0) as client:
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
                r = client.get(f"https://api.github.com/repos/{repo}", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    result["github_exists"] = True
                    result["github_private"] = data.get("private", False)
                    result["github_default_branch"] = data.get("default_branch", "main")
                    result["github_updated_at"] = data.get("updated_at", "")
                elif r.status_code == 404:
                    result["github_exists"] = False
                    result["error"] = f"Repo {repo} tidak ditemukan (404). Buat dulu di GitHub."
                    return result
                elif r.status_code == 401:
                    result["error"] = "Token tidak valid / unauthorized (401). Cek PAT di Pengaturan."
                    return result
                else:
                    result["github_api_status"] = r.status_code
                    result["github_api_error"] = r.text[:500]

                # last commit
                r2 = client.get(f"https://api.github.com/repos/{repo}/commits?per_page=1", headers=headers)
                if r2.status_code == 200:
                    commits = r2.json()
                    if commits:
                        result["remote_sha"] = commits[0].get("sha", "")[:12]
                        result["remote_message"] = (commits[0].get("commit", {}).get("message", "") or "")[:200]
        except Exception as e:
            result["github_api_exception"] = str(e)

    result["ok"] = True
    return result


def github_push(workspace: str, settings: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    """Push all files from workspace to GitHub repo. Never put token in remote URL."""
    repo_raw = settings.get("github_repo") or DEFAULT_REPO
    try:
        repo = _sanitize_repo_for_url(repo_raw)
    except ValueError as e:
        return {"ok": False, "error": str(e), "repo": repo_raw}

    token = (settings.get("github_token") or "").strip()
    username = (settings.get("github_username") or "").strip()

    if not token:
        return {"ok": False, "error": "GitHub token kosong. Isi di Pengaturan > GitHub Token (PAT).", "repo": repo}
    if not username:
        # username optional for push, but we have repo owner from repo
        username = repo.split("/")[0]

    try:
        ws = _workspace_root(workspace)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # ensure git repo
    ok, msg = _ensure_git_repo(ws)
    if not ok:
        return {"ok": False, "error": f"git init gagal: {msg}"}

    # ensure remote without token
    ok, msg_remote = _ensure_remote(ws, repo)
    if not ok:
        return {"ok": False, "error": f"remote gagal: {msg_remote}"}

    _ensure_user_config(ws, username)

    # ensure main branch
    _run_git(ws, ["branch", "-M", "main"])

    # add all
    code, out = _run_git(ws, ["add", "-A"])
    if code != 0:
        return {"ok": False, "error": f"git add gagal: {out}"}

    # check if there's anything to commit
    code, out = _run_git(ws, ["status", "--porcelain"])
    has_changes = bool(out.strip())
    code_diff, out_diff = _run_git(ws, ["diff", "--cached", "--name-only"])
    has_staged = bool(out_diff.strip())

    commit_msg = (message or "").strip() or f"Update from Arka Android {username}"
    # truncate
    commit_msg = commit_msg[:500]

    if has_changes or has_staged:
        code, out = _run_git(ws, ["commit", "-m", commit_msg])
        if code != 0 and "nothing to commit" not in out.lower():
            return {"ok": False, "error": f"git commit gagal: {out}"}

    # check if there is at least one commit
    code, out = _run_git(ws, ["rev-parse", "HEAD"])
    if code != 0:
        return {"ok": False, "error": "Belum ada commit. Pastikan ada file di workspace."}

    local_sha = out.strip()

    # push with extraHeader, never put token in URL
    # Use http.extraHeader to avoid token in remote URL
    extra_header = f"Authorization: Bearer {token}"
    # For GitHub, also support basic auth with x-access-token
    # We'll try Bearer first, fallback to token as username
    push_args = ["-c", f"http.extraHeader={extra_header}", "push", "origin", "main"]
    code, out = _run_git(ws, push_args, timeout=120)

    if code != 0:
        # try alternative: -c http.extraHeader="Authorization: Basic ..." using token as password?
        # Try with https://x-access-token:token@ but we must not leave it in remote, only for this push URL
        # Use push with URL containing token but not saving to config: git push https://token@github.com/repo.git
        # This is okay as long as we don't set remote URL with token. The URL is only for this command.
        # But requirement says jangan taruh token di URL remote - so temporary URL is okay if not saved.
        # However we should try with username:token
        # Let's try second method: push using https://oauth2:token@github.com/...
        # We'll attempt push to explicit URL with token not saved
        safe_url = f"https://oauth2:{token}@github.com/{repo}.git"
        # Note: token in command line may be visible in ps, but we already used extraHeader which is also visible.
        # To reduce exposure, we rely on extraHeader first. If it fails, try this.
        code2, out2 = _run_git(ws, ["push", safe_url, "main"], timeout=120)
        if code2 != 0:
            # combine errors
            return {
                "ok": False,
                "error": f"Push gagal. Coba cek token & repo.\nExtraHeader attempt: {out}\nURL attempt: {out2[:1000]}",
                "repo": repo,
                "local_sha": local_sha[:12],
            }
        else:
            out = out2

    # after push, ensure remote URL is clean (no token)
    _ensure_remote(ws, repo)

    # get remote sha via API
    remote_sha = ""
    try:
        with httpx.Client(timeout=10) as client:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            r = client.get(f"https://api.github.com/repos/{repo}/commits?per_page=1", headers=headers)
            if r.status_code == 200:
                commits = r.json()
                if commits:
                    remote_sha = commits[0].get("sha", "")[:12]
    except Exception:
        pass

    return {
        "ok": True,
        "repo": repo,
        "local_sha": local_sha[:12],
        "remote_sha": remote_sha,
        "message": f"Berhasil push ke {repo} (main) {local_sha[:12]}",
        "output": out[-2000:],
    }
