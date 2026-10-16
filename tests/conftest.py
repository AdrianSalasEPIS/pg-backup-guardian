import os

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

# Las pruebas usan una base propia para no tocar datos reales.
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/guardian_test")
ADMIN_URL = make_conninfo(TEST_URL, dbname="postgres")
os.environ["DATABASE_URL"] = TEST_URL
os.environ["ADMIN_PIN"] = "2468"
ADMIN = {"X-Admin-Pin": "2468"}


@pytest.fixture(scope="session", autouse=True)
def fresh_database():
    name = conninfo_to_dict(TEST_URL)["dbname"]
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield


@pytest.fixture(scope="session")
def client(fresh_database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
