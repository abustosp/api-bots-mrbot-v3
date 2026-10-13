"""Esquemas de entrada del bot ``comprobantes`` (worker V3).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/comprobantes_bot.py``:
``descargar_mis_comprobantes``, ``solicitar_consulta_mis_comprobantes`` y
``descarga_csv_consulta``) a modelos Pydantic que fallan antes de abrir
el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios).
- Fechas ``dd/mm/aaaa`` con ``desde <= hasta``.
- Al menos un tipo (emitidos o recibidos). Como en V2, se acepta pedir ninguna salida (solo se consulta).
- Aliases de compatibilidad V1/V2: ``desde``/``hasta`` y ``cuit``, mas los
  nombres de V1 para los tipos y las salidas (``descarga_emitidos``,
  ``descarga_recibidos``, ``carga_json`` y ``carga_minio``).
- Puntos de venta opcionales por tipo (``puntos_venta_emitidos`` /
  ``puntos_venta_recibidos``), tal como los acepta V1; se conservan como texto
  para no perder los ceros a la izquierda con los que V1 llena el portal.
- ``timeout_mc`` (segundos de espera del boton CSV, 30 en V1) y ``carga_b64``
  (devolver el archivo descargado en base64, False en V1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

CUIT_PATTERN = r"^\d{11}$"
FORMATO_FECHA = "%d/%m/%Y"
TIMEOUT_MC_POR_DEFECTO = 30
MAX_PUNTOS_VENTA = 200


def normalizar_puntos_venta(valor: Any) -> list[str] | None:
    """Normaliza los puntos de venta a texto, como los acepta V1.

    V1 une la lista con comas (``",".join(puntos_venta)``) para llenar el
    campo ``Punto de Venta`` del portal: los valores viajan como texto para no
    perder los ceros a la izquierda (``002`` sigue siendo ``002``).
    """
    if valor is None:
        return None
    if isinstance(valor, (str, int)):
        valor = [valor]
    if not isinstance(valor, list):
        raise ValueError("puntos de venta debe ser una lista")
    if len(valor) > MAX_PUNTOS_VENTA:
        raise ValueError(f"puntos de venta admite hasta {MAX_PUNTOS_VENTA} valores")
    puntos: list[str] = []
    for punto in valor:
        texto = str(punto).strip()
        if not texto or not texto.isdigit():
            raise ValueError(f"punto de venta invalido: {punto!r}")
        puntos.append(texto)
    return puntos


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


def normalizar_fecha(valor: Any) -> str:
    """Acepta ``dd/mm/aaaa`` y la devuelve normalizada en ese formato."""
    texto = str(valor).strip()
    try:
        return datetime.strptime(texto, FORMATO_FECHA).strftime(FORMATO_FECHA)
    except ValueError:
        raise ValueError(f"fecha debe tener formato dd/mm/aaaa: {texto!r}") from None


class _RangoFechas(_Base):
    fecha_desde: str = Field(
        validation_alias=AliasChoices("fecha_desde", "desde"),
        min_length=10,
        max_length=10,
    )
    fecha_hasta: str = Field(
        validation_alias=AliasChoices("fecha_hasta", "hasta"),
        min_length=10,
        max_length=10,
    )

    @model_validator(mode="after")
    def _rango_valido(self) -> _RangoFechas:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        return self


class _Representado(_Base):
    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos


class _TiposComprobante(_Base):
    emitidos: bool = Field(
        default=False,
        validation_alias=AliasChoices("emitidos", "descarga_emitidos"),
    )
    recibidos: bool = Field(
        default=False,
        validation_alias=AliasChoices("recibidos", "descarga_recibidos"),
    )
    puntos_venta_emitidos: list[int | str] | None = None
    puntos_venta_recibidos: list[int | str] | None = None
    timeout_mc: int | None = Field(default=None, ge=1, le=86400)
    # V1/V2 pedian el archivo en Base64 dentro del resultado. Ya no se devuelve:
    # el archivo se entrega siempre por ``files[]``. Se acepta y se ignora.
    carga_b64: bool = Field(default=False, exclude=True, deprecated=True)
    proxy_request: bool | None = None
    eliminar_descargas: bool = True
    verbose: bool = False

    @field_validator("puntos_venta_emitidos", "puntos_venta_recibidos", mode="after")
    @classmethod
    def _normalizar_puntos(
        cls, valor: list[int | str] | None
    ) -> list[str] | None:
        return normalizar_puntos_venta(valor)

    @model_validator(mode="after")
    def _al_menos_un_tipo(self) -> _TiposComprobante:
        if not self.emitidos and not self.recibidos:
            raise ValueError("seleccionar al menos emitidos o recibidos")
        return self

    def puntos_venta(self, tipo: str) -> list[str] | None:
        """Puntos de venta pedidos para ``emitidos`` o ``recibidos``."""
        return (
            self.puntos_venta_emitidos
            if tipo == "emitidos"
            else self.puntos_venta_recibidos
        )


class _SalidasComprobante(_Base):
    incluir_json: bool = Field(
        default=True,
        validation_alias=AliasChoices("incluir_json", "carga_json"),
    )
    subir_csv: bool = Field(
        default=True,
        validation_alias=AliasChoices("subir_csv", "carga_minio"),
    )


class ComprobantesConsultarInput(_RangoFechas, _Representado, _TiposComprobante, _SalidasComprobante):
    """Operacion ``consultar``: descarga emitidos/recibidos del rango."""


class ComprobantesSolicitarInput(_RangoFechas, _Representado, _TiposComprobante):
    """Operacion ``solicitar``: pide la consulta async sin descargar."""


class ComprobantesHistorialInput(_RangoFechas, _Representado, _TiposComprobante, _SalidasComprobante):
    """Operacion ``historial``: descarga CSV del historial existente."""

    cookies_header: str | None = Field(default=None, repr=False, max_length=8192)


OperacionComprobantes = Literal["consultar", "solicitar", "historial"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": ComprobantesConsultarInput,
    "solicitar": ComprobantesSolicitarInput,
    "historial": ComprobantesHistorialInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema combinado de las tres operaciones, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "fecha_desde", "fecha_hasta"],
        "properties": {
            "operacion": {
                "type": "string",
                "enum": ["consultar", "solicitar", "historial"],
            },
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "emitidos": {"type": "boolean"},
            "recibidos": {"type": "boolean"},
            "puntos_venta_emitidos": {
                "type": "array",
                "items": {"type": ["integer", "string"]},
            },
            "puntos_venta_recibidos": {
                "type": "array",
                "items": {"type": ["integer", "string"]},
            },
            "timeout_mc": {"type": "integer", "minimum": 1, "maximum": 86400},
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
