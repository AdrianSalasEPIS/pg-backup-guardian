import hmac
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import github, snapshots
from app.db import connect, init_schema
from app.strategies import STRATEGIES

STATIC = Path(__file__).with_name("static")
MAX_BOOKS = 300
SNAPSHOT_COOLDOWN = 5  # segundos entre puntos de restauración (evita abuso)

# Render define RENDER_GIT_COMMIT en cada deploy; el pipeline lo usa para
# confirmar que la versión publicada es la del último commit.
COMMIT = os.environ.get("RENDER_GIT_COMMIT", os.environ.get("GIT_COMMIT", "local"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_schema()
    yield


app = FastAPI(
    title="PG Backup Guardian",
    description="App de ejemplo para demostrar estrategias de respaldo y recuperación en PostgreSQL.",
    version="2.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(snapshots.IntegrityError)
async def integrity_error(_: Request, ex: snapshots.IntegrityError):
    return JSONResponse(status_code=409, content={"detail": str(ex)})


@app.exception_handler(LookupError)
async def not_found(_: Request, ex: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(ex)})


# --- PIN de administrador para acciones destructivas ---------------------------

_failures: dict[str, list[float]] = defaultdict(list)
LOCK_AFTER, LOCK_WINDOW = 5, 600


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def require_pin(request: Request) -> None:
    pin = os.environ.get("ADMIN_PIN")
    if not pin:
        if os.environ.get("RENDER"):
            raise HTTPException(503, "Acción deshabilitada: falta configurar ADMIN_PIN en el servidor")
        return  # desarrollo local sin PIN
    ip = _client_ip(request)
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < LOCK_WINDOW]
    if len(_failures[ip]) >= LOCK_AFTER:
        raise HTTPException(429, "Demasiados intentos fallidos. Espera 10 minutos.")
    if not hmac.compare_digest(request.headers.get("x-admin-pin", ""), pin):
        _failures[ip].append(now)
        raise HTTPException(401, "PIN de administrador incorrecto")


# --- Páginas y estado ---------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    start = time.perf_counter()
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        db = "up"
    except Exception:
        db = "down"
    return {"status": "ok" if db == "up" else "degraded", "database": db, "commit": COMMIT,
            "latency_ms": round((time.perf_counter() - start) * 1000)}


@app.get("/api/config")
def config():
    return {"pin_required": bool(os.environ.get("ADMIN_PIN")) or bool(os.environ.get("RENDER")),
            "repo": github.REPO, "can_dispatch": bool(github.TOKEN), "commit": COMMIT,
            "disasters": snapshots.DISASTERS}


@app.post("/api/auth/check", dependencies=[Depends(require_pin)])
def auth_check():
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    with connect() as conn:
        def count(table):
            exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS e", (f"public.{table}",)).fetchone()["e"]
            return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] if exists else None

        row = conn.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'ok')                  AS backups_ok,
                   count(*)                                             AS backups_total,
                   max(created_at) FILTER (WHERE status = 'ok')         AS last_backup,
                   max(created_at) FILTER (WHERE status = 'ok' AND kind = 'logical-full') AS last_full,
                   extract(epoch FROM now() - max(created_at) FILTER (WHERE status = 'ok'))::int AS rpo_seconds
            FROM backup_log
            """
        ).fetchone()
        sizes = conn.execute(
            "SELECT created_at, size_bytes, kind FROM backup_log WHERE status = 'ok' AND size_bytes IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        n_snap = conn.execute("SELECT count(*) AS n FROM vault.snapshots").fetchone()["n"]
        n_books, n_loans = count("books"), count("loans")
    total = row["backups_total"] or 0
    return {**row, "books": n_books, "loans": n_loans, "snapshots": n_snap,
            "success_rate": round(100 * row["backups_ok"] / total) if total else None,
            "sizes": list(reversed(sizes))}


@app.get("/api/strategies")
def api_strategies():
    return STRATEGIES


@app.get("/api/events")
def api_events(limit: int = 30):
    with connect() as conn:
        return conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT %s", (min(limit, 100),)).fetchall()


# --- Biblioteca (CRUD) --------------------------------------------------------

class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=0, le=2100)


@app.get("/api/books")
def list_books():
    with connect() as conn:
        return conn.execute("SELECT * FROM books ORDER BY id").fetchall()


@app.get("/api/books/{book_id}")
def get_book(book_id: int):
    with connect() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = %s", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Libro no encontrado")
    return book


@app.post("/api/books", status_code=201)
def create_book(book: BookIn):
    with connect() as conn:
        if conn.execute("SELECT count(*) AS n FROM books").fetchone()["n"] >= MAX_BOOKS:
            raise HTTPException(409, f"Límite de {MAX_BOOKS} libros alcanzado en la demo")
        new = conn.execute(
            "INSERT INTO books (title, author, year) VALUES (%s, %s, %s) RETURNING *",
            (book.title.strip(), book.author.strip(), book.year),
        ).fetchone()
        snapshots.log_event(conn, "info", "info", f"📚 Libro agregado: «{new['title']}» (#{new['id']})")
        return new


@app.delete("/api/books/{book_id}", status_code=204, dependencies=[Depends(require_pin)])
def delete_book(book_id: int):
    with connect() as conn:
        deleted = conn.execute("DELETE FROM books WHERE id = %s RETURNING title", (book_id,)).fetchone()
        if not deleted:
            raise HTTPException(404, "Libro no encontrado")
        snapshots.log_event(conn, "info", "warn", f"🗑️ Libro eliminado: «{deleted['title']}» (#{book_id})")


# --- Respaldos ----------------------------------------------------------------

@app.get("/api/backups")
def list_backups(limit: int = 50):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT %s", (min(limit, 200),)
        ).fetchall()


@app.get("/api/lab/state")
def lab_state():
    return snapshots.table_state()


_last_snapshot = {"at": 0.0}


class SnapshotIn(BaseModel):
    label: str | None = Field(default=None, max_length=80)


@app.get("/api/snapshots")
def api_list_snapshots():
    return snapshots.list_snapshots()


@app.post("/api/snapshots", status_code=201)
def api_create_snapshot(body: SnapshotIn | None = None):
    wait = SNAPSHOT_COOLDOWN - (time.time() - _last_snapshot["at"])
    if wait > 0:
        raise HTTPException(429, f"Espera {int(wait) + 1} s antes de crear otro punto de restauración")
    _last_snapshot["at"] = time.time()
    return snapshots.create_snapshot(body.label if body else None)


@app.post("/api/snapshots/{snapshot_id}/verify")
def api_verify_snapshot(snapshot_id: int):
    return snapshots.verify_snapshot(snapshot_id)


@app.post("/api/snapshots/{snapshot_id}/restore", dependencies=[Depends(require_pin)])
def api_restore_snapshot(snapshot_id: int):
    return snapshots.restore_snapshot(snapshot_id)


@app.get("/api/snapshots/{snapshot_id}/download")
def api_download_snapshot(snapshot_id: int):
    found = snapshots.get_payload(snapshot_id)
    if not found:
        raise HTTPException(404, "Punto de restauración no encontrado")
    return Response(found[0], media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="guardian_snapshot_{snapshot_id}.zip"'})


@app.post("/api/disaster/{kind}", dependencies=[Depends(require_pin)])
def api_disaster(kind: str):
    if kind not in snapshots.DISASTERS:
        raise HTTPException(400, "Escenario de desastre desconocido")
    return snapshots.simulate_disaster(kind)


# --- GitHub Actions -----------------------------------------------------------

@app.get("/api/github/runs")
def api_github_runs():
    return github.recent_runs()


@app.post("/api/github/backup", status_code=202, dependencies=[Depends(require_pin)])
def api_github_backup():
    try:
        github.dispatch_backup()
    except PermissionError as ex:
        raise HTTPException(501, str(ex))
    except OSError as ex:  # urllib.error.URLError / HTTPError / timeout
        raise HTTPException(502, f"GitHub rechazó la solicitud: {ex}")
    return {"ok": True, "message": "Workflow de respaldo diario disparado en GitHub Actions"}
