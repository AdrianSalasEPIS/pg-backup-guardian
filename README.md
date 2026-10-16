# 🛡️ PG Backup Guardian

[![CI/CD](https://github.com/AdrianSalasEPIS/pg-backup-guardian/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/AdrianSalasEPIS/pg-backup-guardian/actions/workflows/ci-cd.yml)
[![Backup diario](https://github.com/AdrianSalasEPIS/pg-backup-guardian/actions/workflows/backup.yml/badge.svg)](https://github.com/AdrianSalasEPIS/pg-backup-guardian/actions/workflows/backup.yml)

App de ejemplo (una pequeña biblioteca) para demostrar **estrategias de respaldo de bases de datos con PostgreSQL**:
respaldos lógicos automatizados y cifrados, restauración verificada, política de retención y respaldos físicos
incrementales nativos de PostgreSQL 17.

🌐 **App en vivo:** https://pg-backup-guardian.onrender.com (plan gratuito: la primera carga tras inactividad tarda ~50 s)

Proyecto del curso **Base de Datos II** — Escuela Profesional de Ingeniería de Sistemas, Universidad Privada de Tacna.
Autores: **Adrian Fabricio Salas Acuña** y **Keyla Noelia Lopez Ojeda**.

## 🧪 Laboratorio de recuperación (en vivo)

La página principal no es solo un panel: permite recorrer el ciclo completo contra la base de producción.

| Paso | Qué hace | Cómo se comprueba |
|---|---|---|
| ⚡ **Crear punto** | `COPY ... TO STDOUT` de cada tabla en una transacción `REPEATABLE READ`, ZIP con CSV + manifiesto | Filas + md5 del contenido por tabla + SHA-256 del ZIP |
| ✅ **Verificar** | Restaura en un esquema temporal dentro de una transacción que **siempre se revierte** | Compara filas y md5 tabla por tabla |
| 💥 **Simular desastre** | `DELETE` sin `WHERE`, datos corrompidos o `DROP TABLE` (requiere PIN) | El panel detecta la diferencia contra el último punto |
| ♻️ **Restaurar** | `TRUNCATE` + `COPY FROM` + reajuste de secuencias, todo atómico (requiere PIN) | Si la huella final no coincide, se revierte y no queda a medias |

Además: ejecuciones de **GitHub Actions en vivo**, indicador de **RPO actual**, gráfico de tamaños, línea de tiempo,
descarga de cada punto como ZIP, tabla **"qué protege / qué NO protege"** de cada estrategia, modo oscuro y diseño
responsive. Las acciones destructivas piden un **PIN de administrador** (con bloqueo tras 5 intentos fallidos).

## Arquitectura

```
 git push ──► GitHub Actions ─┬─ CI: pruebas con PostgreSQL 17 real (API + backup/restore)
                              ├─ CD: deploy hook ──► Render (FastAPI)  ──►  Neon (PostgreSQL 17)
                              └─ Cron diario: pg_dump ─► restauración de prueba ─► cifrado AES-256
                                                     ─► artefacto (30 días) ─► registro en backup_log
```

| Pieza | Tecnología |
|---|---|
| App / API | Python 3.12, FastAPI, psycopg 3 |
| Base de datos | PostgreSQL 17 en [Neon](https://neon.tech) (con PITR/branching del proveedor) |
| Hosting | [Render](https://render.com) (Blueprint `render.yaml`) |
| Automatización | GitHub Actions (`ci-cd.yml`, `backup.yml`) |

## Estrategia de respaldo implementada

| Capa | Qué | Frecuencia | RPO |
|---|---|---|---|
| 1. PITR del proveedor | Historial WAL de Neon (ventana de 6 h en el plan gratuito) | Continuo | segundos |
| 2. Lógico completo | `pg_dump -Fc` cifrado, fuera del proveedor (GitHub) | Diario 06:00 UTC | 24 h |
| 3. Físico incremental | `pg_basebackup --incremental` + `pg_combinebackup` (demo local) | Bajo demanda | — |

Cada respaldo lógico **se restaura de verdad** en un PostgreSQL desechable y se compara el número de filas de cada
tabla con el manifiesto (SHA-256 + conteos). Un respaldo que nunca se restauró no es un respaldo.

## Ejecutar en local (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/guardian"
$env:PG_BIN = "C:\Program Files\PostgreSQL\17\bin"
psql -U postgres -c "CREATE DATABASE guardian"
.\.venv\Scripts\uvicorn app.main:app --reload     # http://127.0.0.1:8000
.\.venv\Scripts\pytest -v                          # 15 pruebas (API, backup/restore y laboratorio)
```

Respaldo manual, verificación y retención:

```powershell
python scripts/backup.py create --out-dir backups
python scripts/backup.py verify --file backups/<archivo>.dump --admin-url postgresql://postgres:postgres@localhost:5432/postgres
python scripts/backup.py prune --dir backups --keep 7
```

Demo de respaldo físico completo + incremental (PostgreSQL 17):

```powershell
$env:PGPASSWORD = "postgres"
.\scripts\demo_incremental.ps1
```

## Restaurar un respaldo de producción

1. Descargar el artefacto `pg-backup-N` desde la pestaña **Actions → Backup diario**.
2. Descifrar: `gpg --decrypt guardian_XXXX.dump.gpg > guardian.dump`
3. Restaurar: `pg_restore --no-owner --clean --if-exists -d "$DATABASE_URL" guardian.dump`

## Despliegue (configuración única)

1. **Neon**: crear proyecto PostgreSQL 17 y copiar la cadena de conexión.
2. **Render**: *New → Blueprint* con este repositorio; pegar `DATABASE_URL`. Copiar el *Deploy Hook* del servicio.
3. **GitHub → Settings → Secrets and variables → Actions**:
   - Secretos: `DATABASE_URL`, `BACKUP_PASSPHRASE`, `RENDER_DEPLOY_HOOK_URL`
   - Variable: `APP_URL` (p. ej. `https://pg-backup-guardian.onrender.com`)

Desde ahí, cada `git push` a `main` prueba, despliega y verifica la versión publicada automáticamente.

## Endpoints

| Ruta | Descripción |
|---|---|
| `GET /` | Panel + laboratorio de recuperación |
| `GET /health` | Estado, latencia a la BD y commit desplegado |
| `GET /api/stats` · `GET /api/backups` · `GET /api/events` | Indicadores, historial y línea de tiempo |
| `GET/POST /api/snapshots` | Listar / crear puntos de restauración |
| `POST /api/snapshots/{id}/verify` | Restauración de prueba con rollback |
| `POST /api/snapshots/{id}/restore` 🔒 | Restauración atómica en producción |
| `GET /api/snapshots/{id}/download` | Descargar el ZIP (CSV + manifiesto) |
| `POST /api/disaster/{delete_all\|corrupt\|drop_table}` 🔒 | Simular un desastre |
| `GET /api/github/runs` | Últimas ejecuciones de GitHub Actions |
| `GET/POST/DELETE /api/books` | CRUD de la biblioteca (eliminar 🔒) |
| `GET /api/strategies` | Qué protege y qué no cada estrategia |
| `GET /docs` | Swagger |

🔒 = requiere la cabecera `X-Admin-Pin` (variable `ADMIN_PIN` en Render).
