"""Laboratorio de recuperación: punto → verificar → desastre → restaurar."""

import io
import zipfile

import pytest
from conftest import ADMIN

from app import main


@pytest.fixture(autouse=True)
def no_cooldown(monkeypatch):
    monkeypatch.setattr(main, "SNAPSHOT_COOLDOWN", 0)


def books(client):
    return client.get("/api/books").json()


def test_destructive_actions_require_pin(client):
    assert client.post("/api/disaster/delete_all").status_code == 401
    assert client.post("/api/disaster/delete_all", headers={"X-Admin-Pin": "0000"}).status_code == 401
    assert client.post("/api/auth/check", headers=ADMIN).json() == {"ok": True}
    assert client.post("/api/disaster/no_existe", headers=ADMIN).status_code == 400


@pytest.mark.parametrize("kind", ["delete_all", "corrupt", "drop_table"])
def test_full_recovery_cycle(client, kind):
    original = books(client)

    snap = client.post("/api/snapshots", json={"label": f"antes de {kind}"})
    assert snap.status_code == 201, snap.text
    snap_id = snap.json()["id"]
    assert client.get("/api/lab/state").json()["matches_last_snapshot"] is True

    verify = client.post(f"/api/snapshots/{snap_id}/verify").json()
    assert verify["ok"] is True and all(c["ok"] for c in verify["checks"])

    r = client.post(f"/api/disaster/{kind}", headers=ADMIN)
    assert r.status_code == 200, r.text
    state = client.get("/api/lab/state").json()
    assert state["matches_last_snapshot"] is False
    assert client.get("/api/stats").status_code == 200  # el panel sigue respondiendo dañado

    restored = client.post(f"/api/snapshots/{snap_id}/restore", headers=ADMIN)
    assert restored.status_code == 200, restored.text
    assert all(c["ok"] for c in restored.json()["checks"])
    assert books(client) == original
    assert client.get("/api/lab/state").json()["matches_last_snapshot"] is True

    # Las secuencias quedaron bien: se puede seguir insertando sin chocar ids.
    new = client.post("/api/books", json={"title": "Post-restauración", "author": "QA"})
    assert new.status_code == 201
    assert new.json()["id"] > max(b["id"] for b in original)
    client.delete(f"/api/books/{new.json()['id']}", headers=ADMIN)


def test_snapshot_download_is_valid_zip(client):
    snap_id = client.post("/api/snapshots").json()["id"]
    r = client.get(f"/api/snapshots/{snap_id}/download")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"books.csv", "loans.csv", "manifest.json"} <= set(names)


def test_retention_keeps_last_ten(client):
    for _ in range(12):
        assert client.post("/api/snapshots").status_code == 201
    assert len(client.get("/api/snapshots").json()) == 10


def test_events_and_strategies(client):
    assert any(e["type"] == "snapshot" for e in client.get("/api/events").json())
    strategies = client.get("/api/strategies").json()
    assert len(strategies) >= 5 and all(s["not_protects"] for s in strategies)
