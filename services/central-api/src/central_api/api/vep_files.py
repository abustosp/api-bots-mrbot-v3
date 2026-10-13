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
    cuit: str = Field(
        min_length=11,
        max_length=14,
        description="CUIT del pagador. Se normaliza a dígitos y debe contener exactamente 11.",
        examples=["00000000000"],
    )
    version: str | None = Field(default=None, description="Versión del formato de registro del encabezado.", examples=["0100"])
    nro_formulario: str | None = Field(default=None, description="Número de formulario; si se omite se usa 20001.", examples=["20001"])
    impuesto: str | None = Field(default=None, description="Código de impuesto del encabezado; si se omite se usa 003.", examples=["003"])
    concepto: str | None = Field(default=None, description="Código de concepto del encabezado; si se omite se usa 003.", examples=["003"])
    tipo_registro: str | None = Field(default=None, description="Tipo de registro del encabezado; si se omite se usa 01.", examples=["01"])


class VEPRegistro(BaseModel):
    nro_form: str = Field(description="Número de formulario del registro.", examples=["20001"])
    cod_tipo_pago: str = Field(description="Código numérico del tipo de pago.", examples=["19"])
    contribuyente_cuit: str = Field(description="CUIT del contribuyente; debe normalizarse a 11 dígitos.", examples=["00000000000"])
    concepto: str = Field(description="Código de concepto del pago.", examples=["19"])
    sub_concepto: str = Field(description="Código de subconcepto del pago.", examples=["0"])
    periodo_fiscal: str = Field(description="Período fiscal que se informará en el TXT.", examples=["202609"])
    obligacion_impuesto: str = Field(description="Código de impuesto de la obligación.", examples=["003"])
    importe: float = Field(description="Importe del registro.", examples=[1234.56])


class VEPTxtRequest(BaseModel):
    header: VEPHeader = Field(description="Datos que forman el encabezado del archivo TXT.")
    registros: list[VEPRegistro] = Field(description="Registros VEP que se incluirán en el archivo; debe enviar al menos uno.")
    fecha: str | None = Field(default=None, description="Fecha del nombre del archivo en formato YYYYMMDD. Si se omite se usa la fecha actual.", examples=["20261012"])


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


@router.post(
    "/txt",
    summary="Genera un archivo TXT de VEP",
    description=(
        "Recibe un objeto JSON con encabezado, fecha opcional y uno o más registros, y devuelve "
        "un archivo de texto descargable en formato VEP. Los CUIT se aceptan con separadores "
        "siempre que, al quitarlos, queden exactamente 11 dígitos. La fecha, si se informa, "
        "debe tener ocho dígitos (YYYYMMDD). No se aplica un límite de registros en esta operación."
    ),
    responses={
        200: {
            "description": "Archivo TXT generado y adjunto. El nombre incluye formulario, CUIT y fecha.",
            "content": {"text/plain": {"schema": {"type": "string"}, "example": "010000000000020001001000030030002\n02<VEP nroFormulario=\"20001\" codTipoPago=\"19\" contribuyenteCUIT=\"00000000000\" concepto=\"19\" subConcepto=\"0\" periodoFiscal=\"202609\" importe=\"1234.56\"> <Obligacion impuesto=\"003\" importe=\"1234.56\"/></VEP>"}},
            "headers": {"Content-Disposition": {"description": "Nombre del archivo descargable.", "schema": {"type": "string"}}},
        },
        400: {
            "description": "No se enviaron registros, la fecha no tiene formato YYYYMMDD o un CUIT no contiene exactamente 11 dígitos. Corrija los datos y vuelva a enviar la solicitud.",
            "content": {"application/json": {"example": {"detail": "Debe enviar al menos un registro."}}},
        },
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        422: {
            "description": "El JSON no cumple el esquema de encabezado y registros: revise los campos obligatorios, tipos y longitud declarada del CUIT.",
        },
        503: {
            "description": "No se pudo verificar la autenticación por indisponibilidad del servicio de datos. Reintente más tarde.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
    },
)
def txt(payload: VEPTxtRequest, principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    """Genera un TXT descargable a partir de un encabezado y sus registros VEP."""
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


@router.post(
    "/zip",
    summary="Genera un ZIP de TXT de VEP desde una planilla",
    description=(
        "Genera un ZIP con archivos TXT agrupados por lote a partir de una planilla XLSX previamente "
        "cargada al bucket uploads. La primera hoja debe incluir las columnas "
        "ID_Lote_mrbot, Fecha VEP_mrbot, CUIT Pagador_mrbot, Fecha_Expiracion_mrbot, "
        "CUIT_Contibuyente_mrbot, Impuesto_mrbot, Concepto_mrbot, Subconcepto_mrbot, "
        "Anticipo_mrbot, Periodo_mrbot, Importe_mrbot, Formulario_mrbot y Cod Pago_mrbot. "
        "Cada TXT contiene como máximo 600 registros; los lotes mayores se dividen en varios archivos. "
        "El XLSX debe tener hasta 52.428.800 bytes (50 MiB).\n\n"
        "Flujo de dos pasos: (1) solicite un ticket en POST /api/v3/uploads con `bot=vep_archivo`, "
        "`operacion=generar`, `campo=archivo_excel`, nombre, tipo y tamaño de la planilla. La respuesta "
        "incluye `upload_url` y `object_key`; cargue los bytes usando esa URL y el encabezado "
        '`Content-Type` indicado. (2) envíe `{"object_key":"<object_key>"}` a este endpoint.'
    ),
    openapi_extra={
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
                                "description": "Clave recibida al cargar previamente el XLSX en el bucket uploads.",
                                "example": "550e8400-e29b-41d4-a716-446655440000/vep_archivo/sin_cuit/lotes.xlsx",
                            }
                        },
                    },
                    "example": {"object_key": "550e8400-e29b-41d4-a716-446655440000/vep_archivo/sin_cuit/lotes.xlsx"},
                },
            },
        },
    },
    responses={
        200: {
            "description": "Archivo ZIP descargable con los TXT generados, organizados por CUIT pagador y lote.",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {"Content-Disposition": {"description": "Nombre del archivo ZIP descargable.", "schema": {"type": "string"}, "example": "attachment; filename=\"veps_20261012.zip\""}},
        },
        400: {
            "description": "La carga está vacía o no es un XLSX legible, faltan columnas requeridas, ningún registro contiene lote, o un CUIT/código numérico no tiene formato válido. Use una planilla XLSX con todos los encabezados requeridos y corrija los valores indicados.",
            "content": {"application/json": {"example": {"detail": "Faltan columnas requeridas: Importe_mrbot"}}},
        },
        413: {"description": "El archivo supera 52.428.800 bytes (50 MiB). Redúzcalo y vuelva a enviarlo."},
        415: {"description": "El archivo no tiene formato XLSX. Envíe un archivo con extensión .xlsx."},
        422: {"description": "La referencia no pertenece a la cuenta autenticada, el archivo no está en el bucket uploads o fue eliminado. Envíe un object_key obtenido previamente con POST /api/v3/uploads."},
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No fue posible guardar o leer la copia del archivo, o no pudo verificarse la autenticación. Revise el servicio y vuelva a intentar.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
    },
)
async def zip_vep(request: Request, principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    """Convierte una planilla XLSX en un ZIP de archivos TXT de VEP."""
    upload = await uploaded_file_from_request(
        request, field_name="archivo_excel", principal=principal,
        bot="vep_archivo", operation="generar",
    )
    try:
        body = _zip_from_rows(_xlsx_rows(upload.content), datetime.now().strftime("%Y%m%d"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = f"veps_{datetime.now().strftime('%Y%m%d')}.zip"
    return Response(body, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get(
    "/modelo",
    summary="Descarga el modelo de Excel para VEP",
    description=(
        "Devuelve una planilla XLSX vacía con los encabezados requeridos para generar archivos "
        "TXT de VEP mediante la operación de conversión por lote. Descargue el archivo, agregue "
        "una fila por registro y conserve los nombres de las columnas."
    ),
    responses={
        200: {
            "description": "Plantilla XLSX descargable con una fila de encabezados y sin registros.",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {"Content-Disposition": {"description": "Nombre de la plantilla descargable.", "schema": {"type": "string"}, "example": "attachment; filename=\"vep_consolidado_modelo.xlsx\""}},
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
def modelo(principal: ApiPrincipal = Depends(require_api_principal)) -> Response:
    """Entrega la plantilla XLSX con los encabezados requeridos para VEP."""
    _ = principal
    return Response(_model_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="vep_consolidado_modelo.xlsx"'})
