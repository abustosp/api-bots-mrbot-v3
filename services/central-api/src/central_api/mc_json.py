"""Reconstrucción del JSON de Mis Comprobantes desde el ZIP guardado en S4.

El worker sube siempre el ZIP original de ARCA como artefacto y no deja filas en
el resultado. Cuando el pedido trae ``carga_json`` en verdadero, al devolver el
resultado la central descarga el ZIP a un directorio temporal propio, lo
descomprime, lo parsea con las mismas funciones que usa el worker
(``mrbot_contracts.planillas``) y arma el JSON en el momento. Ese JSON no se
guarda en ninguna tabla: el temporal se elimina siempre en ``finally``.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from mrbot_contracts.planillas import (
    MAX_FILAS_JSON,
    reconstruir_filas_desde_zip,
)

from central_api.settings import Settings, get_settings
from central_api.storage import get_object_bytes

#: Bot y operaciones que guardan ZIP y admiten reconstrucción.
BOT = "mis_comprobantes"
OPERACIONES = ("consulta", "consultar", "historial")
MAX_BYTES_ZIP = 52_428_800


def pide_json(payload: Mapping[str, Any] | None) -> bool:
    """``carga_json``/``incluir_json`` del pedido original (por defecto, no)."""
    payload = payload or {}
    for clave in ("carga_json", "incluir_json"):
        if clave in payload:
            return bool(payload[clave])
    return False


def aplica(bot: str, operacion: str, payload: Mapping[str, Any] | None) -> bool:
    return bot == BOT and operacion in OPERACIONES and pide_json(payload)


def _zip_de(artefactos: Sequence[Mapping[str, Any]], tipo: str) -> Mapping[str, Any] | None:
    """Artefacto ZIP de ``tipo`` (el último si hubo reintentos).

    Se identifica por el ``artifact_id`` que la central incluye en el
    ``object_key`` (``jobs/<job>/<intento>/<tipo>_zip/<uuid>``): el nombre del
    archivo lo arma el worker con CUIT y razón social y no es confiable.
    """
    sigla = " - MCE - " if tipo == "emitidos" else " - MCR - "
    claves = [a for a in artefactos if str(a.get("object_key") or "")]
    # Layout actual: ``jobs/{job}/{n} - MCE - ... .zip`` (el nombre identifica el tipo).
    nuevos = [
        a for a in claves
        if str(a["object_key"]).count("/") == 2
        and str(a["object_key"]).lower().endswith(".zip")
        and sigla in str(a["object_key"]).rsplit("/", 1)[-1]
    ]
    if nuevos:
        return nuevos[-1]
    marca = f"/{tipo}_zip/"
    candidatos = [a for a in claves if marca in str(a["object_key"])]
    return candidatos[-1] if candidatos else None


def _leer_zip(settings: Settings, object_key: str) -> bytes:
    """Lee el ZIP del bucket de Mis Comprobantes; si aún no se migró, del general."""
    ultimo: Exception | None = None
    for bucket in settings.buckets_de_lectura(BOT):
        try:
            return get_object_bytes(
                endpoint=settings.object_storage_endpoint,
                region=settings.object_storage_region,
                bucket=bucket,
                access_key=settings.object_storage_access_key,
                secret_key=settings.object_storage_secret_key,
                object_key=object_key,
                max_bytes=MAX_BYTES_ZIP,
            )
        except RuntimeError as exc:
            ultimo = exc
    raise ultimo or RuntimeError("no se pudo leer el archivo del almacenamiento")


def _reconstruir_tipo(
    settings: Settings,
    artefacto: Mapping[str, Any],
    *,
    tipo: str,
    cuit: str,
    desde: str,
    hasta: str,
    limite: int,
) -> dict[str, Any]:
    base = Path(settings.backup_dir)
    base.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix="mc-json-", dir=str(base)))
    try:
        zip_path = trabajo / "descarga.zip"
        zip_path.write_bytes(_leer_zip(settings, str(artefacto["object_key"])))
        filas, total, truncado = reconstruir_filas_desde_zip(
            zip_path,
            tipo=tipo,
            cuit=cuit,
            desde=desde,
            hasta=hasta,
            trabajo=trabajo,
            limite=limite,
        )
        return {"filas": total, "items": filas, "truncado": truncado}
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def reconstruir_sync(
    *,
    payload: Mapping[str, Any],
    datos: Mapping[str, Any],
    artefactos: Sequence[Mapping[str, Any]],
    limite: int = MAX_FILAS_JSON,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Devuelve ``datos`` con ``items`` por tipo, reconstruidos desde S4.

    Si falta el ZIP o falla la lectura, el tipo queda con ``json_error`` y el
    resto del resultado se devuelve igual (nunca se rompe la consulta del job).
    """
    ajustes = settings or get_settings()
    salida = dict(datos)
    cuit = str(datos.get("representado_cuit") or payload.get("representado_cuit") or "")
    desde = str(datos.get("fecha_desde") or payload.get("fecha_desde") or "")
    hasta = str(datos.get("fecha_hasta") or payload.get("fecha_hasta") or "")
    for tipo in ("emitidos", "recibidos"):
        resumen = salida.get(tipo)
        if not isinstance(resumen, Mapping):
            continue
        resumen = dict(resumen)
        if "muestra" in resumen and "zip" not in resumen:
            # Job histórico (anterior al ZIP): ya trae su muestra guardada.
            salida[tipo] = resumen
            continue
        artefacto = _zip_de(artefactos, tipo)
        if artefacto is None:
            resumen["json_error"] = "zip_no_disponible"
        else:
            try:
                resumen.update(
                    _reconstruir_tipo(
                        ajustes,
                        artefacto,
                        tipo=tipo,
                        cuit=cuit,
                        desde=desde,
                        hasta=hasta,
                        limite=limite,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - se informa el tipo, no el detalle
                resumen["json_error"] = f"{type(exc).__name__}"
        salida[tipo] = resumen
    return salida


async def reconstruir(
    *,
    bot: str,
    operacion: str,
    payload: Mapping[str, Any] | None,
    datos: Mapping[str, Any] | None,
    artefactos: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Reconstruye el JSON si corresponde; si no, devuelve ``datos`` sin cambios."""
    if not isinstance(datos, Mapping) or not aplica(bot, operacion, payload):
        return datos
    return await asyncio.to_thread(
        reconstruir_sync,
        payload=payload or {},
        datos=datos,
        artefactos=artefactos,
    )


__all__ = ["aplica", "pide_json", "reconstruir", "reconstruir_sync"]
