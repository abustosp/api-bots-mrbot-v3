"""Small dependency-free parser for the upload shapes used by legacy V1 routes.

The central service intentionally does not depend on ``python-multipart``.  The
legacy endpoints nevertheless accept normal browser multipart uploads, plus a
JSON/base64 representation that is useful to API clients and tests.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes
    fields: dict[str, str]


_FILENAME_RE = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)
_NAME_RE = re.compile(r'name="?([^";]+)"?', re.IGNORECASE)


def _multipart_upload(content_type: str, raw: bytes, field_name: str | None) -> UploadedFile:
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;\s]+))", content_type, re.I)
    if match is None:
        raise HTTPException(status_code=400, detail="multipart inválido: boundary ausente.")
    boundary = (match.group(1) or match.group(2)).encode("utf-8")
    fields: dict[str, str] = {}
    candidates: list[tuple[str, str, bytes]] = []
    for part in raw.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        headers, content = part.split(b"\r\n\r\n", 1)
        content = content.rstrip(b"\r\n")
        disposition = next(
            (line.decode("latin-1") for line in headers.split(b"\r\n") if line.lower().startswith(b"content-disposition:")),
            "",
        )
        name_match = _NAME_RE.search(disposition)
        name = name_match.group(1) if name_match else ""
        filename_match = _FILENAME_RE.search(disposition)
        if filename_match:
            candidates.append((name, filename_match.group(1), content))
        elif name:
            fields[name] = content.decode("utf-8", errors="replace")
    selected = next((entry for entry in candidates if entry[0] == field_name), None)
    if selected is None and candidates:
        selected = candidates[0]
    if selected is None:
        raise HTTPException(status_code=400, detail="Debe enviar un archivo multipart.")
    _, filename, content = selected
    return UploadedFile(filename=filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1], content=content, fields=fields)


async def uploaded_file_from_request(request: Request, *, field_name: str | None = None) -> UploadedFile:
    """Read a V1 upload safely without retaining it on disk.

    Accepted forms are multipart (the historical contract), JSON with
    ``filename`` + ``content_b64``/``data_b64``, and raw bytes with
    ``X-Filename``.  The alternatives are additive and do not change multipart
    compatibility.
    """
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="El archivo no puede estar vacío.")
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        return _multipart_upload(content_type, raw, field_name)
    if "application/json" in content_type:
        try:
            body: Any = json.loads(raw)
            filename = str(body.get("filename") or body.get("nombre_archivo") or "")
            encoded = body.get("content_b64", body.get("data_b64", body.get("archivo_b64")))
            if not filename or not isinstance(encoded, str):
                raise ValueError
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="JSON de archivo inválido.") from exc
        return UploadedFile(filename=filename, content=content, fields={})
    filename = request.headers.get("x-filename", "archivo")
    return UploadedFile(filename=filename, content=raw, fields={})
