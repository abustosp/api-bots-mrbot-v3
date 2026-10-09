"""Esquemas de entrada del bot ``declaracion_en_linea`` (worker V3).

Porta la validacion implicita de V1
(``api-bots-mrbot/app/bot/declaracion_en_linea_bot.py``,
``bot_declaracion_en_linea`` / ``declaracion_en_linea_consulta``):
CUIT de representado (por defecto el representante), rango de periodos
(``AAAAMM`` o los formatos ``MM/AAAA``/``AAAA-MM`` que V1 normaliza) y flags
de salida (``carga_minio`` en V1). Falla antes del navegador.

Incluye el generador puro de rango de periodos (port de
``normalize_periodo_range``/``generate_period_range`` de V1) para que el
plugin itere ``[periodo_desde, periodo_hasta]`` sin logica de fechas en
el flujo de navegador.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from bot_worker.bots.declaracion_en_linea.helpers import periodo_a_yyyymm

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"
#: Campos del contrato compartido con V1 que V3 no toma del payload (van en el
#: sobre sellado o los resuelve la infraestructura).
CAMPOS_SOBRE_SELLADO = (
    "clave",
    "clave_representante",
    "clave_encriptada",
    "cuit_representante",
    "cuit_inicio_sesion",
    "proxy_request",
    "proxy",
)


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


def normalizar_periodo(valor: Any) -> str:
    """Normaliza el periodo a ``AAAAMM`` con mes 01-12 (port de V1).

    V1 acepta ``AAAAMM`` y tambien ``MM/AAAA``, ``AAAA/MM``, ``MM-AAAA`` y
    ``AAAA-MM`` via ``periodo_to_yyyymm``.
    """
    texto = periodo_a_yyyymm(str(valor) if valor is not None else None)
    if not texto:
        raise ValueError(f"Periodo desde/hasta invalido. Use formato AAAAMM: {valor!r}")
    mes = int(texto[4:6])
    if mes < 1 or mes > 12:
        raise ValueError(f"periodo con mes invalido: {texto!r}")
    return texto


def generar_rango_periodos(desde: str, hasta: str) -> list[str]:
    """Genera los periodos ``AAAAMM`` de ``[desde, hasta]`` inclusive.

    Funcion pura (port de V2): avanza mes a mes y corta en 1.200
    periodos como guarda ante rangos absurdos.
    """
    inicio = normalizar_periodo(desde)
    fin = normalizar_periodo(hasta)
    if inicio > fin:
        raise ValueError("periodo_desde no puede ser posterior a periodo_hasta")
    periodos: list[str] = []
    anio, mes = int(inicio[:4]), int(inicio[4:6])
    anio_fin, mes_fin = int(fin[:4]), int(fin[4:6])
    while (anio, mes) <= (anio_fin, mes_fin):
        periodos.append(f"{anio:04d}{mes:02d}")
        if len(periodos) >= 1200:
            break
        mes += 1
        if mes > 12:
            mes = 1
            anio += 1
    return periodos


class DeclaracionEnLineaConsultarInput(_Base):
    """Entrada de ``consultar``: DDJJ y VEP del rango de periodos."""

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(default="", max_length=128)
    periodo_desde: str = Field(min_length=6, max_length=6, pattern=PERIODO_PATTERN)
    periodo_hasta: str = Field(min_length=6, max_length=6, pattern=PERIODO_PATTERN)
    incluir_json: bool = True
    subir_archivos: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "subir_archivos", "carga_minio", "minio_upload", "archivo_historico_minio"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit", "cuit_representado"):
                if clave in datos and datos[clave] not in (None, ""):
                    datos[clave] = limpiar_cuit(datos[clave])
                elif clave in datos and datos[clave] == "":
                    datos[clave] = None
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            for clave in ("periodo_desde", "periodo_hasta"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = normalizar_periodo(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_valido(self) -> DeclaracionEnLineaConsultarInput:
        # V1 ordena el rango: si vienen invertidos, los intercambia.
        if self.periodo_desde > self.periodo_hasta:
            desde, hasta = self.periodo_hasta, self.periodo_desde
            object.__setattr__(self, "periodo_desde", desde)
            object.__setattr__(self, "periodo_hasta", hasta)
        if not self.incluir_json and not self.subir_archivos:
            raise ValueError("seleccionar al menos una salida (json o archivos)")
        return self


OperacionDeclaracionEnLinea = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": DeclaracionEnLineaConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "periodo_desde", "periodo_hasta"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit_representado": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "periodo_desde": {"type": "string"},
            "periodo_hasta": {"type": "string"},
            "incluir_json": {"type": "boolean"},
            "subir_archivos": {"type": "boolean"},
            "carga_minio": {"type": "boolean"},
        },
    }
