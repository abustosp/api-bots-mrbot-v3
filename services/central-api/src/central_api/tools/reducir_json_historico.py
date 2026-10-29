#!/usr/bin/env python3
"""Quita de ``job_results.payload`` el dato scrapeado de CCMA/RCEL ya archivado como CSV en S4.

Corre DENTRO del contenedor de la central. Los CSV viven en
``{bucket}/jobs/{job_id}/{archivo}`` (los generó ``migrar_datos_a_csv`` y los movió
``migrar_layout``). El response se vuelve a armar al consultarlo
(``datos_archivo.reconstruir``).

Seguridad, por job y en este orden:

1. ``separar`` parte el payload en (reducido, archivos),
2. se LEEN de S4 los CSV registrados del job (no los recién calculados),
3. solo si ``reconstruir(reducido, csv_de_S4) == payload`` EXACTO se continúa,
4. el payload original se guarda en un respaldo (JSONL local y en S4 general,
   ``_migraciones/json_historico_{bot}.jsonl``) ANTES de tocar la base,
5. ``UPDATE job_results SET payload = reducido WHERE job_id AND payload = original``
   (un trigger replica el cambio a ``bot_jobs_*``).

Por defecto simula. Idempotente (un payload ya reducido se saltea). Un job sin dato
que archivar (CCMA V1, RCEL sin facturas) no se toca. ``--restaurar`` devuelve los
payloads originales desde el respaldo.

    python - --bot ccma|rcel [--aplicar] [--limite N] [--restaurar]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from central_api import datos_archivo
from central_api.db import nueva_sesion
from central_api.settings import get_settings
from central_api.storage import get_object_bytes, put_object_bytes
from mrbot_contracts import datos_csv as d

SEPARAR = {"ccma": d.separar_ccma, "rcel": d.separar_rcel}
_SQL = """
SELECT j.id::text AS job_id, jr.payload AS payload,
       (SELECT json_agg(json_build_object('object_key', a.object_key))
          FROM job_artifacts a WHERE a.job_id = j.id) AS arts
FROM jobs j JOIN job_results jr ON jr.job_id = j.id
WHERE j.bot = :bot AND j.status = 'COMPLETO'
ORDER BY j.created_at
"""


def _kw(s):
    return dict(endpoint=s.object_storage_endpoint, region=s.object_storage_region,
                access_key=s.object_storage_access_key, secret_key=s.object_storage_secret_key)


def evaluar(s, bot: str, fila: dict) -> tuple[str, dict | None]:
    """(estado, trabajo). ``trabajo`` trae lo necesario para aplicar si el estado es ``listo``."""
    payload = fila["payload"] if isinstance(fila["payload"], dict) else {}
    if payload.get(d.CLAVE_DATOS_EN_ARCHIVO):
        return "ya_reducido", None
    separar = SEPARAR[bot]
    reducido, archivos = separar(payload)
    if not archivos:
        return "sin_dato_que_archivar", None
    arts = fila["arts"] or []
    _, ids = datos_archivo.BOTS[bot]
    textos: dict[str, str] = {}
    try:
        for clave in archivos:
            key = datos_archivo._clave_de(arts, ids[clave])
            if key is None:
                return "SIN_CSV_EN_S4", None
            textos[clave] = get_object_bytes(
                **_kw(s), bucket=s.bucket_para_bot(bot), object_key=key, max_bytes=60 * 1024 * 1024
            ).decode("utf-8")
        reconstruir = datos_archivo.BOTS[bot][0]
        if reconstruir(reducido, textos) != payload:
            return "NO_COINCIDE", None
    except (RuntimeError, ValueError, UnicodeDecodeError, KeyError):
        return "ERROR_LECTURA", None
    return "listo", {"job_id": fila["job_id"], "original": payload, "reducido": reducido}


async def reducir(bot: str, *, aplicar: bool, limite: int | None, hilos: int = 4) -> dict:
    s = get_settings()
    async with nueva_sesion() as ses:
        filas = [dict(r) for r in (await ses.execute(text(_SQL), {"bot": bot})).mappings().all()]
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(hilos) as pool:
        res = await asyncio.gather(*[loop.run_in_executor(pool, evaluar, s, bot, f) for f in filas])
    cuenta: Counter = Counter(estado for estado, _ in res)
    trabajos = [t for estado, t in res if estado == "listo"]
    if limite is not None:
        trabajos = trabajos[:limite]
    cuenta["se_reduciria" if not aplicar else "a_reducir"] = len(trabajos)
    if aplicar and trabajos:
        ruta = f"/tmp/json_historico_{bot}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"
        cuerpo = "\n".join(json.dumps({"job_id": t["job_id"], "payload": t["original"]}, ensure_ascii=False)
                           for t in trabajos).encode("utf-8")
        with open(ruta, "wb") as fh:
            fh.write(cuerpo)
        clave = f"_migraciones/json_historico_{bot}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"
        put_object_bytes(**_kw(s), bucket=s.object_storage_bucket, object_key=clave, content=cuerpo,
                         content_type="application/x-ndjson")
        # el respaldo se relee de S4 antes de modificar nada
        if get_object_bytes(**_kw(s), bucket=s.object_storage_bucket, object_key=clave,
                            max_bytes=200 * 1024 * 1024) != cuerpo:
            raise RuntimeError("el respaldo en S4 no coincide: no se modifica nada")
        cuenta["respaldo_s4"] = clave
        cuenta["respaldo_local"] = ruta
        hechos = 0
        async with nueva_sesion() as ses:
            for i, t in enumerate(trabajos, 1):
                r = await ses.execute(
                    text("UPDATE job_results SET payload = CAST(:nuevo AS jsonb) "
                         "WHERE job_id = CAST(:id AS uuid) AND payload = CAST(:viejo AS jsonb)"),
                    {"nuevo": json.dumps(t["reducido"]), "viejo": json.dumps(t["original"]), "id": t["job_id"]},
                )
                hechos += r.rowcount or 0
                if i % 100 == 0:
                    await ses.commit()
            await ses.commit()
        cuenta["reducidos"] = hechos
        cuenta["distintos_al_actualizar"] = len(trabajos) - hechos
    return {"bot": bot, "modo": "APLICADO" if aplicar else "SIMULACION", **cuenta}


async def restaurar(bot: str, ruta: str) -> dict:
    n = 0
    async with nueva_sesion() as ses:
        for linea in open(ruta, encoding="utf-8"):
            if not linea.strip():
                continue
            o = json.loads(linea)
            r = await ses.execute(
                text("UPDATE job_results SET payload = CAST(:p AS jsonb) WHERE job_id = CAST(:id AS uuid)"),
                {"p": json.dumps(o["payload"]), "id": o["job_id"]},
            )
            n += r.rowcount or 0
        await ses.commit()
    return {"bot": bot, "modo": "RESTAURADO", "filas": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot", choices=sorted(SEPARAR), required=True)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--hilos", type=int, default=4)
    ap.add_argument("--restaurar", metavar="JSONL")
    a = ap.parse_args()
    if a.restaurar:
        print(json.dumps(asyncio.run(restaurar(a.bot, a.restaurar)), indent=1))
        return 0
    r = asyncio.run(reducir(a.bot, aplicar=a.aplicar, limite=a.limite, hilos=a.hilos))
    print(json.dumps(r, indent=1, ensure_ascii=False))
    return 1 if r.get("NO_COINCIDE") or r.get("ERROR_LECTURA") else 0


if __name__ == "__main__":
    raise SystemExit(main())
