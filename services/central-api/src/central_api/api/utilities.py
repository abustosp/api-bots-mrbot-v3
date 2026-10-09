"""Synchronous, deterministic V1 utility endpoints.

They process only request bytes and do not invoke browser bots.  File uploads use
the same multipart wire format as V1 while avoiding a global multipart runtime
dependency in central-api.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import base64
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
MAX_UTILITY_BYTES = 7_000_000


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


def _rcel_fields(text: str, content: bytes, include_b64: bool) -> dict[str, Any]:
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
    if include_b64:
        fields["B64"] = base64.b64encode(content).decode("ascii")
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


@router.get("/utilidades/cuit/{cuit}")
def cuit_individual(cuit: str, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    _ = principal
    digits = _validate_cuit(cuit)
    return JSONResponse({"cuit": digits, "valido": True, "fuente": "validacion_modulo_11"})


@router.post("/utilidades/pem/convertir")
async def pem_convertir(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    _ = principal
    upload = await uploaded_file_from_request(request, field_name="file")
    if not upload.filename.lower().endswith(".pem"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .pem")
    if len(upload.content) > MAX_UTILITY_BYTES:
        raise HTTPException(status_code=400, detail="El archivo excede el tamaño permitido.")
    return JSONResponse({"nombre_archivo": upload.filename, "datos": _convert_pem(upload.content, upload.filename)})


@router.post("/utilidades/rcel/extraer-texto")
async def rcel_extraer_texto(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
    _ = principal
    upload = await uploaded_file_from_request(request, field_name="file")
    if not upload.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if len(upload.content) > MAX_UTILITY_BYTES:
        raise HTTPException(status_code=400, detail="El archivo excede el tamaño permitido.")
    include_b64 = upload.fields.get("incluir_b64", "false").lower() in {"1", "true", "si", "sí"}
    return JSONResponse({"success": True, "message": "PDF procesado con éxito.", "extracted_data": [_rcel_fields(_pdf_text(upload.content), upload.content, include_b64)]})


@router.get("/utilidades/apoc/{cuit}")
def apoc(cuit: str, principal: ApiPrincipal = Depends(require_api_principal)) -> JSONResponse:
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
