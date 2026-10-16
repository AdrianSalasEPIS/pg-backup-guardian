import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.db import connect, init_schema

templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))

# Render define RENDER_GIT_COMMIT en cada deploy; el pipeline lo usa para
# confirmar que la versión publicada es la del último commit.
COMMIT = os.environ.get("RENDER_GIT_COMMIT", os.environ.get("GIT_COMMIT", "local"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_schema()
    yield


app = FastAPI(
    title="PG Backup Guardian",
    description="App de ejemplo para demostrar estrategias de respaldo en PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
)


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=0, le=2100)


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT (SELECT count(*) FROM books)  AS books,
                   (SELECT count(*) FROM loans)  AS loans,
                   (SELECT count(*) FROM backup_log WHERE status = 'ok') AS backups_ok,
                   (SELECT max(created_at) FROM backup_log WHERE status = 'ok') AS last_backup
            """
        ).fetchone()
    return row


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with connect() as conn:
        backups = conn.execute(
            "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
        books = conn.execute("SELECT * FROM books ORDER BY id DESC LIMIT 10").fetchall()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"stats": stats(), "backups": backups, "books": books, "commit": COMMIT[:7]},
    )


@app.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        db = "up"
    except Exception:
        db = "down"
    return {"status": "ok" if db == "up" else "degraded", "database": db, "commit": COMMIT}


@app.get("/api/stats")
def api_stats():
    return stats()


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
        return conn.execute(
            "INSERT INTO books (title, author, year) VALUES (%s, %s, %s) RETURNING *",
            (book.title, book.author, book.year),
        ).fetchone()


@app.delete("/api/books/{book_id}", status_code=204)
def delete_book(book_id: int):
    with connect() as conn:
        deleted = conn.execute("DELETE FROM books WHERE id = %s", (book_id,)).rowcount
    if not deleted:
        raise HTTPException(404, "Libro no encontrado")


@app.get("/api/backups")
def list_backups(limit: int = 50):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT %s", (min(limit, 200),)
        ).fetchall()
