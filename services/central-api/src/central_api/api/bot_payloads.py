"""Espejos Pydantic de entradas de bots para la documentación OpenAPI.

La central no ejecuta ni importa plugins del worker. Estas clases son DTOs de
borde, deliberadamente duplicadas de los esquemas declarativos del worker, para
que cada alias V2 muestre en Swagger los campos que el worker espera. La
validación de negocio definitiva continúa en el plugin del worker.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class CompatBodyBase(BaseModel):
    """Base común de los cuerpos públicos de compatibilidad."""

    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        json_schema_extra={
            "description": (
                "Payload del bot. Los campos desconocidos se conservan para "
                "permitir evolución compatible del esquema del worker."
            )
        },
    )


# Cada descriptor es (tipo, requerido, opciones de Field). Los nombres y
# restricciones principales son un espejo intencional de bots/*/schema.py del
# worker. No se importan esos módulos desde central-api.
def _string(*, required: bool = False, pattern: str | None = None,
            min_length: int | None = None, max_length: int | None = None):
    options: dict[str, Any] = {}
    if pattern:
        options["pattern"] = pattern
    if min_length is not None:
        options["min_length"] = min_length
    if max_length is not None:
        options["max_length"] = max_length
    return str, Field(... if required else None, **options)


def _boolean(*, default: bool = False):
    return bool, Field(default=default)


def _list(*, item: Any = str, required: bool = False,
          min_length: int | None = None, max_length: int | None = None):
    options: dict[str, Any] = {}
    if min_length is not None:
        options["min_length"] = min_length
    if max_length is not None:
        options["max_length"] = max_length
    annotation = list[item]
    return annotation, Field(... if required else None, **options)


CUIT = r"^\d{11}$"
DATE = r"^\d{2}/\d{2}/\d{4}$"
PERIOD = r"^\d{6}$"


# Campos públicos por familia. ``operacion`` no se expone como requerido: la
# ruta ya fija la operación canónica y el worker la recibe en el envelope.
_FAMILY_FIELDS: dict[str, dict[str, tuple[Any, Any]]] = {
    "apoc": {"cuit": _string(required=True, pattern=CUIT, min_length=11, max_length=11)},
    "aportes_en_linea": {
        "representado_cuit": _string(pattern=CUIT),
        "incluir_base64": _boolean(), "subir": _boolean(default=True),
    },
    "arba": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "periodo": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "representado_nombre": _string(max_length=256), "subir": _boolean(default=True),
    },
    "carga_portal_iva": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "periodo": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "denominacion": _string(max_length=128),
        "operaciones_ng_o_e": _boolean(), "prorrateo_global": _boolean(),
        "prorrateo_asignacion_directa": _boolean(), "prorrateo_ambos": _boolean(),
        "importacion_definitiva_bienes": _boolean(), "importacion_servicios": _boolean(),
        "regimen_turiva": _boolean(), "bienes_usados": _boolean(),
        "ninguna_anteriores": _boolean(default=True),
        "liv_cbte_b64": _string(max_length=5_000_000),
        "liv_alicuota_b64": _string(max_length=5_000_000),
        "lic_cbte_b64": _string(max_length=5_000_000),
        "lic_alicuota_b64": _string(max_length=5_000_000),
        "csv_cf_b64": _string(max_length=5_000_000),
        "csv_cf_restitucion_b64": _string(max_length=5_000_000),
        "csv_df_b64": _string(max_length=5_000_000),
        "csv_df_restitucion_b64": _string(max_length=5_000_000),
    },
    "ccma": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "periodo_desde": _string(pattern=r"^\d{2}/\d{4}$"),
        "incluir_movimientos": _boolean(), "incluir_pdf": _boolean(),
        "subir": _boolean(default=True),
    },
    "certificado_mipyme": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "subir": _boolean(default=True),
    },
    "compensaciones": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "excel": _boolean(), "csv": _boolean(), "pdf": _boolean(),
        "subir": _boolean(default=True),
    },
    "comprobantes": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "representado_nombre": _string(min_length=1, max_length=128),
        "emitidos": _boolean(), "recibidos": _boolean(),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
    },
    "consulta_cuit": {
        "cuit": _string(pattern=CUIT),
        "cuits": _list(item=str, min_length=1, max_length=100),
    },
    "consulta_pagos_vep": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "periodo": _string(pattern=r"^\d{1,3}$", min_length=1, max_length=3),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
    },
    "controladores_fiscales": {
        "archivos": _list(item=str, required=True, min_length=1, max_length=50),
        "subir_constancia": _boolean(default=True), "incluir_json": _boolean(default=True),
    },
    "declaracion_en_linea": {
        "representado_cuit": _string(pattern=CUIT),
        "representado_nombre": _string(max_length=128),
        "periodo_desde": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "periodo_hasta": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "incluir_json": _boolean(default=True), "subir_archivos": _boolean(default=True),
    },
    "facturometro": {"representado_cuit": _string(required=True, pattern=CUIT)},
    "hacienda": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "denominacion": _string(required=True, min_length=1, max_length=256),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "por_emisor": _boolean(default=True), "por_receptor": _boolean(default=True),
        "incluir_json": _boolean(default=True), "subir_excel": _boolean(default=True),
    },
    "libros_portal_iva": {
        "representado_cuit": _string(pattern=CUIT),
        "periodo_desde": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "periodo_hasta": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "denominacion": _string(max_length=256), "incluir_json": _boolean(default=True),
        "subir_archivos": _boolean(default=True),
    },
    "liquidacion_granos": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "representado_nombre": _string(required=True, min_length=1, max_length=128),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "lpg_emitidas": _boolean(default=True), "lpg_recibidas": _boolean(default=True),
        "lsg_emitidas": _boolean(default=True), "lsg_recibidas": _boolean(default=True),
        "certificados_deposito": _boolean(default=True), "subir_archivos": _boolean(default=True),
    },
    "mis_comprobantes": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "representado_nombre": _string(min_length=1, max_length=128),
        "emitidos": _boolean(), "recibidos": _boolean(),
        "puntos_venta_emitidos": _list(item=int), "puntos_venta_recibidos": _list(item=int),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
    },
    "mis_facilidades": {
        "representado_cuit": _string(pattern=CUIT),
        "denominacion": _string(min_length=1, max_length=128),
        "situacion_excluyente": _list(item=str, max_length=20),
        "incluir_pdf": _boolean(default=True), "incluir_xlsx": _boolean(default=True),
        "subir_archivos": _boolean(default=True),
    },
    "mis_retenciones": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "representado_nombre": _string(required=True, min_length=1, max_length=128),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "impuestos": _list(item=str, max_length=6), "tipos": _list(item=str),
        "exportar_para_aplicativo": _boolean(), "incluir_json": _boolean(default=True),
        "subir_csv": _boolean(default=True),
    },
    "mis_retenciones_iva_simple": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "representado_nombre": _string(required=True, min_length=1, max_length=128),
        "fecha_desde": _string(required=True, pattern=DATE),
        "fecha_hasta": _string(required=True, pattern=DATE),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
    },
    "moa": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "despachos": _list(item=str, required=True, min_length=1, max_length=100),
        "tipo_agente": _string(min_length=1, max_length=128),
        "rol": _string(min_length=1, max_length=128),
        "metodo": (Literal["url", "form"], Field(default="url")),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
    },
    "pago_devoluciones": {
        "representado_cuit": _string(pattern=CUIT),
        "incluir_json": _boolean(default=True), "subir_archivo": _boolean(default=True),
    },
    "portal_iva": {
        "periodo": _string(required=True, min_length=6, max_length=7),
        "representado_cuit": _string(pattern=CUIT),
        "representado_nombre": _string(min_length=1, max_length=128),
        "descarga_ventas": _boolean(), "descarga_compras": _boolean(),
        "incluir_json": _boolean(default=True), "subir_csv": _boolean(default=True),
        "ventas_txt": _string(min_length=1, max_length=5_000_000),
        "compras_txt": _string(min_length=1, max_length=5_000_000),
    },
    "rcel": {
        "representado_cuit": _string(pattern=CUIT),
        "representado_nombre": _string(min_length=1, max_length=128),
        "fecha_desde": _string(pattern=DATE), "fecha_hasta": _string(pattern=DATE),
        "incluir_json": _boolean(default=True), "subir_pdf": _boolean(default=True),
    },
    "retper_iibb_agip": {
        "representado_cuit": _string(pattern=CUIT),
        "denominacion": _string(min_length=1, max_length=256),
        "periodo_desde": _string(pattern=PERIOD), "periodo_hasta": _string(pattern=PERIOD),
        "incluir_json": _boolean(default=True), "subir_archivo": _boolean(default=True),
    },
    "retper_iibb_misiones": {
        "representado_cuit": _string(pattern=CUIT),
        "denominacion": _string(min_length=1, max_length=256),
        "periodo_desde": _string(pattern=PERIOD), "periodo_hasta": _string(pattern=PERIOD),
        "incluir_json": _boolean(default=True), "subir_archivo": _boolean(default=True),
    },
    "sct": {
        "representado_cuit": _string(pattern=CUIT),
        "secciones": _list(item=str, min_length=1), "formatos": _list(item=str, min_length=1),
        "incluir_json": _boolean(default=True), "subir": _boolean(default=True),
    },
    "sifere": {
        "representado_cuit": _string(pattern=CUIT),
        "periodo": _string(pattern=PERIOD), "representado_nombre": _string(max_length=256),
        "jurisdicciones": _list(item=int), "incluir_json": _boolean(default=True),
        "subir": _boolean(default=True),
    },
    "siper": {
        "representado_cuit": _string(pattern=CUIT),
        "incluir_detalle": _boolean(default=True), "incluir_categorias": _boolean(default=True),
        "incluir_json": _boolean(default=True), "subir": _boolean(default=True),
    },
    "srt": {
        "cuits_consulta": _list(item=str, min_length=1, max_length=50),
        "incluir_json": _boolean(default=True), "subir_json": _boolean(default=True),
    },
    "vep_archivo": {
        "representado_cuit": _string(pattern=CUIT),
        "medio_pago": (Literal["link", "pago_mis_cuentas", "internet_banking", "xn_group"], Field(default="internet_banking")),
        "archivo_nombre": _string(min_length=1, max_length=128), "archivo_b64": _string(min_length=1),
        "incluir_json": _boolean(default=True), "subir_pdf": _boolean(default=True),
    },
    "vep_ccma": {
        "representado_cuit": _string(pattern=CUIT),
        "medio_pago": (Literal["link", "pago_mis_cuentas", "internet_banking", "xn_group"], Field(default="internet_banking")),
        "seleccionar_impuestos": _boolean(default=True), "seleccionar_intereses": _boolean(default=True),
        "generar_volante": _boolean(default=True), "incluir_json": _boolean(default=True),
        "subir_pdf": _boolean(default=True),
    },
}


@lru_cache(maxsize=None)
def public_bot_body_model(bot: str, operation: str) -> type[BaseModel]:
    """Crea una clase Pydantic estable por pareja bot/operación para OpenAPI."""
    fields = dict(_FAMILY_FIELDS.get(bot, {}))
    fields["credentials"] = (
        dict[str, Any] | None,
        Field(default=None, description="Credenciales fiscales efímeras. Nunca se persisten."),
    )
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}".split("_")) + "BotBody"
    return create_model(model_name, __base__=CompatBodyBase, **fields)


def _example_value(field_name: str) -> Any:
    """Valor seguro de ejemplo, nunca una credencial real."""
    if "cuit" in field_name:
        return "20123456789"
    if field_name.startswith("fecha_"):
        return "01/08/2026"
    if field_name.startswith("periodo"):
        return "202608"
    if field_name in {"cuits", "cuits_consulta", "archivos", "despachos"}:
        return ["20123456789"] if "cuit" in field_name else ["object-key-ejemplo"]
    if field_name.startswith("puntos_venta"):
        return [1]
    if field_name == "jurisdicciones":
        return [901]
    if field_name in {"representado_nombre", "denominacion"}:
        return "Empresa de ejemplo"
    if field_name.endswith("_b64"):
        return "BASE64_DEL_ARCHIVO"
    if field_name in {"impuestos", "tipos", "secciones", "formatos"}:
        return ["ejemplo"]
    return "valor-de-ejemplo"


@lru_cache(maxsize=None)
def public_bot_body_schema(bot: str, operation: str) -> dict[str, Any]:
    """Esquema inline para aliases, con ejemplo seguro visible en Swagger."""
    model = public_bot_body_model(bot, operation)
    schema = model.model_json_schema()
    example = {
        name: _example_value(name)
        for name, field in model.model_fields.items()
        if field.is_required()
    }
    schema["examples"] = [example]
    return schema


__all__ = [
    "CompatBodyBase",
    "public_bot_body_model",
    "public_bot_body_schema",
]
