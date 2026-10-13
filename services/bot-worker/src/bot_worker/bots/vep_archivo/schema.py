"""Esquemas de entrada del plugin ``vep_archivo``.

Paridad con V1 (``app/bot/vep_archivo_bot.py``: ``generar_vep_desde_archivo``):
los medios de pago son los de V1 (incluido ``qr``) y se aceptan los flags
``minio_upload``/``subir_a_minio`` y ``proxy_request`` con los mismos nombres
que el contrato compartido. ``eliminar_descargas`` se acepta por compatibilidad:
el worker ya borra el ``work_dir`` al terminar, asi que no cambia el resultado.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
#: Medios de pago habilitados en V1 (``medio_pagos_habilitados``).
MEDIOS_PAGO = ("link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr")
MAX_REGISTROS_POR_LOTE = 600
MAX_ARCHIVO_BYTES = 7_000_000
#: Campos que V1 recibe por HTTP pero V3 nunca toma del payload.
CAMPOS_SOBRE_SELLADO = ("contrasena", "clave", "clave_representante", "clave_encriptada", "proxy_request")
#: Nombres equivalentes de la subida del PDF (V1: ``minio_upload``/``subir_a_minio``).
ALIAS_SUBIDA = ("subir_pdf", "subir_a_minio", "minio_upload")



def _unificar_cuit(datos: dict[str, Any], alias: tuple[str, ...] = ("representado_cuit", "cuit", "cuit_representado")) -> None:
    """Reduce los alias de CUIT del bot a uno.

    Si vienen varios con el mismo valor no hay contradiccion y se acepta. Si
    difieren, el pedido es ambiguo y se rechaza.
    """
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


def _unificar_subida(datos: dict[str, Any]) -> None:
    """Reduce ``subir_pdf``/``subir_a_minio``/``minio_upload`` a un solo valor.

    Si el cliente manda varios con valores distintos el pedido es
    contradictorio y se rechaza, en vez de dejar que gane el orden.
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


class VepArchivoGenerarInput(_Base):
    """Operacion ``generar``: VEP desde archivo .txt.

    El cliente envía la referencia al TXT previamente cargado, el medio de
    pago y si se sube el PDF. El worker recibe el nombre local de ese archivo.
    El CUIT se toma de la sesión fiscal cuando no se informa. Los archivos
    temporales siempre se eliminan al terminar.

    ``cuit_inicio_sesion`` es el nombre que usa V1 para el CUIT que inicia
    sesion: se acepta como alias de ``representado_cuit``.
    """

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_inicio_sesion"),
        min_length=11,
        max_length=14,
    )
    medio_pago: str = Field(default="internet_banking", min_length=1)
    archivo_path: str = Field(
        min_length=1,
        description="Nombre local descargado desde una referencia object_key.",
    )
    incluir_json: bool = True
    subir_pdf: bool = True
    eliminar_descargas: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            _unificar_cuit(datos, ("representado_cuit", "cuit", "cuit_inicio_sesion"))
            if "medio_pago" in datos and datos["medio_pago"] is not None:
                datos["medio_pago"] = str(datos["medio_pago"]).strip().lower()
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            for clave in (*ALIAS_SUBIDA, "incluir_json", "eliminar_descargas"):
                if clave in datos and datos[clave] is None:
                    del datos[clave]
            _unificar_subida(datos)
        return datos

    @model_validator(mode="after")
    def _archivo_y_medio(self) -> VepArchivoGenerarInput:
        if self.medio_pago not in MEDIOS_PAGO:
            raise ValueError(f"medio_pago debe ser uno de {list(MEDIOS_PAGO)}")
        if not self.incluir_json and not self.subir_pdf:
            raise ValueError("seleccionar al menos una salida (json o pdf)")
        return self


OperacionVepArchivo = Literal["generar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "generar": VepArchivoGenerarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``generar``, sin credenciales.

    Publica los nombres del contrato compartido (``minio_upload``) y los
    medios de V1. ``subir_pdf`` y ``subir_a_minio`` siguen siendo aceptados.
    """
    return {
        "type": "object",
        "required": ["operacion", "archivo_path"],
        "properties": {
            "operacion": {"type": "string", "enum": ["generar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "medio_pago": {"type": "string", "enum": list(MEDIOS_PAGO)},
            "archivo_path": {
                "type": "string",
                "minLength": 1,
                "description": "Nombre local descargado desde una referencia object_key.",
            },
            "incluir_json": {"type": "boolean"},
            "subir_pdf": {"type": "boolean"},
            "minio_upload": {"type": "boolean"},
            "eliminar_descargas": {"type": "boolean"},
        },
    }
