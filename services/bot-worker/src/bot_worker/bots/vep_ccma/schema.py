"""Esquemas de entrada del plugin ``vep_ccma``.

Paridad con V1 (``app/bot/vep_ccma_bot.py``: ``bot_vep_ccma``): los medios
de pago son los de V1 (incluido ``qr``), y el bot acepta los filtros de capital
e intereses, los flags de seleccion y ``subir_a_minio``/``minio_upload`` y
``proxy_request`` con los mismos nombres que el contrato compartido. Sin la
clave del representante en el schema: viaja en el sobre sellado.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
#: Medios de pago habilitados en V1 (``medio_pagos_habilitados``).
MEDIOS_PAGO = ("link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr")



def _unificar_cuit(datos: dict[str, Any]) -> None:
    """Reduce los alias de CUIT (``representado_cuit``/``cuit``/``cuit_representado``) a uno.

    Si vienen varios con el mismo valor no hay contradiccion y se acepta. Si
    difieren, el pedido es ambiguo y se rechaza.
    """
    alias = ("representado_cuit", "cuit", "cuit_representado")
    presentes = [c for c in alias if datos.get(c) is not None]
    if not presentes:
        return
    valores = {limpiar_cuit(datos[c]) for c in presentes}
    if len(valores) > 1:
        raise ValueError("los alias de CUIT del pedido no coinciden")
    valor = valores.pop()
    for c in presentes:
        datos.pop(c, None)
    datos["representado_cuit"] = valor


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def limpiar_cuit(valor: Any) -> str:
    """Quita separadores comunes y exige 11 digitos (port de V2)."""
    texto = (
        str(valor)
        .replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("/", "")
    )
    if len(texto) != 11 or not texto.isdigit():
        raise ValueError("cuit debe tener 11 digitos")
    return texto


#: Filtro de capital (V1 ``FiltroImpuesto``): claves AND, valor escalar o lista.
FILTRO_IMPUESTO_CLAVES = ("periodo", "impuesto", "concepto", "subconcepto", "categoria")
#: Filtro de intereses (V1 ``FiltroInteres``): sin categoria. V1 acepta ``subcpto``
#: como alias de ``subconcepto`` (``FIELD_MAP_INTERESES``).
FILTRO_INTERES_CLAVES = ("periodo", "impuesto", "concepto", "subconcepto", "subcpto")


#: Campos que V1 recibe por HTTP pero V3 nunca toma del payload: las
#: credenciales y el proxy viajan en el sobre sellado del job.
CAMPOS_SOBRE_SELLADO = (
    "clave_representante",
    "clave",
    "contrasena",
    "clave_encriptada",
    "cuit_representante",
    "proxy_request",
)
#: Flags que V1 trata como booleanos opcionales con default True.
FLAGS_CON_DEFAULT = (
    "seleccionar_impuestos",
    "seleccionar_intereses",
    "generar_volante",
    "minio_upload",
    "subir_a_minio",
    "subir_pdf",
)
#: Nombres equivalentes de la subida del PDF (V1: ``minio_upload``/``subir_a_minio``).
ALIAS_SUBIDA = ("subir_pdf", "subir_a_minio", "minio_upload")


def _filtros(valor: Any, claves_validas: tuple[str, ...], nombre: str) -> list[dict[str, Any]] | None:
    """Normaliza filtros V1 como ``_filtros_a_dicts`` del router.

    Como los modelos pydantic de V1, ignora claves desconocidas y quita solo
    los ``None``: un ``""`` se conserva porque en V1 matchea todas las filas.
    Las entradas que quedan vacias se descartan.
    """
    if valor is None:
        return None
    if not isinstance(valor, list):
        raise ValueError(f"{nombre} debe ser una lista")
    salida: list[dict[str, Any]] = []
    for entrada in valor:
        if entrada is None:
            continue
        if not isinstance(entrada, dict):
            raise ValueError(f"cada elemento de {nombre} debe ser objeto")
        limpio = {
            clave: entrada[clave]
            for clave in claves_validas
            if entrada.get(clave) is not None
        }
        if limpio:
            salida.append(limpio)
    return salida or None


def _unificar_subida(datos: dict[str, Any]) -> None:
    """Reduce ``subir_pdf``/``subir_a_minio``/``minio_upload`` a un solo valor.

    Los tres nombres son el mismo flag en V1 y V3. Si el cliente manda varios
    con valores distintos, es un pedido contradictorio y se rechaza, en vez de
    dejar que gane el orden de los alias.
    """
    presentes = [clave for clave in ALIAS_SUBIDA if clave in datos]
    if not presentes:
        return
    valores = {bool(datos[clave]) for clave in presentes}
    if len(valores) > 1:
        raise ValueError("subir_pdf y minio_upload/subir_a_minio contradictorios")
    valor = valores.pop()
    for clave in ALIAS_SUBIDA:
        datos.pop(clave, None)
    datos["subir_pdf"] = valor


class VepCcmaGenerarInput(_Base):
    """Operacion ``generar``: VEP desde CCMA con volante y QR."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    medio_pago: str = Field(default="internet_banking", min_length=1)
    filtro_impuestos: list[dict[str, Any]] | None = None
    filtro_intereses: list[dict[str, Any]] | None = None
    seleccionar_impuestos: bool = True
    seleccionar_intereses: bool = True
    generar_volante: bool = True
    incluir_json: bool = True
    subir_pdf: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            _unificar_cuit(datos)
            if "medio_pago" in datos and datos["medio_pago"] is not None:
                datos["medio_pago"] = str(datos["medio_pago"]).strip().lower()
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            for clave in FLAGS_CON_DEFAULT:
                if clave in datos and datos[clave] is None:
                    # V1 hacia bool(None) == False; aca el null usa el default.
                    del datos[clave]
            _unificar_subida(datos)
            if "filtro_impuestos" in datos:
                datos["filtro_impuestos"] = _filtros(
                    datos["filtro_impuestos"], FILTRO_IMPUESTO_CLAVES, "filtro_impuestos"
                )
            if "filtro_intereses" in datos:
                datos["filtro_intereses"] = _filtros(
                    datos["filtro_intereses"], FILTRO_INTERES_CLAVES, "filtro_intereses"
                )
        return datos

    @model_validator(mode="after")
    def _origen_y_salida(self) -> VepCcmaGenerarInput:
        if self.medio_pago not in MEDIOS_PAGO:
            raise ValueError(f"medio_pago debe ser uno de {list(MEDIOS_PAGO)}")
        if not self.seleccionar_impuestos and not self.seleccionar_intereses:
            raise ValueError("seleccionar al menos impuestos o intereses")
        if not self.incluir_json and not self.subir_pdf:
            raise ValueError("seleccionar al menos una salida (json o pdf)")
        return self


OperacionVepCcma = Literal["generar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "generar": VepCcmaGenerarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``generar``, sin credenciales.

    Publica los nombres del contrato compartido (``minio_upload``) y los
    filtros V1. ``subir_pdf`` y ``subir_a_minio`` siguen siendo aceptados.
    """
    filtro_impuesto = {
        "type": "object",
        "properties": {clave: {"type": "string"} for clave in FILTRO_IMPUESTO_CLAVES},
    }
    filtro_interes = {
        "type": "object",
        "properties": {clave: {"type": "string"} for clave in FILTRO_INTERES_CLAVES},
    }
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["generar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "medio_pago": {"type": "string", "enum": list(MEDIOS_PAGO)},
            "filtro_impuestos": {"type": "array", "items": filtro_impuesto},
            "filtro_intereses": {"type": "array", "items": filtro_interes},
            "seleccionar_impuestos": {"type": "boolean"},
            "seleccionar_intereses": {"type": "boolean"},
            "generar_volante": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
            "subir_pdf": {"type": "boolean"},
            "minio_upload": {"type": "boolean"},
        },
    }
