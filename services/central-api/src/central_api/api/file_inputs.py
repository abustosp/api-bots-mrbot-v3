"""Valida referencias de archivos previamente cargados en ``uploads``.

Los endpoints públicos nunca reciben el contenido del archivo. El cliente
solicita un ticket en ``POST /api/v3/uploads``, carga allí el contenido y envía
solo su ``object_key`` en la solicitud de la operación.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import json
import mimetypes
import re
from typing import Any

from fastapi import HTTPException, Request

from central_api.input_files import (
    INPUT_FILE_FIELDS,
    KEY_FIELDS,
    UPLOAD_FIELD_MAX_BYTES,
    UPLOAD_FIELDS,
)

MAX_UPLOAD_BYTES = 52_428_800
MAX_MULTIPART_BODY_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes
    fields: dict[str, str]
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class MultipartFile:
    field_name: str
    filename: str
    content: bytes
    content_type: str


# Solo se conservan los nombres históricos para indicar qué campo de referencia
# corresponde a un adjunto enviado por error. Nunca se usa para almacenar nada.
MULTIPART_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("carga_portal_iva", "cargar"): {
        name: f"{name}_key"
        for name in (
            "liv_cbte", "liv_alicuota", "lic_cbte", "lic_alicuota",
            "csv_cf", "csv_cf_restitucion", "csv_df", "csv_df_restitucion",
        )
    },
    ("vep_archivo", "generar"): {
        "archivo_txt": "archivo_key", "archivo": "archivo_key", "file": "archivo_key",
    },
    ("controladores_fiscales", "presentar"): {
        "files": "archivos", "archivos": "archivos", "file": "archivos",
    },
    ("portal_iva", "importar"): {
        "ventas_txt": "ventas_txt_key", "compras_txt": "compras_txt_key",
        "ventas_alicuotas_txt": "ventas_alicuotas_txt_key",
        "compras_alicuotas_txt": "compras_alicuotas_txt_key",
    },
    ("portal_iva", "gestionar"): {
        "ventas_txt": "ventas_txt_key", "compras_txt": "compras_txt_key",
        "ventas_alicuotas_txt": "ventas_alicuotas_txt_key",
        "compras_alicuotas_txt": "compras_alicuotas_txt_key",
    },
}

_INLINE_TEXT_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("portal_iva", "importar"): {
        "ventas_txt": "ventas_txt_key", "compras_txt": "compras_txt_key",
        "ventas_alicuotas_txt": "ventas_alicuotas_txt_key",
        "compras_alicuotas_txt": "compras_alicuotas_txt_key",
    },
    ("portal_iva", "gestionar"): {
        "ventas_txt": "ventas_txt_key", "compras_txt": "compras_txt_key",
        "ventas_alicuotas_txt": "ventas_alicuotas_txt_key",
        "compras_alicuotas_txt": "compras_alicuotas_txt_key",
    },
}

_ALLOWED_EXTENSIONS: dict[tuple[str, str, str], frozenset[str]] = {
    ("carga_portal_iva", "cargar", field): frozenset(
        {".txt" if field.startswith(("liv_", "lic_")) else ".csv"}
    )
    for field in UPLOAD_FIELDS[("carga_portal_iva", "cargar")]
}
_ALLOWED_EXTENSIONS.update({
    ("vep_archivo", "generar", "archivo"): frozenset({".txt"}),
    ("vep_archivo", "generar", "archivo_excel"): frozenset({".xlsx"}),
    ("controladores_fiscales", "presentar", "archivos"): frozenset({".pem", ".zip"}),
    **{
        ("portal_iva", operation, field): frozenset({".txt"})
        for operation in ("importar", "gestionar")
        for field in (
            "ventas_txt", "ventas_alicuotas_txt",
            "compras_txt", "compras_alicuotas_txt",
        )
    },
    ("rcel", "descargar", "pem_convertir"): frozenset({".pem"}),
    ("rcel", "descargar", "rcel_extraer_texto"): frozenset({".pdf"}),
    ("rcel", "descargar", "scrapping_pdf"): frozenset({".pdf"}),
})


def _file_not_uploaded_message(
    expected_field: str,
    *,
    bot: str | None = None,
    operation: str | None = None,
    upload_field: str | None = None,
) -> str:
    flujo = (
        "Flujo de ejemplo: 1) solicite POST /api/v3/uploads con "
        '{"bot":"vep_archivo","operacion":"generar","campo":"archivo",'
        '"filename":"vep.txt","content_type":"text/plain","size_bytes":120}; '
        "2) cargue los bytes usando la URL recibida; 3) envíe el object_key recibido "
        'en POST /api/v3/vep_archivo/generar como {"archivo_key":"<object_key>"}.'
    )
    if bot and operation:
        campos = UPLOAD_FIELDS.get((bot, operation), frozenset())
        if expected_field == "object_key":
            campo_ticket = upload_field or (sorted(campos)[0] if campos else "archivo")
            extension, media_type = ".txt", "text/plain"
            if campo_ticket in {"archivos", "pem_convertir"}:
                extension, media_type = ".pem", "application/x-pem-file"
            elif campo_ticket in {"rcel_extraer_texto", "scrapping_pdf"}:
                extension, media_type = ".pdf", "application/pdf"
            elif campo_ticket.startswith("csv_"):
                extension, media_type = ".csv", "text/csv"
            elif campo_ticket == "archivo_excel":
                extension = ".xlsx"
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            flujo = (
                "Flujo de ejemplo: 1) solicite POST /api/v3/uploads con "
                f'{{"bot":"{bot}","operacion":"{operation}","campo":"{campo_ticket}",'
                f'"filename":"documento{extension}","content_type":"{media_type}","size_bytes":12345}}; '
                "2) cargue los bytes usando la URL recibida; 3) envíe "
                f'{{"object_key":"<object_key>"}} en esta solicitud.'
            )
        else:
            path = f"/api/v3/{bot}/{operation}"
            flujo = (
                "Flujo de ejemplo: 1) solicite POST /api/v3/uploads con "
                f'{{"bot":"{bot}","operacion":"{operation}","campo":"{expected_field.removesuffix("_key")}",'
                '"filename":"documento.txt","content_type":"text/plain","size_bytes":120}; '
                "2) cargue los bytes usando la URL recibida; 3) envíe "
                f'{{"{expected_field}":"<object_key>"}} en POST {path}.'
            )
    return (
        "Los archivos deben subirse antes con POST /api/v3/uploads y enviarse por su "
        f"object_key (campo {expected_field}). No se aceptan archivos adjuntos ni Base64 "
        f"en esta solicitud. {flujo}"
    )


def file_not_uploaded(
    expected_field: str,
    *,
    bot: str | None = None,
    operation: str | None = None,
    upload_field: str | None = None,
) -> HTTPException:
    """Error público uniforme para adjuntos y contenido de archivo inline."""
    return HTTPException(
        status_code=422,
        detail={
            "error_code": "file_not_uploaded",
            "message": _file_not_uploaded_message(
                expected_field, bot=bot, operation=operation, upload_field=upload_field
            ),
        },
    )


async def _read_limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="La solicitud supera el tamaño total permitido.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length inválido.") from None
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="La solicitud supera el tamaño total permitido.")
        chunks.extend(chunk)
    return bytes(chunks)


def _parse_multipart(content_type: str, raw: bytes) -> list[tuple[str, str | None, str, bytes]]:
    """Parsea el formulario para que la capa pública pueda rechazar adjuntos."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + raw
        )
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="multipart inválido.") from exc
    if not message.is_multipart():
        raise HTTPException(status_code=400, detail="multipart inválido: boundary ausente.")
    partes: list[tuple[str, str | None, str, bytes]] = []
    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        field = part.get_param("name", header="content-disposition")
        if disposition != "form-data" or not field:
            continue
        filename = part.get_filename()
        content_type = part.get_content_type() or "application/octet-stream"
        content = part.get_payload(decode=True) or b""
        partes.append((str(field), str(filename) if filename is not None else None, content_type, content))
    return partes


async def multipart_job_request(
    request: Request,
) -> tuple[dict[str, Any], dict[str, Any], list[MultipartFile]]:
    """Lee el formulario histórico; los adjuntos se rechazan más adelante."""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=415, detail="Se esperaba multipart/form-data.")
    raw = await _read_limited_body(request, MAX_MULTIPART_BODY_BYTES)
    payload: dict[str, Any] = {}
    credentials: dict[str, Any] = {}
    files: list[MultipartFile] = []
    for name, filename, media_type, content in _parse_multipart(content_type, raw):
        if filename is not None:
            files.append(MultipartFile(name, filename, content, media_type))
        elif name == "credentials":
            try:
                parsed = json.loads(content.decode("utf-8"))
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail="credentials debe ser JSON válido.") from exc
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=422, detail="credentials debe ser un objeto JSON.")
            credentials = parsed
        else:
            payload[name] = content.decode("utf-8", errors="replace")
    return payload, credentials, files


def _basename(filename: str) -> str:
    return str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _validar_archivo(
    bot: str, operation: str, field: str, filename: str, content: bytes
) -> tuple[str, str]:
    basename = _basename(filename)
    if not basename or basename in {".", ".."}:
        raise HTTPException(status_code=422, detail="El nombre del archivo no es válido.")
    if len(basename) > 255:
        raise HTTPException(status_code=422, detail="El nombre del archivo supera 255 caracteres.")
    if not content:
        raise HTTPException(status_code=422, detail=f"El archivo {basename} está vacío.")
    limite = UPLOAD_FIELD_MAX_BYTES.get((bot, operation, field), MAX_UPLOAD_BYTES)
    if len(content) > limite:
        raise HTTPException(
            status_code=422,
            detail=f"El archivo supera el máximo permitido para {field}: {limite:,} bytes.",
        )
    extension = "." + basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    permitidas = _ALLOWED_EXTENSIONS.get((bot, operation, field))
    if permitidas and extension not in permitidas:
        opciones = ", ".join(sorted(permitidas))
        raise HTTPException(status_code=415, detail=f"Formato no admitido para {field}; use {opciones}.")
    if bot == "controladores_fiscales" and extension == ".pem" and not re.fullmatch(r"F801[01].*\.pem", basename, re.IGNORECASE):
        raise HTTPException(status_code=415, detail="El nombre PEM debe comenzar con F8010 o F8011.")
    media_type = mimetypes.guess_type(basename)[0] or "application/octet-stream"
    return basename, media_type


def _expected_key_field(bot: str, operation: str, payload: dict[str, Any], files: list[MultipartFile]) -> str:
    if files:
        aliases = MULTIPART_FIELDS.get((bot, operation), {})
        expected = aliases.get(files[0].field_name)
        if expected:
            return expected
        return (
            next(iter(KEY_FIELDS.get((bot, operation), {})), None)
            or INPUT_FILE_FIELDS.get((bot, operation))
            or "object_key"
        )
    for name in (*payload.keys(),):
        lowered = str(name).lower()
        if "b64" in lowered or "base64" in lowered:
            key = next(
                (
                    field_key for field_key in KEY_FIELDS.get((bot, operation), {})
                    if name in {
                        field_key.removesuffix("_key"),
                        field_key.removesuffix("_key") + "_b64",
                    }
                ),
                None,
            )
            if key:
                return key
            if name == "archivos_base64":
                return "archivos"
            return "object_key"
    for field, key in _INLINE_TEXT_FIELDS.get((bot, operation), {}).items():
        if field in payload:
            return key
    return (
        next(iter(KEY_FIELDS.get((bot, operation), {})), None)
        or INPUT_FILE_FIELDS.get((bot, operation))
        or "object_key"
    )


def _has_inline_file_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            ("b64" in str(key).lower() or "base64" in str(key).lower())
            or _has_inline_file_content(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_inline_file_content(item) for item in value)
    return False


async def externalizar_archivos(
    bot: str,
    operation: str,
    payload: dict[str, Any],
    credentials: dict[str, Any],
    files: list[MultipartFile],
    principal: Any,
) -> dict[str, Any]:
    """Rechaza contenido adjunto/inline y valida referencias a ``uploads``."""
    if files or _has_inline_file_content(payload) or _has_inline_file_content(credentials):
        expected = _expected_key_field(bot, operation, payload, files)
        raise file_not_uploaded(expected, bot=bot, operation=operation)
    for inline_field, key_field in _INLINE_TEXT_FIELDS.get((bot, operation), {}).items():
        if payload.get(inline_field) not in (None, ""):
            raise file_not_uploaded(key_field, bot=bot, operation=operation)

    referencias: dict[str, str] = {}
    for key_field in KEY_FIELDS.get((bot, operation), {}):
        value = payload.get(key_field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=422,
                detail={"error_code": "validation", "message": f"{key_field} debe ser un object_key válido."},
            )
        referencias[value] = key_field.removesuffix("_key")

    array_field = INPUT_FILE_FIELDS.get((bot, operation))
    if array_field and array_field in payload:
        values = payload[array_field]
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item.strip() for item in values):
            raise HTTPException(
                status_code=422,
                detail={"error_code": "validation", "message": f"{array_field} debe contener object_key emitidas por POST /api/v3/uploads."},
            )
        referencias.update({value: array_field for value in values})

    if referencias:
        from central_api.api.uploads import resolver_archivos

        try:
            await resolver_archivos(
                str(principal.user_id), bot, list(referencias),
                operation=operation, expected_fields=referencias,
            )
        except LookupError as exc:
            code = "file_not_uploaded" if "archivo no encontrado" in str(exc).lower() else "validation"
            raise HTTPException(status_code=422, detail={"error_code": code, "message": str(exc)}) from exc
    return dict(payload)


async def uploaded_file_from_request(
    request: Request,
    *,
    field_name: str,
    principal: Any,
    bot: str,
    operation: str,
) -> UploadedFile:
    """Lee exclusivamente un ``object_key`` JSON del bucket ``uploads``."""
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        raise file_not_uploaded(
            "object_key", bot=bot, operation=operation, upload_field=field_name
        )
    if not content_type.startswith("application/json"):
        raise file_not_uploaded(
            "object_key", bot=bot, operation=operation, upload_field=field_name
        )
    try:
        body: Any = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="El cuerpo debe ser un objeto JSON con object_key.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="El cuerpo debe ser un objeto JSON con object_key.")
    if _has_inline_file_content(body):
        raise file_not_uploaded(
            "object_key", bot=bot, operation=operation, upload_field=field_name
        )
    object_key = body.get("object_key")
    if not isinstance(object_key, str) or not object_key.strip():
        raise file_not_uploaded(
            "object_key", bot=bot, operation=operation, upload_field=field_name
        )

    from central_api.api.uploads import resolver_archivos

    try:
        archivos = await resolver_archivos(
            str(principal.user_id), bot, [object_key],
            operation=operation,
            expected_fields={object_key: field_name},
            include_content=True,
        )
    except LookupError as exc:
        code = "file_not_uploaded" if "archivo no encontrado" in str(exc).lower() else "validation"
        raise HTTPException(status_code=422, detail={"error_code": code, "message": str(exc)}) from exc
    archivo = archivos[0]
    return UploadedFile(
        str(archivo["filename"]),
        bytes(archivo["content"]),
        {},
        str(archivo["content_type"] or "application/octet-stream"),
    )


__all__ = [
    "INPUT_FILE_FIELDS", "KEY_FIELDS", "MAX_UPLOAD_BYTES", "MULTIPART_FIELDS",
    "MultipartFile", "UploadedFile", "externalizar_archivos", "file_not_uploaded",
    "multipart_job_request", "uploaded_file_from_request",
]
