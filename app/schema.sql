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
    kind        TEXT        NOT NULL,              -- logical-full, physical-full, incremental...
    status      TEXT        NOT NULL,              -- ok | failed
    file_name   TEXT,
    size_bytes  BIGINT,
    sha256      TEXT,
    verified    BOOLEAN     NOT NULL DEFAULT false, -- ¿se probó la restauración?
    location    TEXT,                               -- dónde quedó guardado
    details     TEXT
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
