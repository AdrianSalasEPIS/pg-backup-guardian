import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/guardian"
SCHEMA = Path(__file__).with_name("schema.sql")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@contextmanager
def connect():
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=10) as conn:
        yield conn


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
