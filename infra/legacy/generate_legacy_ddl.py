"""Genera el DDL Postgres del esquema `legacy` desde el SQLite V2.

Lee la V2 en modo solo lectura y emite `02-legacy-tables.generated.sql`
determinista (tablas ordenadas, columnas en orden original): una tabla
`legacy.<nombre>` por cada tabla V2, sin reinterpretar semantica.

Mapa de tipos SQLite -> Postgres (tipos interpretables, plan 07 3.7.5):
  INTEGER  -> INTEGER (las PK enteras se conservan como historial)
  VARCHAR / TEXT / CHAR / CLOB -> TEXT
  DATETIME / DATE / TIMESTAMP   -> TIMESTAMPTZ
  BOOLEAN / BOOL                -> BOOLEAN
  JSON                          -> JSONB
  REAL / FLOAT / DOUBLE         -> DOUBLE PRECISION
  BLOB                          -> BYTEA
  cualquier otro                -> TEXT (sin perdida de lectura)

Decision consciente: no se copian las FOREIGN KEY de V2 al esquema
`legacy` (evita orden de carga y deja el historial sin restricciones
cruzadas); se crean indices para las lecturas historicas (`user_id`,
`job_id`, `timestamp`/`created_at` cuando la columna exista).
El rol de aplicacion queda sin DML via `03-legacy-readonly.sql`.

Sobre sellado: este script solo copia nombres de columnas, nunca valores;
los valores sensibles viajan opacos en `import_legacy.py` y jamas aparecen
en el DDL ni en reportes.

Uso:
    python3 infra/legacy/generate_legacy_ddl.py \
        --source ../api-bots-mrbot-v2/data/sql_app.db \
        --out infra/legacy/02-legacy-tables.generated.sql
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3

PROTOCOL_VERSION: int = 1
"""Version del sobre de migracion (entero, 1)."""

# Tablas internas de SQLite que no forman parte del historial a importar.
SKIP_TABLES = frozenset({"sqlite_sequence"})


def map_type(sqlite_type: str) -> str:
    """Traduce un tipo declarado en SQLite a un tipo Postgres interpretable."""
    t = (sqlite_type or "").strip().upper()
    if "INT" in t:
        return "INTEGER"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT", "VARCHAR")):
        return "TEXT"
    if any(k in t for k in ("DATETIME", "TIMESTAMP", "DATE")):
        return "TIMESTAMPTZ"
    if "BOOL" in t:
        return "BOOLEAN"
    if "JSON" in t:
        return "JSONB"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BLOB" in t:
        return "BYTEA"
    return "TEXT"


def quote_ident(name: str) -> str:
    """Entrecomilla un identificador Postgres de forma segura."""
    return '"' + name.replace('"', '""') + '"'


def legacy_ddl_for_table(cur: sqlite3.Cursor, table: str) -> str:
    """Construye el CREATE TABLE + indices de `legacy.<table>`."""
    cols = list(cur.execute(f"PRAGMA table_info({quote_ident(table)})"))
    if not cols:
        raise ValueError(f"tabla V2 sin columnas: {table}")
    pk_cols = [c[1] for c in cols if c[5] > 0]
    lines = [f"CREATE TABLE IF NOT EXISTS legacy.{quote_ident(table)} ("]
    defs = [f"    {quote_ident(c[1])} {map_type(c[2] or '')}" for c in cols]
    if len(pk_cols) == 1:
        defs.append(f"    PRIMARY KEY ({quote_ident(pk_cols[0])})")
    lines.append(",\n".join(defs))
    lines.append(");")
    names = {c[1] for c in cols}
    for idx_col in ("user_id", "job_id", "timestamp", "created_at"):
        if idx_col in names:
            idx = index_name(table, idx_col)
            lines.append(
                f"CREATE INDEX IF NOT EXISTS {quote_ident(idx)} "
                f"ON legacy.{quote_ident(table)} ({quote_ident(idx_col)});"
            )
    return "\n".join(lines)


def index_name(table: str, column: str) -> str:
    """Nombre de indice determinista dentro del limite de 63 caracteres.

    Los nombres largos de tablas V2 truncados por Postgres colisionaban
    (dos indices distintos terminaban con el mismo nombre y uno se omitia
    en silencio). Si `ix_legacy_<tabla>_<col>` excede 63, se acorta con un
    sufijo SHA-1 de 8 hex: unico, estable y auditable.
    """
    base = f"ix_legacy_{table}_{column}"
    if len(base) <= 63:
        return base
    digest = hashlib.sha1(f"{table}.{column}".encode("utf-8")).hexdigest()[:8]
    # 63 = len("ix_") + keep + len("_") + len(column) + len("_") + 8.
    keep = 63 - len(column) - 13
    assert keep > 0, f"columna demasiado larga para indice: {column}"
    return f"ix_{table[:keep]}_{column}_{digest}"


def generate(source: str) -> str:
    """Genera el DDL completo del esquema `legacy` desde el SQLite origen."""
    uri = f"file:{source}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            )
            if r[0] not in SKIP_TABLES
        ]
        if not tables:
            raise ValueError("origen SQLite sin tablas de aplicacion")
        parts = [
            "-- 02-legacy-tables.generated.sql: GENERADO, no editar a mano.",
            f"-- Fuente SQLite: {source}",
            f"-- Sobre de migracion, version de protocolo: {PROTOCOL_VERSION}.",
            "-- Generado con: python3 infra/legacy/generate_legacy_ddl.py",
            "-- Claves foraneas V2 omitidas a proposito (historial sin "
            "restricciones cruzadas); ver docstring del generador.",
            "",
        ]
        for table in tables:
            parts.append(legacy_ddl_for_table(cur, table))
            parts.append("")
        return "\n".join(parts)
    finally:
        con.close()


def main() -> int:
    """Punto de entrada CLI del generador de DDL legacy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SQLite V2 (se abre en modo ro)")
    parser.add_argument("--out", required=True, help="Destino del .sql generado")
    args = parser.parse_args()
    ddl = generate(args.source)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(ddl)
    tables = len(re.findall(r"^CREATE TABLE", ddl, re.M))
    print(f"OK: {tables} tablas legacy -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
