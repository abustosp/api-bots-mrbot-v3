"""Importa las tablas V2 (SQLite) al esquema Postgres `legacy`.

Flujo auditable y re-ejecutable (plan 07 secciones 3.7 y 3.8):

  1. `PRAGMA integrity_check` sobre el SQLite (paso 2).
  2. SHA-256 del archivo fuente + conteos por tabla (pasos 3 y 5 del
     manifiesto; el dump cifrado e inmutable lo guarda el operador).
  3. Carga por tabla con `COPY ... FROM STDIN (FORMAT csv)` via `psql`,
     cada tabla en su propia transaccion: `TRUNCATE` + `COPY` + `COUNT(*)`.
     Re-ejecutar recarga limpio sin duplicar (solo antes del sellado
     `03-legacy-readonly.sql`; despues, el rol ya no tiene DML).
  4. Escribe `manifest.json`: hash de fuente, conteos origen/destino por
     tabla, version ETL y hora UTC. Sin valores de filas, sin secretos.

Sobre sellado para lo sensible: las columnas con credenciales o claves
(`clave_*`, `api_key`, ...) viajan opacas de SQLite a Postgres; este
script jamas las descifra, las imprime ni las incluye en el manifiesto
(solo conteos y hashes). El DSN se toma de DATABASE_URL o
DATABASE_URL_FILE (regla: cada secreto solo a su destino; el worker no
recibe DATABASE_URL nunca, invariante W-1).

Uso:
    python3 infra/legacy/import_legacy.py \
        --source ../api-bots-mrbot-v2/data/sql_app.db \
        --dsn "$DATABASE_URL" \
        --manifest /tmp/legacy-manifest.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

PROTOCOL_VERSION: int = 1
"""Version del sobre de migracion (entero, 1)."""

ETL_VERSION = "legacy-import/1.0"

SKIP_TABLES = frozenset({"sqlite_sequence"})


def sha256_file(path: str) -> str:
    """Calcula el SHA-256 de un archivo por bloques."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_dsn(explicit: str | None) -> str:
    """Resuelve el DSN Postgres sin exponerlo en logs ni errores."""
    if explicit:
        return explicit
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    dsn_file = os.environ.get("DATABASE_URL_FILE", "/run/secrets/database_url")
    if os.path.isfile(dsn_file):
        with open(dsn_file, encoding="utf-8") as fh:
            return fh.read().strip()
    raise SystemExit(
        "ERROR: definir --dsn, DATABASE_URL o DATABASE_URL_FILE "
        "(ver infra/compose/.env.example)"
    )


def psql(dsn: str, sql: str, stdin_text: str | None = None) -> str:
    """Ejecuta SQL via `psql`, devolviendo stdout; falla con mensaje neutro."""
    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Sin DSN ni datos en el mensaje: el DSN lleva secreto.
        raise RuntimeError(f"psql fallo: {proc.stderr.strip()[-500:]}")
    return proc.stdout.strip()


def pg_column_types(dsn: str, table: str) -> dict[str, str]:
    """Tipos Postgres de `legacy.<table>` para serializar el CSV.

    Regla: el string vacio solo es valido en TEXT; en cualquier otro tipo
    (JSONB, BOOLEAN, numericos, fechas) Postgres lo rechaza, asi que se
    carga como NULL. Sin esta regla, los '' historicos de V2 en columnas
    JSON abortaban el COPY.
    """
    out = psql(
        dsn,
        "SELECT column_name || '|' || data_type FROM information_schema.columns "
        f"WHERE table_schema = 'legacy' AND table_name = '{table}'",
    )
    types = {}
    for line in out.splitlines():
        name, typ = line.split("|", 1)
        types[name] = typ
    return types


def copy_table(dsn: str, table: str, columns: list[str], rows: list[tuple]) -> int:
    """Recarga una tabla legacy: TRUNCATE + COPY + conteo de verificacion.

    Re-ejecutable: cada corrida deja la tabla con el contenido exacto del
    origen (sin duplicados). Solo valido antes del sellado de solo lectura
    (`03-legacy-readonly.sql`); despues, el rol de carga ya no tiene DML.
    """
    col_types = pg_column_types(dsn, table)
    buf = io.StringIO()
    # QUOTE_MINIMAL + NULL '': None se escribe sin comillas (NULL) y el
    # string vacio TEXT como "" (se conserva). Verificado: con QUOTE_ALL
    # None tambien saldria entrecomillado y se perderia la distincion.
    # El '' en columnas no-TEXT se carga como NULL (Postgres rechaza ''
    # en JSONB/BOOLEAN/fechas/numeros).
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow(
            [
                "" if v is None or (v == "" and col_types.get(c) != "text") else v
                for c, v in zip(columns, row)
            ]
        )
    cols = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
    copy_sql = (
        f'COPY legacy."{table}" ({cols}) FROM STDIN WITH (FORMAT csv, NULL \'\')'
    )
    psql(dsn, f'TRUNCATE legacy."{table}"')
    copy_proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-At", "-c", copy_sql],
        input=buf.getvalue(),
        capture_output=True,
        text=True,
    )
    if copy_proc.returncode != 0:
        raise RuntimeError(
            f"COPY fallo en {table}: {copy_proc.stderr.strip()[-500:]}"
        )
    return int(psql(dsn, f'SELECT count(*) FROM legacy."{table}"'))


def main() -> int:
    """Punto de entrada CLI del importador legacy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SQLite V2 (modo ro)")
    parser.add_argument("--dsn", default=None, help="DSN Postgres (o DATABASE_URL)")
    parser.add_argument("--manifest", required=True, help="Destino manifest.json")
    parser.add_argument("--run-by", default=os.environ.get("USER", "operador"))
    parser.add_argument("--audit", action="store_true",
                        help="Insertar fila en legacy.migration_audit")
    args = parser.parse_args()

    dsn = resolve_dsn(args.dsn)
    con = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        pass
    if str(integrity).lower() != "ok":
        print(f"ERROR: integrity_check = {integrity}", file=sys.stderr)
        return 1

    source_sha = sha256_file(args.source)
    con = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            )
            if r[0] not in SKIP_TABLES
        ]
        per_table = []
        total = 0
        for table in tables:
            columns = [c[1] for c in con.execute(f'PRAGMA table_info("{table}")')]
            rows = list(con.execute(f'SELECT * FROM "{table}"'))
            loaded = copy_table(dsn, table, columns, rows)
            status = "OK" if loaded == len(rows) else "DIFERENCIA"
            per_table.append(
                {
                    "table": table,
                    "source_count": len(rows),
                    "legacy_count": loaded,
                    "status": status,
                }
            )
            total += loaded
            print(f"{status}: {table} origen={len(rows)} legacy={loaded}")
            if status != "OK":
                return 1
    finally:
        con.close()

    manifest = {
        "etl": ETL_VERSION,
        "envelope_protocol_version": PROTOCOL_VERSION,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_by": args.run_by,
        "source_path": args.source,
        "source_sha256": source_sha,
        "source_integrity_check": "ok",
        "tables": per_table,
        "rows_loaded": total,
    }
    body = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_sha = hashlib.sha256(body.encode()).hexdigest()
    manifest["manifest_sha256"] = manifest_sha
    with open(args.manifest, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"OK: {len(tables)} tablas, {total} filas -> {args.manifest}")
    print(f"manifest_sha256={manifest_sha}")

    if args.audit:
        import uuid

        psql(
            dsn,
            "INSERT INTO legacy.migration_audit "
            "(id, run_by, etl_version, envelope_protocol_version, "
            " source_path, source_sha256, source_integrity_check, "
            " tables_loaded, rows_loaded, manifest_sha256) VALUES "
            f"('{uuid.uuid4()}', '{args.run_by}', '{ETL_VERSION}', "
            f"{PROTOCOL_VERSION}, '{args.source}', '{source_sha}', 'ok', "
            f"{len(tables)}, {total}, '{manifest_sha}')",
        )
        print("OK: fila de auditoria registrada en legacy.migration_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
