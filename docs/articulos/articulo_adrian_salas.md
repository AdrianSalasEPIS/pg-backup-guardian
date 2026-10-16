---
title: "Respaldos que sí se pueden restaurar: estrategia automatizada para PostgreSQL con GitHub Actions, Render y Neon"
published: true
description: "Cómo diseñé un pipeline de backups para PostgreSQL que cifra, verifica la restauración y se despliega solo, sin usar SQL Server."
tags: postgres, database, devops, githubactions
cover_image: https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/portada_adrian.png
---

> **TL;DR** — Un respaldo que nunca se restauró es solo una esperanza. En este artículo construyo una app real en la nube (FastAPI + PostgreSQL 17) cuyo respaldo diario se **genera, se restaura en una base desechable, se compara fila por fila, se cifra con AES-256 y se guarda fuera del proveedor**, todo con GitHub Actions. Cada `git push` además prueba y despliega la app automáticamente.
>
> 🔗 **Repositorio:** https://github.com/AdrianSalasEPIS/pg-backup-guardian
> 🌐 **App publicada:** https://pg-backup-guardian.onrender.com
> 🎥 **Video (menos de 5 min):** próximamente

---

## 1. El problema: tener backups no es lo mismo que poder recuperarse

Casi todos los equipos "tienen backups". Pocos pueden responder con seguridad estas tres preguntas:

1. **¿Cuántos datos puedo perder como máximo?** → **RPO** (*Recovery Point Objective*).
2. **¿Cuánto tiempo tardo en volver a estar en línea?** → **RTO** (*Recovery Time Objective*).
3. **¿Cuándo fue la última vez que restauré un backup y funcionó?**

Si la respuesta a la tercera es "nunca", el RPO y el RTO reales son desconocidos. Por eso el principio que guía todo este proyecto es simple: **cada respaldo se restaura automáticamente antes de darlo por bueno**.

> Decidí trabajar con **PostgreSQL** (y no con MS SQL Server) porque es open source, sus herramientas de respaldo (`pg_dump`, `pg_restore`, `pg_basebackup`) vienen incluidas y existen proveedores en la nube con plan gratuito.

## 2. La regla 3-2-1 aplicada a un proyecto pequeño

La regla clásica dice: **3** copias de los datos, en **2** medios distintos, **1** fuera del sitio. Así la apliqué:

| Copia | Dónde vive | Tipo | Protege contra |
|---|---|---|---|
| 1. Datos vivos | Neon (PostgreSQL 17 gestionado) | Producción | — |
| 2. Historial WAL del proveedor | Neon (PITR / *branching*) | Continuo, segundos de RPO | Errores humanos (`DELETE` sin `WHERE`) |
| 3. Dump lógico cifrado | GitHub (artefacto de Actions, 30 días) | Diario | Caída o pérdida de la cuenta del proveedor |

La copia 3 es la clave: **está fuera del proveedor de la base de datos**. Si mañana pierdo el acceso a Neon, el dump cifrado sigue en GitHub y puedo restaurarlo en cualquier PostgreSQL.

## 3. Arquitectura

```
 git push ──► GitHub Actions ─┬─ CI: pruebas contra PostgreSQL 17 real (API + backup/restore)
                              ├─ CD: deploy hook ──► Render (FastAPI) ──► Neon (PostgreSQL 17)
                              └─ Cron diario: pg_dump ─► restauración de prueba ─► cifrado AES-256
                                                     ─► artefacto (30 días) ─► registro en backup_log
```

- **App:** una pequeña biblioteca (libros y préstamos) con un panel que muestra el historial de respaldos.
- **Base de datos:** PostgreSQL 17 en Neon.
- **Hosting:** Render, configurado como código con un `render.yaml`.
- **Automatización:** dos workflows de GitHub Actions: `ci-cd.yml` y `backup.yml`.

![Panel de la app publicada en Render: indicadores de respaldo, RPO actual y estado de integridad de los datos](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/app_panel.png)

## 4. El corazón: `backup.py`

Escribí un script de Python con cuatro subcomandos: `create`, `verify`, `prune` y `log`.

### 4.1 Crear el respaldo + manifiesto

```python
subprocess.run(
    [pg_tool("pg_dump"), "--format=custom", "--compress=9", "--no-owner", "--no-acl",
     "--file", str(dump), "--dbname", args.database_url],
    check=True,
)
```

Uso el **formato custom** (`-Fc`) porque está comprimido y permite restaurar tablas sueltas con `pg_restore`. Junto al `.dump` se guarda un **manifiesto JSON** con la huella del respaldo:

```json
{
  "file": "guardian_20261016T060304Z.dump",
  "kind": "logical-full",
  "server_version": "17.11",
  "size_bytes": 7757,
  "sha256": "9213e404c8ddf8f8...",
  "row_counts": { "backup_log": 0, "books": 5, "loans": 2 }
}
```

### 4.2 Verificar = restaurar de verdad

La verificación tiene tres pasos:

1. **Integridad:** se recalcula el SHA-256 y se compara con el manifiesto (detecta archivos corruptos o alterados).
2. **Restauración real:** se crea una base temporal `restore_check_<fecha>` y se ejecuta `pg_restore --exit-on-error`.
3. **Comparación:** se cuentan las filas de cada tabla restaurada y se comparan con `row_counts`.

```text
Integridad OK (SHA-256 coincide)
  backup_log           esperado=0        restaurado=0
  books                esperado=5        restaurado=5
  loans                esperado=2        restaurado=2
Restauración verificada en 'restore_check_20261016060305': 3 tablas coinciden
```

Si algo no coincide, el script termina con error y **el pipeline falla en rojo**: me entero el mismo día, no cuando ya necesito el backup.

### 4.3 Retención

`prune --keep 7` conserva los N respaldos más recientes. En producción la retención la aplica GitHub (`retention-days: 30`), pero el script sirve para copias locales.

## 5. El workflow de respaldo diario

```yaml
on:
  schedule:
    - cron: "0 6 * * *"   # 06:00 UTC = 01:00 hora de Perú
  workflow_dispatch:

jobs:
  backup:
    services:
      restore-target:          # PostgreSQL desechable para probar la restauración
        image: postgres:17
    steps:
      - name: 1. Crear respaldo completo
        run: python scripts/backup.py create --out-dir backups --github-output
      - name: 2. Verificar restauración
        run: python scripts/backup.py verify --file "$DUMP_FILE" --admin-url postgresql://postgres:postgres@localhost:5432/postgres
      - name: 3. Cifrar con AES-256
        run: gpg --batch --pinentry-mode loopback --passphrase "$PASSPHRASE" --symmetric --cipher-algo AES256 "$DUMP_FILE"
      - name: 4. Guardar copia externa (artefacto, 30 días)
        uses: actions/upload-artifact@v5
      - name: 5. Registrar en backup_log
        run: python scripts/backup.py log --status ok --verified ...
```

*(Versión simplificada: `$DUMP_FILE` es la ruta que el paso 1 exporta como output. El archivo completo está en `.github/workflows/backup.yml` del repositorio.)*

Tres detalles importantes:

- **Cifrado obligatorio.** El repositorio es público; un dump sin cifrar sería una filtración de datos. La contraseña vive en `secrets.BACKUP_PASSPHRASE`, nunca en el código.
- **Versión del cliente = versión del servidor.** `pg_dump` 16 no puede respaldar un servidor 17, así que el workflow instala `postgresql-client-17` desde el repositorio oficial PGDG.
- **Los fallos también se registran.** Un paso con `if: failure()` escribe `status = failed` en `backup_log`, y el panel de la app lo muestra en rojo.

## 6. Despliegue automatizado (CI/CD)

El workflow `ci-cd.yml` se ejecuta en cada `push` a `main`:

1. **test:** levanta un PostgreSQL 17 como *service container* y ejecuta 15 pruebas con `pytest`: la API, un backup que se restaura de verdad, un archivo alterado que la verificación debe detectar y el ciclo completo punto → desastre → restauración para los tres escenarios de desastre.
2. **deploy:** solo si las pruebas pasan, llama al *Deploy Hook* de Render.
3. **smoke test:** consulta `/health` de la URL pública hasta que responde con el **SHA del commit recién desplegado** y `"database": "up"`. Así el pipeline confirma que lo publicado es exactamente lo que se probó.

```bash
for i in $(seq 1 40); do
  body=$(curl -fsS "$APP_URL/health" || true)
  if echo "$body" | grep -q "$GITHUB_SHA"; then echo "Deploy verificado"; exit 0; fi
  sleep 20
done
exit 1
```

En Render, `autoDeploy: false` evita que se publique código que no pasó las pruebas: la única puerta de entrada a producción es el pipeline.

![Historial de GitHub Actions: ejecuciones de CI/CD y del respaldo diario](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/github_actions.png)

El historial también es honesto: la primera ejecución del respaldo falló (un carácter invisible se había colado en el secreto de la conexión), quedó registrada en rojo y la siguiente, ya corregida, pasó en verde.

## 7. Laboratorio de recuperación: provocar un desastre a propósito

Explicar que un backup funciona no es lo mismo que **demostrarlo**. Por eso la página principal de la app tiene un laboratorio que cualquiera puede ver funcionar contra la base de producción:

1. **⚡ Crear punto de restauración.** En una transacción `REPEATABLE READ` (instantánea consistente) se copia cada tabla con `COPY ... TO STDOUT` a CSV, se empaqueta en un ZIP y se guarda la huella: filas y **md5 del contenido** por tabla, más el SHA-256 del archivo.
2. **✅ Verificar.** Se restaura en un esquema temporal **dentro de una transacción que siempre se revierte** (`force_rollback`): producción ni se entera, y se comparan filas y md5 tabla por tabla.
3. **💥 Simular desastre.** Tres escenarios reales: `DELETE` sin `WHERE`, un script que corrompe datos y un `DROP TABLE`. El panel detecta al instante que los datos ya no coinciden con el último punto y se pone en rojo.
4. **♻️ Restaurar.** `TRUNCATE` + `COPY FROM` + reajuste de secuencias en **una sola transacción**: si la huella final no coincide, se revierte todo. En producción (Render + Neon) la base vuelve a estar sana en unos 3 segundos.

Así se ve justo después de provocar el desastre: el aviso se pone en rojo y la consola narra cada paso.

![Laboratorio tras simular un DELETE sin WHERE: libros y préstamos quedan en 0 y el panel lo detecta](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/lab_desastre.png)

Y unos segundos después de pulsar **Restaurar**:

![Laboratorio tras restaurar: las tablas vuelven a 5 y 2 filas con la huella md5 verificada](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/lab_restaurado.png)

```python
with conn.transaction():
    conn.execute("TRUNCATE books, loans RESTART IDENTITY CASCADE")
    loaded = _load(conn, payload, manifest, "public")      # COPY FROM de cada CSV
    if not all(c["ok"] for c in _compare(manifest["tables"], loaded)):
        raise IntegrityError("La restauración no coincide; se revirtió")
```

Las acciones destructivas están protegidas con un **PIN de administrador** (con bloqueo tras 5 intentos fallidos), porque la app es pública. Este punto de restauración es una capa **rápida** que vive en el mismo servidor; no reemplaza la copia externa cifrada del paso anterior, y la propia app lo dice en su tabla de "qué NO protege".

Cada acción queda registrada en una línea de tiempo, y cada punto de restauración se puede descargar como ZIP (CSV + manifiesto):

![Puntos de restauración verificados y línea de tiempo del laboratorio, en modo oscuro](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/puntos_timeline_dark.png)

## 8. ¿Y si hay que restaurar producción?

```bash
# 1. Descargar el artefacto pg-backup-N desde GitHub Actions
gpg --decrypt guardian_20261016T060000Z.dump.gpg > guardian.dump
# 2. Restaurar (en una base nueva o sobre la existente)
pg_restore --no-owner --clean --if-exists -d "$DATABASE_URL" guardian.dump
```

Para errores recientes (un `DELETE` accidental de hace 10 minutos), es más rápido usar el **PITR del proveedor**: crear una *branch* de Neon en el instante anterior al error. El dump diario es el seguro para cuando el proveedor completo falla.

## 9. Lecciones aprendidas

1. **Automatiza la restauración, no solo el respaldo.** Es lo que convierte un archivo en una garantía.
2. **Guarda al menos una copia fuera del proveedor**, y **cífrala** si sale de tu infraestructura.
3. **Usa la misma versión de herramientas que el servidor.** Es el error más común con `pg_dump`.
4. **Combina estrategias.** El PITR da un RPO de segundos; el dump lógico da portabilidad e independencia del proveedor.
5. **Haz visible el estado.** Una tabla `backup_log` y un panel convierten "creo que hay backups" en evidencia.

## Enlaces

- 📦 **Repositorio:** https://github.com/AdrianSalasEPIS/pg-backup-guardian
- 🌐 **App en la nube:** https://pg-backup-guardian.onrender.com
- 🎥 **Video:** próximamente
- 📖 Artículo complementario de mi compañera Keyla López: *Completo, incremental y PITR en PostgreSQL 17* — https://dev.to/keylalopezz/completo-incremental-y-pitr-en-postgresql-17-guia-practica-con-restauracion-verificada-37pl

## Referencias

- PostgreSQL Global Development Group. (2024). *PostgreSQL 17 documentation: Backup and restore*. https://www.postgresql.org/docs/17/backup.html
- PostgreSQL Global Development Group. (2024). *pg_dump*. https://www.postgresql.org/docs/17/app-pgdump.html
- GitHub. (s. f.). *Workflow syntax for GitHub Actions*. https://docs.github.com/actions
- Render. (s. f.). *Deploy hooks*. https://render.com/docs/deploy-hooks

---

*Adrian Fabricio Salas Acuña — Estudiante de Ingeniería de Sistemas, Universidad Privada de Tacna. Proyecto del curso Base de Datos II.*
