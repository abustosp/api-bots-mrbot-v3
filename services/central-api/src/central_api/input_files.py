"""Archivos de entrada de un job, guardados en el bucket temporal ``uploads``.

El cliente sube cada archivo con ``POST /api/v3/uploads`` (clave
``{usuario}/{bot}/{cuit}/{archivo}``) y referencia la ``object_key`` en el
payload. El job persiste solo las claves. Al despachar, la central:

* reemplaza las claves por nombres simples de archivo en el payload que ve el
  worker, y
* entrega URLs ``GET`` prefirmadas y frescas dentro del sobre sellado
  (``input_files``), para que el worker las baje a su ``work_dir``.
"""

from __future__ import annotations

from typing import Any

from central_api.settings import get_settings
from central_api.storage import presign_get_url

#: ``(bot, operacion)`` -> campo del payload con la lista de object keys.
INPUT_FILE_FIELDS: dict[tuple[str, str], str] = {
    ("controladores_fiscales", "presentar"): "archivos",
}
#: Vigencia de la URL GET entregada al worker.
INPUT_GET_TTL_SECONDS = 900


def campo_de_archivos(bot: str, operacion: str) -> str | None:
    return INPUT_FILE_FIELDS.get((bot, operacion))


def nombre_de_clave(object_key: str) -> str:
    return str(object_key).rsplit("/", 1)[-1]


def _nombres_unicos(claves: list[str]) -> list[str]:
    vistos: dict[str, int] = {}
    nombres: list[str] = []
    for clave in claves:
        nombre = nombre_de_clave(clave)
        n = vistos.get(nombre, 0)
        vistos[nombre] = n + 1
        nombres.append(nombre if n == 0 else f"{n}_{nombre}")
    return nombres


def preparar_entrada(
    bot: str, operacion: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Payload para el worker y lista ``input_files`` con URLs GET frescas."""
    campo = campo_de_archivos(bot, operacion)
    claves = payload.get(campo) if campo else None
    if not campo or not isinstance(claves, list) or not claves:
        return payload, []
    ajustes = get_settings()
    nombres = _nombres_unicos([str(c) for c in claves])
    archivos: list[dict[str, Any]] = []
    for clave, nombre in zip(claves, nombres):
        try:
            url = presign_get_url(
                endpoint=(
                    ajustes.object_storage_public_endpoint
                    or ajustes.object_storage_endpoint
                ),
                region=ajustes.object_storage_region,
                bucket=ajustes.object_storage_uploads_bucket,
                access_key=ajustes.object_storage_access_key,
                secret_key=ajustes.object_storage_secret_key,
                object_key=str(clave),
                expires_seconds=INPUT_GET_TTL_SECONDS,
            )
        except ValueError:
            # Sin storage configurado: modo desarrollo sin bytes reales.
            url = ""
        archivos.append({"campo": campo, "nombre": nombre, "url": url})
    nuevo = dict(payload)
    nuevo[campo] = nombres
    return nuevo, archivos
