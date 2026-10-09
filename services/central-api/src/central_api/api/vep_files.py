"""Pure-computation V1 VEP file endpoints.

Browser-backed ``/vep/carga`` remains a job alias.  TXT, ZIP and template
operations are deterministic and intentionally execute inside central-api.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from central_api.api.dependencies import require_api_principal
from central_api.api.file_inputs import uploaded_file_from_request
from central_api.security.principals import ApiPrincipal

router = APIRouter(prefix="/vep", tags=["VEP"])

COLUMNAS_REQUERIDAS = (
    "ID_Lote_mrbot", "Fecha VEP_mrbot", "CUIT Pagador_mrbot",
    "Fecha_Expiracion_mrbot", "CUIT_Contibuyente_mrbot", "Impuesto_mrbot",
    "Concepto_mrbot", "Subconcepto_mrbot", "Anticipo_mrbot", "Periodo_mrbot",
    "Importe_mrbot", "Formulario_mrbot", "Cod Pago_mrbot",
)
MAX_REGISTROS_POR_LOTE = 600
_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class VEPHeader(BaseModel):
    cuit: str = Field(min_length=11, max_length=14)
    version: str | None = None
    nro_formulario: str | None = None
    impuesto: str | None = None
    concepto: str | None = None
    tipo_registro: str | None = None


class VEPRegistro(BaseModel):
    nro_form: str
    cod_tipo_pago: str
    contribuyente_cuit: str
    concepto: str
    sub_concepto: str
    periodo_fiscal: str
    obligacion_impuesto: str
    importe: float


class VEPTxtRequest(BaseModel):
    header: VEPHeader
    registros: list[VEPRegistro]
    fecha: str | None = None


def _digits(value: object, field: str = "CUIT") -> str:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 11:
        raise HTTPException(status_code=400, detail=f"{field} debe tener 11 dígitos.")
    return digits


def _number(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text


def _integer(value: object, field: str) -> str:
    value = _number(value)
    if not value.isdigit():
        raise ValueError(f"El campo {field} debe ser un entero.")
    return value


def _header(cuit: str, count: int, *, version: str = "0100", nro_formulario: str = "20001", impuesto: str = "003", concepto: str = "003", tipo_registro: str = "01") -> str:
    return f"{tipo_registro}{cuit.zfill(11)}{nro_formulario.zfill(5)}{version.zfill(5)}{impuesto.zfill(3)}{concepto.zfill(3)}{count:04d}"


def _registry(*, nro_form: str, cod_tipo_pago: str, contribuyente_cuit: str, concepto: str, sub_concepto: str, periodo_fiscal: str, obligacion_impuesto: str, importe: float) -> str:
    return (
        f'02<VEP nroFormulario="{nro_form}" codTipoPago="{cod_tipo_pago}" '
        f'contribuyenteCUIT="{contribuyente_cuit}" concepto="{concepto}" '
        f'subConcepto="{sub_concepto}" periodoFiscal="{periodo_fiscal}" '
        f'importe="{importe}"> <Obligacion impuesto="{obligacion_impuesto}" '
        f'importe="{importe}"/></VEP>'
    )


def _xlsx_rows(content: bytes) -> list[dict[str, str]]:
    """Read the first XLSX sheet without adding pandas/openpyxl to central-api."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root.findall("m:si", _XLSX_NS)]
            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in archive.namelist():
                sheet_name = next(name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
            sheet = ET.fromstring(archive.read(sheet_name))
    except (KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError("No se pudo leer el archivo Excel.") from exc
    rows: list[list[str]] = []
    for row in sheet.findall(".//m:sheetData/m:row", _XLSX_NS):
        values: list[str] = []
        for cell in row.findall("m:c", _XLSX_NS):
            ref = cell.get("r", "A1")
            col = re.sub(r"\d", "", ref)
            index = 0
            for char in col:
                index = index * 26 + ord(char.upper()) - 64
            while len(values) < index:
                values.append("")
            cell_type = cell.get("t")
            if cell_type == "inlineStr":
                value = "".join(cell.find("m:is", _XLSX_NS).itertext()) if cell.find("m:is", _XLSX_NS) is not None else ""
            else:
                node = cell.find("m:v", _XLSX_NS)
                value = node.text if node is not None and node.text is not None else ""
                if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
            values[index - 1] = value
        rows.append(values)
    if not rows:
        raise ValueError("El archivo Excel está vacío.")
    headers = rows[0]
    missing = [column for column in COLUMNAS_REQUERIDAS if column not in headers]
    if missing:
        raise ValueError("Faltan columnas requeridas: " + ", ".join(missing))
    positions = {name: headers.index(name) for name in COLUMNAS_REQUERIDAS}
    return [
        {name: row[position] if position < len(row) else "" for name, position in positions.items()}
        for row in rows[1:]
    ]


def _zip_from_rows(rows: list[dict[str, str]], fecha: str) -> bytes:
    batches: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        batch = _number(row["ID_Lote_mrbot"])
        if batch:
            batches.setdefault(batch, []).append(row)
    if not batches:
        raise HTTPException(status_code=400, detail="No se generaron archivos TXT.")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for batch_id, batch_rows in batches.items():
            cuit = _digits(batch_rows[0]["CUIT Pagador_mrbot"], "CUIT Pagador_mrbot")
            count = (len(batch_rows) + MAX_REGISTROS_POR_LOTE - 1) // MAX_REGISTROS_POR_LOTE
            for index in range(count):
                subset = batch_rows[index * MAX_REGISTROS_POR_LOTE:(index + 1) * MAX_REGISTROS_POR_LOTE]
                records = [
                    _registry(
                        nro_form=str(row["Formulario_mrbot"]), cod_tipo_pago=_integer(row["Cod Pago_mrbot"], "codTipoPago"),
                        contribuyente_cuit=_digits(row["CUIT_Contibuyente_mrbot"], "CUIT_Contibuyente_mrbot"), concepto=_integer(row["Concepto_mrbot"], "concepto"),
                        sub_concepto=_integer(row["Subconcepto_mrbot"], "subConcepto"), periodo_fiscal=str(row["Periodo_mrbot"]),
                        obligacion_impuesto=_integer(row["Impuesto_mrbot"], "Obligacion impuesto"), importe=float(row["Importe_mrbot"]),
                    ) for row in subset
                ]
                name = f"F20001.cuit.{cuit}.fecha.{fecha}.nro.{index + 1:03d}de{count:03d}.txt"
                output.writestr(f"{cuit}/lote_{batch_id}/{name}", _header(cuit, len(records) + 1) + "\n" + "\n".join(records))
    return buffer.getvalue()


def _model_xlsx() -> bytes:
    headers = list(COLUMNAS_REQUERIDAS)
    cells = "".join(f'<c r="{chr(65 + index)}1" t="inlineStr"><is><t>{header}</t></is></c>' for index, header in enumerate(headers))
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="VEP" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">{cells}</row></sheetData></worksheet>')
    return buffer.getvalue()


@router.post("/txt", summary="Genera un TXT individual")
def txt(payload: VEPTxtRequest, principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    _ = principal
    if not payload.registros:
        raise HTTPException(status_code=400, detail="Debe enviar al menos un registro.")
    fecha = payload.fecha or datetime.now().strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", fecha):
        raise HTTPException(status_code=400, detail="fecha debe tener formato YYYYMMDD.")
    cuit = _digits(payload.header.cuit, "header.cuit")
    options = {name: value for name, value in payload.header.model_dump(exclude_none=True).items() if name != "cuit"}
    records = [
        _registry(nro_form=row.nro_form, cod_tipo_pago=row.cod_tipo_pago, contribuyente_cuit=_digits(row.contribuyente_cuit, "contribuyente_cuit"), concepto=row.concepto, sub_concepto=row.sub_concepto, periodo_fiscal=row.periodo_fiscal, obligacion_impuesto=row.obligacion_impuesto, importe=row.importe)
        for row in payload.registros
    ]
    form = _number(options.get("nro_formulario", "20001")).zfill(5) or "20001"
    name = f"F{form}.cuit.{cuit}.fecha.{fecha}.nro.001de001.txt"
    return Response(_header(cuit, len(records) + 1, **options) + "\n" + "\n".join(records), media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/zip", summary="Genera un ZIP con TXT de VEP")
async def zip_vep(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    _ = principal
    upload = await uploaded_file_from_request(request, field_name="archivo_excel")
    if not upload.content:
        raise HTTPException(status_code=400, detail="El archivo esta vacio.")
    try:
        body = _zip_from_rows(_xlsx_rows(upload.content), datetime.now().strftime("%Y%m%d"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = f"veps_{datetime.now().strftime('%Y%m%d')}.zip"
    return Response(body, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/modelo", summary="Descarga el modelo de Excel de VEP")
def modelo(principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    _ = principal
    return Response(_model_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="vep_consolidado_modelo.xlsx"'})
