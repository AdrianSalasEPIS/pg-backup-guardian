"""Lectura (y disparo opcional) de los workflows de GitHub Actions del repositorio."""

import json
import os
import time
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "AdrianSalasEPIS/pg-backup-guardian")
TOKEN = os.environ.get("GITHUB_TOKEN")  # opcional: permite ejecutar el respaldo desde la app
API = "https://api.github.com"
CACHE_SECONDS = 45

_cache: dict = {"at": 0.0, "data": None}


def _request(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pg-backup-guardian",
                 **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def recent_runs(force: bool = False) -> dict:
    if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    try:
        data = _request("GET", f"/repos/{REPO}/actions/runs?per_page=12")
        runs = [{
            "id": r["id"], "name": r["name"], "number": r["run_number"], "event": r["event"],
            "status": r["status"], "conclusion": r["conclusion"], "sha": r["head_sha"][:7],
            "title": r.get("display_title"), "created_at": r["created_at"], "updated_at": r["updated_at"],
            "url": r["html_url"],
        } for r in data.get("workflow_runs", [])]
        result = {"repo": REPO, "runs": runs, "can_dispatch": bool(TOKEN), "error": None}
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as ex:
        result = {"repo": REPO, "runs": [], "can_dispatch": bool(TOKEN), "error": f"GitHub no respondió: {ex}"}
    _cache.update(at=time.time(), data=result)
    return result


def dispatch_backup() -> None:
    if not TOKEN:
        raise PermissionError("GITHUB_TOKEN no configurado")
    _request("POST", f"/repos/{REPO}/actions/workflows/backup.yml/dispatches", {"ref": "main"})
    _cache["at"] = 0.0
