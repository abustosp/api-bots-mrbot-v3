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
#: ``(bot, operacion)`` -> ``{campo_key: campo_b64}``. Alternativa opcional al
#: base64 inline: el cliente manda ``archivo_key`` (object_key de /uploads) y el
#: worker rellena ``archivo_b64`` con el contenido antes de validar.
KEY_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("vep_archivo", "generar"): {"archivo_key": "archivo_b64"},
    ("carga_portal_iva", "cargar"): {
        f"{base}_key": f"{base}_b64"
        for base in (
            "liv_cbte", "liv_alicuota", "lic_cbte", "lic_alicuota",
            "csv_cf", "csv_cf_restitucion", "csv_df", "csv_df_restitucion",
        )
    },
}
#: Vigencia de la URL GET entregada al worker.
INPUT_GET_TTL_SECONDS = 900


def campo_de_archivos(bot: str, operacion: str) -> str | None:
    return INPUT_FILE_FIELDS.get((bot, operacion))


def claves_referenciadas(
    bot: str, operacion: str, payload: dict[str, Any]
) -> list[str]:
    """Todas las object keys que el payload referencia (lista o ``*_key``)."""
    campo = campo_de_archivos(bot, operacion)
    if campo:
        valor = payload.get(campo)
        return [str(c) for c in valor] if isinstance(valor, list) else []
    claves: list[str] = []
    for campo_key in KEY_FIELDS.get((bot, operacion), {}):
        if payload.get(campo_key) is not None:
            claves.append(payload[campo_key])
    return claves


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
    ajustes = get_settings()

    def _url(clave: str) -> str:
        try:
            return presign_get_url(
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
            return ""

    campo = campo_de_archivos(bot, operacion)
    claves = payload.get(campo) if campo else None
    if campo and isinstance(claves, list) and claves:
        nombres = _nombres_unicos([str(c) for c in claves])
        archivos = [
            {"campo": campo, "nombre": nombre, "url": _url(clave)}
            for clave, nombre in zip(claves, nombres)
        ]
        nuevo = dict(payload)
        nuevo[campo] = nombres
        return nuevo, archivos

    mapa = KEY_FIELDS.get((bot, operacion))
    if not mapa:
        return payload, []
    nuevo = dict(payload)
    archivos = []
    for campo_key, campo_b64 in mapa.items():
        clave = nuevo.pop(campo_key, None)
        if clave:
            archivos.append({
                "campo": campo_b64,
                "nombre": nombre_de_clave(str(clave)),
                "url": _url(str(clave)),
                "modo": "b64",
            })
    return nuevo, archivos
