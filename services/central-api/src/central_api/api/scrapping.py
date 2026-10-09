"""Legacy PDF extraction endpoints that are safe to run in central-api.

These operations parse an uploaded document only.  They do not create a browser
session and consequently remain synchronous utilities rather than worker jobs.
"""

from __future__ import annotations

from io import BytesIO
import re
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from central_api.api.dependencies import require_api_principal
from central_api.api.file_inputs import uploaded_file_from_request
from central_api.security.principals import ApiPrincipal

router = APIRouter(prefix="/scrapping", tags=["Scrapping PDF"])


def _pdf_text(content: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depends on deployment extras
        raise HTTPException(
            status_code=503,
            detail="La extracción PDF requiere la dependencia pdfplumber en central-api.",
        ) from exc
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            if not pdf.pages:
                raise ValueError("El PDF no contiene páginas.")
            return pdf.pages[0].extract_text() or ""
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error al procesar el PDF: {exc}") from exc


def _extract(text: str, patterns: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            continue
        value = match.group(match.lastindex or 1).strip()
        result[name] = value
    return result


def _f2002(text: str) -> dict[str, str]:
    patterns = {
        "CUIT Nº": r"CUIT Nº:\s*(\d{11})",
        "Apellido y Nombre o Razón Social": r"Apellido y Nombre o Razón Social:\s*(.+)$",
        "Periodo": r"Per[ií]odo:\s*(\d{6})",
        "Secuencia": r"Secuencia:\s*(.+)$",
        "Fecha de Presentación": r"Fecha de Presentación:\s*(\d{2}/\d{2}/\d{4})",
        "Total del Débito Fiscal": r"Total del Débito Fiscal\s+\$?\s*([\d,.]+)",
        "Total del Crédito Fiscal": r"Total del Crédito Fiscal\s+\$?\s*([\d,.]+)",
        "A favor del Responsable": r"A favor del Responsable\s+\$?\s*([\d,.]+)",
        "Saldo Técnico a Favor del Responsable del Período": r"Saldo Técnico a Favor del Responsable del Per[ií]odo\s+\$?\s*([\d,.]+)",
        "Saldo de Libre Disponibilidad a favor del contribuyente del período": r"Saldo de Libre Disponibilidad a favor del contribuyente del per[ií]odo\s+\$?\s*([\d,.]+)",
    }
    return _extract(text, patterns)


def _f731(text: str) -> dict[str, str]:
    patterns = {
        "CUIT": r"CUIT:\s*(\d{2}-\d{8}-\d)",
        "Periodo": r"\b(\d{1,2}-\d{4})\b",
        "Apellido y nombre o denominación": r"Apellido y nombre o denominación:\s*(.+)$",
        "Total del débito fiscal del período": r"Total del d[eé]bito fiscal del per[ií]odo\s*\$?\s*([\d,.]+)",
        "Total del crédito fiscal del período": r"Total del cr[eé]dito fiscal del per[ií]odo\s*\$?\s*([\d,.]+)",
    }
    return _extract(text, patterns)


def _f931(text: str) -> dict[str, str]:
    patterns = {
        "CUIT": r"CUIT:\s*(\d{2}-\d{8}-\d|\d{11})",
        "Periodo": r"Per[ií]odo:\s*(\d{2}/\d{4}|\d{6})",
        "Servicios Eventuales": r"Servicios Eventuales:\s*(SI|NO)",
        "Empleados en nómina": r"Empleados en n[oó]mina:\s*([\d.,]+)",
        "Total de aportes (S.S.)": r"Total de aportes \(S\.S\.\)\s*\$?\s*([\d,.]+)",
        "Total de contribuciones (S.S.)": r"Total de contribuciones \(S\.S\.\)\s*\$?\s*([\d,.]+)",
    }
    return _extract(text, patterns)


def _route(extractor: Callable[[str], dict[str, str]]):
    async def extract_pdf(
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        _ = principal
        upload = await uploaded_file_from_request(request, field_name="file")
        if not upload.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF")
        return JSONResponse({"nombre_archivo": upload.filename, "datos": extractor(_pdf_text(upload.content))})

    return extract_pdf


router.add_api_route("/f2002/extraer", _route(_f2002), methods=["POST"], summary="Extraer datos de PDF F2002")
router.add_api_route("/f731/extraer", _route(_f731), methods=["POST"], summary="Extraer datos de PDF F731")
router.add_api_route("/f931/extraer", _route(_f931), methods=["POST"], summary="Extraer datos de PDF F931")
