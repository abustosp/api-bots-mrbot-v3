"""Archivos de entrada de jobs en el bucket temporal ``uploads``.

Los aliases conservan las formas V1/V2, pero el job persistido conserva solo
object keys. Al despachar, la central emite URLs GET temporales y el worker
materializa esos objetos dentro del directorio efímero del job.
"""

from __future__ import annotations

from typing import Any

from central_api.settings import get_settings
from central_api.storage import presign_get_url

#: ``(bot, operacion)`` -> campo del payload que contiene object keys múltiples.
INPUT_FILE_FIELDS: dict[tuple[str, str], str] = {
    ("controladores_fiscales", "presentar"): "archivos",
}

#: ``(bot, operacion)`` -> ``{campo_key: campo_que_recibe_el_archivo_en_runtime}``.
KEY_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("vep_archivo", "generar"): {"archivo_key": "archivo_path"},
    ("carga_portal_iva", "cargar"): {
        f"{base}_key": f"{base}_path"
        for base in (
            "liv_cbte", "liv_alicuota", "lic_cbte", "lic_alicuota",
            "csv_cf", "csv_cf_restitucion", "csv_df", "csv_df_restitucion",
        )
    },
    ("portal_iva", "importar"): {
        "ventas_txt_key": "ventas_txt_path",
        "ventas_alicuotas_txt_key": "ventas_alicuotas_txt_path",
        "compras_txt_key": "compras_txt_path",
        "compras_alicuotas_txt_key": "compras_alicuotas_txt_path",
    },
    ("portal_iva", "gestionar"): {
        "ventas_txt_key": "ventas_txt_path",
        "ventas_alicuotas_txt_key": "ventas_alicuotas_txt_path",
        "compras_txt_key": "compras_txt_path",
        "compras_alicuotas_txt_key": "compras_alicuotas_txt_path",
    },
}

# Campos que pueden solicitar un ticket en ``POST /api/v3/uploads``.
# Las utilidades síncronas usan operaciones existentes para compartir el mismo
# bucket y la misma política de retención.
UPLOAD_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("controladores_fiscales", "presentar"): frozenset({"archivos"}),
    ("vep_archivo", "generar"): frozenset({"archivo", "archivo_excel"}),
    ("carga_portal_iva", "cargar"): frozenset({
        "liv_cbte", "liv_alicuota", "lic_cbte", "lic_alicuota",
        "csv_cf", "csv_cf_restitucion", "csv_df", "csv_df_restitucion",
    }),
    ("portal_iva", "importar"): frozenset({
        "ventas_txt", "ventas_alicuotas_txt", "compras_txt", "compras_alicuotas_txt",
    }),
    ("portal_iva", "gestionar"): frozenset({
        "ventas_txt", "ventas_alicuotas_txt", "compras_txt", "compras_alicuotas_txt",
    }),
    ("rcel", "descargar"): frozenset({"pem_convertir", "rcel_extraer_texto", "scrapping_pdf"}),
}

# Límites máximos del contenido, definidos por operación y campo.
UPLOAD_FIELD_MAX_BYTES: dict[tuple[str, str, str], int] = {
    ("carga_portal_iva", "cargar", field): 20_971_520
    for field in UPLOAD_FIELDS[("carga_portal_iva", "cargar")]
}
UPLOAD_FIELD_MAX_BYTES.update({
    ("vep_archivo", "generar", "archivo"): 7_000_000,
    ("vep_archivo", "generar", "archivo_excel"): 52_428_800,
})
UPLOAD_FIELD_MAX_BYTES.update({
    ("portal_iva", operation, field): 5_000_000
    for operation in ("importar", "gestionar")
    for field in UPLOAD_FIELDS[("portal_iva", operation)]
})
UPLOAD_FIELD_MAX_BYTES.update({
    (bot, operation, field): 52_428_800
    for (bot, operation), fields in UPLOAD_FIELDS.items()
    for field in fields
    if (bot, operation, field) not in UPLOAD_FIELD_MAX_BYTES
})

#: Traducción de cada campo de referencia a la propiedad que consume el plugin.
KEY_FIELD_NAMES: dict[tuple[str, str], dict[str, str]] = {
    (bot, operation): {
        field_key: field_key.removesuffix("_key")
        for field_key in fields
    }
    for (bot, operation), fields in KEY_FIELDS.items()
}

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
            claves.append(str(payload[campo_key]))
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
    """Retorna el payload de runtime y URLs GET frescas para sus objetos."""
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
            # Sin storage configurado: modo local de desarrollo.
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
    for campo_key, campo_runtime in mapa.items():
        clave = nuevo.pop(campo_key, None)
        if clave:
            archivos.append({
                "campo": campo_runtime,
                "nombre": nombre_de_clave(str(clave)),
                "url": _url(str(clave)),
            })
    return nuevo, archivos


__all__ = [
    "INPUT_FILE_FIELDS", "KEY_FIELDS", "UPLOAD_FIELDS", "UPLOAD_FIELD_MAX_BYTES",
    "KEY_FIELD_NAMES", "INPUT_GET_TTL_SECONDS",
    "campo_de_archivos", "claves_referenciadas", "nombre_de_clave", "preparar_entrada",
]
