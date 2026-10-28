"""Reconstrucción del response de CCMA y RCEL desde los CSV guardados en S4.

El worker sube el dato scrapeado (saldos y movimientos de CCMA, campos de cada
factura de RCEL) como CSV con ``|`` y deja en la base solo un payload reducido
con ``datos_en_archivo``. Al devolver el resultado, la central descarga esos
archivos a un directorio temporal propio, los parsea con las mismas funciones
del worker (``mrbot_contracts.datos_csv``) y vuelve a armar el response original.
Nada de lo reconstruido se guarda: el temporal se borra siempre en ``finally``.

Los jobs anteriores (sin ``datos_en_archivo``) se devuelven tal cual.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mrbot_contracts import datos_csv

from central_api.settings import Settings, get_settings
from central_api.storage import get_object_bytes

MAX_BYTES_CSV = 52_428_800
#: bot -> (función de reconstrucción, artifact_id por clave de archivo)
BOTS: dict[str, tuple[Callable[..., dict[str, Any]], dict[str, str]]] = {
    "ccma": (
        datos_csv.reconstruir_ccma,
        {"saldos": "ccma_saldos.csv", "movimientos": "ccma_movimientos.csv"},
    ),
    "rcel": (datos_csv.reconstruir_rcel, {"facturas": "rcel_facturas.csv"}),
}


def aplica(bot: str, datos: Mapping[str, Any] | None) -> bool:
    return bot in BOTS and isinstance(datos, Mapping) and bool(
        datos.get(datos_csv.CLAVE_DATOS_EN_ARCHIVO)
    )


def _clave_de(artefactos: Sequence[Mapping[str, Any]], artifact_id: str) -> str | None:
    """``object_key`` del último artefacto con ese ``artifact_id`` (intentos previos incluidos)."""
    marca = f"/{artifact_id}/"
    claves = [str(a.get("object_key") or "") for a in artefactos]
    candidatas = [c for c in claves if marca in c]
    return candidatas[-1] if candidatas else None


def _leer(settings: Settings, object_key: str) -> bytes:
    return get_object_bytes(
        endpoint=settings.object_storage_endpoint,
        region=settings.object_storage_region,
        bucket=settings.bucket_para_artefacto(object_key),
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        object_key=object_key,
        max_bytes=MAX_BYTES_CSV,
    )


def reconstruir_sync(
    *,
    bot: str,
    datos: Mapping[str, Any],
    artefactos: Sequence[Mapping[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Devuelve el payload original; si algo falla, el reducido con ``datos_error``.

    Una falla de lectura no rompe la consulta del job: el response sale con lo
    que hay en la base y un ``datos_error`` que indica qué archivo no se pudo leer.
    """
    ajustes = settings or get_settings()
    reconstruir, ids = BOTS[bot]
    pedidos = list(datos.get(datos_csv.CLAVE_DATOS_EN_ARCHIVO) or [])
    base = Path(ajustes.backup_dir)
    base.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix=f"{bot}-datos-", dir=str(base)))
    try:
        textos: dict[str, str] = {}
        for clave in pedidos:
            artifact_id = ids.get(clave)
            object_key = _clave_de(artefactos, artifact_id) if artifact_id else None
            if object_key is None:
                return {**datos, "datos_error": f"archivo_no_disponible:{clave}"}
            archivo = trabajo / f"{clave}.csv"
            archivo.write_bytes(_leer(ajustes, object_key))
            textos[clave] = archivo.read_text(encoding="utf-8")
        return reconstruir(datos, textos)
    except Exception as exc:  # noqa: BLE001 - se informa el tipo, no el detalle
        return {**datos, "datos_error": type(exc).__name__}
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


async def reconstruir(
    *,
    bot: str,
    datos: Mapping[str, Any] | None,
    artefactos: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Reconstruye si corresponde; si no, devuelve ``datos`` sin cambios."""
    if not aplica(bot, datos):
        return datos
    return await asyncio.to_thread(
        reconstruir_sync, bot=bot, datos=datos or {}, artefactos=artefactos
    )


__all__ = ["aplica", "reconstruir", "reconstruir_sync"]
