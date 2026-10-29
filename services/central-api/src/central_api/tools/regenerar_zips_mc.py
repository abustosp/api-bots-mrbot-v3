#!/usr/bin/env python3
"""Regenera en S4 el ZIP de Mis Comprobantes de los jobs que solo tienen su CSV.

Corre DENTRO del contenedor de la central. Para cada job COMPLETO de
``mis_comprobantes`` y cada tipo (emitidos/recibidos) que tiene el CSV completo
en S4 pero no su ZIP:

1. lee el CSV del bucket ``miscomprobantes``,
2. lo guarda dentro de un ZIP de un miembro (mismo formato que genera el worker,
   ``mrbot_contracts.planillas.empaquetar_zip``),
3. sube el ZIP con el patron de clave del worker
   (``jobs/<job>/<nombre>.zip``) y registra un ``job_artifacts``.

Seguridad: por defecto solo simula. Verifica que el ZIP leído de vuelta contenga
exactamente el CSV de origen (mismos bytes). Es idempotente (un job con su ZIP se
saltea) y NO modifica ni borra el CSV ni el JSON del resultado.

Un job sin CSV completo no se regenera: se lista aparte (hace falta volver a
consultar con credenciales).

    python -m central_api.tools.regenerar_zips_mc [--limite N] [--aplicar]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import uuid
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from central_api.db import nueva_sesion
from central_api.settings import get_settings
from central_api.storage import get_object_bytes, put_object_bytes

BOT = "mis_comprobantes"
MAX_BYTES = 60 * 1024 * 1024
_SQL = """
SELECT j.id::text AS id, j.attempts, j.request_payload AS rq, jr.payload AS pl,
       (SELECT json_agg(json_build_object('k', a.object_key, 'n', a.filename, 'e', a.expires_at))
          FROM job_artifacts a WHERE a.job_id = j.id) AS arts
FROM jobs j JOIN job_results jr ON jr.job_id = j.id
WHERE j.bot = 'mis_comprobantes' AND j.status = 'COMPLETO'
  AND j.app_version IS DISTINCT FROM 'legacy-v1v2'
ORDER BY j.created_at
"""


def _kw(s):
    return dict(
        endpoint=s.object_storage_endpoint, region=s.object_storage_region,
        access_key=s.object_storage_access_key, secret_key=s.object_storage_secret_key,
    )


def _zip_de(csv: bytes, nombre_csv: str) -> bytes:
    """ZIP de un solo miembro con el CSV (mismo criterio que ``empaquetar_zip``)."""
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(nombre_csv, csv)
    return salida.getvalue()


def _nombre_zip(archivo_csv: str | None, tipo: str, jid: str) -> str:
    base = (archivo_csv or "").rsplit("/", 1)[-1]
    if base.lower().endswith(".csv"):
        return base[:-4] + ".zip"
    return f"{tipo}-{jid[:8]}.zip"


def _tiene_zip(arts, tipo: str) -> bool:
    """ZIP del tipo en layout nuevo (``jobs/<job>/<n> - MCE - ... .zip``) o histórico."""
    sigla = " - MCE - " if tipo == "emitidos" else " - MCR - "
    return any(
        f"/{tipo}_zip/" in a["k"]
        or (a["k"].lower().endswith(".zip") and sigla in a["k"].rsplit("/", 1)[-1])
        for a in arts
    )


def _es_csv(clave: str, tipo: str) -> bool:
    sigla = " - MCE - " if tipo == "emitidos" else " - MCR - "
    return f"/{tipo}_csv/" in clave or (
        clave.lower().endswith(".csv") and sigla in clave.rsplit("/", 1)[-1]
    )


def plan(filas) -> tuple[list[dict], list[dict]]:
    """(trabajos regenerables, jobs que no se pueden regenerar y por que)."""
    trabajos, faltan = [], []
    for f in filas:
        pl = f["pl"] if isinstance(f["pl"], dict) else {}
        arts = f["arts"] or []
        for tipo in ("emitidos", "recibidos"):
            r = pl.get(tipo)
            if not isinstance(r, dict):
                continue
            if _tiene_zip(arts, tipo):
                continue
            csv = next((a for a in arts if _es_csv(a["k"], tipo)), None)
            if csv is not None:
                trabajos.append({"id": f["id"], "tipo": tipo, "intento": max(1, int(f["attempts"] or 1)),
                                 "csv_key": csv["k"], "csv_nombre": csv["n"], "expira": csv.get("e"), "filas": r.get("filas")})
            else:
                filas_n = r.get("filas")
                motivo = "sin_comprobantes" if not filas_n else (
                    "solo_muestra" if r.get("muestra") else "sin_datos")
                if filas_n:
                    faltan.append({"id": f["id"], "tipo": tipo, "motivo": motivo, "filas": filas_n,
                                   "cuit": (f["rq"] or {}).get("representado_cuit"),
                                   "desde": (f["rq"] or {}).get("fecha_desde"),
                                   "hasta": (f["rq"] or {}).get("fecha_hasta")})
    return trabajos, faltan


def _hacer(s, aplicar: bool, t: dict) -> tuple[str, dict]:
    try:
        csv = get_object_bytes(**_kw(s), bucket=s.bucket_para_bot(BOT), object_key=t["csv_key"], max_bytes=MAX_BYTES)
    except RuntimeError:
        return "CSV_NO_LEGIBLE", t
    nombre_zip = _nombre_zip(t["csv_nombre"], t["tipo"], t["id"])
    miembro = t["csv_nombre"].rsplit("/", 1)[-1]
    cuerpo = _zip_de(csv, miembro)
    # el ZIP debe devolver exactamente el CSV de origen
    with zipfile.ZipFile(io.BytesIO(cuerpo)) as z:
        if z.read(miembro) != csv:
            return "ZIP_NO_COINCIDE", t
    t = {**t, "zip_nombre": nombre_zip, "zip_bytes": len(cuerpo), "zip_sha": hashlib.sha256(cuerpo).hexdigest()}
    if not aplicar:
        return "se_generaria", t
    clave = f"jobs/{t['id']}/{t['zip_nombre']}"
    put_object_bytes(**_kw(s), bucket=s.bucket_para_bot(BOT), object_key=clave, content=cuerpo,
                     content_type="application/zip")
    leido = get_object_bytes(**_kw(s), bucket=s.bucket_para_bot(BOT), object_key=clave, max_bytes=MAX_BYTES)
    if hashlib.sha256(leido).hexdigest() != t["zip_sha"]:
        return "SUBIDA_NO_COINCIDE", t
    return "subido", {**t, "clave": clave}


async def correr(limite: int | None, aplicar: bool, hilos: int = 12) -> dict:
    s = get_settings()
    async with nueva_sesion() as ses:
        filas = [dict(r) for r in (await ses.execute(text(_SQL))).mappings().all()]
    trabajos, faltan = plan(filas)
    if limite is not None:
        trabajos = trabajos[:limite]
    cuenta: Counter = Counter()
    subidos: list[dict] = []
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(hilos) as pool:
        for est, t in await asyncio.gather(*[loop.run_in_executor(pool, _hacer, s, aplicar, x) for x in trabajos]):
            cuenta[est] += 1
            if est == "subido":
                subidos.append(t)
    if aplicar and subidos:
        async with nueva_sesion() as ses:
            for t in subidos:
                await ses.execute(
                    text("INSERT INTO job_artifacts (id, job_id, kind, object_key, filename, content_type, size_bytes, sha256, expires_at)"
                         " VALUES (CAST(:id AS uuid), CAST(:job AS uuid), 'ARCHIVO', :k, :n, 'application/zip', :z, :h, CAST(:e AS timestamptz))"),
                    {"id": str(uuid.uuid4()), "job": t["id"], "k": t["clave"], "n": t["zip_nombre"],
                     "z": t["zip_bytes"], "h": t["zip_sha"], "e": t.get("expira")},
                )
            await ses.commit()
    return {"modo": "APLICADO" if aplicar else "SIMULACION", "trabajos": len(trabajos), **cuenta,
            "no_regenerables": len(faltan), "_faltan": faltan}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--hilos", type=int, default=12)
    a = ap.parse_args()
    r = asyncio.run(correr(a.limite, a.aplicar, a.hilos))
    faltan = r.pop("_faltan")
    print(json.dumps(r, indent=1, ensure_ascii=False))
    ruta = "/tmp/mc_zip_no_regenerables.json"
    json.dump(faltan, open(ruta, "w"), ensure_ascii=False)
    print("no regenerables guardados en", ruta)
    return 1 if any(k in r for k in ("CSV_NO_LEGIBLE", "ZIP_NO_COINCIDE", "SUBIDA_NO_COINCIDE")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
