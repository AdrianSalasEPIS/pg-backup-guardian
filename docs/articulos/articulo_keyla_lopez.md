---
title: "Completo, incremental y PITR en PostgreSQL 17: guía práctica con restauración verificada"
published: true
description: "Los tipos de backup explicados con comandos reales: pg_dump, pg_basebackup --incremental, pg_combinebackup y recuperación a un punto en el tiempo."
tags: postgres, database, backup, tutorial
cover_image: https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/portada_keyla.png
---

> **Resumen** — Hay muchas formas de respaldar una base de datos, y cada una responde a un riesgo distinto. En este artículo comparo los **respaldos lógicos y físicos**, el **completo, el diferencial y el incremental**, y la **recuperación a un punto en el tiempo (PITR)**, usando PostgreSQL 17, que por primera vez trae **backups incrementales nativos**. Todo con comandos que ejecuté y con restauraciones que realmente comprobé.
>
> 🔗 **Repositorio:** https://github.com/AdrianSalasEPIS/pg-backup-guardian
> 🌐 **App publicada:** https://pg-backup-guardian.onrender.com
> 🎥 **Video:** próximamente

---

## 1. Antes de elegir: ¿de qué te quieres proteger?

| Riesgo | Ejemplo | Estrategia adecuada |
|---|---|---|
| Error humano | `DELETE FROM clientes;` sin `WHERE` | PITR (volver al segundo anterior) |
| Corrupción o fallo del disco | El servidor no arranca | Respaldo físico + WAL |
| Pérdida del proveedor o la cuenta | Cuenta cloud suspendida | Copia lógica **fuera** del proveedor |
| Migración o cambio de versión | Pasar de PostgreSQL 16 a 17 | Respaldo lógico (`pg_dump`) |
| Ransomware | Archivos cifrados por un atacante | Copias inmutables y desconectadas |

No hay un único "mejor backup": hay una **combinación** que cubre tus riesgos con un costo razonable.

> Trabajamos con **PostgreSQL** en lugar de MS SQL Server: es libre, multiplataforma y trae todas las herramientas de respaldo incluidas.

## 2. Lógico vs. físico

| | Lógico (`pg_dump`) | Físico (`pg_basebackup`) |
|---|---|---|
| Qué copia | Sentencias / datos (SQL o formato custom) | Los archivos del clúster, bloque a bloque |
| Alcance | Una base, un esquema o una tabla | El clúster completo |
| Portabilidad | Alta: otra versión u otro sistema operativo | Misma versión mayor y arquitectura |
| Velocidad en bases grandes | Lenta | Rápida |
| Permite PITR | No | Sí, junto con el archivado de WAL |

```bash
# Lógico: una base, formato custom comprimido
pg_dump -Fc -d biblioteca -f biblioteca.dump
pg_restore -d biblioteca_restaurada biblioteca.dump

# Físico: todo el clúster
pg_basebackup -h localhost -U postgres -D /backups/full -c fast -P
```

## 3. Completo, diferencial e incremental

- **Completo:** copia todo. Es el más simple de restaurar, pero el más pesado.
- **Diferencial:** copia lo que cambió **desde el último completo**. Para restaurar: completo + último diferencial.
- **Incremental:** copia lo que cambió **desde el último respaldo de cualquier tipo**. Es el más liviano; para restaurar se necesita el completo + **toda** la cadena de incrementales.

```
Domingo   Lunes      Martes     Miércoles
[COMPLETO] [INC 1]   [INC 2]    [INC 3]      → restaurar el miércoles = C + I1 + I2 + I3
[COMPLETO] [DIF 1]   [DIF 2]    [DIF 3]      → restaurar el miércoles = C + D3
```

Hasta PostgreSQL 16 los incrementales requerían herramientas externas (pgBackRest, Barman). **PostgreSQL 17 los trae de serie.**

## 4. Práctica: backup incremental nativo en PostgreSQL 17

En el repositorio hay un script (`scripts/demo_incremental.ps1`) que ejecuta todo el ciclo. Estos son los pasos y lo que obtuvimos.

**Paso 0: activar el resumen de WAL** (necesario para saber qué bloques cambiaron):

```sql
ALTER SYSTEM SET summarize_wal = on;
SELECT pg_reload_conf();
```

**Paso 1: datos iniciales y respaldo completo** (100 000 filas):

```bash
pg_basebackup -h localhost -U postgres -D full -c fast -P
```

**Paso 2: cambios** (+50 000 filas) y **respaldo incremental** a partir del manifiesto del completo:

```bash
pg_basebackup -h localhost -U postgres -D incr1 -c fast -P --incremental full/backup_manifest
```

| Respaldo | Tamaño |
|---|---|
| Completo | 86,6 MB |
| Incremental | 31,4 MB |

El incremental pesó **alrededor de un tercio** del completo, y gran parte de ese tamaño son los archivos WAL que todo respaldo físico incluye. En una base grande con pocos cambios diarios, la diferencia es mucho mayor.

**Paso 3: reconstruir y verificar.** Un incremental no se puede arrancar solo; `pg_combinebackup` lo une con el completo:

```bash
pg_combinebackup full incr1 -o combined
pg_verifybackup combined          # → "copia de seguridad verificada correctamente"
```

**Paso 4: la prueba de verdad**, levantar el clúster reconstruido en otro puerto y contar filas:

```bash
pg_ctl -D combined -o "-p 5433" -w start
psql -p 5433 -d demo_incremental -c "SELECT count(*) FROM ventas;"   # → 150000
```

Esta es la salida real del script de principio a fin:

![Salida del demo: respaldo completo de 86,6 MB, incremental de 31,4 MB, pg_verifybackup correcto y 150 000 filas restauradas](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/demo_incremental.png)

## 5. PITR: volver a cualquier segundo del pasado

La recuperación a un punto en el tiempo combina un **respaldo físico base** con el **archivo continuo de WAL** (el registro de cada cambio). En un servidor propio se configura así:

```ini
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'copy "%p" "D:\\wal_archive\\%f"'     # en Linux: cp %p /wal_archive/%f
```

Para recuperar hasta justo antes de un error:

```ini
# postgresql.conf de la copia restaurada (+ archivo vacío recovery.signal)
restore_command = 'copy "D:\\wal_archive\\%f" "%p"'
recovery_target_time = '2026-10-16 10:29:59-05'
recovery_target_action = 'promote'
```

En la nube no tuvimos que montar nada de esto: **Neon** conserva el historial WAL y permite crear una *branch* de la base en cualquier instante dentro de la ventana de retención de su plan (en nuestro proyecto, con el plan gratuito, la ventana es de **6 horas**). Es PITR como servicio. Por eso no basta solo con él: si el error se detecta al día siguiente, la ventana ya pasó, y ahí entra el respaldo lógico diario.

## 6. Cómo lo combinamos en nuestro proyecto

| Capa | Herramienta | Frecuencia | RPO aproximado | Para qué |
|---|---|---|---|---|
| PITR | Historial WAL de Neon | Continuo | segundos | Errores humanos |
| Lógico completo cifrado | `pg_dump` + GPG en GitHub Actions | Diario | 24 h | Independencia del proveedor |
| Físico + incremental | `pg_basebackup --incremental` | Bajo demanda | según la cadena | Bases grandes, restauración rápida |

Y la regla que aplicamos a todas: **un backup no cuenta hasta que se restauró**. El pipeline de nuestra app restaura cada dump diario en un PostgreSQL desechable y compara las filas de cada tabla antes de marcarlo como válido.

La app publicada muestra cada estrategia con lo que **protege** y, sobre todo, lo que **no protege**. Esa segunda columna es la que explica por qué hay que combinarlas:

![Tarjetas de estrategias en la app: qué protege y qué no protege cada una](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/estrategias.png)

Además, la app tiene un **laboratorio de recuperación** donde se puede provocar un desastre real (un `DELETE` sin `WHERE`) y restaurar en segundos, con la huella de cada tabla verificada:

![Laboratorio de recuperación después de restaurar: los datos vuelven a coincidir con el punto de restauración](https://raw.githubusercontent.com/AdrianSalasEPIS/pg-backup-guardian/main/docs/img/lab_restaurado.png)

## 7. Checklist para tu propia estrategia

- [ ] Definí RPO y RTO con el negocio (¿cuántos datos y cuánto tiempo puedo perder?).
- [ ] Tengo al menos una copia **fuera** del servidor y del proveedor (regla 3-2-1).
- [ ] Las copias que salen de mi infraestructura están **cifradas**.
- [ ] Las herramientas tienen la **misma versión** que el servidor.
- [ ] La restauración se **prueba automáticamente** y alguien se entera si falla.
- [ ] Hay una **política de retención** escrita (cuántos días o copias se guardan).
- [ ] El procedimiento de restauración está documentado paso a paso.

## Enlaces

- 📦 **Repositorio:** https://github.com/AdrianSalasEPIS/pg-backup-guardian
- 🌐 **App en la nube:** https://pg-backup-guardian.onrender.com
- 🎥 **Video:** próximamente
- 📖 Artículo complementario de mi compañero Adrian Salas: *Respaldos que sí se pueden restaurar* — https://dev.to/adriansalasepis/respaldos-que-si-se-pueden-restaurar-estrategia-automatizada-para-postgresql-con-github-actions-3glj

## Referencias

- PostgreSQL Global Development Group. (2024). *Continuous archiving and point-in-time recovery (PITR)*. https://www.postgresql.org/docs/17/continuous-archiving.html
- PostgreSQL Global Development Group. (2024). *pg_basebackup*. https://www.postgresql.org/docs/17/app-pgbasebackup.html
- PostgreSQL Global Development Group. (2024). *pg_combinebackup*. https://www.postgresql.org/docs/17/app-pgcombinebackup.html
- Neon. (s. f.). *Branching and point-in-time restore*. https://neon.tech/docs

---

*Keyla Noelia López Ojeda — Estudiante de Ingeniería de Sistemas, Universidad Privada de Tacna. Proyecto del curso Base de Datos II.*
