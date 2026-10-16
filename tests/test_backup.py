import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ADMIN_URL, TEST_URL

SCRIPT = Path(__file__).parents[1] / "scripts" / "backup.py"


def run(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, encoding="utf-8", env=env)


def make_backup(client, tmp_path) -> Path:
    client.get("/health")  # asegura que el esquema y los datos semilla existan
    r = run("create", "--database-url", TEST_URL, "--out-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    return next(tmp_path.glob("*.dump"))


def test_backup_and_verified_restore(client, tmp_path):
    dump = make_backup(client, tmp_path)
    manifest = json.loads(dump.with_suffix(".json").read_text())
    assert manifest["row_counts"]["books"] >= 5

    r = run("verify", "--file", str(dump), "--admin-url", ADMIN_URL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Restauración verificada" in r.stdout


def test_verify_detects_tampering(client, tmp_path):
    dump = make_backup(client, tmp_path)
    with dump.open("ab") as f:
        f.write(b"corrupto")
    r = run("verify", "--file", str(dump), "--admin-url", ADMIN_URL)
    assert r.returncode != 0
    assert "integridad" in r.stderr


def test_prune_keeps_latest(tmp_path):
    for i in range(5):
        (tmp_path / f"db_{i}.dump").write_bytes(b"x")
        (tmp_path / f"db_{i}.json").write_text("{}")
    r = run("prune", "--dir", str(tmp_path), "--keep", "2")
    assert r.returncode == 0, r.stderr
    assert len(list(tmp_path.glob("*.dump"))) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2
