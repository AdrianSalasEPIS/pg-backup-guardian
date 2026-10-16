"""Herramienta de respaldos lógicos para PostgreSQL.

Subcomandos:
  create   Genera un respaldo completo con pg_dump (formato custom) + manifiesto JSON.
  verify   Restaura el respaldo en una base temporal y compara filas con el manifiesto.
  prune    Aplica la política de retención (conserva los N más recientes).
  log      Registra el resultado en la tabla backup_log de la app.

Ejemplo:
  python scripts/backup.py create --database-url "$DATABASE_URL" --out-dir backups
  python scripts/backup.py verify --file backups/guardian_20261016T060000Z.dump \
         --admin-url postgresql://postgres:postgres@localhost:5432/postgres
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def pg_tool(name: str) -> str:
    """Ubica pg_dump/pg_restore; PG_BIN permite forzar una versión concreta."""
    if os.environ.get("PG_BIN"):
        exe = Path(os.environ["PG_BIN"]) / (name + (".exe" if os.name == "nt" else ""))
        if exe.exists():
            return str(exe)
    found = shutil.which(name)
    if not found:
        sys.exit(f"No se encontró {name}. Instala el cliente de PostgreSQL o define PG_BIN.")
    return found


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def row_counts(url: str) -> dict[str, int]:
    """Cuenta exacta de filas por tabla del esquema public (huella para verificar)."""
    with psycopg.connect(url) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        )]
        return {
            t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in tables
        }


def cmd_create(args) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dbname = conninfo_to_dict(args.database_url).get("dbname", "db")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = out_dir / f"{dbname}_{stamp}.dump"

    # Huella de filas ANTES del dump; en una base con escrituras concurrentes
    # puede diferir levemente (pg_dump usa su propia instantánea).
    counts = row_counts(args.database_url)
    subprocess.run(
        [pg_tool("pg_dump"), "--format=custom", "--compress=9", "--no-owner", "--no-acl",
         "--file", str(dump), "--dbname", args.database_url],
        check=True,
    )
    with psycopg.connect(args.database_url) as conn:
        server_version = conn.execute("SHOW server_version").fetchone()[0]

    manifest = {
        "file": dump.name,
        "kind": "logical-full",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": dbname,
        "server_version": server_version,
        "size_bytes": dump.stat().st_size,
        "sha256": sha256sum(dump),
        "row_counts": counts,
    }
    dump.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Respaldo creado: {dump} ({manifest['size_bytes']} bytes)")
    print(f"SHA-256: {manifest['sha256']}")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"file={dump}\nmanifest={dump.with_suffix('.json')}\n")


def cmd_verify(args) -> None:
    dump = Path(args.file)
    manifest = json.loads(dump.with_suffix(".json").read_text(encoding="utf-8"))

    # 1) Integridad: el archivo no cambió desde que se creó.
    actual = sha256sum(dump)
    if actual != manifest["sha256"]:
        sys.exit(f"FALLO de integridad: SHA-256 {actual} != {manifest['sha256']}")
    print("Integridad OK (SHA-256 coincide)")

    # 2) Restauración real en una base temporal desechable.
    scratch = f"restore_check_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    with psycopg.connect(args.admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
    target = make_conninfo(args.admin_url, dbname=scratch)
    try:
        subprocess.run(
            [pg_tool("pg_restore"), "--no-owner", "--no-acl", "--exit-on-error",
             "--dbname", target, str(dump)],
            check=True,
        )
        restored = row_counts(target)
    finally:
        if not args.keep:
            with psycopg.connect(args.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch)))

    # 3) Comparación tabla por tabla con la huella del manifiesto.
    expected = manifest["row_counts"]
    bad = {t: (expected.get(t), restored.get(t))
           for t in set(expected) | set(restored) if expected.get(t) != restored.get(t)}
    for t in sorted(expected):
        print(f"  {t:<20} esperado={expected[t]:<8} restaurado={restored.get(t)}")
    if bad:
        sys.exit(f"FALLO de verificación, tablas distintas: {bad}")
    print(f"Restauración verificada en '{scratch}': {len(expected)} tablas coinciden")


def cmd_prune(args) -> None:
    dumps = sorted(Path(args.dir).glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[args.keep:]:
        old.unlink()
        old.with_suffix(".json").unlink(missing_ok=True)
        print(f"Eliminado por retención: {old.name}")
    print(f"Retención aplicada: se conservan {min(len(dumps), args.keep)} respaldos")


def cmd_log(args) -> None:
    manifest = {}
    if args.manifest and Path(args.manifest).exists():
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    with psycopg.connect(args.database_url) as conn:
        conn.execute(
            "INSERT INTO backup_log (kind, status, file_name, size_bytes, sha256, verified, location, details) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (manifest.get("kind", args.kind), args.status, manifest.get("file"),
             manifest.get("size_bytes"), manifest.get("sha256"), args.verified,
             args.location, args.details),
        )
    print(f"Registrado en backup_log: {args.status}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), required=not os.environ.get("DATABASE_URL"))
    c.add_argument("--out-dir", default="backups")
    c.add_argument("--github-output", action="store_true", help="Exporta rutas como outputs de GitHub Actions")
    c.set_defaults(func=cmd_create)

    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    v.add_argument("--admin-url", required=True, help="Conexión a un servidor desechable (base 'postgres')")
    v.add_argument("--keep", action="store_true", help="No borrar la base temporal")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("prune")
    r.add_argument("--dir", default="backups")
    r.add_argument("--keep", type=int, default=7)
    r.set_defaults(func=cmd_prune)

    lg = sub.add_parser("log")
    lg.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), required=not os.environ.get("DATABASE_URL"))
    lg.add_argument("--status", choices=["ok", "failed"], required=True)
    lg.add_argument("--manifest")
    lg.add_argument("--kind", default="logical-full")
    lg.add_argument("--verified", action="store_true")
    lg.add_argument("--location")
    lg.add_argument("--details")
    lg.set_defaults(func=cmd_log)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
