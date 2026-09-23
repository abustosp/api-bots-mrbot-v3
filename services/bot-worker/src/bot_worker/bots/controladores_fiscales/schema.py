"""Esquemas de entrada del bot ``controladores_fiscales`` (worker V3).

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/controladores_fiscales_bot.py``,
``controladores_fiscales_bot``): en V2 los archivos a presentar se
reciben como directorios del host (``archivos_dir``/``descargas_dir``);
en V3 no hay rutas del host: los archivos llegan como adjuntos del
sobre y el worker los deja bajo ``runtime.work_dir``. La entrada lista
sus nombres (no rutas) y el plugin los resuelve con
``artifact_store.resolve``. Las constancias PDF descargadas se suben por
slot prefirmado, nunca a MinIO con credenciales.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ARCHIVOS = 50


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _nombre_archivo_seguro(valor: Any) -> str:
    """Exige un nombre de archivo simple, sin rutas ni separadores."""
    texto = str(valor).strip()
    if not texto or len(texto) > 255:
        raise ValueError("nombre de archivo invalido")
    if "/" in texto or "\\" in texto or texto in (".", ".."):
        raise ValueError("nombre de archivo no puede ser ruta")
    if texto.startswith("."):
        raise ValueError("nombre de archivo invalido")
    return texto


class ControladoresFiscalesPresentarInput(_Base):
    """Entrada de ``presentar``: archivos del work_dir a presentar."""

    archivos: list[str] = Field(min_length=1, max_length=MAX_ARCHIVOS)
    subir_constancia: bool = True
    incluir_json: bool = True

    @field_validator("archivos", mode="before")
    @classmethod
    def _archivos(cls, valor: Any) -> list[str]:
        if not isinstance(valor, list):
            raise ValueError("archivos debe ser lista")
        vistos: list[str] = []
        for item in valor:
            nombre = _nombre_archivo_seguro(item)
            if nombre not in vistos:
                vistos.append(nombre)
        if not vistos:
            raise ValueError("archivos no puede estar vacia")
        return vistos

    @model_validator(mode="after")
    def _salida_valida(self) -> ControladoresFiscalesPresentarInput:
        if not self.subir_constancia and not self.incluir_json:
            raise ValueError("seleccionar al menos una salida (json o constancia)")
        return self


OperacionControladoresFiscales = Literal["presentar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "presentar": ControladoresFiscalesPresentarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``presentar``, sin credenciales ni rutas."""
    return {
        "type": "object",
        "required": ["operacion", "archivos"],
        "properties": {
            "operacion": {"type": "string", "enum": ["presentar"]},
            "archivos": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_ARCHIVOS,
                "items": {"type": "string", "minLength": 1, "maxLength": 255},
            },
            "subir_constancia": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
        },
    }
