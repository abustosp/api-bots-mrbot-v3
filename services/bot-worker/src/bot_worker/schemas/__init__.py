"""Worker-local copies of public bot request models and examples.

This package intentionally depends only on Pydantic. It mirrors the central
public contract so the worker can publish useful OpenAPI without importing
``central_api`` or altering the signed assignment envelope.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, create_model
from pydantic_core import PydanticUndefined
from mrbot_contracts.request_fields import V2_OPERATION_FIELDS, V2_REQUIRED_FIELDS


class CompatBodyBase(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        json_schema_extra={
            "description": (
                "Payload plano del bot. Los campos desconocidos se ignoran "
                "para compatibilidad. Los archivos temporales se eliminan "
                "siempre al terminar la operación."
            )
        },
    )


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
    return list[item], Field(... if required else None, **options)


CUIT = r"^\d{11}$"
DATE = r"^\d{2}/\d{2}/\d{4}$"
PERIOD = r"^\d{6}$"

# Independent copy of the central payload contract, grouped by bot family.
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
        "representado_cuit": _string(pattern=CUIT), "periodo": _string(pattern=PERIOD),
        "representado_nombre": _string(max_length=256), "jurisdicciones": _list(item=int),
        "incluir_json": _boolean(default=True), "subir": _boolean(default=True),
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

_OPERATION_FIELD_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("consulta_cuit", "consulta"): ("cuit",),
    ("consulta_cuit", "consultar"): ("cuit",),
    ("consulta_cuit", "consultar_masivo"): ("cuits",),
    ("comprobantes", "solicitar"): (
        "representado_cuit", "fecha_desde", "fecha_hasta", "representado_nombre", "emitidos", "recibidos",
    ),
    ("mis_comprobantes", "solicitar"): (
        "representado_cuit", "fecha_desde", "fecha_hasta", "representado_nombre", "emitidos", "recibidos",
        "puntos_venta_emitidos", "puntos_venta_recibidos",
    ),
    ("portal_iva", "descargar"): (
        "periodo", "representado_cuit", "representado_nombre", "descarga_ventas", "descarga_compras",
        "incluir_json", "subir_csv",
    ),
    ("portal_iva", "importar"): (
        "periodo", "representado_cuit", "representado_nombre", "ventas_txt", "compras_txt",
    ),
}

_V2_COMPAT_FIELDS: dict[str, tuple[Any, Any]] = {
    "cuit_representante": _string(pattern=CUIT),
    "cuit": _string(pattern=CUIT),
    "cuit_representado": _string(pattern=CUIT),
    "cuit_inicio_sesion": _string(pattern=CUIT),
    "cuit_login": _string(pattern=CUIT),
    "clave": _string(max_length=4096),
    "clave_representante": _string(max_length=4096),
    "contrasena": _string(max_length=4096),
    "clave_encriptada": (str | None, Field(default=None, max_length=16384)),
    "desde": _string(), "hasta": _string(),
    "proxy_request": (bool | None, Field(default=None)),
    "movimientos": _boolean(), "pdf": _boolean(),
    "descarga_emitidos": (bool, Field(default=False, validation_alias=AliasChoices("descarga_emitidos", "emitidos"))),
    "descarga_recibidos": (bool, Field(default=False, validation_alias=AliasChoices("descarga_recibidos", "recibidos"))),
    "descarga_csv_ventas": _boolean(), "descarga_csv_compras": _boolean(),
    "carga_minio": _boolean(default=True), "carga_json": _boolean(), "minio_upload": _boolean(default=True),
    "timeout_mc": (int | None, Field(default=None, ge=1, le=86400)),
    "usuario": _string(max_length=256), "denominacion": _string(max_length=256),
    "nombre_rcel": _string(max_length=256), "medio_pago": _string(),
    "archivo_historico_minio": _boolean(default=True),
    "lista_exclusion_situacion": _list(item=str), "detalle_minio": _boolean(),
    "categorias_minio": _boolean(),
    "vencimientos_excel_minio": _boolean(), "vencimientos_csv_minio": _boolean(),
    "vencimientos_pdf_minio": _boolean(), "deudas_excel_minio": _boolean(),
    "deudas_csv_minio": _boolean(), "deudas_pdf_minio": _boolean(),
    "ddjj_pendientes_excel_minio": _boolean(), "ddjj_pendientes_csv_minio": _boolean(),
    "ddjj_pendientes_pdf_minio": _boolean(),
    "filtro_impuestos": _list(item=dict), "filtro_intereses": _list(item=dict),
    "seleccionar_impuestos": _boolean(default=True), "seleccionar_intereses": _boolean(default=True),
    "generar_volante": _boolean(default=True), "representado_cuit": _string(pattern=CUIT),
    "representado_nombre": _string(max_length=256), "puntos_venta_emitidos": _list(item=str),
    "puntos_venta_recibidos": _list(item=str), "periodo": _string(),
    "excel": _boolean(), "csv": _boolean(), "cuits": _list(item=str),
    "despachos": _list(item=str), "tipo_agente": _string(), "rol": _string(),
    "impuestos": _list(item=str), "jurisdicciones": _list(item=int),
    "cuits_consulta": _list(item=str), "periodo_desde": _string(), "periodo_hasta": _string(),
    "operaciones_ng_o_e": _boolean(), "prorrateo_global": _boolean(),
    "prorrateo_asignacion_directa": _boolean(), "prorrateo_ambos": _boolean(),
    "importacion_definitiva_bienes": _boolean(), "importacion_servicios": _boolean(),
    "regimen_turiva": _boolean(), "bienes_usados": _boolean(),
    "ninguna_anteriores": _boolean(default=True), "archivo_nombre": _string(min_length=1, max_length=128),
    "archivo_b64": _string(min_length=1), "incluir_json": _boolean(default=True),
    "incluir_pdf": _boolean(default=True), "subir_pdf": _boolean(default=True),
    "subir_csv": _boolean(default=True), "subir_archivos": _boolean(default=True),
    "ventas_txt": _string(min_length=1, max_length=5_000_000),
    "compras_txt": _string(min_length=1, max_length=5_000_000),
}


def _fields_for_operation(bot: str, operation: str) -> dict[str, tuple[Any, Any]]:
    key = (bot, operation)
    historical_names = V2_OPERATION_FIELDS.get(key)
    if historical_names is not None:
        required = V2_REQUIRED_FIELDS.get(key, frozenset())
        fields: dict[str, tuple[Any, Any]] = {}
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
            fields[name] = (annotation, field_info)
        return fields

    fields = dict(_FAMILY_FIELDS.get(bot, {}))
    names = _OPERATION_FIELD_NAMES.get((bot, operation))
    if names is not None:
        fields = {name: fields[name] for name in names if name in fields}
    return fields


@lru_cache(maxsize=None)
def _compat_model(bot: str, operation: str) -> type[BaseModel]:
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}_v2_compat".split("_")) + "Body"
    return create_model(model_name, __base__=CompatBodyBase, **_fields_for_operation(bot, operation))


_FIELD_DESCRIPTIONS: dict[str, str] = {
    "cuit_representante": "CUIT/CUIL del representante que inicia sesión en ARCA.",
    "cuit_login": "CUIT/CUIL de la cuenta que se autentica en ARCA.",
    "cuit_inicio_sesion": "CUIT/CUIL del representante que inicia sesión.",
    "clave": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "clave_representante": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "contrasena": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "clave_encriptada": "Clave fiscal cifrada usando la clave pública de la API.",
    "cuit_representado": "CUIT/CUIL del contribuyente cuyos datos se consultan o modifican.",
    "representado_cuit": "CUIT/CUIL del contribuyente cuyos datos se consultan o modifican.",
    "representado_nombre": "Nombre o razón social del contribuyente representado.",
    "denominacion": "Denominación o razón social del contribuyente representado.",
    "fecha_desde": "Inicio del período solicitado, en formato DD/MM/AAAA.",
    "fecha_hasta": "Fin del período solicitado, en formato DD/MM/AAAA.",
    "desde": "Inicio del período solicitado, en formato DD/MM/AAAA.",
    "hasta": "Fin del período solicitado, en formato DD/MM/AAAA.",
    "periodo": "Período fiscal solicitado, normalmente AAAAMM.",
    "periodo_desde": "Primer período fiscal del rango, en formato AAAAMM.",
    "periodo_hasta": "Último período fiscal del rango, en formato AAAAMM.",
    "emitidos": "Incluir comprobantes emitidos.", "recibidos": "Incluir comprobantes recibidos.",
    "incluir_json": "Incluir los datos del resultado en formato JSON.",
    "subir_csv": "Solicitar la generación y carga del archivo CSV de resultado.",
    "subir_archivos": "Solicitar la generación y carga de los archivos de resultado.",
    "subir": "Solicitar la carga del archivo de resultado.",
    "proxy_request": "Configuración opcional de proxy para la solicitud.",
    "archivo_nombre": (
        "Nombre del archivo de entrada TXT que el cliente envió. Solo lo usa el "
        "bot para localizar la entrada temporal, no para nombrar archivos subidos."
    ),
}


def _description(name: str) -> str:
    if name in _FIELD_DESCRIPTIONS:
        return _FIELD_DESCRIPTIONS[name]
    if name.endswith("_b64"):
        return "Contenido del archivo codificado en Base64."
    if name.startswith(("incluir_", "descarga_", "subir_")):
        return f"Indica si se debe {name.replace('_', ' ')}."
    return f"Valor de {name.replace('_', ' ')} para esta operación del bot."


def _example_value(name: str, bot: str, operation: str, default: Any) -> Any:
    if default is PydanticUndefined:
        default = None
    if name in {"clave", "clave_representante", "contrasena"}:
        return "clave_fiscal"
    if name == "clave_encriptada":
        return "BASE64_RSA_OAEP_CIPHERTEXT"
    if name in {"cuit_representante", "cuit_representado", "cuit_inicio_sesion", "cuit_login", "cuit", "representado_cuit"}:
        return "20123456789"
    if name in {"desde", "hasta"} or name.startswith("fecha_"):
        return "01/08/2026"
    if name == "timeout_mc":
        return 30
    if name == "proxy_request":
        return False
    if name == "puntos_venta":
        return [1, 2]
    if name == "usuario":
        return "usuario@ejemplo.com"
    if name in {"cuits", "cuits_consulta"}:
        return ["20123456789", "27222222222"]
    if name == "periodo_desde":
        return "01/2026" if bot == "ccma" else "202608"
    if name == "periodo_hasta":
        return "02/2026" if bot == "ccma" else "202609"
    if name == "periodo":
        return "8" if bot == "consulta_pagos_vep" else "202608"
    if name == "archivos":
        return ["ventas-2026-08.csv", "compras-2026-08.csv"]
    if name == "despachos":
        return ["23-12345-1", "23-12346-8"]
    if name.startswith("puntos_venta"):
        return [1, 2]
    if name == "jurisdicciones":
        return [901, 902]
    if name == "situacion_excluyente":
        return ["moroso"]
    if name == "impuestos":
        return ["217"] if bot == "mis_retenciones" else ["216", "217"]
    if name == "lista_exclusion_situacion":
        return ["Vigente", "Plan Cancelado", "Plan Caduco"]
    if name == "tipos":
        return ["Retencion", "Percepcion"]
    if name == "secciones":
        return ["vencimientos", "deudas"]
    if name == "formatos":
        return ["xlsx", "csv"]
    if name == "medio_pago":
        return "internet_banking"
    if name == "metodo":
        return "url"
    if name in {"emitidos", "recibidos"}:
        return True
    if name in {"excel", "csv", "pdf"}:
        return name == "excel"
    if name in {"descarga_ventas", "descarga_compras"}:
        return name == "descarga_ventas"
    if name.endswith("_b64"):
        if name == "archivo_b64" and bot == "vep_archivo":
            return (
                "MDEyMDEyMzQ1Njc4OTIwMDAxMDAxMDAwMDMwMDMwMDAxCjAyPFZFUCBucm9Gb3JtdWxhcmlvPSIyMDAwMSIgY29kVGlwb1BhZ289IjAyMCIgY29udHJpYnV5ZW50ZUNVSVQ9IjIwMTIzNDU2Nzg5IiBjb25jZXB0bz0iMDAzIiBzdWJDb25jZXB0bz0iMDAzIiBwZXJpb2RvRmlzY2FsPSIyMDI2MDgiIGltcG9ydGU9IjEwMC4wMCI+IDxPYmxpZ2FjaW9uIGltcHVlc3RvPSIwMDMiIGltcG9ydGU9IjEwMC4wMCIvPjwvVkVQPgo="
            )
        return "SGVsbG8gV29ybGQ="
    if name in {"ventas_txt", "compras_txt"}:
        return "contenido-de-archivo-de-ejemplo"
    if name in {"representado_nombre", "denominacion"}:
        return "Empresa de ejemplo"
    if isinstance(default, bool):
        return default
    if name == "archivo_nombre":
        return "vep_entrada.txt"
    if name in {"filtro_impuestos", "filtro_intereses"}:
        return [{"periodo": "04/2024", "impuesto": "011", "concepto": "019"}]
    if isinstance(default, bool):
        return default
    if default is not None:
        return default
    return "Empresa Ejemplo SA"


def _example_values(bot: str, operation: str, *, alternate: bool = False,
                    encrypted: bool = False) -> dict[str, Any]:
    compat = _compat_model(bot, operation)
    values = {
        name: _example_value(name, bot, operation, field.default)
        for name, field in compat.model_fields.items()
    }
    if bot == "mis_comprobantes" and operation in {"consulta", "consultar", "solicitar", "historial"}:
        values.update({
            "clave_encriptada": "BASE64_RSA_OAEP_CIPHERTEXT",
            "desde": "01/01/2024",
            "hasta": "31/12/2024",
            "cuit_inicio_sesion": "20123456780",
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
        else:
            values.pop("emitidos", None)
            values.pop("recibidos", None)
        if operation == "historial":
            values.pop("timeout_mc", None)
            values.pop("puntos_venta_emitidos", None)
            values.pop("puntos_venta_recibidos", None)
    if encrypted and "clave_encriptada" in values:
        values["clave_encriptada"] = "BASE64_RSA_OAEP_CIPHERTEXT_DEMO"
    return values


def _examples(bot: str, operation: str) -> list[dict[str, Any]]:
    return [_example_values(bot, operation)]


@lru_cache(maxsize=None)
def get_request_model(bot: str, operation: str) -> type[BaseModel]:
    """Return the independent worker copy of a public bot request model."""
    source_model = _compat_model(bot, operation)
    fields: dict[str, tuple[Any, Any]] = {}
    for name, field in source_model.model_fields.items():
        field_info = deepcopy(field)
        field_info.description = field_info.description or _description(name)
        fields[name] = (field.annotation, field_info)
    model_examples = _examples(bot, operation)
    base = type(
        f"{bot}_{operation}_RequestBase",
        (CompatBodyBase,),
        {
            "model_config": ConfigDict(
                extra="allow",
                str_strip_whitespace=True,
                json_schema_extra={
                    "title": f"{bot.replace('_', ' ').title()} {operation.replace('_', ' ').title()} Request",
                    "description": (
                        f"Cuerpo plano de la operación {bot}/{operation}. "
                        "Incluye los campos de consulta y las credenciales fiscales "
                        "del representante. Los archivos temporales se eliminan "
                        "siempre al terminar la operación."
                    ),
                    "examples": model_examples,
                },
            )
        },
    )
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}_request".split("_"))
    return create_model(model_name, __base__=base, **fields)


def get_openapi_examples(bot: str, operation: str) -> dict[str, dict[str, Any]]:
    """Return the operation's flat examples in FastAPI's OpenAPI format."""
    examples = _examples(bot, operation)
    return {
        "consulta_habitual": {
            "summary": "Ejemplo del esquema histórico",
            "description": "Valores tomados del schema del bot o de sus campos de entrada reales.",
            "value": examples[0],
        }
    }


def get_schema_document(bot: str, operation: str) -> dict[str, Any]:
    """Public, secret-safe schema document for the worker's internal OpenAPI."""
    model = get_request_model(bot, operation)
    return {
        "bot": bot,
        "operation": operation,
        "schema": model.model_json_schema(),
        "openapi_examples": get_openapi_examples(bot, operation),
    }


__all__ = ["get_request_model", "get_openapi_examples", "get_schema_document", "BotSchemaDocument"]


class BotSchemaDocument(BaseModel):
    """Response shape for the worker's public schema-documentation route."""

    model_config = ConfigDict(populate_by_name=True)

    bot: str = Field(..., description="Identificador del bot consultado.")
    operation: str = Field(..., description="Operación canónica del bot.")
    request_schema: dict[str, Any] = Field(
        ...,
        alias="schema",
        description="JSON Schema del body plano, con credenciales documentales.",
    )
    openapi_examples: dict[str, dict[str, Any]] = Field(
        ...,
        description=(
            "Ejemplos documentales en formato FastAPI: nombre, summary, "
            "description y value. Los secretos son placeholders."
        ),
    )
