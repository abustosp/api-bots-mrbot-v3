#!/usr/bin/env python3
"""Genera en S4 los CSV de los jobs históricos de RCEL y CCMA.

Corre DENTRO del contenedor de la central (usa su configuración de storage y de
base) y es seguro por diseño:

* Por defecto es una simulación (``--aplicar`` para subir de verdad).
* SOLO escribe objetos nuevos en S4 y una fila nueva en ``job_artifacts`` por
  archivo. NO modifica ni borra ``job_results.payload``: el JSON original sigue
  siendo la fuente hasta que alguien decida quitarlo.
* Es idempotente: un job que ya tiene su CSV registrado se saltea.
* Antes de subir verifica que el CSV reconstruya EXACTAMENTE el payload original;
  si no coincide, no sube nada de ese job y lo informa.

Uso (dentro del contenedor)::

    python -m central_api.tools.migrar_datos_a_csv --bot rcel --limite 5
    python -m central_api.tools.migrar_datos_a_csv --bot ccma --aplicar
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import text

from central_api.db import nueva_sesion
from central_api.settings import get_settings
from central_api.storage import put_object_bytes
from mrbot_contracts import datos_csv as d

#: bot -> (separar, reconstruir, {clave de archivo: (artifact_id, función de nombre)})
BOTS = {
    "ccma": (
        d.separar_ccma,
        d.reconstruir_ccma,
        {
            "saldos": ("ccma_saldos.csv", d.nombre_ccma_saldos),
            "movimientos": ("ccma_movimientos.csv", d.nombre_ccma_movimientos),
        },
    ),
    "rcel": (
        d.separar_rcel,
        d.reconstruir_rcel,
        {"facturas": ("rcel_facturas.csv", d.nombre_rcel_facturas)},
    ),
}

_SQL_JOBS = """
SELECT j.id::text AS job_id, j.attempts, j.request_payload, jr.payload,
       (SELECT array_agg(a.object_key) FROM job_artifacts a WHERE a.job_id = j.id) AS claves
FROM jobs j JOIN job_results jr ON jr.job_id = j.id
WHERE j.bot = :bot AND j.status = 'COMPLETO'
ORDER BY j.created_at
"""


def _cuit(request: dict[str, Any], payload: dict[str, Any]) -> str:
    return str(
        request.get("representado_cuit")
        or request.get("cuit_representado")
        or payload.get("representado_cuit")
        or payload.get("cuit")
        or ""
    )


def _ya_migrado(claves: list[str] | None, artifact_id: str) -> bool:
    return any(f"/{artifact_id}/" in c for c in (claves or []))


async def migrar(bot: str, *, aplicar: bool, limite: int | None) -> Counter:
    separar, reconstruir, archivos_def = BOTS[bot]
    ajustes = get_settings()
    cuenta: Counter = Counter()
    async with nueva_sesion() as sesion:
        filas = (await sesion.execute(text(_SQL_JOBS), {"bot": bot})).mappings().all()
        for fila in filas:
            payload = fila["payload"] if isinstance(fila["payload"], dict) else {}
            reducido, archivos = separar(payload)
            if not archivos:
                cuenta["sin_datos_para_archivar"] += 1
                continue
            if reconstruir(reducido, archivos) != payload:
                cuenta["NO_COINCIDE_no_se_sube"] += 1
                continue
            pendientes = {
                k: v for k, v in archivos.items()
                if not _ya_migrado(fila["claves"], archivos_def[k][0])
            }
            if not pendientes:
                cuenta["ya_migrado"] += 1
                continue
            if limite is not None and cuenta["migrados"] >= limite:
                break
            cuit = _cuit(fila["request_payload"] or {}, payload)
            intento = max(1, int(fila["attempts"] or 1))
            for clave, texto in pendientes.items():
                artifact_id, armar = archivos_def[clave]
                nombre = armar(cuit)
                cuerpo = texto.encode("utf-8")
                object_key = f"jobs/{fila['job_id']}/{nombre}"
                cuenta[f"archivos_{clave}"] += 1
                cuenta["bytes"] += len(cuerpo)
                if not aplicar:
                    continue
                put_object_bytes(
                    endpoint=ajustes.object_storage_endpoint,
                    region=ajustes.object_storage_region,
                    bucket=ajustes.bucket_para_bot(bot),
                    access_key=ajustes.object_storage_access_key,
                    secret_key=ajustes.object_storage_secret_key,
                    object_key=object_key,
                    content=cuerpo,
                    content_type="text/csv",
                )
                await sesion.execute(
                    text(
                        "INSERT INTO job_artifacts (id, job_id, kind, object_key, filename,"
                        " content_type, size_bytes, sha256) VALUES (:id, CAST(:job AS uuid),"
                        " 'ARCHIVO', :key, :name, 'text/csv', :size, :sha)"
                    ),
                    {
                        "id": str(uuid.uuid4()), "job": fila["job_id"], "key": object_key,
                        "name": nombre, "size": len(cuerpo),
                        "sha": hashlib.sha256(cuerpo).hexdigest(),
                    },
                )
            cuenta["migrados"] += 1
            if aplicar and cuenta["migrados"] % 50 == 0:
                await sesion.commit()
        if aplicar:
            await sesion.commit()
    return cuenta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot", choices=sorted(BOTS), required=True)
    ap.add_argument("--aplicar", action="store_true", help="sube a S4 (por defecto solo simula)")
    ap.add_argument("--limite", type=int, default=None, help="máximo de jobs a migrar")
    args = ap.parse_args()
    cuenta = asyncio.run(migrar(args.bot, aplicar=args.aplicar, limite=args.limite))
    print(json.dumps({"bot": args.bot, "modo": "APLICADO" if args.aplicar else "SIMULACION", **cuenta}, indent=1))


if __name__ == "__main__":
    main()
