-- Esquema de la app de ejemplo (biblioteca) + bitácora de respaldos.
-- Es idempotente: se ejecuta en cada arranque de la app.

CREATE TABLE IF NOT EXISTS books (
    id          SERIAL PRIMARY KEY,
    title       TEXT        NOT NULL,
    author      TEXT        NOT NULL,
    year        INTEGER     CHECK (year BETWEEN 0 AND 2100),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loans (
    id          SERIAL PRIMARY KEY,
    book_id     INTEGER     NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    member      TEXT        NOT NULL,
    loaned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    returned_at TIMESTAMPTZ
);

-- Cada ejecución del pipeline de respaldos deja aquí su resultado.
CREATE TABLE IF NOT EXISTS backup_log (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT        NOT NULL,              -- logical-full, snapshot, ...
    status      TEXT        NOT NULL,              -- ok | failed
    file_name   TEXT,
    size_bytes  BIGINT,
    sha256      TEXT,
    verified    BOOLEAN     NOT NULL DEFAULT false, -- ¿se probó la restauración?
    location    TEXT,                               -- dónde quedó guardado
    details     TEXT
);

-- Línea de tiempo del laboratorio de recuperación (respaldos, desastres, restauraciones).
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    type        TEXT        NOT NULL,   -- snapshot | verify | disaster | restore | info
    level       TEXT        NOT NULL DEFAULT 'info',  -- info | ok | warn | error
    message     TEXT        NOT NULL
);

-- Puntos de restauración rápidos generados desde la app (CSV comprimidos en ZIP).
-- Viven en un esquema aparte para que un desastre sobre "public" no los alcance.
CREATE SCHEMA IF NOT EXISTS vault;
CREATE TABLE IF NOT EXISTS vault.snapshots (
    id           SERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    label        TEXT,
    size_bytes   INTEGER     NOT NULL,
    sha256       TEXT        NOT NULL,
    duration_ms  INTEGER     NOT NULL,
    manifest     JSONB       NOT NULL,   -- columnas, filas y huella md5 por tabla
    payload      BYTEA       NOT NULL,   -- ZIP con un CSV por tabla + manifest.json
    verified_at  TIMESTAMPTZ
);

-- Datos de ejemplo solo si la tabla está vacía.
INSERT INTO books (title, author, year)
SELECT * FROM (VALUES
    ('Cien años de soledad', 'Gabriel García Márquez', 1967),
    ('La ciudad y los perros', 'Mario Vargas Llosa', 1963),
    ('Los ríos profundos', 'José María Arguedas', 1958),
    ('Database System Concepts', 'Silberschatz, Korth, Sudarshan', 2019),
    ('PostgreSQL 17 Administration Cookbook', 'Simon Riggs, Gianni Ciolli', 2024)
) AS seed(title, author, year)
WHERE NOT EXISTS (SELECT 1 FROM books);

INSERT INTO loans (book_id, member)
SELECT b.id, m.member
FROM (VALUES (1, 'Adrian Salas'), (3, 'Keyla Lopez')) AS m(book_id, member)
JOIN books b ON b.id = m.book_id
WHERE NOT EXISTS (SELECT 1 FROM loans);
