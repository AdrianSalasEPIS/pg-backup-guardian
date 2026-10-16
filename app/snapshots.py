"""Laboratorio de recuperación: puntos de restauración lógicos hechos con COPY.

Cada punto de restauración es un ZIP con un CSV por tabla y un manifest.json
que guarda, por tabla, las columnas, el número de filas y una huella md5 del
contenido. La verificación y la restauración comparan contra esa huella, así
que no basta con que "no haya errores": los datos tienen que ser idénticos.
"""

import hashlib
import io
import json
import secrets
import time
import zipfile
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from app.db import connect, init_schema

# Orden padre → hijo (loans referencia a books).
DOMAIN_TABLES = ["books", "loans"]
KEEP_SNAPSHOTS = 10

DISASTERS = {
    "delete_all": "DELETE sin WHERE: se borraron todos los libros y préstamos",
    "corrupt": "Un script con errores sobrescribió títulos y autores",
    "drop_table": "DROP TABLE loans: se eliminó la tabla de préstamos completa",
}


class IntegrityError(Exception):
    """Los datos restaurados no coinciden con la huella del punto de restauración."""


def log_event(conn, type_: str, level: str, message: str) -> None:
    conn.execute("INSERT INTO events (type, level, message) VALUES (%s, %s, %s)", (type_, level, message))


def _exists(conn, table: str, schema: str = "public") -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS e", (f"{schema}.{table}",)).fetchone()["e"]


def _columns(conn, table: str) -> list[list[str]]:
    """[[nombre, tipo], ...] en el orden físico de la tabla."""
    rows = conn.execute(
        "SELECT attname, format_type(atttypid, atttypmod) AS type FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        (f"public.{table}",),
    ).fetchall()
    return [[r["attname"], r["type"]] for r in rows]


def _names(cols: list[list[str]]) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(c[0]) for c in cols)


def _fingerprint(conn, table: str, schema: str = "public") -> dict:
    """Filas + md5 del contenido completo ordenado por id."""
    q = sql.SQL(
        "SELECT count(*) AS rows, md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) AS md5 "
        "FROM {}.{} t"
    ).format(sql.Identifier(schema), sql.Identifier(table))
    return dict(conn.execute(q).fetchone())


def table_state() -> dict:
    """Estado actual de las tablas de negocio comparado con el último punto."""
    with connect() as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        tables = {}
        for t in DOMAIN_TABLES:
            tables[t] = _fingerprint(conn, t) if _exists(conn, t) else {"rows": None, "md5": None, "missing": True}
        last = conn.execute(
            "SELECT id, created_at, manifest FROM vault.snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    changed = []
    if last:
        for t, info in last["manifest"]["tables"].items():
            now = tables.get(t, {})
            if now.get("md5") != info["md5"]:
                changed.append({"table": t, "snapshot_rows": info["rows"], "current_rows": now.get("rows"),
                                "missing": bool(now.get("missing"))})
    return {
        "tables": tables,
        "last_snapshot": {"id": last["id"], "created_at": last["created_at"]} if last else None,
        "matches_last_snapshot": bool(last) and not changed,
        "changed": changed,
    }


def create_snapshot(label: str | None = None) -> dict:
    start = time.perf_counter()
    buf = io.BytesIO()
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "format": "csv+zip", "tables": {}}

    with connect() as conn:
        # Instantánea consistente: todas las tablas se leen en el mismo momento lógico.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction():
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for t in DOMAIN_TABLES:
                    if not _exists(conn, t):
                        raise IntegrityError(f"La tabla {t} no existe: no se puede respaldar un estado dañado")
                    cols = _columns(conn, t)
                    data = io.BytesIO()
                    copy_sql = sql.SQL("COPY (SELECT {} FROM {} ORDER BY id) TO STDOUT WITH (FORMAT csv, HEADER)").format(
                        _names(cols), sql.Identifier(t))
                    with conn.cursor().copy(copy_sql) as copy:
                        for chunk in copy:
                            data.write(chunk)
                    zf.writestr(f"{t}.csv", data.getvalue())
                    manifest["tables"][t] = {"columns": cols, **_fingerprint(conn, t)}
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        payload = buf.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        duration = int((time.perf_counter() - start) * 1000)
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO vault.snapshots (label, size_bytes, sha256, duration_ms, manifest, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (label, len(payload), digest, duration, json.dumps(manifest), payload),
            ).fetchone()
            conn.execute(
                "DELETE FROM vault.snapshots WHERE id NOT IN "
                "(SELECT id FROM vault.snapshots ORDER BY id DESC LIMIT %s)", (KEEP_SNAPSHOTS,))
            conn.execute(
                "INSERT INTO backup_log (kind, status, file_name, size_bytes, sha256, verified, location, details) "
                "VALUES ('snapshot', 'ok', %s, %s, %s, false, 'Neon · vault.snapshots', %s)",
                (f"snapshot_{row['id']}.zip", len(payload), digest, label),
            )
            rows = " · ".join(f"{t} {v['rows']}" for t, v in manifest["tables"].items())
            log_event(conn, "snapshot", "ok",
                      f"⚡ Punto de restauración #{row['id']} creado — {rows} ({len(payload)} bytes, {duration} ms)")
    return {"id": row["id"], "created_at": row["created_at"], "size_bytes": len(payload), "sha256": digest,
            "duration_ms": duration, "tables": manifest["tables"]}


def list_snapshots() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, label, size_bytes, sha256, duration_ms, verified_at, manifest "
            "FROM vault.snapshots ORDER BY id DESC"
        ).fetchall()
    for r in rows:
        r["rows"] = {t: v["rows"] for t, v in r.pop("manifest")["tables"].items()}
    return rows


def get_payload(snapshot_id: int) -> tuple[bytes, dict] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, sha256, manifest FROM vault.snapshots WHERE id = %s", (snapshot_id,)
        ).fetchone()
    if not row:
        return None
    payload = bytes(row["payload"])
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise IntegrityError("El SHA-256 del punto de restauración no coincide: archivo alterado")
    return payload, row["manifest"]


def _load(conn, payload: bytes, manifest: dict, schema: str) -> dict:
    """Carga los CSV del ZIP en `schema` y devuelve la huella de cada tabla cargada."""
    result = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for t in DOMAIN_TABLES:
            target = sql.SQL("{}.{} ({})").format(
                sql.Identifier(schema), sql.Identifier(t), _names(manifest["tables"][t]["columns"]))
            with conn.cursor().copy(sql.SQL("COPY {} FROM STDIN WITH (FORMAT csv, HEADER)").format(target)) as copy:
                copy.write(zf.read(f"{t}.csv"))
            result[t] = _fingerprint(conn, t, schema)
    return result


def _compare(expected: dict, actual: dict) -> list[dict]:
    return [
        {"table": t, "expected": {"rows": e["rows"], "md5": e["md5"]}, "actual": actual.get(t), "ok": actual.get(t) == {"rows": e["rows"], "md5": e["md5"]}}
        for t, e in expected.items()
    ]


def verify_snapshot(snapshot_id: int) -> dict:
    """Restaura en un esquema temporal dentro de una transacción que SIEMPRE se revierte."""
    start = time.perf_counter()
    found = get_payload(snapshot_id)
    if not found:
        raise LookupError("Punto de restauración no encontrado")
    payload, manifest = found
    scratch = f"restore_check_{secrets.token_hex(4)}"
    with connect() as conn:
        with conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch)))
            for t in DOMAIN_TABLES:
                # Mismas columnas y tipos que en el manifiesto: no depende de que
                # la tabla original siga existiendo (p. ej. tras un DROP TABLE).
                defs = sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(typ))
                    for name, typ in manifest["tables"][t]["columns"])
                conn.execute(sql.SQL("CREATE TABLE {}.{} ({})").format(
                    sql.Identifier(scratch), sql.Identifier(t), defs))
            checks = _compare(manifest["tables"], _load(conn, payload, manifest, scratch))
        ok = all(c["ok"] for c in checks)
        duration = int((time.perf_counter() - start) * 1000)
        with conn.transaction():
            if ok:
                conn.execute("UPDATE vault.snapshots SET verified_at = now() WHERE id = %s", (snapshot_id,))
                conn.execute("UPDATE backup_log SET verified = true WHERE file_name = %s", (f"snapshot_{snapshot_id}.zip",))
            log_event(conn, "verify", "ok" if ok else "error",
                      f"{'✅' if ok else '❌'} Verificación del punto #{snapshot_id}: "
                      f"{'todas las tablas coinciden' if ok else 'NO coincide'} ({duration} ms)")
    return {"id": snapshot_id, "ok": ok, "duration_ms": duration, "checks": checks}


def simulate_disaster(kind: str) -> dict:
    if kind not in DISASTERS:
        raise ValueError(f"Escenario desconocido: {kind}")
    before = table_state()["tables"]
    with connect() as conn:
        with conn.transaction():
            if kind == "delete_all":
                conn.execute("DELETE FROM loans")
                conn.execute("DELETE FROM books")
            elif kind == "corrupt":
                conn.execute("UPDATE books SET title = upper(reverse(title)), author = '¿¿¿???'")
            elif kind == "drop_table":
                conn.execute("DROP TABLE IF EXISTS loans")
            log_event(conn, "disaster", "error", f"💥 Desastre simulado — {DISASTERS[kind]}")
    return {"kind": kind, "description": DISASTERS[kind], "before": before, "after": table_state()["tables"]}


def restore_snapshot(snapshot_id: int) -> dict:
    """Restauración atómica: si la huella final no coincide, se revierte todo."""
    start = time.perf_counter()
    found = get_payload(snapshot_id)
    if not found:
        raise LookupError("Punto de restauración no encontrado")
    payload, manifest = found
    before = table_state()["tables"]
    init_schema()  # recrea tablas si un DROP TABLE las eliminó
    with connect() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
            conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(map(sql.Identifier, DOMAIN_TABLES))))
            loaded = _load(conn, payload, manifest, "public")
            checks = _compare(manifest["tables"], loaded)
            if not all(c["ok"] for c in checks):
                raise IntegrityError(f"La restauración no coincide con la huella; se revirtió. {checks}")
            for t in DOMAIN_TABLES:
                conn.execute(sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({t}, 'id'), coalesce(max(id), 1), max(id) IS NOT NULL) FROM {tid}"
                ).format(t=sql.Literal(t), tid=sql.Identifier(t)))
        duration = int((time.perf_counter() - start) * 1000)
        with conn.transaction():
            log_event(conn, "restore", "ok",
                      f"♻️ Restaurado desde el punto #{snapshot_id} en {duration} ms — huella verificada")
    return {"id": snapshot_id, "duration_ms": duration, "before": before,
            "after": table_state()["tables"], "checks": checks}
