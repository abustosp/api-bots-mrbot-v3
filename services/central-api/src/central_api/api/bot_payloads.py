"""Espejos Pydantic de entradas de bots para la documentación OpenAPI.

La central no ejecuta ni importa plugins del worker. Estas clases son DTOs de
borde, deliberadamente duplicadas de los esquemas declarativos del worker, para
que cada alias V2 muestre en Swagger los campos que el worker espera. La
validación de negocio definitiva continúa en el plugin del worker.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, create_model
from pydantic_core import PydanticUndefined


class CompatBodyBase(BaseModel):
    """Base común de los cuerpos públicos de compatibilidad."""

    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        json_schema_extra={
            "description": (
                "Payload del bot. Los campos desconocidos se ignoran para "
                "permitir compatibilidad. Los archivos temporales se eliminan "
                "siempre al terminar la operación."
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


def _object_key(*, required: bool = False):
    return _string(required=required, min_length=1, max_length=2048)


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
        "subir": _boolean(default=True),
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
        "liv_cbte_key": _object_key(),
        "liv_alicuota_key": _object_key(),
        "lic_cbte_key": _object_key(),
        "lic_alicuota_key": _object_key(),
        "csv_cf_key": _object_key(),
        "csv_cf_restitucion_key": _object_key(),
        "csv_df_key": _object_key(),
        "csv_df_restitucion_key": _object_key(),
    },
    "ccma": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "periodo_desde": _string(pattern=r"^\d{2}/\d{4}$"),
        "incluir_movimientos": _boolean(), "incluir_pdf": _boolean(),
        "subir": _boolean(default=True),
    },
    "ccma_compensacion": {
        "representado_cuit": _string(required=True, pattern=CUIT),
        "compensar": _boolean(default=True), "confirmar": _boolean(),
        "incluir_movimientos": _boolean(),
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
        "puntos_venta_emitidos": _list(item=str | int),
        "puntos_venta_recibidos": _list(item=str | int),
        "timeout_mc": (int | None, Field(default=None, ge=1, le=86400)),
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
    "libro_sueldos_digital": {
        "cuit_representante": _string(pattern=CUIT),
        "representado_cuit": _string(pattern=CUIT),
        "periodo_desde": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "periodo_hasta": _string(required=True, pattern=PERIOD, min_length=6, max_length=6),
        "incluir_json": _boolean(default=True), "subir_archivos": _boolean(default=True),
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
        "ventas_txt_key": _object_key(),
        "ventas_alicuotas_txt_key": _object_key(),
        "compras_txt_key": _object_key(),
        "compras_alicuotas_txt_key": _object_key(),
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
        "medio_pago": (Literal["link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr"], Field(default="internet_banking")),
        "archivo_key": _object_key(required=True),
        "incluir_json": _boolean(default=True), "subir_pdf": _boolean(default=True),
    },
    "vep_ccma": {
        "representado_cuit": _string(pattern=CUIT),
        "medio_pago": (Literal["link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr"], Field(default="internet_banking")),
        "seleccionar_impuestos": _boolean(default=True), "seleccionar_intereses": _boolean(default=True),
        "generar_volante": _boolean(default=True), "incluir_json": _boolean(default=True),
        "subir_pdf": _boolean(default=True),
    },
}


_OPERATION_FIELD_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    # El worker usa modelos estrictos distintos para individual y masivo.
    ("consulta_cuit", "consulta"): ("cuit",),
    ("consulta_cuit", "consultar"): ("cuit",),
    ("consulta_cuit", "consultar_masivo"): ("cuits",),
    # solicitar no tiene opciones de salida porque solo inicia la consulta.
    ("comprobantes", "solicitar"): (
        "representado_cuit", "fecha_desde", "fecha_hasta", "representado_nombre",
        "emitidos", "recibidos",
    ),
    ("mis_comprobantes", "solicitar"): (
        "representado_cuit", "fecha_desde", "fecha_hasta", "representado_nombre",
        "emitidos", "recibidos", "puntos_venta_emitidos", "puntos_venta_recibidos",
    ),
    # descargar no recibe el contenido TXT y importar no recibe flags de salida.
    ("portal_iva", "descargar"): (
        "periodo", "representado_cuit", "representado_nombre", "descarga_ventas",
        "descarga_compras", "incluir_json", "subir_csv",
    ),
    ("portal_iva", "importar"): (
        "periodo", "representado_cuit", "representado_nombre", "ventas_txt_key",
        "ventas_alicuotas_txt_key", "compras_txt_key", "compras_alicuotas_txt_key",
    ),
    ("portal_iva", "gestionar"): (
        "periodo", "representado_cuit", "representado_nombre", "descarga_ventas",
        "descarga_compras", "incluir_json", "subir_csv", "ventas_txt_key",
        "ventas_alicuotas_txt_key", "compras_txt_key", "compras_alicuotas_txt_key",
    ),
    ("vep_archivo", "generar"): (
        "representado_cuit", "medio_pago", "archivo_key", "incluir_json",
        "subir_pdf", "eliminar_descargas",
    ),
}


# Filtros anidados de VEP-CCMA, espejo de las entradas V2. Los modelos
# históricos ignoraban propiedades adicionales en cada fila.
class _V2FiltroImpuesto(BaseModel):
    model_config = ConfigDict(extra="ignore")

    periodo: str | None = None
    impuesto: str | None = None
    concepto: str | None = None
    subconcepto: str | None = None
    categoria: str | None = None


class _V2FiltroInteres(BaseModel):
    model_config = ConfigDict(extra="ignore")

    periodo: str | None = None
    impuesto: str | None = None
    concepto: str | None = None
    subconcepto: str | None = None


# La API V2 recibía un objeto plano. Cada descriptor conserva el tipo Python
# del modelo histórico; los defaults específicos por bot se aplican debajo.
_V2_COMPAT_FIELDS: dict[str, tuple[Any, Any]] = {
    "cuit_representante": _string(),
    "cuit_representado": _string(),
    "cuit_inicio_sesion": _string(),
    "cuit_login": _string(),
    "cuit": _string(),
    "clave": _string(),
    "clave_representante": _string(),
    "contrasena": _string(),
    "clave_encriptada": (str | None, Field(default=None)),
    "desde": _string(),
    "hasta": _string(),
    "proxy_request": (bool | None, Field(default=None)),
    "movimientos": (bool | None, Field(default=None)),
    "compensar": (bool | None, Field(default=True)),
    "confirmar": (bool | None, Field(default=False)),
    "pdf": (bool | None, Field(default=None)),
    "descarga_emitidos": (bool, Field(default=False, validation_alias=AliasChoices("descarga_emitidos", "emitidos"))),
    "descarga_recibidos": (bool, Field(default=False, validation_alias=AliasChoices("descarga_recibidos", "recibidos"))),
    "descarga_csv_ventas": _boolean(),
    "descarga_csv_compras": _boolean(),
    "carga_minio": _boolean(default=False),
    "pv": (bool | None, Field(default=False)),
    "carga_json": _boolean(),
    "minio_upload": (bool | None, Field(default=None)),
    "eliminar_descargas": (bool | None, Field(default=True)),
    "descartar_borrador": (bool | None, Field(default=True)),
    "timeout_mc": (int | None, Field(default=None)),
    "exportar_para_aplicativo": (bool | None, Field(default=False)),
    "usuario": _string(),
    "denominacion": _string(),
    "nombre_rcel": _string(),
    "medio_pago": _string(),
    "archivo_historico_minio": _boolean(default=True),
    "lista_exclusion_situacion": (list[str] | None, Field(default=None)),
    "detalle_minio": _boolean(),
    "categorias_minio": _boolean(),
    "vencimientos_excel_minio": _boolean(),
    "vencimientos_csv_minio": _boolean(),
    "vencimientos_pdf_minio": _boolean(),
    "deudas_excel_minio": _boolean(),
    "deudas_csv_minio": _boolean(),
    "deudas_pdf_minio": _boolean(),
    "ddjj_pendientes_excel_minio": _boolean(),
    "ddjj_pendientes_csv_minio": _boolean(),
    "ddjj_pendientes_pdf_minio": _boolean(),
    "filtro_impuestos": (list[_V2FiltroImpuesto] | None, Field(default=None)),
    "filtro_intereses": (list[_V2FiltroInteres] | None, Field(default=None)),
    "seleccionar_impuestos": (bool | None, Field(default=True)),
    "seleccionar_intereses": (bool | None, Field(default=True)),
    "generar_volante": (bool | None, Field(default=True)),
    "representado_cuit": _string(),
    "representado_nombre": _string(),
    "puntos_venta_emitidos": (list[str] | None, Field(default=None)),
    "puntos_venta_recibidos": (list[str] | None, Field(default=None)),
    "periodo": _string(),
    "excel": _boolean(),
    "csv": _boolean(),
    "cuits": _list(item=str),
    "despachos": _list(item=str),
    "tipo_agente": _string(),
    "rol": _string(),
    "impuestos": (list[str] | None, Field(default=None)),
    "jurisdicciones": (list[int] | None, Field(default=None)),
    "cuits_consulta": _list(item=str),
    "periodo_desde": _string(),
    "periodo_hasta": _string(),
    "operaciones_ng_o_e": _boolean(),
    "prorrateo_global": _boolean(),
    "prorrateo_asignacion_directa": _boolean(),
    "prorrateo_ambos": _boolean(),
    "importacion_definitiva_bienes": _boolean(),
    "importacion_servicios": _boolean(),
    "regimen_turiva": _boolean(),
    "bienes_usados": _boolean(),
    "ninguna_anteriores": _boolean(default=True),
    "emitidos": _boolean(),
    "recibidos": _boolean(),
}


# Campos exactos de los modelos de entrada V2. Se listan por clase/operación,
# no como una unión global. Los archivos multipart de V2 no forman parte del
# modelo JSON y se procesan en la capa de carga.
_V2_OPERATION_FIELD_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("aportes_en_linea", "descargar"): ("clave_encriptada", "cuit_login", "clave", "cuit_representado", "archivo_historico_minio", "proxy_request"),
    ("arba", "descargar"): ("clave_encriptada", "cuit", "clave", "periodo", "denominacion", "proxy_request", "carga_minio"),
    ("carga_portal_iva", "cargar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "denominacion", "periodo", "operaciones_ng_o_e", "prorrateo_global", "prorrateo_asignacion_directa", "prorrateo_ambos", "importacion_definitiva_bienes", "importacion_servicios", "regimen_turiva", "bienes_usados", "ninguna_anteriores", "proxy_request"),
    ("ccma", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "proxy_request", "movimientos", "pdf"),
    ("ccma_compensacion", "compensar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "proxy_request", "movimientos", "pdf", "compensar", "confirmar"),
    ("certificado_mipyme", "descargar"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "proxy_request"),
    ("compensaciones", "consultar"): ("clave_encriptada", "cuit_login", "clave", "cuit_representado", "desde", "hasta", "excel", "csv", "pdf", "proxy_request"),
    ("consulta_cuit", "consulta"): ("cuit",),
    ("consulta_cuit", "consultar"): ("cuit",),
    ("consulta_cuit", "consultar_masivo"): ("cuits",),
    ("consulta_pagos_vep", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "periodo", "minio_upload", "proxy_request"),
    ("controladores_fiscales", "presentar"): ("clave_encriptada", "cuit_representante", "clave", "proxy_request", "minio_upload"),
    ("declaracion_en_linea", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "representado_nombre", "periodo_desde", "periodo_hasta", "proxy_request", "carga_minio"),
    ("facturometro", "consultar"): ("clave_encriptada", "cuit_login", "clave", "cuit_representado", "proxy_request"),
    ("hacienda", "consultar"): ("clave_encriptada", "desde", "hasta", "cuit_representante", "denominacion", "representado_cuit", "clave", "minio_upload", "proxy_request"),
    ("libros_portal_iva", "descargar_ddjj"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "proxy_request"),
    ("libros_portal_iva", "descargar_libros"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "proxy_request"),
    ("libros_portal_iva", "descargar"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "proxy_request"),
    ("liquidacion_granos", "consultar"): ("clave_encriptada", "desde", "hasta", "cuit_representante", "clave", "denominacion", "cuit_representado", "minio_upload", "proxy_request"),
    ("mis_comprobantes", "consulta"): ("clave_encriptada", "desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos", "puntos_venta_emitidos", "puntos_venta_recibidos", "carga_minio", "carga_json", "timeout_mc", "proxy_request"),
    ("mis_comprobantes", "consultar"): ("clave_encriptada", "desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos", "puntos_venta_emitidos", "puntos_venta_recibidos", "carga_minio", "carga_json", "timeout_mc", "proxy_request"),
    ("mis_comprobantes", "solicitar"): ("clave_encriptada", "desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "emitidos", "recibidos", "puntos_venta_emitidos", "puntos_venta_recibidos", "proxy_request"),
    ("mis_comprobantes", "historial"): ("clave_encriptada", "desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos", "carga_minio", "carga_json", "proxy_request"),
    ("mis_facilidades", "consultar"): ("clave_encriptada", "cuit_login", "clave", "cuit_representado", "denominacion", "lista_exclusion_situacion", "proxy_request", "carga_minio"),
    ("mis_retenciones", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "denominacion", "desde", "hasta", "impuestos", "exportar_para_aplicativo", "proxy_request", "carga_minio"),
    ("mis_retenciones_iva_simple", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "denominacion", "desde", "hasta", "proxy_request", "carga_minio"),
    ("moa", "consultar"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "despachos", "tipo_agente", "rol", "minio_upload", "proxy_request"),
    ("pago_devoluciones", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "proxy_request", "carga_minio"),
    ("portal_iva", "descargar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "denominacion", "periodo", "operaciones_ng_o_e", "prorrateo_global", "prorrateo_asignacion_directa", "prorrateo_ambos", "importacion_definitiva_bienes", "importacion_servicios", "regimen_turiva", "bienes_usados", "ninguna_anteriores", "descarga_csv_ventas", "descarga_csv_compras", "carga_minio", "proxy_request", "descartar_borrador"),
    ("rcel", "descargar"): ("clave_encriptada", "desde", "hasta", "cuit_representante", "nombre_rcel", "representado_cuit", "clave", "minio_upload", "proxy_request"),
    ("retper_iibb_agip", "consultar"): ("clave_encriptada", "usuario", "clave", "cuit_representado", "denominacion", "desde", "hasta", "proxy_request", "pv", "carga_minio"),
    ("retper_iibb_misiones", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "desde", "hasta", "denominacion", "proxy_request", "carga_minio"),
    ("sct", "consultar"): ("clave_encriptada", "cuit_login", "clave", "cuit_representado", "proxy_request", "vencimientos_excel_minio", "vencimientos_csv_minio", "vencimientos_pdf_minio", "deudas_excel_minio", "deudas_csv_minio", "deudas_pdf_minio", "ddjj_pendientes_excel_minio", "ddjj_pendientes_csv_minio", "ddjj_pendientes_pdf_minio"),
    ("sifere", "consultar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "periodo", "representado_nombre", "proxy_request", "jurisdicciones", "carga_minio"),
    ("siper", "consultar"): ("clave_encriptada", "cuit_representante", "clave", "cuit_representado", "detalle_minio", "categorias_minio", "proxy_request"),
    ("srt", "consultar_alicuotas"): ("clave_encriptada", "cuit_login", "clave", "cuits_consulta", "proxy_request"),
    ("vep_archivo", "generar"): ("clave_encriptada", "cuit_inicio_sesion", "medio_pago", "contrasena", "minio_upload", "eliminar_descargas", "proxy_request"),
    ("vep_ccma", "generar"): ("clave_encriptada", "cuit_representante", "clave_representante", "cuit_representado", "medio_pago", "filtro_impuestos", "filtro_intereses", "seleccionar_impuestos", "seleccionar_intereses", "minio_upload", "proxy_request", "generar_volante"),
}


_V2_REQUIRED_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("aportes_en_linea", "descargar"): frozenset({"cuit_login", "clave"}),
    ("arba", "descargar"): frozenset({"cuit", "clave", "periodo", "denominacion"}),
    ("carga_portal_iva", "cargar"): frozenset({"cuit_representante", "clave_representante", "periodo"}),
    ("ccma", "consultar"): frozenset({"cuit_representante", "clave_representante", "cuit_representado"}),
    ("ccma_compensacion", "compensar"): frozenset({"cuit_representante", "clave_representante", "cuit_representado"}),
    ("certificado_mipyme", "descargar"): frozenset({"cuit_representante", "clave", "cuit_representado"}),
    ("compensaciones", "consultar"): frozenset({"cuit_login", "clave", "cuit_representado", "desde", "hasta"}),
    ("consulta_cuit", "consulta"): frozenset({"cuit"}),
    ("consulta_cuit", "consultar"): frozenset({"cuit"}),
    ("consulta_cuit", "consultar_masivo"): frozenset({"cuits"}),
    ("consulta_pagos_vep", "consultar"): frozenset({"cuit_representante", "clave_representante", "cuit_representado"}),
    ("controladores_fiscales", "presentar"): frozenset({"cuit_representante", "clave"}),
    ("declaracion_en_linea", "consultar"): frozenset({"cuit_representante", "clave_representante", "periodo_desde", "periodo_hasta"}),
    ("facturometro", "consultar"): frozenset({"cuit_login", "clave", "cuit_representado"}),
    ("hacienda", "consultar"): frozenset({"desde", "hasta", "cuit_representante", "denominacion", "representado_cuit", "clave"}),
    ("libros_portal_iva", "descargar_ddjj"): frozenset({"cuit_representante", "clave", "periodo_desde", "periodo_hasta"}),
    ("libros_portal_iva", "descargar_libros"): frozenset({"cuit_representante", "clave", "periodo_desde", "periodo_hasta"}),
    ("libros_portal_iva", "descargar"): frozenset({"cuit_representante", "clave", "periodo_desde", "periodo_hasta"}),
    ("liquidacion_granos", "consultar"): frozenset({"desde", "hasta", "cuit_representante", "clave", "denominacion"}),
    ("mis_comprobantes", "consulta"): frozenset({"desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos"}),
    ("mis_comprobantes", "consultar"): frozenset({"desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos"}),
    ("mis_comprobantes", "solicitar"): frozenset({"desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "emitidos", "recibidos"}),
    ("mis_comprobantes", "historial"): frozenset({"desde", "hasta", "cuit_inicio_sesion", "representado_nombre", "representado_cuit", "contrasena", "descarga_emitidos", "descarga_recibidos"}),
    ("mis_facilidades", "consultar"): frozenset({"cuit_login", "clave"}),
    ("mis_retenciones", "consultar"): frozenset({"cuit_representante", "clave_representante", "denominacion", "desde", "hasta"}),
    ("mis_retenciones_iva_simple", "consultar"): frozenset({"cuit_representante", "clave_representante", "denominacion", "desde", "hasta"}),
    ("moa", "consultar"): frozenset({"cuit_representante", "clave", "cuit_representado", "despachos"}),
    ("pago_devoluciones", "consultar"): frozenset({"cuit_representante", "clave_representante"}),
    ("portal_iva", "descargar"): frozenset({"cuit_representante", "clave_representante", "periodo"}),
    ("rcel", "descargar"): frozenset({"desde", "hasta", "cuit_representante", "nombre_rcel", "representado_cuit", "clave"}),
    ("retper_iibb_agip", "consultar"): frozenset({"usuario", "clave", "cuit_representado", "denominacion", "desde", "hasta"}),
    ("retper_iibb_misiones", "consultar"): frozenset({"cuit_representante", "clave_representante", "desde", "hasta", "denominacion"}),
    ("sct", "consultar"): frozenset({"cuit_login", "clave", "cuit_representado"}),
    ("sifere", "consultar"): frozenset({"cuit_representante", "clave_representante", "cuit_representado", "periodo"}),
    ("siper", "consultar"): frozenset({"cuit_representante", "clave"}),
    ("srt", "consultar_alicuotas"): frozenset({"cuit_login", "clave", "cuits_consulta"}),
    ("vep_archivo", "generar"): frozenset({"cuit_inicio_sesion", "medio_pago", "contrasena"}),
    ("vep_ccma", "generar"): frozenset({"cuit_representante", "clave_representante", "cuit_representado"}),
}


# Defaults explícitos de los modelos V2. Se aplican por operación porque una
# misma propiedad (por ejemplo, minio_upload) no tenía el mismo default en todos
# los modelos.
_V2_OPERATION_DEFAULTS: dict[tuple[str, str], dict[str, Any]] = {
    ("aportes_en_linea", "descargar"): {"proxy_request": False},
    ("arba", "descargar"): {"carga_minio": True, "proxy_request": False},
    ("carga_portal_iva", "cargar"): {"proxy_request": False},
    ("ccma_compensacion", "compensar"): {"compensar": True, "confirmar": False},
    ("compensaciones", "consultar"): {"proxy_request": False},
    ("controladores_fiscales", "presentar"): {"minio_upload": True, "proxy_request": False},
    ("declaracion_en_linea", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("facturometro", "consultar"): {"proxy_request": False},
    ("hacienda", "consultar"): {"minio_upload": False},
    ("libros_portal_iva", "descargar_ddjj"): {"proxy_request": False},
    ("libros_portal_iva", "descargar_libros"): {"proxy_request": False},
    ("libros_portal_iva", "descargar"): {"proxy_request": False},
    ("liquidacion_granos", "consultar"): {"minio_upload": False, "proxy_request": False},
    ("mis_comprobantes", "consulta"): {"carga_minio": False, "carga_json": False},
    ("mis_comprobantes", "consultar"): {"carga_minio": False, "carga_json": False},
    ("mis_comprobantes", "solicitar"): {},
    ("mis_comprobantes", "historial"): {"carga_minio": True, "carga_json": False},
    ("mis_facilidades", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("mis_retenciones", "consultar"): {
        "carga_minio": True,
        "proxy_request": False,
        "exportar_para_aplicativo": False,
    },
    ("mis_retenciones_iva_simple", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("moa", "consultar"): {
        "tipo_agente": "IMEX-IMEX-IMPORTADOR/EXPORT.",
        "rol": "IMEX-Rol Importador Exportador",
        "minio_upload": True,
        "proxy_request": False,
    },
    ("pago_devoluciones", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("portal_iva", "descargar"): {"carga_minio": True, "proxy_request": False},
    ("rcel", "descargar"): {"minio_upload": False},
    ("retper_iibb_agip", "consultar"): {
        "carga_minio": True,
        "proxy_request": False,
        "pv": False,
    },
    ("retper_iibb_misiones", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("sifere", "consultar"): {"carga_minio": True, "proxy_request": False},
    ("srt", "consultar_alicuotas"): {"proxy_request": False},
    ("vep_archivo", "generar"): {"minio_upload": True, "eliminar_descargas": True},
    ("vep_ccma", "generar"): {"medio_pago": "internet_banking", "minio_upload": True},
    ("consulta_pagos_vep", "consultar"): {
        "periodo": "72",
        "minio_upload": True,
        "proxy_request": False,
    },
}

_V2_REQUIRED_AS_CREDENTIAL_ALTERNATIVE = frozenset(
    {"clave", "clave_representante", "contrasena"}
)


# El checkout de V1 no existe en el contenedor de la central. Este snapshot se
# generó desde ``/home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/api-bots/api-bots-mrbot``
# y se versiona junto con la central para que el contrato OpenAPI no dependa de
# una ruta del host de desarrollo. ``eliminar_descargas`` se omitió del snapshot
# al retirar esa opción: V3 siempre limpia el workdir temporal en ``finally``.
_V1_SCHEMA_SNAPSHOT = Path(__file__).with_name("v1_request_schemas.json")


@lru_cache(maxsize=1)
def _v1_request_schemas() -> dict[str, dict[str, Any]]:
    with _V1_SCHEMA_SNAPSHOT.open(encoding="utf-8") as stream:
        return json.load(stream)


# Una pareja bot/operación V3 puede tener más de un body histórico V1. La ruta
# del alias es parte de la clave a propósito: ``portal_iva/consulta`` y
# ``portal_iva/carga`` no compartían el mismo modelo en V1.
V1_SCHEMA_BY_ALIAS: dict[str, str] = {
    "/mis_comprobantes/consulta": "mis_comprobantes.MCRequest",
    "/mis_comprobantes/solicitar_consulta": "mis_comprobantes.MCConsultaRequest",
    "/mis_comprobantes/historial": "mis_comprobantes.MCHistorialRequest",
    "/ccma/consulta": "ccma.ConsultaCCMARequest",
    "/ccma/compensacion": "ccma_compensacion.CompensacionCCMARequest",
    "/siper/consulta": "siper.ConsultaSIPERRequest",
    "/sct/consulta": "sct.ConsultaSCTRequest",
    "/sct/compensaciones/consulta": "sct.ConsultaSCTCompensacionesRequest",
    "/portal_iva/consulta": "portal_iva.ConsultaPortalIvaRequest",
    "/portal_iva/carga": "carga_portal_iva.CargaPortalIvaRequest",
    "/rcel/consulta": "rcel.ConsultaRCELRequest",
    "/hacienda/consulta": "hacienda.ConsultaHaciendaRequest",
    "/sifere/consulta": "sifere.ConsultaSIFERERequest",
    "/aportes-en-linea/consulta": "aportes_en_linea.ConsultaAportesEnLineaRequest",
    "/declaracion-en-linea/consulta": "declaracion_en_linea.ConsultaDeclaracionEnLineaRequest",
    "/mis_facilidades/consulta": "mis_facilidades.ConsultaMisFacilidadesRequest",
    "/mis_retenciones/consulta": "mis_retenciones.ConsultaMisRetencionesRequest",
    "/mis_retenciones_iva_simple/consulta": "mis_retenciones_iva_simple.ConsultaMisRetencionesIvaSimpleRequest",
    "/retenciones_percepciones_iibb/misiones/consulta": "retper_iibb_misiones.ConsultaRetPerIIBBMisionesRequest",
    "/retenciones_percepciones_iibb/agip/consulta": "retper_iibb_agip.ConsultaRetPerIIBBAGIPRequest",
    "/retenciones_percepciones_iibb/arba/consulta": "retper_iibb_arba.ConsultaRetPerIIBBARBARequest",
    "/arba/consulta": "retper_iibb_arba.ConsultaRetPerIIBBARBARequest",
    "/pago_devoluciones/consulta": "pago_devoluciones.ConsultaPagoDevolucionesRequest",
    "/moa/consulta": "moa.ConsultaMOARequest",
    "/libros_iva/consulta": "libros_portal_iva.ConsultaLibrosIvaRequest",
    "/libros_iva/ddjj": "libros_portal_iva.ConsultaLibrosIvaRequest",
    "/facturometro/consulta": "facturometro.ConsultaFacturometroRequest",
    "/controladores-fiscales/carga": "controladores_fiscales.ConsultaControladoresFiscalesRequest",
    "/certificado-mipyme/consulta": "certificado_mipyme.CertificadoMipymeRequest",
    "/srt/alicuotas/consulta": "srt.ConsultaSRTAlicuotasRequest",
    "/vep/carga": "vep.VEPArchivoRequest",
    "/vep_archivo/carga": "vep.VEPArchivoRequest",
    "/vep-ccma/generar": "vep_ccma.VEPCCMARequest",
    "/vep/consulta-pagos": "vep.ConsultaPagosVEPRequest",
    "/liquidacion_granos/consulta": "liquidacion_granos.ConsultaLiquidacionGranosRequest",
    "/consulta_cuit/individual": "consulta_cuits.ConsultaCUITIndividualRequest",
    "/consulta_cuit/masivo": "consulta_cuits.ConsultaCUITMasivoRequest",
}


# La ruta histórica y la operación canónica no siempre tienen el mismo nombre
# (por ejemplo ``/ccma/consulta`` se publica como ``ccma/consultar`` en V3).
# Este índice permite que el envelope canónico conserve los mismos valores de
# ejemplo que el body plano V1/V2, en vez de inventar placeholders nuevos.
_HISTORICAL_SCHEMA_BY_OPERATION: dict[tuple[str, str], str] = {
    ("aportes_en_linea", "descargar"): "aportes_en_linea.ConsultaAportesEnLineaRequest",
    ("arba", "descargar"): "retper_iibb_arba.ConsultaRetPerIIBBARBARequest",
    ("carga_portal_iva", "cargar"): "carga_portal_iva.CargaPortalIvaRequest",
    ("ccma", "consultar"): "ccma.ConsultaCCMARequest",
    ("ccma_compensacion", "compensar"): "ccma_compensacion.CompensacionCCMARequest",
    ("certificado_mipyme", "descargar"): "certificado_mipyme.CertificadoMipymeRequest",
    ("compensaciones", "consultar"): "sct.ConsultaSCTCompensacionesRequest",
    ("consulta_cuit", "consulta"): "consulta_cuits.ConsultaCUITIndividualRequest",
    ("consulta_cuit", "consultar"): "consulta_cuits.ConsultaCUITIndividualRequest",
    ("consulta_cuit", "consultar_masivo"): "consulta_cuits.ConsultaCUITMasivoRequest",
    ("consulta_pagos_vep", "consultar"): "vep.ConsultaPagosVEPRequest",
    ("controladores_fiscales", "presentar"): "controladores_fiscales.ConsultaControladoresFiscalesRequest",
    ("declaracion_en_linea", "consultar"): "declaracion_en_linea.ConsultaDeclaracionEnLineaRequest",
    ("facturometro", "consultar"): "facturometro.ConsultaFacturometroRequest",
    ("hacienda", "consultar"): "hacienda.ConsultaHaciendaRequest",
    ("libros_portal_iva", "descargar_ddjj"): "libros_portal_iva.ConsultaLibrosIvaRequest",
    ("libros_portal_iva", "descargar_libros"): "libros_portal_iva.ConsultaLibrosIvaRequest",
    ("libros_portal_iva", "descargar"): "libros_portal_iva.ConsultaLibrosIvaRequest",
    ("liquidacion_granos", "consultar"): "liquidacion_granos.ConsultaLiquidacionGranosRequest",
    ("mis_comprobantes", "consulta"): "mis_comprobantes.MCRequest",
    ("mis_comprobantes", "consultar"): "mis_comprobantes.MCRequest",
    ("mis_comprobantes", "solicitar"): "mis_comprobantes.MCConsultaRequest",
    ("mis_comprobantes", "historial"): "mis_comprobantes.MCHistorialRequest",
    ("mis_facilidades", "consultar"): "mis_facilidades.ConsultaMisFacilidadesRequest",
    ("mis_retenciones", "consultar"): "mis_retenciones.ConsultaMisRetencionesRequest",
    ("mis_retenciones_iva_simple", "consultar"): "mis_retenciones_iva_simple.ConsultaMisRetencionesIvaSimpleRequest",
    ("moa", "consultar"): "moa.ConsultaMOARequest",
    ("pago_devoluciones", "consultar"): "pago_devoluciones.ConsultaPagoDevolucionesRequest",
    ("portal_iva", "descargar"): "portal_iva.ConsultaPortalIvaRequest",
    ("rcel", "descargar"): "rcel.ConsultaRCELRequest",
    ("retper_iibb_agip", "consultar"): "retper_iibb_agip.ConsultaRetPerIIBBAGIPRequest",
    ("retper_iibb_misiones", "consultar"): "retper_iibb_misiones.ConsultaRetPerIIBBMisionesRequest",
    ("sct", "consultar"): "sct.ConsultaSCTRequest",
    ("sifere", "consultar"): "sifere.ConsultaSIFERERequest",
    ("siper", "consultar"): "siper.ConsultaSIPERRequest",
    ("srt", "consultar_alicuotas"): "srt.ConsultaSRTAlicuotasRequest",
    ("vep_archivo", "generar"): "vep.VEPArchivoRequest",
    ("vep_ccma", "generar"): "vep_ccma.VEPCCMARequest",
}


_HISTORICAL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "representado_cuit": ("cuit_representado", "representado_cuit"),
    "representado_nombre": ("representado_nombre", "denominacion", "nombre_rcel"),
    "fecha_desde": ("fecha_desde", "desde"),
    "fecha_hasta": ("fecha_hasta", "hasta"),
    "periodo_desde": ("periodo_desde", "periodo"),
    "periodo_hasta": ("periodo_hasta", "periodo"),
    "subir": ("subir", "carga_minio", "minio_upload", "archivo_historico_minio"),
    "subir_archivo": ("subir_archivo", "carga_minio", "minio_upload"),
    "subir_archivos": ("subir_archivos", "carga_minio", "minio_upload"),
    "subir_csv": ("subir_csv", "carga_minio", "minio_upload"),
    "subir_pdf": ("subir_pdf", "pdf", "minio_upload"),
    "incluir_json": ("incluir_json", "carga_json"),
    "incluir_pdf": ("incluir_pdf", "pdf"),
    "incluir_movimientos": ("incluir_movimientos", "movimientos"),
    "emitidos": ("emitidos", "descarga_emitidos"),
    "recibidos": ("recibidos", "descarga_recibidos"),
    "descarga_ventas": ("descarga_ventas", "descarga_csv_ventas"),
    "descarga_compras": ("descarga_compras", "descarga_csv_compras"),
}


def _historical_example_values(bot: str, operation: str) -> dict[str, Any]:
    """Traduce los ejemplos V1/V2 al vocabulario del envelope V3."""
    schema_name = _HISTORICAL_SCHEMA_BY_OPERATION.get((bot, operation))
    if schema_name is None:
        return {}
    properties = _v1_request_schemas()[schema_name]["schema"]["properties"]

    values: dict[str, Any] = {}
    for canonical_name in _fields_for_operation(bot, operation):
        candidates = (canonical_name,) + _HISTORICAL_FIELD_ALIASES.get(
            canonical_name, ()
        )
        for historical_name in candidates:
            property_schema = properties.get(historical_name)
            if property_schema is not None and "example" in property_schema:
                values[canonical_name] = property_schema["example"]
                break

    if bot == "mis_comprobantes" and operation in {"consulta", "consultar", "solicitar", "historial"}:
        values.update({
            "clave_encriptada": "BASE64_RSA_OAEP_CIPHERTEXT",
            "desde": "01/01/2024",
            "hasta": "31/12/2024",
            "cuit_inicio_sesion": "20123456786",
            "representado_nombre": "Empresa Ejemplo S.A.",
            "representado_cuit": "30876543210",
            "contrasena": "mi_contraseña_secreta",
            "descarga_emitidos": True,
            "descarga_recibidos": False,
            "emitidos": True,
            "recibidos": False,
            "puntos_venta_emitidos": ["1", "002", "00003"],
            "puntos_venta_recibidos": ["1", "002", "00003"],
            "carga_minio": True,
            "carga_json": False,
            "timeout_mc": 30,
            "proxy_request": False,
        })
        if operation == "solicitar":
            for name in ("descarga_emitidos", "descarga_recibidos", "carga_minio", "carga_json", "timeout_mc"):
                values.pop(name, None)
        elif operation == "historial":
            values.pop("timeout_mc", None)

    context = next(
        (
            properties[name]["example"]
            for name in ("cuit_representante", "cuit_login", "cuit_inicio_sesion")
            if name in properties and "example" in properties[name]
        ),
        None,
    )
    secret = next(
        (
            properties[name]["example"]
            for name in ("clave_representante", "clave", "contrasena")
            if name in properties and "example" in properties[name]
        ),
        None,
    )
    values["credentials"] = (
        {
            "cuit_representante": context or "20123456786",
            "clave": secret or "clave_fiscal",
        }
        if context is not None or secret is not None
        else {}
    )
    return values


_FIELD_DESCRIPTIONS: dict[str, str] = {
    "archivo_historico_minio": "Solicita cargar el archivo histórico de Aportes descargado en el almacenamiento de la API; si se produce, queda disponible en `files[]`.",
    "bienes_usados": "Indica si la declaración de IVA incluye operaciones con bienes usados.",
    "carga_json": "Alias histórico para solicitar que los datos del resultado se incluyan en formato JSON.",
    "carga_minio": "Solicita guardar en el almacenamiento de la API los archivos de resultado de esta operación; los archivos disponibles se informan en `files[]`.",
    "categorias_minio": "Alias histórico de carga de capturas SIPER; cuando se solicitan, las capturas de categorías quedan disponibles en `files[]`.",
    "clave": "Clave fiscal de la cuenta que se autentica. Use un valor real solo en la solicitud protegida y no en ejemplos compartidos.",
    "clave_encriptada": "Clave fiscal cifrada con la clave pública de la API; use el mecanismo de cifrado documentado para obtener el valor.",
    "clave_representante": "Clave fiscal del representante que inicia sesión. Use un valor real solo en la solicitud protegida y no en ejemplos compartidos.",
    "compras_txt_key": "Clave object_key de un archivo TXT de comprobantes de compras cargado previamente en el bucket uploads.",
    "compras_alicuotas_txt_key": "Clave object_key de un archivo TXT de alícuotas de compras cargado previamente en el bucket uploads.",
    "contrasena": "Clave fiscal de la cuenta que inicia sesión. Use un valor real solo en la solicitud protegida y no en ejemplos compartidos.",
    "csv": "Solicita generar el resultado en formato CSV.",
    "cuit": "CUIT/CUIL del contribuyente que se consulta.",
    "cuit_inicio_sesion": "CUIT/CUIL de la cuenta representante que inicia sesión en el servicio fiscal.",
    "cuit_login": "CUIT/CUIL de la cuenta que inicia sesión en el servicio fiscal.",
    "cuit_representado": "CUIT/CUIL del contribuyente cuyos datos se consultan o modifican.",
    "cuit_representante": "CUIT/CUIL de la cuenta representante que inicia sesión en el servicio fiscal.",
    "cuits": "Lista de CUIT/CUIL de contribuyentes para la consulta masiva.",
    "cuits_consulta": "Lista de CUIT/CUIL de contribuyentes cuyas alícuotas se consultan.",
    "ddjj_pendientes_csv_minio": "Solicita cargar el reporte CSV de declaraciones juradas pendientes en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "ddjj_pendientes_excel_minio": "Solicita cargar el reporte Excel de declaraciones juradas pendientes en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "ddjj_pendientes_pdf_minio": "Solicita cargar el reporte PDF de declaraciones juradas pendientes en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "denominacion": "Nombre o razón social del contribuyente representado, tal como se identifica en el organismo.",
    "descarga_compras": "Solicita descargar el libro o los comprobantes correspondientes a compras.",
    "descarga_csv_compras": "Solicita generar y cargar el archivo CSV de compras; si se genera, queda disponible en `files[]`.",
    "descarga_csv_ventas": "Solicita generar y cargar el archivo CSV de ventas; si se genera, queda disponible en `files[]`.",
    "descarga_emitidos": "Solicita consultar o descargar los comprobantes emitidos por el contribuyente.",
    "descarga_recibidos": "Solicita consultar o descargar los comprobantes recibidos por el contribuyente.",
    "descarga_ventas": "Solicita descargar el libro o los comprobantes correspondientes a ventas.",
    "descartar_borrador": "Al finalizar correctamente la operación, solicita descartar el borrador de la declaración fiscal.",
    "desde": "Límite inicial del intervalo consultado; se interpreta como fecha o período según la operación.",
    "despachos": "Lista de identificadores de destinaciones o despachos aduaneros que se consultarán.",
    "detalle_minio": "Alias histórico de carga de capturas SIPER; cuando se solicita la vista de detalle, su captura queda disponible en `files[]`.",
    "deudas_csv_minio": "Solicita cargar el reporte CSV de deudas en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "deudas_excel_minio": "Solicita cargar el reporte Excel de deudas en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "deudas_pdf_minio": "Solicita cargar el reporte PDF de deudas en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "eliminar_descargas": "Campo histórico aceptado por compatibilidad. La API V3 elimina siempre los archivos temporales y no usa este valor para conservarlos.",
    "emitidos": "Incluye en la consulta los comprobantes emitidos por el contribuyente.",
    "excel": "Solicita generar el resultado en formato Excel.",
    "exportar_para_aplicativo": "Solicita exportar las retenciones en un formato preparado para importar en un aplicativo fiscal.",
    "fecha_desde": "Fecha inicial del intervalo de consulta.",
    "fecha_hasta": "Fecha final del intervalo de consulta.",
    "filtro_impuestos": "Lista opcional de filtros de obligaciones impositivas; cada elemento puede incluir período, impuesto, concepto, subconcepto y categoría.",
    "filtro_intereses": "Lista opcional de filtros de intereses; cada elemento puede incluir período, impuesto, concepto y subconcepto.",
    "generar_volante": "Solicita generar el volante electrónico de pago para las obligaciones seleccionadas.",
    "hasta": "Límite final del intervalo consultado; se interpreta como fecha o período según la operación.",
    "archivo_key": "Clave object_key del archivo TXT de VEP cargado previamente en el bucket uploads.",
    "liv_cbte_key": "Clave object_key del archivo TXT de comprobantes de ventas cargado previamente en el bucket uploads.",
    "liv_alicuota_key": "Clave object_key del archivo TXT de alícuotas de ventas cargado previamente en el bucket uploads.",
    "lic_cbte_key": "Clave object_key del archivo TXT de comprobantes de compras cargado previamente en el bucket uploads.",
    "lic_alicuota_key": "Clave object_key del archivo TXT de alícuotas de compras cargado previamente en el bucket uploads.",
    "csv_cf_key": "Clave object_key del archivo CSV de crédito fiscal cargado previamente en el bucket uploads.",
    "csv_cf_restitucion_key": "Clave object_key del archivo CSV de restitución de crédito fiscal cargado previamente en el bucket uploads.",
    "csv_df_key": "Clave object_key del archivo CSV de débito fiscal cargado previamente en el bucket uploads.",
    "csv_df_restitucion_key": "Clave object_key del archivo CSV de restitución de débito fiscal cargado previamente en el bucket uploads.",
    "importacion_definitiva_bienes": "Indica si la declaración incluye importaciones definitivas de bienes.",
    "importacion_servicios": "Indica si la declaración incluye importaciones de servicios.",
    "impuestos": "Lista de códigos de impuestos que se incluirán en la consulta.",
    "incluir_json": "Solicita incluir los datos estructurados del resultado en formato JSON.",
    "jurisdicciones": "Lista de códigos numéricos de jurisdicción que se incluirán en la consulta.",
    "lista_exclusion_situacion": "Lista de estados de planes de pago que se excluirán de la consulta.",
    "medio_pago": "Medio de pago para generar el VEP; se admite `link`, `pago_mis_cuentas`, `internet_banking`, `xn_group` o `qr`.",
    "minio_upload": "Solicita guardar en el almacenamiento de la API los archivos de resultado de esta operación; los archivos disponibles se informan en `files[]`.",
    "movimientos": "Solicita incluir los movimientos de cuenta en el resultado.",
    "compensar": "Reafecta los pagos con saldo a favor de CCMA contra las obligaciones con saldo deudor (mismo impuesto primero, luego otros en orden cronológico).",
    "confirmar": "Confirma (ejecuta) las reafectaciones en ARCA. Con `false` sólo devuelve la propuesta y cancela el formulario sin modificar ARCA.",
    "ninguna_anteriores": "Indica que no corresponde ninguna de las opciones de actividad declaradas en los campos relacionados.",
    "nombre_rcel": "Nombre o razón social del contribuyente para identificar la consulta de comprobantes en línea.",
    "operaciones_ng_o_e": "Indica si se realizaron operaciones no gravadas o exentas en el período declarado.",
    "pdf": "Solicita generar o incluir el resultado en formato PDF.",
    "periodo": "Período fiscal o intervalo de meses solicitado; el formato depende de la operación.",
    "periodo_desde": "Primer período fiscal del intervalo solicitado.",
    "periodo_hasta": "Último período fiscal del intervalo solicitado.",
    "prorrateo_ambos": "Indica que se aplican prorrateo global y asignación directa del crédito fiscal.",
    "prorrateo_asignacion_directa": "Indica que se aplica asignación directa del crédito fiscal.",
    "prorrateo_global": "Indica que se aplica prorrateo global del crédito fiscal.",
    "proxy_request": "Indicador histórico para solicitar el uso de una conexión mediante proxy cuando esté habilitada para la cuenta.",
    "puntos_venta_emitidos": "Lista de puntos de venta cuyos comprobantes emitidos se consultarán.",
    "puntos_venta_recibidos": "Lista de puntos de venta cuyos comprobantes recibidos se consultarán.",
    "pv": "Activa la modalidad alternativa de consulta disponible para esta operación de AGIP.",
    "recibidos": "Incluye en la consulta los comprobantes recibidos por el contribuyente.",
    "regimen_turiva": "Indica si la declaración corresponde al régimen de turismo y devolución de IVA.",
    "representado_cuit": "CUIT/CUIL del contribuyente representado cuyos datos se consultan o modifican.",
    "representado_nombre": "Nombre o razón social del contribuyente representado.",
    "rol": "Rol con el que se realiza la consulta de operaciones de comercio exterior.",
    "seleccionar_impuestos": "Incluye las obligaciones impositivas en la selección para generar el VEP.",
    "seleccionar_intereses": "Incluye los intereses en la selección para generar el VEP.",
    "subir_csv": "Solicita cargar el archivo CSV de resultado en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "subir_archivos": "Solicita guardar en el almacenamiento de la API los archivos de resultado de esta operación; los archivos disponibles se informan en `files[]`.",
    "timeout_mc": "Tiempo máximo de espera, en segundos, para obtener el archivo CSV de comprobantes.",
    "tipo_agente": "Tipo de agente con el que se realiza la consulta de operaciones de comercio exterior.",
    "usuario": "Nombre de usuario de la cuenta del organismo que inicia sesión.",
    "vencimientos_csv_minio": "Solicita cargar el reporte CSV de vencimientos en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "vencimientos_excel_minio": "Solicita cargar el reporte Excel de vencimientos en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "vencimientos_pdf_minio": "Solicita cargar el reporte PDF de vencimientos en el almacenamiento de la API; si se genera, queda disponible en `files[]`.",
    "ventas_txt_key": "Clave object_key de un archivo TXT de comprobantes de ventas cargado previamente en el bucket uploads.",
    "ventas_alicuotas_txt_key": "Clave object_key de un archivo TXT de alícuotas de ventas cargado previamente en el bucket uploads.",
}

_FIELD_TITLES: dict[str, str] = {
    "archivo_historico_minio": "Cargar archivo histórico de Aportes",
    "carga_minio": "Cargar archivos de resultado",
    "minio_upload": "Cargar archivos de resultado",
    "detalle_minio": "Cargar captura del detalle SIPER",
    "categorias_minio": "Cargar captura de categorías SIPER",
    "vencimientos_excel_minio": "Cargar reporte Excel de vencimientos",
    "vencimientos_csv_minio": "Cargar reporte CSV de vencimientos",
    "vencimientos_pdf_minio": "Cargar reporte PDF de vencimientos",
    "deudas_excel_minio": "Cargar reporte Excel de deudas",
    "deudas_csv_minio": "Cargar reporte CSV de deudas",
    "deudas_pdf_minio": "Cargar reporte PDF de deudas",
    "ddjj_pendientes_excel_minio": "Cargar reporte Excel de DDJJ pendientes",
    "ddjj_pendientes_csv_minio": "Cargar reporte CSV de DDJJ pendientes",
    "ddjj_pendientes_pdf_minio": "Cargar reporte PDF de DDJJ pendientes",
}

_FIELD_EXAMPLES: dict[str, Any] = {
    "clave": "CLAVE_FISCAL_DE_EJEMPLO",
    "clave_encriptada": "BASE64_RSA_OAEP_CIPHERTEXT",
    "clave_representante": "CLAVE_FISCAL_DE_EJEMPLO",
    "contrasena": "CLAVE_FISCAL_DE_EJEMPLO",
    "cuit": "20123456786",
    "cuit_inicio_sesion": "20123456786",
    "cuit_login": "20123456786",
    "cuit_representado": "20123456786",
    "cuit_representante": "20123456786",
    "representado_cuit": "20123456786",
    "cuits": ["20123456786", "27249279191"],
    "cuits_consulta": ["20123456786", "27249279191"],
    "compras_txt_key": "550e8400-e29b-41d4-a716-446655440000/portal_iva/20123456786/compras.txt",
    "compras_alicuotas_txt_key": "550e8400-e29b-41d4-a716-446655440000/portal_iva/20123456786/compras-alicuotas.txt",
    "ventas_txt_key": "550e8400-e29b-41d4-a716-446655440000/portal_iva/20123456786/ventas.txt",
    "ventas_alicuotas_txt_key": "550e8400-e29b-41d4-a716-446655440000/portal_iva/20123456786/ventas-alicuotas.txt",
    "archivo_key": "550e8400-e29b-41d4-a716-446655440000/vep_archivo/20123456786/vep.txt",
    "liv_cbte_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_cbte.txt",
    "liv_alicuota_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/liv_alicuota.txt",
    "lic_cbte_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/lic_cbte.txt",
    "lic_alicuota_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/lic_alicuota.txt",
    "csv_cf_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/cf.csv",
    "csv_cf_restitucion_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/cf_restitucion.csv",
    "csv_df_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/df.csv",
    "csv_df_restitucion_key": "550e8400-e29b-41d4-a716-446655440000/carga_portal_iva/20123456786/df_restitucion.csv",
    "denominacion": "Empresa Ejemplo S.A.",
    "representado_nombre": "Empresa Ejemplo S.A.",
    "nombre_rcel": "Empresa Ejemplo S.A.",
    "usuario": "usuario@ejemplo.com",
    "fecha_desde": "01/08/2026",
    "fecha_hasta": "31/08/2026",
    "desde": "01/08/2026",
    "hasta": "31/08/2026",
    "periodo": "202608",
    "periodo_desde": "202608",
    "periodo_hasta": "202608",
    "medio_pago": "internet_banking",
    "proxy_request": False,
    "despachos": ["23-12345-1"],
    "filtro_impuestos": [{"periodo": "04/2024", "impuesto": "011", "concepto": "019"}],
    "filtro_intereses": [{"periodo": "04/2024", "impuesto": "011", "concepto": "019"}],
    "impuestos": ["217"],
    "jurisdicciones": [901, 902],
    "lista_exclusion_situacion": ["Vigente", "Plan Cancelado"],
    "puntos_venta_emitidos": ["0001", "0002"],
    "puntos_venta_recibidos": ["0001", "0002"],
    "timeout_mc": 30,
}


def _field_type_label(annotation: Any) -> str:
    """Return a concise public type label without changing validation."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        choices = ", ".join(str(value) for value in args)
        return f"cadena (valores admitidos: {choices})"
    if origin is list:
        item_type = args[0] if args else Any
        return f"lista de {_field_type_label(item_type)}"
    if origin is dict:
        return "objeto JSON"
    if args and type(None) in args:
        members = [arg for arg in args if arg is not type(None)]
        return f"{_field_type_label(members[0]) if members else 'valor'} o null"
    if annotation is str:
        return "cadena"
    if annotation is bool:
        return "booleano"
    if annotation is int:
        return "entero"
    if annotation is float:
        return "número"
    if annotation is Any:
        return "valor JSON"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "objeto JSON"
    return getattr(annotation, "__name__", str(annotation))


def _field_example(field_name: str, bot: str, operation: str, default: Any) -> Any:
    """Return a type-correct, non-sensitive example for one public property."""
    if field_name in {"desde", "hasta"} and bot in {
        "retper_iibb_agip", "retper_iibb_misiones"
    }:
        return "202601" if field_name == "desde" else "202608"
    if field_name == "periodo" and bot == "consulta_pagos_vep":
        return "72"
    if field_name == "periodo_desde" and bot == "ccma":
        return "01/2026"
    if field_name == "periodo_hasta" and bot == "ccma":
        return "02/2026"
    if field_name in _FIELD_EXAMPLES:
        return _FIELD_EXAMPLES[field_name]
    if isinstance(default, bool):
        return default
    if isinstance(default, (str, int, float)):
        return default
    if field_name in _FIELD_TITLES:
        return False if default is None else default
    if field_name in {"descarga_emitidos", "emitidos", "descarga_ventas", "excel"}:
        return True
    if field_name in {"descarga_recibidos", "recibidos"}:
        return False
    if field_name in {"carga_b64", "csv", "pdf", "pv", "movimientos"}:
        return False
    if field_name in {
        "bienes_usados", "carga_json", "carga_minio", "descarga_compras",
        "descarga_csv_compras", "descarga_csv_ventas", "descartar_borrador",
        "eliminar_descargas", "exportar_para_aplicativo", "generar_volante",
        "importacion_definitiva_bienes", "importacion_servicios", "incluir_json",
        "ninguna_anteriores", "operaciones_ng_o_e", "prorrateo_ambos",
        "prorrateo_asignacion_directa", "prorrateo_global", "recibidos",
        "regimen_turiva", "seleccionar_impuestos", "seleccionar_intereses",
        "subir_csv", "minio_upload", "archivo_historico_minio",
        "detalle_minio", "categorias_minio", "vencimientos_excel_minio",
        "vencimientos_csv_minio", "vencimientos_pdf_minio", "deudas_excel_minio",
        "deudas_csv_minio", "deudas_pdf_minio", "ddjj_pendientes_excel_minio",
        "ddjj_pendientes_csv_minio", "ddjj_pendientes_pdf_minio",
    }:
        return False
    if field_name in {"puntos_venta_emitidos", "puntos_venta_recibidos"}:
        return ["0001"]
    if field_name in {"cuits", "cuits_consulta"}:
        return ["20123456786"]
    if field_name in {"filtro_impuestos", "filtro_intereses"}:
        return [{"periodo": "04/2024", "impuesto": "011", "concepto": "019"}]
    if field_name in {"despachos", "impuestos", "lista_exclusion_situacion"}:
        return ["ejemplo"]
    if field_name == "jurisdicciones":
        return [901]
    if field_name == "timeout_mc":
        return 30
    raise KeyError(f"Falta ejemplo documentado para {bot}/{operation}.{field_name}")


def _field_description(
    field_name: str, bot: str, operation: str, annotation: Any, field_info: Any
) -> str:
    """Describe meaning and the actual public type/default of one request field."""
    meaning = _FIELD_DESCRIPTIONS.get(field_name)
    if meaning is None:
        raise KeyError(f"Falta descripción documentada para {bot}/{operation}.{field_name}")

    if field_name in {"desde", "hasta"}:
        if bot in {"retper_iibb_agip", "retper_iibb_misiones"}:
            meaning = f"{meaning} Formato: período `AAAAMM`."
        else:
            meaning = f"{meaning} Formato: fecha `DD/MM/AAAA`."
    elif field_name in {"fecha_desde", "fecha_hasta"}:
        meaning = f"{meaning} Formato: fecha `DD/MM/AAAA`."
    elif field_name == "periodo" and bot == "consulta_pagos_vep":
        meaning = f"{meaning} Formato: cantidad de meses como cadena de 1 a 3 dígitos; mínimo 1."
    elif field_name == "periodo_desde" and bot == "ccma":
        meaning = f"{meaning} Formato: `MM/AAAA`."
    elif field_name in {"periodo", "periodo_desde", "periodo_hasta"}:
        meaning = f"{meaning} Formato recomendado: `AAAAMM` (año y mes)."
    elif "cuit" in field_name.lower() or field_name in {"cuits", "cuits_consulta"}:
        meaning = f"{meaning} Formato recomendado: CUIT/CUIL de 11 dígitos, sin guiones (por ejemplo `20123456786`)."

    required = field_info.default is PydanticUndefined
    if required:
        requirement = "Requerido."
    else:
        default_value = json.dumps(field_info.default, ensure_ascii=False, separators=(",", ":"))
        requirement = f"Opcional; valor predeterminado: `{default_value}`."
    example = _field_example(field_name, bot, operation, field_info.default)
    example_value = json.dumps(example, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{meaning} Tipo: {_field_type_label(annotation)}. {requirement} "
        f"Ejemplo: `{example_value}`."
    )


def _apply_field_documentation(
    field_name: str, bot: str, operation: str, annotation: Any, field_info: Any
) -> Any:
    """Attach documentation-only metadata to a copied Pydantic field descriptor."""
    if field_name in _FIELD_TITLES:
        field_info.title = _FIELD_TITLES[field_name]
        field_info._attributes_set["title"] = field_info.title
    field_info.description = _field_description(
        field_name, bot, operation, annotation, field_info
    )
    field_info._attributes_set["description"] = field_info.description
    field_info.examples = [
        _field_example(field_name, bot, operation, field_info.default)
    ]
    field_info._attributes_set["examples"] = field_info.examples
    return field_info


def _fields_for_operation(bot: str, operation: str) -> dict[str, tuple[Any, Any]]:
    historical_names = _V2_OPERATION_FIELD_NAMES.get((bot, operation))
    if historical_names is not None:
        required = _V2_REQUIRED_FIELDS.get((bot, operation), frozenset())
        historical_fields: dict[str, tuple[Any, Any]] = {}
        for name in historical_names:
            descriptor = _V2_COMPAT_FIELDS.get(name) or _FAMILY_FIELDS.get(bot, {}).get(name)
            if descriptor is None:
                raise RuntimeError(f"descriptor ausente para {bot}/{operation}.{name}")
            annotation, field_info = descriptor
            field_info = deepcopy(field_info)
            if name in required:
                field_info.default = PydanticUndefined
                field_info._attributes_set.pop("default", None)
                if annotation == str | None:
                    annotation = str
                elif annotation == bool | None:
                    annotation = bool
                elif annotation == int | None:
                    annotation = int
            elif field_info.default is None and annotation in {str, bool, int, list[str]}:
                annotation = annotation | None
            if bot == "mis_comprobantes" and name == "carga_minio":
                field_info.default = operation == "historial"
                field_info._attributes_set["default"] = operation == "historial"
            if name in _V2_OPERATION_DEFAULTS.get((bot, operation), {}):
                default = _V2_OPERATION_DEFAULTS[(bot, operation)][name]
                field_info.default = default
                field_info._attributes_set["default"] = default
                if annotation == str | None and isinstance(default, str):
                    annotation = str
            if bot == "sct" and operation == "compensaciones" and name == "pdf":
                annotation = bool
                field_info.default = False
                field_info._attributes_set["default"] = False
            if bot == "moa" and name in {"tipo_agente", "rol"}:
                annotation = str
            if bot == "vep_ccma" and name == "medio_pago":
                annotation = str
            if bot == "consulta_pagos_vep" and name == "periodo":
                annotation = str
            if name == "minio_upload" and (bot, operation) in {
                ("moa", "consultar"),
                ("consulta_pagos_vep", "consultar"),
            }:
                annotation = bool
            if bot == "mis_comprobantes" and operation == "historial" and name in {
                "descarga_emitidos",
                "descarga_recibidos",
            }:
                field_info.validation_alias = None
                field_info._attributes_set.pop("validation_alias", None)
            _apply_field_documentation(name, bot, operation, annotation, field_info)
            historical_fields[name] = (annotation, field_info)
        return historical_fields

    fields = dict(_FAMILY_FIELDS.get(bot, {}))
    names = _OPERATION_FIELD_NAMES.get((bot, operation))
    if names is not None:
        fields = {name: fields[name] for name in names if name in fields}
    for name, (annotation, field_info) in fields.items():
        _apply_field_documentation(name, bot, operation, annotation, field_info)
    return fields


@lru_cache(maxsize=None)
def public_bot_body_model(bot: str, operation: str) -> type[BaseModel]:
    """Crea una clase Pydantic estable por pareja bot/operación para OpenAPI."""
    fields = _fields_for_operation(bot, operation)
    fields["credentials"] = (
        dict[str, Any] | None,
        Field(default=None, description="Credenciales fiscales efímeras. Nunca se persisten."),
    )
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}".split("_")) + "BotBody"
    return create_model(model_name, __base__=CompatBodyBase, **fields)


@lru_cache(maxsize=None)
def public_bot_compat_body_model(bot: str, operation: str) -> type[BaseModel]:
    """Clase de borde con el cuerpo plano que aceptaban las rutas V2."""
    fields = _fields_for_operation(bot, operation)
    model_name = "".join(
        part.capitalize() for part in f"{bot}_{operation}_v2_compat".split("_")
    ) + "Body"
    return create_model(model_name, __base__=CompatBodyBase, **fields)


def _example_value(field_name: str, bot: str, operation: str, default: Any) -> Any:
    """Valor representativo y seguro para cada propiedad pública."""
    if default is PydanticUndefined:
        default = None
    if field_name == "credentials":
        return {
            "cuit_representante": "20123456786",
            "clave": "clave_fiscal",
        }
    if field_name in {"clave", "clave_representante", "contrasena"}:
        return "clave_fiscal"
    if field_name == "clave_encriptada":
        return "BASE64_RSA_OAEP_CIPHERTEXT"
    if field_name in {"cuit_representante", "cuit_representado", "cuit_inicio_sesion", "cuit_login"}:
        return "20123456786"
    if field_name in {"desde", "hasta"}:
        return "01/08/2026"
    if field_name == "timeout_mc":
        return 30
    if field_name == "proxy_request":
        return False
    if field_name in {
        "movimientos", "pdf", "descartar_borrador", "eliminar_descargas",
        "detalle_minio", "categorias_minio", "archivo_historico_minio",
        "exportar_para_aplicativo", "seleccionar_impuestos",
        "seleccionar_intereses", "generar_volante",
    }:
        return False if default is None else default
    if field_name == "puntos_venta":
        return [1, 2]
    if field_name == "usuario":
        return "usuario@ejemplo.com"
    if field_name in {"cuit", "representado_cuit"}:
        return "20123456786"
    if field_name in {"cuits", "cuits_consulta"}:
        return ["20123456786", "27249279191"]
    if field_name.startswith("fecha_"):
        return "01/08/2026"
    if field_name == "periodo_desde":
        return "01/2026" if bot == "ccma" else "202608"
    if field_name == "periodo_hasta":
        return "02/2026" if bot == "ccma" else "202609"
    if field_name == "periodo":
        return "8" if bot == "consulta_pagos_vep" else "202608"
    if field_name == "archivos":
        if bot == "controladores_fiscales":
            return ["<usuario>/controladores_fiscales/20123456786/F8010.001.pem"]
        return ["ventas-2026-08.csv", "compras-2026-08.csv"]
    if field_name == "despachos":
        return ["23-12345-1", "23-12346-8"]
    if field_name.startswith("puntos_venta"):
        return [1, 2]
    if field_name == "jurisdicciones":
        return [901, 902]
    if field_name in {"situacion_excluyente"}:
        return ["moroso"]
    if field_name == "impuestos":
        return ["217"] if bot == "mis_retenciones" else ["216", "217"]
    if field_name == "lista_exclusion_situacion":
        return ["Vigente", "Plan Cancelado", "Plan Caduco"]
    if field_name == "tipos":
        return ["Retencion", "Percepcion"]
    if field_name == "secciones":
        return ["vencimientos", "deudas"]
    if field_name == "formatos":
        return ["xlsx", "csv"]
    if field_name == "medio_pago":
        return "internet_banking"
    if field_name == "metodo":
        return "url"
    if field_name in {"emitidos", "recibidos"}:
        return True
    if field_name in {"excel", "csv", "pdf"}:
        return field_name == "excel"
    if field_name in {"descarga_ventas", "descarga_compras"}:
        return field_name == "descarga_ventas"
    if field_name == "carga_b64":
        return False
    if field_name.endswith("_b64"):
        if field_name == "archivo_b64" and bot == "vep_archivo":
            return (
                "MDEyMDEyMzQ1Njc4OTIwMDAxMDAxMDAwMDMwMDMwMDAxCjAyPFZFUCBucm9Gb3JtdWxhcmlvPSIyMDAwMSIgY29kVGlwb1BhZ289IjAyMCIgY29udHJpYnV5ZW50ZUNVSVQ9IjIwMTIzNDU2Nzg5IiBjb25jZXB0bz0iMDAzIiBzdWJDb25jZXB0bz0iMDAzIiBwZXJpb2RvRmlzY2FsPSIyMDI2MDgiIGltcG9ydGU9IjEwMC4wMCI+IDxPYmxpZ2FjaW9uIGltcHVlc3RvPSIwMDMiIGltcG9ydGU9IjEwMC4wMCIvPjwvVkVQPgo="
            )
        return "SGVsbG8gV29ybGQ="
    if field_name in {"ventas_txt", "compras_txt"}:
        return "contenido-de-archivo-de-ejemplo"
    if field_name.endswith("_key"):
        return _FIELD_EXAMPLES.get(
            field_name,
            "550e8400-e29b-41d4-a716-446655440000/portal_iva/20123456786/archivo.txt",
        )
    if field_name in {"representado_nombre", "denominacion"}:
        return "Empresa de ejemplo"
    if field_name == "pv":
        return False
    if isinstance(default, bool):
        return default
    if field_name == "archivo_nombre":
        return "vep_entrada.txt"
    if field_name in {"filtro_impuestos", "filtro_intereses"}:
        return [{"periodo": "04/2024", "impuesto": "011", "concepto": "019"}]
    if isinstance(default, bool):
        return default
    if default is not None:
        return default
    return "Empresa Ejemplo SA"


@lru_cache(maxsize=None)
def public_bot_body_schema(bot: str, operation: str) -> dict[str, Any]:
    """Esquema inline para aliases, con ejemplo seguro visible en Swagger."""
    model = public_bot_body_model(bot, operation)
    schema = model.model_json_schema()
    historical = _historical_example_values(bot, operation)
    example = {
        name: historical.get(
            name,
            _example_value(name, bot, operation, field.default),
        )
        for name, field in model.model_fields.items()
    }
    schema["examples"] = [example]
    return schema


@lru_cache(maxsize=None)
def public_bot_payload_schema(bot: str, operation: str) -> dict[str, Any]:
    """Schema del payload anidado, sin duplicar credentials del envelope."""
    schema = deepcopy(public_bot_body_schema(bot, operation))
    schema["properties"].pop("credentials", None)
    schema["examples"][0].pop("credentials", None)
    return schema


@lru_cache(maxsize=None)
def public_bot_compat_body_schema(
    bot: str,
    operation: str,
    route_path: str | None = None,
) -> dict[str, Any]:
    """Schema plano individual para los campos históricos de la operación."""
    model = public_bot_compat_body_model(bot, operation)
    schema = model.model_json_schema()
    historical = _historical_example_values(bot, operation)
    schema["examples"] = [
        {
            name: historical.get(name, _example_value(name, bot, operation, field.default))
            for name, field in model.model_fields.items()
        }
    ]
    return schema


def install_v1_openapi_patch(app: Any) -> Any:
    """Compatibilidad histórica; los modelos por operación gobiernan OpenAPI."""
    return app


__all__ = [
    "CompatBodyBase",
    "public_bot_body_model",
    "public_bot_compat_body_model",
    "public_bot_body_schema",
    "public_bot_compat_body_schema",
    "public_bot_payload_schema",
    "V1_SCHEMA_BY_ALIAS",
    "install_v1_openapi_patch",
]
