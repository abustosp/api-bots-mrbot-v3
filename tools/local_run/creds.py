#!/usr/bin/env python3
"""Explorador de credenciales fiscales de las bases V1/V2 para pruebas locales.

Lee las tablas ``consulta_*_logs`` de las bases SQLite del operador y devuelve
los registros mas recientes (por defecto solo ``status='success'``) con las
credenciales fiscales usadas en cada corrida.

Uso tipico:

    tools/local_run/creds.py --module sct
    tools/local_run/creds.py --module vep --cuit 20368208486
    tools/local_run/creds.py --table consulta_sifere_logs --limit 5 --json

Las bases se buscan en ``MRBOT_DB_DIRS`` (separadas por ``:``); por defecto:

    /home/abp/Desktop/api/DBs
    /home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/BD-api-v1

Nunca imprime claves salvo con ``--show-secrets`` (las pruebas locales las
necesitan; no use esa bandera en logs compartidos).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_DIRS = [
    "/home/abp/Desktop/api/DBs",
    "/home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/BD-api-v1",
]

#: modulo -> tabla de logs V1/V2
MODULE_TABLES: dict[str, str] = {
    "apoc": "consulta_logs",
    "aportes_en_linea": "consulta_aportes_en_linea_logs",
    "arba": "consulta_retenciones_percepciones_iibb_arba_logs",
    "carga_portal_iva": "consulta_portal_iva_carga_logs",
    "ccma": "consulta_ccma_logs",
    "ccma_compensacion": "consulta_ccma_compensacion_logs",
    "certificado_mipyme": "consulta_certificado_mipyme_logs",
    "compensaciones": "consulta_sct_compensaciones_logs",
    "consulta_cuit": "consulta_logs",
    "consulta_pagos_vep": "consulta_vep_logs",
    "controladores_fiscales": "consulta_controladores_fiscales_logs",
    "declaracion_en_linea": "consulta_declaracion_en_linea_logs",
    "facturometro": "consulta_facturometro_logs",
    "hacienda": "consulta_hacienda_logs",
    "libros_portal_iva": "consulta_libros_iva_logs",
    "liquidacion_granos": "consulta_liquidacion_granos_logs",
    "mis_comprobantes": "consulta_logs",
    "mis_facilidades": "consulta_mis_facilidades_logs",
    "mis_retenciones": "consulta_mis_retenciones_logs",
    "mis_retenciones_iva_simple": "consulta_mis_retenciones_logs",
    "moa": "consulta_moa_logs",
    "pago_devoluciones": "consulta_pago_devoluciones_logs",
    "portal_iva": "consulta_portal_iva_logs",
    "rcel": "consulta_rcel_logs",
    "retper_iibb_agip": "consulta_retenciones_percepciones_iibb_agip_logs",
    "retper_iibb_misiones": "consulta_retenciones_percepciones_iibb_misiones_logs",
    "sct": "consulta_sct_logs",
    "sifere": "consulta_sifere_logs",
    "siper": "consulta_siper_logs",
    "srt": "consulta_srt_logs",
    "vep": "consulta_vep_logs",
    "vep_archivo": "consulta_vep_logs",
    "vep_ccma": "consulta_vep_ccma_logs",
}

#: filtro extra por modulo (ademas del status)
MODULE_WHERE: dict[str, str] = {
    "consulta_pagos_vep": "medio_pago = 'consulta_pagos_vep'",
}

SECRET_HINTS = ("clave", "contrasena", "password", "secret")


def db_dirs() -> list[Path]:
    raw = os.getenv("MRBOT_DB_DIRS")
    dirs = [Path(p) for p in raw.split(":") if p.strip()] if raw else [Path(p) for p in DEFAULT_DB_DIRS]
    return [d for d in dirs if d.is_dir()]


def find_dbs() -> list[Path]:
    out: list[Path] = []
    for directory in db_dirs():
        out.extend(sorted(directory.glob("*.db")))
    return out


def tables_of(db: Path) -> list[str]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        return [
            r[0]
            for r in con.execute(
                "select name from sqlite_master where type='table' and name like 'consulta%'"
            )
        ]
    finally:
        con.close()


def rows_for(db: Path, table: str, *, status: str | None, limit: int, where: str | None):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if not cols:
            return [], []
        filtros: list[str] = []
        if status and "status" in cols:
            filtros.append(f"status = '{status}'")
        if where:
            filtros.append(f"({where})")
        clause = f" where {' and '.join(filtros)}" if filtros else ""
        orden_cols = []
        if "timestamp" in cols:
            orden_cols.append("timestamp desc")
        if "id" in cols:
            orden_cols.append("id desc")
        orden = f" order by {', '.join(orden_cols)}" if orden_cols else ""
        sql = f"select * from {table}{clause}{orden} limit {int(limit)}"
        return cols, [dict(r) for r in con.execute(sql)]
    except sqlite3.Error:
        return [], []
    finally:
        con.close()


def _timestamp_orden(value) -> float | None:
    """Normaliza timestamps habituales de SQLite para compararlos entre bases."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    texto = str(value).strip()
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        pass

    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        for formato in ("%Y%m%d_%H%M%S", "%Y%m%d-%H%M%S"):
            try:
                fecha = datetime.strptime(texto, formato)
                break
            except ValueError:
                continue
        else:
            return None

    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.timestamp()


def _id_orden(value) -> tuple[int, float | str]:
    """Usa el id como desempate y como fallback si no hay timestamp utilizable."""
    if value is None:
        return 0, ""
    try:
        return 1, float(value)
    except (TypeError, ValueError):
        return 0, str(value)


def _orden_fila(item: dict) -> tuple:
    row = item["row"]
    timestamp = _timestamp_orden(row.get("timestamp"))
    return (
        timestamp is not None,
        timestamp if timestamp is not None else 0,
        _id_orden(row.get("id")),
        item["db"],
    )


def mask(row: dict, show_secrets: bool) -> dict:
    if show_secrets:
        return row
    out = {}
    for key, value in row.items():
        if any(h in key.lower() for h in SECRET_HINTS) and value:
            out[key] = "<redacted>"
        else:
            out[key] = value
    return out


#: orden de preferencia de los campos de la fila que traen cada dato fiscal.
CAMPOS_CUIT_REPRESENTANTE = ("cuit_representante", "cuit_login", "cuit_inicio_sesion", "cuit")
CAMPOS_CLAVE = ("clave_representante", "clave", "contrasena")
CAMPOS_REPRESENTADO = ("cuit_representado", "representado_cuit", "cuit")

#: campos de negocio que se pueden copiar al payload del plugin.
CAMPOS_PAYLOAD = (
    "periodo", "periodo_desde", "periodo_hasta", "desde", "hasta",
    "jurisdicciones", "impuestos", "denominacion", "representado_nombre",
    "secciones", "formatos", "despachos", "cuits_consulta", "tipos",
)


def credenciales_de_fila(fila: dict) -> dict[str, str]:
    """Extrae ``{cuit_representante, clave, representado}`` de una fila de logs."""
    def primero(nombres: tuple[str, ...]) -> str:
        for nombre in nombres:
            valor = fila.get(nombre)
            if valor not in (None, "", "null"):
                return str(valor).strip()
        return ""

    return {
        "cuit_representante": primero(CAMPOS_CUIT_REPRESENTANTE),
        "clave": primero(CAMPOS_CLAVE),
        "representado": primero(CAMPOS_REPRESENTADO),
    }


def filas(
    *,
    modulo: str | None = None,
    tabla: str | None = None,
    status: str | None = "success",
    limit: int = 25,
    where: str | None = None,
    cuit: str | None = None,
    db_name: str | None = None,
) -> list[dict]:
    """Filas candidatas ordenadas de la base más reciente a la más vieja.

    Cada elemento incluye ``db``, ``table`` y ``row``. Pensado para que
    ``run_bot.py`` tome credenciales sin exponerlas en la línea de comandos.
    """
    table = tabla or MODULE_TABLES.get(modulo or "", "")
    if not table:
        raise ValueError(f"modulo desconocido: {modulo}")
    if where is None and modulo:
        where = MODULE_WHERE.get(modulo)
    salida: list[dict] = []
    for db in find_dbs():
        if db_name and db.name != db_name:
            continue
        if table not in tables_of(db):
            continue
        cols, rows = rows_for(db, table, status=status, limit=limit, where=where)
        if cuit:
            rows = [
                r for r in rows
                if str(r.get("cuit_representante")) == cuit
                or str(r.get("cuit_representado")) == cuit
                or str(r.get("cuit_login")) == cuit
            ]
        for row in rows:
            salida.append({"db": db.name, "table": table, "row": row})
    salida.sort(key=_orden_fila, reverse=True)
    return salida


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help="Nombre del modulo (ver MODULE_TABLES).")
    parser.add_argument("--table", help="Tabla de logs explicita (sobreescribe --module).")
    parser.add_argument("--cuit", help="Filtra por CUIT representante o representado.")
    parser.add_argument("--status", default="success", help="status a filtrar; 'any' para todos.")
    parser.add_argument("--limit", type=int, default=3, help="Filas por base/tabla.")
    parser.add_argument("--where", help="Condicion SQL extra (sin 'where').")
    parser.add_argument("--show-secrets", action="store_true", help="No enmascara claves.")
    parser.add_argument("--json", action="store_true", help="Una linea JSON por registro.")
    parser.add_argument("--list-dbs", action="store_true", help="Muestra bases encontradas.")
    args = parser.parse_args()

    dbs = find_dbs()
    if args.list_dbs:
        for db in dbs:
            print(db)
        return 0
    if not dbs:
        print("sin bases: configure MRBOT_DB_DIRS", file=sys.stderr)
        return 2

    if args.table:
        table = args.table
    elif args.module:
        table = MODULE_TABLES.get(args.module, "")
        if not table:
            print(f"modulo desconocido: {args.module}", file=sys.stderr)
            print("tablas conocidas: " + ", ".join(sorted(set(MODULE_TABLES.values()))), file=sys.stderr)
            return 2
    else:
        print("indique --module o --table", file=sys.stderr)
        return 2

    where = args.where
    if args.module and not where:
        where = MODULE_WHERE.get(args.module)
    status = None if args.status.lower() in {"any", "all", ""} else args.status

    encontrados = 0
    for db in dbs:
        if table not in tables_of(db):
            continue
        cols, rows = rows_for(db, table, status=status, limit=args.limit, where=where)
        if args.cuit:
            rows = [
                r for r in rows
                if str(r.get("cuit_representante")) == args.cuit
                or str(r.get("cuit_representado")) == args.cuit
                or str(r.get("cuit_login")) == args.cuit
            ]
        for row in rows:
            encontrados += 1
            salida = {
                "db": db.name,
                "table": table,
                "row": mask(row, args.show_secrets),
            }
            if args.json:
                print(json.dumps(salida, ensure_ascii=False, default=str))
            else:
                print(f"=== {db.name} / {table} / id={row.get('id')} / {row.get('timestamp')}")
                for key in cols:
                    if key in {"id", "timestamp"}:
                        continue
                    valor = mask(row, args.show_secrets).get(key)
                    if valor in (None, "", "null"):
                        continue
                    print(f"    {key}: {valor}")
    if not encontrados:
        print("sin registros con esos filtros", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
