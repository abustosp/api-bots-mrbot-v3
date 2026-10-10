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
#: Campos del contrato compartido que V3 no toma del payload (van en el sobre).
CAMPOS_SOBRE_SELLADO = ("clave", "clave_representante", "clave_encriptada", "cuit_representante", "proxy_request")
#: Nombres equivalentes de la subida de la constancia (V1: ``minio_upload``).
ALIAS_SUBIDA = ("subir_constancia", "minio_upload", "subir_a_minio")


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
    """Entrada de ``presentar``: archivos del work_dir a presentar.

    Paridad con V1 (``controladores_fiscales_bot``): ``minio_upload`` del
    contrato compartido es el mismo flag que ``subir_constancia``.
    """

    archivos: list[str] = Field(min_length=1, max_length=MAX_ARCHIVOS)
    subir_constancia: bool = True
    incluir_json: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            presentes = [c for c in ALIAS_SUBIDA if c in datos]
            if presentes:
                valores = {bool(datos[c]) for c in presentes}
                if len(valores) > 1:
                    raise ValueError("subir_constancia y minio_upload contradictorios")
                valor = valores.pop()
                for c in ALIAS_SUBIDA:
                    datos.pop(c, None)
                datos["subir_constancia"] = valor
        return datos

    @field_validator("archivos", mode="before")
    @classmethod
    def _archivos(cls, valor: Any) -> list[str]:
        if not isinstance(valor, list):
            raise ValueError("archivos debe ser lista")
        vistos: list[str] = []
        for item in valor:
            nombre = _nombre_archivo_seguro(item)
            if not nombre.lower().endswith((".pem", ".zip")):
                raise ValueError(f"solo se aceptan archivos .pem o .zip: {nombre}")
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
            "minio_upload": {"type": "boolean"},
        },
    }
