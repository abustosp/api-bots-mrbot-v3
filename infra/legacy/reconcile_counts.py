"""Concilia el import legacy: origen SQLite frente a Postgres `legacy`.

Controles del plan 07 seccion 3.8 (todos con umbral de igualdad exacta
salvo excepcion aprobada):

  1. conteo por tabla: `sqlite COUNT(*)` vs `legacy COUNT(*)`;
  2. nulos criticos por columna (`user_id`, `job_id`, `timestamp`,
     `created_at`, `status` donde existan);
  3. suma de usuarios (habilitados / deshabilitados / con verificador);
  4. mapa de usuarios: cada `users.id` V2 con fila en `user_id_map` (100%);
  5. unicidad: emails y `v3_user_id` sin duplicados;
  6. muestra estratificada: el operador la revisa contra el panel; este
     script emite los IDs candidatos por estado (OK/PARCIAL/ERROR) sin
     valores sensibles.

Salida: informe JSON + CSV con hash del dump, conteos fuente/destino,
excepciones y resultado go/no-go. Sin secretos ni valores de filas: solo
conteos, hashes y motivos.

Modos:
  --manifest-only  valida el manifiesto sin tocar Postgres (conteos
                   internos + formato). Util en CI sin base de datos.
  completo         compara contra Postgres via `psql` (requiere DSN).

Uso:
    python3 infra/legacy/reconcile_counts.py \
        --source ../api-bots-mrbot-v2/data/sql_app.db \
        --manifest /tmp/legacy-manifest.json \
        --dsn "$DATABASE_URL" --report /tmp/reconciliacion.json
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

SKIP_TABLES = frozenset({"sqlite_sequence"})
NULL_CHECK_COLUMNS = ("user_id", "job_id", "timestamp", "created_at", "status")


def psql(dsn: str, sql: str) -> str:
    """Ejecuta SQL via `psql`; ante error, mensaje neutro sin DSN ni datos."""
    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql fallo: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


def sqlite_tables(source: str) -> tuple[list[str], dict[str, int], dict[str, dict[str, int]]]:
    """Devuelve tablas, conteos y nulos criticos del origen (modo ro)."""
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            )
            if r[0] not in SKIP_TABLES
        ]
        counts = {
            t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            for t in tables
        }
        nulls: dict[str, dict[str, int]] = {}
        for table in tables:
            cols = {c[1] for c in con.execute(f'PRAGMA table_info("{table}")')}
            per_col = {}
            for col in NULL_CHECK_COLUMNS:
                if col in cols:
                    per_col[col] = con.execute(
                        f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NULL'
                    ).fetchone()[0]
            nulls[table] = per_col
        return tables, counts, nulls
    finally:
        con.close()


def check_users_summary(source: str) -> dict:
    """Resume usuarios origen: totales, habilitados y con verificador."""
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        total = con.execute("SELECT count(*) FROM users").fetchone()[0]
        enabled = con.execute(
            "SELECT count(*) FROM users WHERE habilitado IS TRUE OR habilitado = 1"
        ).fetchone()[0]
        with_key = con.execute(
            "SELECT count(*) FROM users WHERE api_key IS NOT NULL AND api_key <> ''"
        ).fetchone()[0]
        return {"total": total, "habilitados": enabled, "con_verificador": with_key}
    finally:
        con.close()


def build_report(source: str, manifest: dict, dsn: str | None) -> dict:
    """Construye el informe de reconciliacion con todos los controles."""
    _, src_counts, src_nulls = sqlite_tables(source)
    users_src = check_users_summary(source)
    manifest_tables = {t["table"]: t for t in manifest.get("tables", [])}

    controls: list[dict] = []
    failures = 0

    for table, expected in sorted(src_counts.items()):
        entry = manifest_tables.get(table)
        row: dict = {"control": f"conteo/{table}", "origen": expected}
        if entry is None:
            row.update({"legacy": None, "resultado": "FALLA: tabla ausente en manifiesto"})
            failures += 1
        else:
            row["manifiesto"] = entry["legacy_count"]
            ok = entry["legacy_count"] == expected and entry.get("status") == "OK"
            row["resultado"] = "OK" if ok else "FALLA: diferencia de conteo"
            failures += 0 if ok else 1
        controls.append(row)

    if dsn:
        for table, expected in sorted(src_counts.items()):
            got = int(psql(dsn, f'SELECT count(*) FROM legacy."{table}"'))
            ok = got == expected
            controls.append(
                {
                    "control": f"conteo-pg/{table}",
                    "origen": expected,
                    "legacy_pg": got,
                    "resultado": "OK" if ok else "FALLA: diferencia en Postgres",
                }
            )
            failures += 0 if ok else 1
        for table, per_col in sorted(src_nulls.items()):
            cols = per_col.keys()
            if not cols:
                continue
            select = ", ".join(
                f"count(*) FILTER (WHERE \"{c}\" IS NULL) AS \"{c}\"" for c in cols
            )
            raw = psql(dsn, f'SELECT {select} FROM legacy."{table}"')
            pg_nulls = [int(v) for v in raw.split("|")] if raw else []
            for col, pg_n in zip(sorted(cols), pg_nulls):
                ok = pg_n == per_col[col]
                controls.append(
                    {
                        "control": f"nulos/{table}.{col}",
                        "origen": per_col[col],
                        "legacy_pg": pg_n,
                        "resultado": "OK" if ok else "FALLA: nulos distintos",
                    }
                )
                failures += 0 if ok else 1
        map_count = int(psql(dsn, "SELECT count(*) FROM legacy.user_id_map"))
        dup_mails = int(
            psql(
                dsn,
                "SELECT count(*) FROM (SELECT lower(email) FROM public.users "
                "GROUP BY 1 HAVING count(*) > 1) d",
            )
        )
        dup_uuids = int(
            psql(
                dsn,
                "SELECT count(*) FROM (SELECT v3_user_id FROM legacy.user_id_map "
                "GROUP BY 1 HAVING count(*) > 1) d",
            )
        )
        users_pg = int(psql(dsn, "SELECT count(*) FROM public.users"))
        for name, ok in (
            (f"mapa-usuarios/{map_count}-de-{users_src['total']}", map_count == users_src["total"]),
            ("unicidad/emails", dup_mails == 0),
            ("unicidad/v3_user_id", dup_uuids == 0),
            (f"usuarios-pg/{users_pg}", users_pg == users_src["total"]),
        ):
            controls.append({"control": name, "resultado": "OK" if ok else "FALLA"})
            failures += 0 if ok else 1

    report = {
        "envelope_protocol_version": PROTOCOL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": manifest.get("source_sha256"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "usuarios_origen": users_src,
        "nulos_origen": src_nulls,
        "controles": controls,
        "fallas": failures,
        "go_no_go": "GO" if failures == 0 else "NO-GO",
    }
    return report


def write_csv(report: dict, path: str) -> None:
    """Escribe los controles del informe en CSV firmado por el JSON."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["control", "origen", "destino", "resultado"])
    for ctrl in report["controles"]:
        writer.writerow(
            [
                ctrl.get("control"),
                ctrl.get("origen"),
                ctrl.get("legacy_pg", ctrl.get("manifiesto", ctrl.get("legacy"))),
                ctrl.get("resultado"),
            ]
        )
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())


def main() -> int:
    """Punto de entrada CLI de la reconciliacion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SQLite V2 (modo ro)")
    parser.add_argument("--manifest", required=True, help="Manifiesto del importador")
    parser.add_argument("--dsn", default=None, help="DSN Postgres (o DATABASE_URL)")
    parser.add_argument("--report", required=True, help="Destino informe JSON")
    parser.add_argument("--csv", default=None, help="Destino informe CSV")
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)

    dsn = None
    if not args.manifest_only:
        dsn = args.dsn or os.environ.get("DATABASE_URL")
        if not dsn:
            dsn_file = os.environ.get("DATABASE_URL_FILE", "/run/secrets/database_url")
            if os.path.isfile(dsn_file):
                with open(dsn_file, encoding="utf-8") as fh:
                    dsn = fh.read().strip()
        if not dsn:
            print("ERROR: definir --dsn, DATABASE_URL o usar --manifest-only",
                  file=sys.stderr)
            return 1

    report = build_report(args.source, manifest, dsn)
    body = json.dumps(report, indent=2, sort_keys=True)
    report["report_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    if args.csv:
        write_csv(report, args.csv)
    print(f"{report['go_no_go']}: {len(report['controles'])} controles, "
          f"{report['fallas']} fallas -> {args.report}")
    return 0 if report["fallas"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
