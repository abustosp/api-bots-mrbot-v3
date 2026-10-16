"""Esquemas de entrada del bot ``libro_sueldos_digital`` (worker V3).

Porta la validación implícita de la versión dev
(``bots_dev/libro_sueldos_digital/libro_sueldos_digital_bot.py``): CUIT de
sesión, CUIT representado opcional y rango de períodos ``AAAAMM``. Una sola
operación (``descargar``) que baja el Libro de Sueldos Digital (PDF) de cada
liquidación de cada período del rango. Falla antes del navegador.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


def limpiar_cuit(valor: Any) -> str:
    """Quita separadores comunes y exige 11 dígitos."""
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
    """Exige período ``AAAAMM`` con mes 01-12."""
    texto = str(valor).strip()
    if len(texto) != 6 or not texto.isdigit():
        raise ValueError(f"periodo debe tener formato AAAAMM: {texto!r}")
    mes = int(texto[4:6])
    if mes < 1 or mes > 12:
        raise ValueError(f"periodo con mes invalido: {texto!r}")
    return texto


def generar_rango_periodos(desde: str, hasta: str) -> list[str]:
    """Genera los períodos ``AAAAMM`` de ``[desde, hasta]`` inclusive."""
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


class LibroSueldosDigitalDescargarInput(_Base):
    """Entrada de ``descargar``: LS Digital de cada liquidación del rango.

    ``subir_archivos`` acepta el alias histórico ``carga_minio``. Los campos
    de V1/V2 que el sobre sellado resuelve (credenciales, proxy) se aceptan
    y se ignoran.
    """

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    periodo_desde: str = Field(min_length=6, max_length=6, pattern=PERIODO_PATTERN)
    periodo_hasta: str = Field(min_length=6, max_length=6, pattern=PERIODO_PATTERN)
    incluir_json: bool = True
    subir_archivos: bool = Field(
        default=True,
        validation_alias=AliasChoices("subir_archivos", "carga_minio"),
    )
    # Campos de V1/V2 que el sobre sellado resuelve: se aceptan y se ignoran.
    cuit_representante: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "cuit_representante", "cuit_inicio_sesion", "cuit_login"
        ),
        repr=False,
        max_length=14,
    )
    proxy_request: bool | None = None
    headless: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in (
                "representado_cuit",
                "cuit",
                "cuit_representado",
                "cuit_representante",
                "cuit_inicio_sesion",
                "cuit_login",
            ):
                if clave in datos and datos[clave] not in (None, ""):
                    datos[clave] = limpiar_cuit(datos[clave])
                elif clave in datos and datos[clave] == "":
                    datos[clave] = None
            for clave in ("periodo_desde", "periodo_hasta"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = normalizar_periodo(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_valido(self) -> "LibroSueldosDigitalDescargarInput":
        if self.periodo_desde > self.periodo_hasta:
            raise ValueError("periodo_desde no puede ser posterior a periodo_hasta")
        return self


OperacionLibroSueldosDigital = Literal["descargar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "descargar": LibroSueldosDigitalDescargarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operación ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["periodo_desde", "periodo_hasta"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo_desde": {"type": "string", "pattern": PERIODO_PATTERN},
            "periodo_hasta": {"type": "string", "pattern": PERIODO_PATTERN},
            "incluir_json": {"type": "boolean"},
            "subir_archivos": {"type": "boolean"},
        },
    }
