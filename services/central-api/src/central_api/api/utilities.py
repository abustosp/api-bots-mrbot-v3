"""Synchronous utilities that process files previously loaded to uploads."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from central_api.api.dependencies import require_api_principal
from central_api.api.file_inputs import uploaded_file_from_request
from central_api.security.principals import ApiPrincipal

router = APIRouter()


def _validate_cuit(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11:
        raise HTTPException(status_code=400, detail="CUIT debe tener 11 dígitos.")
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(digit) * weight for digit, weight in zip(digits[:10], weights, strict=True))
    remainder = 11 - (total % 11)
    verifier = 0 if remainder == 11 else 9 if remainder == 10 else remainder
    if int(digits[-1]) != verifier:
        raise HTTPException(status_code=400, detail="CUIT con dígito verificador inválido.")
    return digits


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _element_to_data(element: ET.Element) -> Any:
    data: dict[str, Any] = {}
    if element.attrib:
        data["@attributes"] = {_strip_namespace(key): value for key, value in element.attrib.items()}
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if data and text:
            data["#text"] = text
        return data or text
    for child in children:
        name = _strip_namespace(child.tag)
        value = _element_to_data(child)
        if name not in data:
            data[name] = value
        elif isinstance(data[name], list):
            data[name].append(value)
        else:
            data[name] = [data[name], value]
    text = (element.text or "").strip()
    if text:
        data["#text"] = text
    return data


def _convert_pem(content: bytes, name: str) -> dict[str, Any]:
    try:
        process = subprocess.run(
            ["openssl", "cms", "-verify", "-noverify", "-inform", "PEM"],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="No se encontró openssl en el PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="La conversión PEM excedió el tiempo permitido.") from exc
    if process.returncode:
        message = process.stderr.decode("utf-8", errors="replace").strip() or "OpenSSL falló sin mensaje de error."
        raise HTTPException(status_code=400, detail=f"Error OpenSSL en {name}: {message}")
    try:
        root = ET.fromstring(process.stdout)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Error parseando XML en {name}: {exc}") from exc
    return {_strip_namespace(root.tag): _element_to_data(root)}


def _pdf_text(content: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise HTTPException(status_code=503, detail="La extracción PDF requiere pdfplumber en central-api.") from exc
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            if not pdf.pages:
                raise ValueError("El PDF no contiene páginas.")
            return pdf.pages[0].extract_text() or ""
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error procesando el PDF: {exc}") from exc


def _rcel_fields(text: str, content: bytes | None = None) -> dict[str, Any]:
    fields = {
        "CUIT del Emisor": "", "COD": "", "Punto de Venta": "", "Número de Factura": "",
        "Domicilio Vendedor": "", "Condición Vendedor": "", "Fecha": "", "Desde": "", "Hasta": "",
        "CUIT del Receptor": "", "Nombre del Receptor": "", "Condición Comprador": "", "Domicilio Cliente": "",
        "Importe Neto Gravado": "", "IVA 0%": "", "IVA 2,5%": "", "IVA 5%": "", "IVA 10,5%": "",
        "IVA 21%": "", "IVA 27%": "", "Otros Tributos": "", "Importe Total": "", "CAE": "", "Vencimiento CAE": "", "AUX": "",
    }
    patterns = {
        "COD": r"(?:CÓD\.|COD\.)\s*(\d+)", "CUIT del Emisor": r"CUIT:\s*(\d+)",
        "Punto de Venta": r"Punto de Venta:\s*(\d+)", "Número de Factura": r"Comp\. Nro:\s*(\d+)",
        "Domicilio Vendedor": r"Domicilio Comercial:\s*([A-Za-z0-9\s:.,-]+?)\s*CUIT:",
        "Condición Vendedor": r"Condición frente al IVA:\s*([A-Za-z0-9\s:.,-]+?)\s*Fecha",
        "Fecha": r"Fecha de Emisión:\s*(\d{2}/\d{2}/\d{4})", "Desde": r"Desde:\s*(\d{2}/\d{2}/\d{4})", "Hasta": r"Hasta:\s*(\d{2}/\d{2}/\d{4})",
        "CUIT del Receptor": r"([0-9]{8,11})\sApellido y Nombre", "Nombre del Receptor": r"Apellido y Nombre / Razón Social:\s*(.+)$",
        "Condición Comprador": r"Condición frente al IVA:\s*([A-Za-z0-9\s:.,-]+?)\s*Domicilio", "Domicilio Cliente": r"Domicilio:\s*(.+)$",
        "Importe Neto Gravado": r"Importe Neto Gravado:\s*\$ ([\d,]+)", "IVA 0%": r"IVA\s*0%:\s*\$ ([\d,]+)",
        "IVA 2,5%": r"IVA\s*2,5%:\s*\$ ([\d,]+)", "IVA 5%": r"IVA\s*5%:\s*\$ ([\d,]+)", "IVA 10,5%": r"IVA\s*10,5%:\s*\$ ([\d,]+)",
        "IVA 21%": r"IVA\s*21%:\s*\$ ([\d,]+)", "IVA 27%": r"IVA\s*27%:\s*\$ ([\d,]+)", "Otros Tributos": r"Otros Tributos:\s*\$ ([\d,]+)",
        "Importe Total": r"Importe Total:\s*\$ ([\d,]+)", "CAE": r"CAE N°:\s*(\d+)", "Vencimiento CAE": r"Fecha de Vto\. de CAE:\s*([\d/]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    fields["AUX"] = "-".join(fields[key] for key in ("CUIT del Emisor", "COD", "Punto de Venta", "Número de Factura"))
    return fields


def _apoc_base_paths() -> list[Path]:
    candidates = []
    configured = os.getenv("MRBOT_APOC_BASE")
    if configured:
        candidates.append(Path(configured))
    candidates.extend((
        Path(__file__).resolve().parents[4] / "data" / "FacturasApocrifas.txt",
        Path.cwd() / "data" / "FacturasApocrifas.txt",
    ))
    return candidates


def _utility_file_request_body(extension: str, *, upload_field: str) -> dict:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["object_key"],
                        "properties": {
                            "object_key": {
                                "type": "string",
                                "description": "Clave recibida al cargar previamente el archivo en el bucket uploads.",
                                "example": "550e8400-e29b-41d4-a716-446655440000/rcel/sin_cuit/documento." + extension,
                            },
                        },
                    },
                    "example": {"object_key": f"550e8400-e29b-41d4-a716-446655440000/rcel/sin_cuit/documento.{extension}"},
                },
            },
        }
    }


@router.get(
    "/utilidades/cuit/{cuit}",
    summary="Valida un CUIT",
    description=(
        "Valida que el valor contenga un CUIT de 11 dígitos y que su dígito verificador "
        "sea correcto. Se permiten separadores, que se eliminan antes de validar. Devuelve "
        "el CUIT normalizado y el resultado de la validación."
    ),
    responses={
        200: {
            "description": "CUIT válido, normalizado a 11 dígitos.",
            "content": {"application/json": {"example": {"cuit": "00000000000", "valido": True, "fuente": "validacion_modulo_11"}}},
        },
        400: {
            "description": "El CUIT no contiene 11 dígitos tras quitar separadores o el dígito verificador no coincide. Corrija el número y vuelva a intentar.",
            "content": {"application/json": {"example": {"detail": "CUIT con dígito verificador inválido."}}},
        },
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No se pudo verificar la autenticación por indisponibilidad del servicio de datos. Reintente más tarde.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
    },
)
def cuit_individual(cuit: str, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    """Valida el formato y el dígito verificador de un CUIT."""
    _ = principal
    digits = _validate_cuit(cuit)
    return JSONResponse({"cuit": digits, "valido": True, "fuente": "validacion_modulo_11"})


@router.post(
    "/utilidades/pem/convertir",
    summary="Convierte un archivo PEM a datos estructurados",
    description=(
        "Convierte un archivo `.pem` cargado previamente al bucket uploads y devuelve su contenido "
        "validado como datos estructurados. El archivo puede tener hasta 52.428.800 bytes (50 MiB); "
        "la conversión tiene un límite de 20 segundos.\n\n"
        "Flujo de dos pasos: (1) solicite un ticket en POST /api/v3/uploads con `bot=rcel`, "
        "`operacion=descargar`, `campo=pem_convertir`, el nombre, tipo y tamaño del archivo; "
        "la respuesta devuelve `upload_url` y `object_key`. Cargue los bytes usando `upload_url` "
        'y el encabezado `Content-Type` indicado. (2) envíe `{"object_key":"<object_key>"}` '
        "a este endpoint."
    ),
    openapi_extra=_utility_file_request_body("pem", upload_field="pem_convertir"),
    responses={
        200: {
            "description": "Archivo convertido. `datos` representa la estructura del contenido y conserva atributos y texto.",
            "content": {"application/json": {"example": {"nombre_archivo": "archivo.pem", "datos": {"Documento": {"@attributes": {}, "Texto": "contenido de ejemplo"}}}}},
        },
        400: {
            "description": "El archivo PEM no puede convertirse o interpretarse. Envíe un archivo PEM válido.",
            "content": {"application/json": {"example": {"detail": "No fue posible convertir el contenido PEM."}}},
        },
        413: {"description": "El archivo supera 52.428.800 bytes (50 MiB). Reduzca el archivo y vuelva a enviarlo."},
        415: {"description": "El formato no es PEM. Envíe un archivo con extensión .pem."},
        422: {"description": "La clave no corresponde a un archivo vigente de esta cuenta, el archivo fue eliminado del bucket uploads o la solicitud no contiene una referencia válida."},
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No fue posible guardar o leer la copia del archivo, o la conversión no está disponible. Revise el almacenamiento y reintente; el archivo no se procesó.",
        },
        504: {
            "description": "La conversión excedió el tiempo permitido. Compruebe que el archivo sea válido y vuelva a intentarlo más tarde.",
            "content": {"application/json": {"example": {"detail": "La conversión PEM excedió el tiempo permitido."}}},
        },
    },
)
async def pem_convertir(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    """Convierte un archivo PEM de hasta 50 MiB a una respuesta JSON."""
    upload = await uploaded_file_from_request(
        request, field_name="pem_convertir", principal=principal,
        bot="rcel", operation="descargar",
    )
    return JSONResponse({"nombre_archivo": upload.filename, "datos": _convert_pem(upload.content, upload.filename)})


@router.post(
    "/utilidades/rcel/extraer-texto",
    summary="Extrae datos de una factura PDF",
    description=(
        "Extrae texto de la primera página de una factura PDF cargada previamente al bucket uploads. "
        "El contenido no puede superar 52.428.800 bytes (50 MiB). La respuesta devuelve solo los "
        "campos extraídos; el archivo original no se incluye.\n\n"
        "Flujo de dos pasos: (1) solicite un ticket en POST /api/v3/uploads con `bot=rcel`, "
        "`operacion=descargar`, `campo=rcel_extraer_texto`, el nombre, tipo y tamaño del PDF; "
        "la respuesta devuelve `upload_url` y `object_key`. Cargue los bytes usando `upload_url` "
        'y el encabezado `Content-Type` indicado. (2) envíe `{"object_key":"<object_key>"}` '
        "a este endpoint."
    ),
    openapi_extra=_utility_file_request_body("pdf", upload_field="rcel_extraer_texto"),
    responses={
        200: {
            "description": "PDF procesado. `extracted_data` contiene un objeto con los campos encontrados; los campos no detectados se devuelven como cadenas vacías.",
            "content": {"application/json": {"example": {"success": True, "message": "PDF procesado con éxito.", "extracted_data": [{
                "CUIT del Emisor": "", "COD": "", "Punto de Venta": "", "Número de Factura": "",
                "Domicilio Vendedor": "", "Condición Vendedor": "", "Fecha": "", "Desde": "", "Hasta": "",
                "CUIT del Receptor": "", "Nombre del Receptor": "", "Condición Comprador": "", "Domicilio Cliente": "",
                "Importe Neto Gravado": "", "IVA 0%": "", "IVA 2,5%": "", "IVA 5%": "", "IVA 10,5%": "",
                "IVA 21%": "", "IVA 27%": "", "Otros Tributos": "", "Importe Total": "", "CAE": "",
                "Vencimiento CAE": "", "AUX": "-"
            }]}}},
        },
        400: {
            "description": "El PDF no se puede procesar. Envíe una factura PDF legible.",
            "content": {"application/json": {"example": {"detail": "No fue posible procesar el PDF."}}},
        },
        413: {"description": "El archivo supera 52.428.800 bytes (50 MiB). Reduzca el archivo y vuelva a enviarlo."},
        415: {"description": "El formato no es PDF. Envíe un archivo con extensión .pdf."},
        422: {"description": "La clave no corresponde a un archivo vigente de esta cuenta, el archivo fue eliminado del bucket uploads o la solicitud no contiene una referencia válida."},
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No fue posible guardar o leer la copia del archivo, la extracción no está disponible o no pudo verificarse la autenticación. Revise el almacenamiento y reintente.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
    },
)
async def rcel_extraer_texto(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    """Extrae campos de una factura PDF de hasta 50 MiB desde la copia guardada."""
    upload = await uploaded_file_from_request(
        request, field_name="rcel_extraer_texto", principal=principal,
        bot="rcel", operation="descargar",
    )
    return JSONResponse({"success": True, "message": "PDF procesado con éxito.", "extracted_data": [_rcel_fields(_pdf_text(upload.content))]})


@router.get(
    "/utilidades/apoc/{cuit}",
    summary="Consulta si un CUIT figura como apócrifo",
    description=(
        "Valida el CUIT y consulta la base local de contribuyentes apócrifos. Devuelve `apoc=true` "
        "con las fechas de condición y publicación si existe una coincidencia; si no figura, "
        "devuelve `apoc=false` y ambas fechas como null."
    ),
    responses={
        200: {
            "description": "Consulta completada, tanto si se encontró el CUIT como si no figura en la base.",
            "content": {"application/json": {"examples": {
                "no_encontrado": {"summary": "CUIT no encontrado", "value": {"cuit": "00000000000", "fecha_condicion": None, "fecha_publicacion": None, "apoc": False}},
            }}},
        },
        400: {
            "description": "El CUIT no contiene 11 dígitos tras quitar separadores o su dígito verificador es incorrecto. Corrija el número y vuelva a intentar.",
            "content": {"application/json": {"example": {"detail": "CUIT con dígito verificador inválido."}}},
        },
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "La base de consulta no está configurada o no se pudo verificar la autenticación. Solicite que habiliten la consulta y vuelva a intentar.",
        },
    },
)
def apoc(cuit: str, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    """Consulta la base de apócrifos para un CUIT válido."""
    _ = principal
    digits = _validate_cuit(cuit)
    for path in _apoc_base_paths():
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 3 and parts[0] == digits:
                    return JSONResponse({"cuit": digits, "fecha_condicion": parts[1], "fecha_publicacion": parts[2], "apoc": True})
        return JSONResponse({"cuit": digits, "fecha_condicion": None, "fecha_publicacion": None, "apoc": False})
    raise HTTPException(status_code=503, detail="Base de apócrifos no configurada en central-api.")
