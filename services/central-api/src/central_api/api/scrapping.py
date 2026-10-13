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
            detail="El servicio de extracción de PDF no está disponible temporalmente.",
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
        upload = await uploaded_file_from_request(
            request, field_name="scrapping_pdf", principal=principal,
            bot="rcel", operation="descargar",
        )
        return JSONResponse({"nombre_archivo": upload.filename, "datos": extractor(_pdf_text(upload.content))})

    return extract_pdf


_EXTRACTION_EXAMPLES = {
    "F2002": {
        "nombre_archivo": "F2002.pdf",
        "datos": {
            "CUIT Nº": "20123456786",
            "Apellido y Nombre o Razón Social": "Empresa Ejemplo S.A.",
            "Periodo": "202608",
            "Secuencia": "Original",
            "Fecha de Presentación": "15/09/2026",
            "Total del Débito Fiscal": "1234,56",
            "Total del Crédito Fiscal": "987,65",
            "A favor del Responsable": "246,91",
            "Saldo Técnico a Favor del Responsable del Período": "100,00",
            "Saldo de Libre Disponibilidad a favor del contribuyente del período": "146,91",
        },
    },
    "F731": {
        "nombre_archivo": "F731.pdf",
        "datos": {
            "CUIT": "20-12345678-6",
            "Periodo": "08-2026",
            "Apellido y nombre o denominación": "Empresa Ejemplo S.A.",
            "Total del débito fiscal del período": "1234,56",
            "Total del crédito fiscal del período": "987,65",
        },
    },
    "F931": {
        "nombre_archivo": "F931.pdf",
        "datos": {
            "CUIT": "20123456786",
            "Periodo": "08/2026",
            "Servicios Eventuales": "NO",
            "Empleados en nómina": "42",
            "Total de aportes (S.S.)": "1234,56",
            "Total de contribuciones (S.S.)": "2345,67",
        },
    },
}


def _upload_openapi(formulario: str) -> dict:
    ejemplo_respuesta = _EXTRACTION_EXAMPLES[formulario]
    return {
        "description": (
            f"Realiza la extracción de datos del formulario {formulario} desde un PDF cargado "
            "previamente al bucket uploads. Se admite únicamente la extensión .pdf y hasta "
            "52.428.800 bytes (50 MiB). Se analiza la primera página. `datos` incluye solo los "
            "campos que se pudieron identificar, con sus valores como cadenas.\n\n"
            "Flujo de dos pasos: (1) solicite un ticket en POST /api/v3/uploads con "
            '`{"bot":"rcel","operacion":"descargar","campo":"scrapping_pdf",'
            '"filename":"f2002.pdf","content_type":"application/pdf","size_bytes":12345}`. '
            "La respuesta incluye `upload_url` y `object_key`; cargue los bytes usando esa URL y "
            'el encabezado `Content-Type` indicado. (2) envíe `{"object_key":"<object_key>"}` '
            "a este endpoint."
        ),
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
                                "description": "Clave recibida al cargar previamente el PDF en el bucket uploads.",
                                "example": "550e8400-e29b-41d4-a716-446655440000/rcel/sin_cuit/f2002.pdf",
                            }
                        },
                    },
                    "example": {"object_key": "550e8400-e29b-41d4-a716-446655440000/rcel/sin_cuit/f2002.pdf"},
                }
            },
        },
        "responses": {
            200: {
                "description": (
                    "La extracción finalizó. Devuelve el nombre recibido y un objeto `datos` "
                    "con las claves reconocidas por el extractor de este formulario."
                ),
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["nombre_archivo", "datos"],
                            "properties": {
                                "nombre_archivo": {"type": "string"},
                                "datos": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                        },
                        "example": ejemplo_respuesta,
                    }
                },
            },
            401: {
                "description": (
                    "La credencial de la cuenta falta, no es válida o está deshabilitada. "
                    "Envíe una clave de API activa con el método de autenticación admitido."
                )
            },
            413: {"description": "El archivo supera 52.428.800 bytes (50 MiB). Reduzca el tamaño y vuelva a intentar."},
            415: {
                "description": (
                    "El nombre del archivo no termina en `.pdf`. Envíe un documento PDF con esa extensión."
                )
            },
            422: {
                "description": (
                    "La referencia no pertenece a la cuenta autenticada, el archivo no está en el bucket uploads "
                    "o fue eliminado. Envíe un object_key obtenido previamente con POST /api/v3/uploads."
                )
            },
            429: {
                "description": (
                    "Se alcanzó temporalmente el límite de solicitudes. Espere el intervalo indicado "
                    "en `Retry-After` si está presente y vuelva a intentar con menor frecuencia."
                )
            },
            503: {
                "description": (
                    "El servicio de extracción o el acceso al archivo no está disponible temporalmente. "
                    "Espere unos minutos y vuelva a intentar; si el problema persiste, contacte con soporte."
                )
            },
        },
    }


router.add_api_route(
    "/f2002/extraer", _route(_f2002), methods=["POST"],
    summary="Extraer datos de PDF F2002", openapi_extra=_upload_openapi("F2002"),
)
router.add_api_route(
    "/f731/extraer", _route(_f731), methods=["POST"],
    summary="Extraer datos de PDF F731", openapi_extra=_upload_openapi("F731"),
)
router.add_api_route(
    "/f931/extraer", _route(_f931), methods=["POST"],
    summary="Extraer datos de PDF F931", openapi_extra=_upload_openapi("F931"),
)
