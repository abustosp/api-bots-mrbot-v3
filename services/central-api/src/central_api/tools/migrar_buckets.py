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


def _procesar(s, etapa: str, aplicar: bool, fila) -> tuple[str, dict]:
    """Procesa UN objeto (sincrono, apto para hilos). Devuelve (contador, evento)."""
    general = s.object_storage_bucket
    destino = s.bucket_para_bot(fila["bot"])
    key = fila["object_key"]
    evento = {"key": key, "bot": fila["bot"], "destino": destino, "etapa": etapa}
    en_dest = object_exists(**_kw(s), bucket=destino, object_key=key)
    en_gral = object_exists(**_kw(s), bucket=general, object_key=key)

    if etapa == "copiar":
        if en_dest:
            return "ya_en_destino", evento
        if not en_gral:
            evento["resultado"] = "origen_ausente"
            return "ORIGEN_AUSENTE", evento
        if not aplicar:
            return "se_copiaria", evento
        cuerpo = _leer(s, general, key)
        if cuerpo is None:
            evento["resultado"] = "error_leyendo_origen"
            return "ERROR_leyendo_origen", evento
        if fila["sha256"] and _sha(cuerpo) != fila["sha256"]:
            evento["resultado"] = "origen_hash_distinto"
            return "ORIGEN_HASH_DISTINTO_no_se_copia", evento
        put_object_bytes(**_kw(s), bucket=destino, object_key=key, content=cuerpo,
                         content_type=fila["content_type"] or "application/octet-stream")
        copia = _leer(s, destino, key)
        if copia is not None and _sha(copia) == _sha(cuerpo):
            evento["resultado"] = "ok"
            return "copiado_y_verificado", evento
        evento["resultado"] = "copia_no_coincide"
        return "ERROR_COPIA_NO_COINCIDE", evento

    # verificar / borrar: el destino se vuelve a leer y comparar AHORA
    if not en_dest:
        evento["resultado"] = "no_esta_en_destino"
        return "NO_ESTA_EN_DESTINO", evento
    copia = _leer(s, destino, key)
    esperado = fila["sha256"]
    if esperado is None and en_gral:
        orig = _leer(s, general, key)
        esperado = _sha(orig) if orig is not None else None
    verificado = (
        copia is not None and esperado is not None and _sha(copia) == esperado
        and (fila["size_bytes"] is None or len(copia) == int(fila["size_bytes"]))
    )
    if not verificado:
        evento["resultado"] = "no_verificado"
        return "NO_VERIFICADO", evento
    if etapa == "verificar":
        evento["resultado"] = "verificado"
        return "verificado", evento
    if not en_gral:
        return "ya_borrado_del_general", evento
    if not aplicar:
        return "se_borraria_(verificado)", evento
    try:
        delete_object_bytes(**_kw(s), bucket=general, object_key=key)
    except RuntimeError:
        evento["resultado"] = "error_borrando"
        return "ERROR_borrando", evento
    evento["resultado"] = "borrado"
    return "borrado_del_general", evento


async def correr(etapa: str, bot: str | None, limite: int | None, aplicar: bool, hilos: int = 16) -> Counter:
    s = get_settings()
    general = s.object_storage_bucket
    cuenta: Counter = Counter()
    log, ruta = _registro(etapa, aplicar)
    pendientes = []
    for fila in await _filas(bot):
        if s.bucket_para_bot(fila["bot"]) == general:
            cuenta["sin_cambio_(modulo_en_bucket_general)"] += 1
        else:
            pendientes.append(fila)
    if limite is not None:
        pendientes = pendientes[:limite]
    cuenta["a_procesar"] = len(pendientes)
    loop = asyncio.get_running_loop()
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(hilos) as pool:
        tareas = [loop.run_in_executor(pool, _procesar, s, etapa, aplicar, f) for f in pendientes]
        for i, tarea in enumerate(asyncio.as_completed(tareas), 1):
            clave, evento = await tarea
            cuenta[clave] += 1
            if "resultado" in evento:
                log.write(json.dumps(evento) + "\n")
            if i % 500 == 0:
                print(f"  ... {i}/{len(pendientes)}", file=sys.stderr, flush=True)
    log.close()
    cuenta["_registro"] = ruta
    return cuenta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("etapa", choices=["copiar", "verificar", "borrar"])
    ap.add_argument("--bot")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--hilos", type=int, default=16)
    a = ap.parse_args()
    c = asyncio.run(correr(a.etapa, a.bot, a.limite, a.aplicar, a.hilos))
    print(json.dumps({"etapa": a.etapa, "modo": "APLICADO" if a.aplicar else "SIMULACION", **c}, indent=1, ensure_ascii=False))
    # Cualquier contador de problema (mayusculas iniciales) hace fallar el proceso.
    problemas = {k: v for k, v in c.items() if isinstance(v, int) and k.startswith(("ERROR", "ORIGEN_", "NO_")) and v}
    if problemas:
        print("PROBLEMAS:", json.dumps(problemas), file=sys.stderr)
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
