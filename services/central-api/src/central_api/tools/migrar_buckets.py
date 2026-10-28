#!/usr/bin/env python3
"""Mueve los artefactos de S4 al bucket de su módulo, con verificación antes de borrar.

Corre DENTRO del contenedor de la central (usa su storage y su base). Cada
objeto referenciado en ``job_artifacts`` pasa por etapas separadas:

1. ``copiar``   copia al bucket del módulo y verifica el sha256 del destino.
2. ``verificar`` re-lee el destino y compara su sha256 con el registrado (no
                 confía en la copia anterior).
3. ``borrar``   elimina el original SOLO si el destino está verificado ahora
                 mismo. Nunca borra por haber copiado: borra por haber verificado.

Por defecto simula. ``--aplicar`` ejecuta la etapa pedida. No toca la base: la
central resuelve el bucket por módulo y, mientras el objeto no esté migrado, cae
al bucket general, así que el sistema sigue funcionando durante la migración.

Un objeto sin sha256 registrado se verifica contra el original (mismo contenido
y tamaño). Los ZIP legacy de la raíz y los objetos sin fila no se tocan.

Uso::

    python -m central_api.tools.migrar_buckets copiar     [--bot X] [--limite N] [--aplicar]
    python -m central_api.tools.migrar_buckets verificar  [--bot X] [--limite N]
    python -m central_api.tools.migrar_buckets borrar     [--bot X] [--limite N] [--aplicar]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import text

from central_api.db import nueva_sesion
from central_api.settings import get_settings
from central_api.storage import delete_object_bytes, get_object_bytes, object_exists, put_object_bytes

MAX_BYTES = 150 * 1024 * 1024
_SQL = """
SELECT a.id::text AS id, a.object_key, a.sha256, a.size_bytes, a.content_type, j.bot
FROM job_artifacts a JOIN jobs j ON j.id = a.job_id
WHERE (CAST(:bot AS text) IS NULL OR j.bot = :bot)
ORDER BY a.created_at, a.id
"""


def _kw(s):
    return dict(
        endpoint=s.object_storage_endpoint, region=s.object_storage_region,
        access_key=s.object_storage_access_key, secret_key=s.object_storage_secret_key,
    )


def _leer(s, bucket: str, key: str) -> bytes | None:
    try:
        return get_object_bytes(**_kw(s), bucket=bucket, object_key=key, max_bytes=MAX_BYTES)
    except RuntimeError:
        return None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def _filas(bot: str | None):
    async with nueva_sesion() as ses:
        return (await ses.execute(text(_SQL), {"bot": bot})).mappings().all()


def _registro(etapa: str, aplicar: bool):
    ruta = f"/tmp/migrar_buckets_{etapa}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"
    return open(ruta, "a", encoding="utf-8"), ruta


async def correr(etapa: str, bot: str | None, limite: int | None, aplicar: bool) -> Counter:
    s = get_settings()
    general = s.object_storage_bucket
    cuenta: Counter = Counter()
    log, ruta = _registro(etapa, aplicar)
    hechos = 0
    for fila in await _filas(bot):
        destino = s.bucket_para_bot(fila["bot"])
        key = fila["object_key"]
        if destino == general:
            cuenta["sin_cambio_(modulo_en_bucket_general)"] += 1
            continue
        if limite is not None and hechos >= limite:
            break
        en_dest = object_exists(**_kw(s), bucket=destino, object_key=key)
        en_gral = object_exists(**_kw(s), bucket=general, object_key=key)
        evento = {"key": key, "bot": fila["bot"], "destino": destino, "etapa": etapa}

        if etapa == "copiar":
            if en_dest:
                cuenta["ya_en_destino"] += 1
                continue
            if not en_gral:
                cuenta["ORIGEN_AUSENTE"] += 1
                evento["resultado"] = "origen_ausente"; log.write(json.dumps(evento) + "\n")
                continue
            hechos += 1
            if not aplicar:
                cuenta["se_copiaria"] += 1
                continue
            cuerpo = _leer(s, general, key)
            if cuerpo is None:
                cuenta["ERROR_leyendo_origen"] += 1; continue
            if fila["sha256"] and _sha(cuerpo) != fila["sha256"]:
                cuenta["ORIGEN_HASH_DISTINTO_no_se_copia"] += 1
                evento["resultado"] = "origen_hash_distinto"; log.write(json.dumps(evento) + "\n")
                continue
            put_object_bytes(**_kw(s), bucket=destino, object_key=key, content=cuerpo,
                             content_type=fila["content_type"] or "application/octet-stream")
            copia = _leer(s, destino, key)
            ok = copia is not None and _sha(copia) == _sha(cuerpo)
            cuenta["copiado_y_verificado" if ok else "ERROR_COPIA_NO_COINCIDE"] += 1
            evento["resultado"] = "ok" if ok else "copia_no_coincide"

        elif etapa in ("verificar", "borrar"):
            if not en_dest:
                cuenta["NO_ESTA_EN_DESTINO"] += 1
                evento["resultado"] = "no_esta_en_destino"; log.write(json.dumps(evento) + "\n")
                continue
            copia = _leer(s, destino, key)
            esperado = fila["sha256"]
            if esperado is None and en_gral:
                orig = _leer(s, general, key)
                esperado = _sha(orig) if orig is not None else None
            verificado = copia is not None and esperado is not None and _sha(copia) == esperado \
                and (fila["size_bytes"] is None or len(copia) == int(fila["size_bytes"]))
            if not verificado:
                cuenta["NO_VERIFICADO"] += 1
                evento["resultado"] = "no_verificado"; log.write(json.dumps(evento) + "\n")
                continue
            if etapa == "verificar":
                cuenta["verificado"] += 1; hechos += 1
                evento["resultado"] = "verificado"
            else:
                if not en_gral:
                    cuenta["ya_borrado_del_general"] += 1
                    continue
                hechos += 1
                if not aplicar:
                    cuenta["se_borraria_(verificado)"] += 1
                    continue
                try:
                    delete_object_bytes(**_kw(s), bucket=general, object_key=key)
                    cuenta["borrado_del_general"] += 1; evento["resultado"] = "borrado"
                except RuntimeError:
                    cuenta["ERROR_borrando"] += 1; evento["resultado"] = "error_borrando"
        log.write(json.dumps(evento) + "\n")
    log.close()
    cuenta["_registro"] = ruta
    return cuenta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("etapa", choices=["copiar", "verificar", "borrar"])
    ap.add_argument("--bot")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    c = asyncio.run(correr(a.etapa, a.bot, a.limite, a.aplicar))
    print(json.dumps({"etapa": a.etapa, "modo": "APLICADO" if a.aplicar else "SIMULACION", **c}, indent=1, ensure_ascii=False))
    # Cualquier contador de problema (mayusculas iniciales) hace fallar el proceso.
    problemas = {k: v for k, v in c.items() if isinstance(v, int) and k.startswith(("ERROR", "ORIGEN_", "NO_")) and v}
    if problemas:
        print("PROBLEMAS:", json.dumps(problemas), file=sys.stderr)
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
