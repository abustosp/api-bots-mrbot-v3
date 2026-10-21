import asyncio
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Playwright, async_playwright, TimeoutError as PlaywrightTimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.public_errors import safe_public_message
from shared.proxy import get_proxy_config

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Helpers de Parsing
# ----------------------------------------------------------------------
def normalize_text(text: str) -> str:
    return " ".join((text or "").split())

def extract_tables_from_html(html_content: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")
    tables_data: List[Dict[str, Any]] = []

    for idx, table in enumerate(tables):
        headers: List[str] = []
        thead = table.find("thead")
        if thead:
            headers = [normalize_text(th.get_text(" ", strip=True)) for th in thead.find_all("th")]
        else:
            header_cells = table.find_all("th")
            if header_cells:
                headers = [normalize_text(th.get_text(" ", strip=True)) for th in header_cells]

        rows: List[List[str]] = []
        for tr in table.find_all("tr"):
            if thead and tr.find_parent("thead"):
                continue
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            row = [normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
            if any(row):
                rows.append(row)

        if not headers and not rows:
            continue

        records: List[Dict[str, str]] = []
        if headers:
            for row in rows:
                if len(row) == len(headers):
                    records.append({headers[i]: row[i] for i in range(len(headers))})

        if not records:
            for row in rows:
                if len(row) >= 2 and len(row) % 2 == 0:
                    record = {}
                    for idx_cell in range(0, len(row), 2):
                        key = row[idx_cell]
                        value = row[idx_cell + 1]
                        if key:
                            record[key] = value
                    if record:
                        records.append(record)

        class_attr = table.get("class")
        if isinstance(class_attr, list):
            class_attr = " ".join(class_attr)
        tables_data.append(
            {
                "index": idx,
                "id": table.get("id"),
                "class": class_attr,
                "headers": headers,
                "rows": rows,
                "records": records,
            }
        )

    return tables_data

def parse_periodo_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"(\d{2})/(\d{2,4})", text)
    if match:
        month = match.group(1)
        year = match.group(2)
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}{month}"
    match = re.search(r"\b(\d{6})\b", text)
    if match:
        return match.group(1)
    return None

def normalize_header_key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (header or "").lower()).strip("_")

def id_to_label(element_id: str) -> str:
    label = re.sub(r"^ucF931_", "", element_id)
    label = re.sub(r"^lbl", "", label, flags=re.I)
    label = re.sub(r"_lblImporte$", "", label, flags=re.I)
    label = label.replace("_", " ")
    label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
    label = re.sub(r"([A-Za-z])(\d)", r"\1 \2", label)
    label = re.sub(r"(\d)([A-Za-z])", r"\1 \2", label)
    return normalize_text(label)

def extract_ddjj_fields_from_html(html_content: str) -> Dict[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")
    fields: Dict[str, str] = {}
    for element in soup.find_all(id=True):
        element_id = element.get("id")
        if not element_id or not re.match(r"^ucF931_[Ll]bl", element_id):
            continue
        value = normalize_text(element.get_text(" ", strip=True))
        if not value:
            continue
        key = id_to_label(element_id)
        if not key:
            continue
        fields[key] = value
    return fields

HEADER_FIELD_MAP = {
    "ucF931_lblCUIT": "C.U.I.T.",
    "ucF931_lblPeriodo": "Mes - Año",
    "ucF931_lblServiciosEventuales": "Servicios Eventuales",
    "ucF931_lblEmpleadosEnNomina": "Empleados en nómina",
    "ucF931_lblSumaRem1_lblImporte": "Suma de Rem. 1",
    "ucF931_lblSumaRem2_lblImporte": "Suma de Rem. 2",
    "ucF931_lblSumaRem3_lblImporte": "Suma de Rem. 3",
    "ucF931_lblSumaRem4_lblImporte": "Suma de Rem. 4",
    "ucF931_lblSumaRem5_lblImporte": "Suma de Rem. 5",
    "ucF931_lblSumaRem6_lblImporte": "Suma de Rem. 6",
    "ucF931_lblSumaRem7_lblImporte": "Suma de Rem. 7",
    "ucF931_lblSumaRem8_lblImporte": "Suma de Rem. 8",
    "ucF931_lblSumaRem9_lblImporte": "Suma de Rem. 9",
    "ucF931_lblSumaRem10_lblImporte": "Suma de Rem. 10",
    "ucF931_lblRazonSocial": "Apellido y Nombre o Razón Social",
    "ucF931_lblNroVerificador": "Nro. Verificador",
}

SECTION_HEADER_RE = re.compile(r"^[IVX]+\s*-", re.IGNORECASE)
CODE_PAIR_RE = re.compile(r"(\d{3}\s*-\s*[^\d]+?)\s+(\d[\d\.,]*)")

def extract_ddjj_header_fields(html_content: str) -> Dict[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")
    fields: Dict[str, str] = {}
    for element_id, label in HEADER_FIELD_MAP.items():
        element = soup.find(id=element_id)
        if not element:
            continue
        value = normalize_text(element.get_text(" ", strip=True))
        if value:
            fields[label] = value
    return fields

def extract_pairs_from_text(text: str) -> List[tuple[str, str]]:
    pairs: List[tuple[str, str]] = []
    if not text:
        return pairs
    for match in CODE_PAIR_RE.finditer(text):
        label = normalize_text(match.group(1))
        value = normalize_text(match.group(2))
        if label and value:
            pairs.append((label, value))
    if ":" in text:
        key, value = text.split(":", 1)
        key = normalize_text(key)
        value = normalize_text(value)
        if key and value:
            pairs.append((key, value))
    return pairs

def extract_ddjj_sections(html_content: str) -> Dict[str, Dict[str, str]]:
    soup = BeautifulSoup(html_content, "html.parser")
    summary_table = None
    for table in soup.find_all("table"):
        if table.select_one(".F931CeldaResumenHeader"):
            summary_table = table
            break

    if not summary_table:
        return {}

    sections: Dict[str, Dict[str, str]] = {}
    current_left: Optional[str] = None
    current_right: Optional[str] = None

    for tr in summary_table.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue

        cell_texts = [normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
        colspans = [int(cell.get("colspan") or 1) for cell in cells]

        col_index = 0
        header_cells = []
        for cell, text, span in zip(cells, cell_texts, colspans):
            is_header = "F931CeldaResumenHeader" in (cell.get("class") or [])
            if not is_header and text and SECTION_HEADER_RE.match(text):
                is_header = True
            if is_header and text:
                header_cells.append((text, col_index, span))
            col_index += span

        for text, col_index, span in header_cells:
            if span >= 6:
                current_left = text
                current_right = None
                sections.setdefault(current_left, {})
            elif col_index >= 3:
                current_right = text
                sections.setdefault(current_right, {})
            else:
                current_left = text
                sections.setdefault(current_left, {})

        col_texts = [""] * 6
        col_index = 0
        for text, span in zip(cell_texts, colspans):
            if col_index < 6:
                col_texts[col_index] = text
            col_index += span

        def add_entry(section: Optional[str], label: str, value: str) -> None:
            if not section or not label or not value:
                return
            sections.setdefault(section, {})[normalize_text(label)] = normalize_text(value)

        def parse_group(group: List[str], section: Optional[str]) -> None:
            code = group[0] if len(group) > 0 else ""
            desc = group[1] if len(group) > 1 else ""
            value = group[2] if len(group) > 2 else ""
            label = normalize_text(" ".join(part for part in [code, desc] if part))

            if value:
                add_entry(section, label or desc or code, value)
                return

            for text in [code, desc]:
                for key, val in extract_pairs_from_text(text):
                    add_entry(section, key, val)

        parse_group(col_texts[0:3], current_left)
        parse_group(col_texts[3:6], current_right)

        if current_left and current_right is None and len(cells) == 1:
            for key, val in extract_pairs_from_text(cell_texts[0]):
                add_entry(current_left, key, val)

        if current_left and current_right is None and len(cells) > 1:
            non_empty = [text for text in cell_texts if text]
            if len(non_empty) % 2 == 0:
                for idx in range(0, len(non_empty), 2):
                    add_entry(current_left, non_empty[idx], non_empty[idx + 1])

    return sections

def es_vep_pagado(estado: str) -> bool:
    """True si el estado del VEP corresponde a un pago acreditado."""
    return "pagado" in (estado or "").lower()


def build_vep_status_map(tables: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    status_map: Dict[str, Dict[str, str]] = {}
    for table in tables:
        headers_raw = table.get("headers", [])
        headers = [header.lower() for header in headers_raw]
        status_idx = None
        desc_idx = None
        for idx_header, header in enumerate(headers):
            if "estado" in header:
                status_idx = idx_header
            if "descrip" in header:
                desc_idx = idx_header

        for row in table.get("rows", []):
            row_data: Dict[str, str] = {}
            for idx_cell, header in enumerate(headers_raw):
                if idx_cell < len(row):
                    row_data[normalize_header_key(header)] = row[idx_cell]

            status = row[status_idx] if status_idx is not None and status_idx < len(row) else row_data.get("estado", "")
            desc = row[desc_idx] if desc_idx is not None and desc_idx < len(row) else row_data.get("descripcion", "")
            periodo = parse_periodo_from_text(desc)
            if not periodo:
                periodo = parse_periodo_from_text(" ".join(row))
            if not periodo:
                continue
            candidato = {
                "estado": status,
                "descripcion": desc,
                "nro_vep": row_data.get("nro_vep", ""),
                "cuit": row_data.get("cuit", ""),
                "importe": row_data.get("importe", ""),
                "fecha_de_pago": row_data.get("fecha_de_pago", ""),
            }
            previo = status_map.get(periodo)
            # Varias filas para el mismo periodo (volante vencido y comprobante
            # pagado): se informa el comprobante pagado.
            if previo is None or (
                not es_vep_pagado(previo.get("estado", ""))
                and es_vep_pagado(candidato["estado"])
            ):
                status_map[periodo] = candidato
    return status_map

def extract_vep_info_from_html(html_content: str, periodo: Optional[str] = None) -> Dict[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")
    # El listado de VEPs de SETI es una tabla con encabezado propio: la primera
    # fila son rotulos ("Estado", "Enviado a", "Nro. VEP", ...) y sus celdas son
    # <th>. Si se leen como pares clave/valor se devuelve basura del estilo
    # {"estado": "Enviado a", "nro_vep": "CUIT"}. Por eso primero se busca la
    # fila real del periodo pedido en la tabla.
    if periodo:
        status_map = build_vep_status_map(extract_tables_from_html(html_content))
        if periodo in status_map:
            return status_map[periodo]

    raw_fields: Dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cell_tags = tr.find_all(["td", "th"])
        if cell_tags and all(cell.name == "th" for cell in cell_tags):
            # Fila de encabezado: sus celdas son rotulos, no datos.
            continue
        cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in cell_tags]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if len(cells) % 2 == 0:
            for idx in range(0, len(cells), 2):
                raw_fields[cells[idx]] = cells[idx + 1]
        else:
            for cell in cells:
                if ":" in cell:
                    key, value = cell.split(":", 1)
                    key = normalize_text(key)
                    value = normalize_text(value)
                    if key and value:
                        raw_fields[key] = value

    vep_info: Dict[str, str] = {}
    for key, value in raw_fields.items():
        key_lower = key.lower()
        if "estado" in key_lower and not vep_info.get("estado"):
            vep_info["estado"] = value
        elif ("nro" in key_lower or "número" in key_lower) and "vep" in key_lower and not vep_info.get("nro_vep"):
            vep_info["nro_vep"] = value
        elif "cuit" in key_lower and not vep_info.get("cuit"):
            vep_info["cuit"] = value
        elif "importe" in key_lower and not vep_info.get("importe"):
            vep_info["importe"] = value
        elif "fecha" in key_lower and ("pago" in key_lower or "venc" in key_lower) and not vep_info.get("fecha_de_pago"):
            vep_info["fecha_de_pago"] = value
        elif ("descripcion" in key_lower or "concepto" in key_lower) and not vep_info.get("descripcion"):
            vep_info["descripcion"] = value

    if vep_info:
        return vep_info

    tables = extract_tables_from_html(html_content)
    status_map = build_vep_status_map(tables)
    if periodo:
        # Periodo pedido y sin fila propia: no se atribuye el VEP de otro
        # periodo al periodo consultado.
        return status_map.get(periodo, {})
    if len(status_map) == 1:
        return next(iter(status_map.values()))
    return {}
def merge_vep_info(primary: Dict[str, str], fallback: Dict[str, str]) -> Dict[str, str]:
    merged = dict(primary)
    for key, value in fallback.items():
        if not merged.get(key) and value:
            merged[key] = value
    return merged

def _sanitize_component(value: str) -> str:
    return (value or "").replace("/", "_").replace("\\", "_").strip()

def _build_archivo_nombre(
    cuit_representado: str,
    tipo: str,
    periodo: str,
    representado_nombre: str,
    extension: str,
) -> str:
    fin_cuit = (cuit_representado or "")[-1:]
    periodo_value = periodo_to_yyyymm(periodo) or (periodo or "sin_periodo")
    partes = [
        _sanitize_component(fin_cuit),
        _sanitize_component(cuit_representado),
        "Declaracion en linea",
        _sanitize_component(tipo),
        _sanitize_component(periodo_value),
        _sanitize_component(representado_nombre),
    ]
    nombre_base = " - ".join([parte for parte in partes if parte])
    return f"{nombre_base}{extension}"

def periodo_to_yyyymm(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\d{6}", value):
        return value
    match = re.fullmatch(r"(\d{2})[./-](\d{4})", value)
    if match:
        return f"{match.group(2)}{match.group(1)}"
    match = re.fullmatch(r"(\d{4})[./-](\d{2})", value)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return None

def normalize_periodo_range(periodo_desde: str, periodo_hasta: str) -> tuple[Optional[str], Optional[str]]:
    start = periodo_to_yyyymm(periodo_desde)
    end = periodo_to_yyyymm(periodo_hasta)
    if start and end and start > end:
        start, end = end, start
    return start, end

def is_periodo_in_range(periodo: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    if not periodo:
        return False
    try:
        value = int(periodo)
    except ValueError:
        return False
    if start and value < int(start):
        return False
    if end and value > int(end):
        return False
    return True

def build_absolute_url(current_url: str, path: str) -> str:
    match = re.match(r"^(https?://[^/]+)", current_url or "")
    base_url = match.group(1) if match else "https://serviciossegsoc.afip.gob.ar"
    return f"{base_url}{path}"

def normalize_cuit_digits(value: str) -> str:
    return re.sub(r"\D+", "", (value or ""))

def _resolve_representado_option_value(options: List[Dict[str, str]], representado_value: str) -> Optional[str]:
    target_digits = normalize_cuit_digits(representado_value)
    if not target_digits:
        return None

    for option in options:
        option_value = (option.get("value") or "").strip()
        if normalize_cuit_digits(option_value) == target_digits:
            return option_value

    for option in options:
        option_label = (option.get("label") or "").strip()
        if normalize_cuit_digits(option_label).startswith(target_digits):
            option_value = (option.get("value") or "").strip()
            return option_value or target_digits

    return None

async def select_representado_cuit(page, representado_value: str) -> tuple[bool, Optional[str], List[Dict[str, str]]]:
    select_locator = page.locator("#ctl00_ContentPlaceHolder1_ddlCUIT")
    if await select_locator.count() == 0:
        return False, None, []

    options: List[Dict[str, str]] = await select_locator.evaluate(
        """el => Array.from(el.options).map(option => ({
            value: (option.value || '').trim(),
            label: (option.textContent || '').trim(),
        }))"""
    )
    option_value = _resolve_representado_option_value(options, representado_value)
    if not option_value:
        return True, None, options

    await select_locator.select_option(value=option_value)
    return True, option_value, options

# ----------------------------------------------------------------------
# Lógica Bot
# ----------------------------------------------------------------------

async def wait_for_ready(page, step_name: str, timeout_ms: int = 12000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 6000))
    except PlaywrightTimeoutError:
        pass

async def goto_with_retry(page, url: str, step_name: str, attempts: int = 3, timeout_ms: int = 30000) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, timeout=timeout_ms)
            await wait_for_ready(page, step_name, timeout_ms=timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
    raise PlaywrightTimeoutError(f"No se pudo navegar a {url}") from last_error

async def wait_for_ddjj_content(page, timeout_ms: int = 15000) -> None:
    try:
        await page.locator("#ucF931_lblCUIT, .F931CeldaResumenHeader").first.wait_for(
            state="attached",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass

async def get_ddjj_html(page) -> str:
    content = await page.content()
    if "ucF931_lblCUIT" in content or "F931CeldaResumenHeader" in content:
        return content
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_content = await frame.content()
        except Exception:
            continue
        if "ucF931_lblCUIT" in frame_content or "F931CeldaResumenHeader" in frame_content:
            return frame_content
    return content

async def extract_periodo_from_row(row) -> Optional[str]:
    f931_div = row.locator("div[id*='dvVerF931']")
    if await f931_div.count() > 0:
        onclick = await f931_div.first.get_attribute("onclick")
        if onclick:
            match = re.search(r"'(\d{6})'", onclick)
            if match:
                return match.group(1)

    cells = row.locator("td")
    if await cells.count() > 1:
        period_text = await cells.nth(1).inner_text()
        return periodo_to_yyyymm(period_text)
    return None

async def collect_ddjj_rows(page) -> List[Dict[str, Any]]:
    table = page.locator("table.TablaCuiles").filter(has_text=re.compile(r"declaraciones juradas", re.I))
    if await table.count() == 0:
        table = page.locator("table.TablaCuiles")
    rows = table.first.locator("tr").filter(has=page.locator("[id*='RptDJGeneradas']"))
    row_count = await rows.count()
    rows_info: List[Dict[str, Any]] = []
    for idx in range(row_count):
        row = rows.nth(idx)
        periodo_row = await extract_periodo_from_row(row)
        rows_info.append({"index": idx, "row": row, "periodo": periodo_row})
    return rows_info

async def download_ddjj_excel_from_row(
    page,
    row_info: Dict[str, Any],
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    select_locator = row_info["row"].locator("select[id*='opPapeles']")
    if await select_locator.count() == 0:
        return None

    popup_page = None
    confirm_page = None

    async def build_nomina_url() -> Optional[str]:
        onchange = await select_locator.first.get_attribute("onchange")
        periodo_nomina = None
        sec_nomina = None
        if onchange:
            match = re.search(r"AbrirPopupNomina\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onchange)
            if match:
                periodo_nomina = match.group("periodo")
                sec_nomina = match.group("sec")

        f931_div = row_info["row"].locator("div[id*='dvVerF931']")
        if (not periodo_nomina or not sec_nomina) and await f931_div.count() > 0:
            onclick = await f931_div.first.get_attribute("onclick")
            match = re.search(r"AbrirPopupF931\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onclick or "")
            if match:
                periodo_nomina = periodo_nomina or match.group("periodo")
                sec_nomina = sec_nomina or match.group("sec")

        periodo_nomina = periodo_nomina or row_info.get("periodo")
        sec_nomina = sec_nomina or "000"
        if not periodo_nomina:
            return None

        base_url = (
            page.url if page and page.url else "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
        )
        return urljoin(base_url, f"../altadj/ver_nomina.aspx?Periodo={periodo_nomina}&SecDJVig={sec_nomina}")

    try:
        await select_locator.first.scroll_into_view_if_needed()
        existing_pages = set(page.context.pages)
        try:
            async with page.expect_popup(timeout=5000) as popup_info:
                await select_locator.first.select_option("2")
            popup_page = await popup_info.value
        except PlaywrightTimeoutError:
            new_pages = [candidate for candidate in page.context.pages if candidate not in existing_pages]
            if new_pages:
                popup_page = new_pages[-1]
            else:
                nomina_url = await build_nomina_url()
                if not nomina_url:
                    return None
                popup_page = await page.context.new_page()
                await goto_with_retry(popup_page, nomina_url, f"ddjj_nomina_{periodo_safe}")

        if popup_page is None:
            return None

        await wait_for_ready(popup_page, f"ddjj_excel_popup_{periodo_safe}")

        descargar_locator = popup_page.locator("#ctl00_ContentPlaceHolder1_btnBajarCuiles")
        await descargar_locator.wait_for(state="visible", timeout=5000)
        async with popup_page.expect_download() as download_info:
            confirm_task = asyncio.create_task(popup_page.wait_for_event("popup", timeout=5000))
            await descargar_locator.click()
            try:
                confirm_page = await confirm_task
            except (PlaywrightTimeoutError, asyncio.TimeoutError):
                confirm_page = None
        download = await download_info.value

        suggested_name = download.suggested_filename or "ddjj_cuiles.xls"
        suffix = Path(suggested_name).suffix or ".xls"
        download_path = download_dir / _build_archivo_nombre(
            cuit_representado=cuit_representado,
            tipo="DDJJ Cuiles",
            periodo=periodo_safe,
            representado_nombre=representado_nombre,
            extension=suffix,
        )
        await download.save_as(str(download_path))
        return download_path
    except Exception as exc:
        return None
    finally:
        if confirm_page:
            await confirm_page.close()
        if popup_page:
            await popup_page.close()

async def build_ddjj_url(page, row_info: Dict[str, Any]) -> Optional[str]:
    onclick = None
    f931_div = row_info["row"].locator("div[id*='dvVerF931']")
    if await f931_div.count() > 0:
        onclick = await f931_div.first.get_attribute("onclick")

    periodo = row_info.get("periodo")
    sec = "000"
    if onclick:
        match = re.search(r"AbrirPopupF931\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onclick)
        if match:
            periodo = match.group("periodo")
            sec = match.group("sec")

    if not periodo:
        return None

    base_url = page.url if page and page.url else "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
    return urljoin(base_url, f"ver_formulario.aspx?Periodo={periodo}&SecDJVig={sec}")

async def build_vep_url(page, row_info: Dict[str, Any]) -> Optional[str]:
    onclick = None
    vep_div = row_info["row"].locator("div[id*='dvVerVep']")
    if await vep_div.count() > 0:
        onclick = await vep_div.first.get_attribute("onclick")

    periodo = row_info.get("periodo")
    sec = "000"
    if onclick:
        match = re.search(r"AbrirPopupVep\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onclick)
        if match:
            periodo = match.group("periodo")
            sec = match.group("sec")

    if not periodo:
        return None

    base_url = page.url if page and page.url else "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
    return urljoin(base_url, f"ver_Vep.aspx?Periodo={periodo}&SecDJVig={sec}")

async def fetch_vep_info_for_period(page, target_url: str, periodo: str) -> Dict[str, str]:
    if not page or not target_url:
        return {}
    await goto_with_retry(page, target_url, "vep_consulta_mapa")
    vep_html = await page.content()
    tables = extract_tables_from_html(vep_html)
    vep_status_map = build_vep_status_map(tables)
    return vep_status_map.get(periodo, {})

async def fetch_vep_status_map(page, target_url: str) -> Dict[str, Dict[str, str]]:
    if not page or not target_url:
        return {}
    await goto_with_retry(page, target_url, "vep_consulta_mapa")
    vep_html = await page.content()
    tables = extract_tables_from_html(vep_html)
    return build_vep_status_map(tables)

async def save_vep_pdf(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    playwright: Playwright,
    context,
    download_dir: Path,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    pdf_path = download_dir / _build_archivo_nombre(
        cuit_representado=cuit_representado,
        tipo="VEP",
        periodo=periodo_safe,
        representado_nombre=representado_nombre,
        extension=".pdf",
    )
    try:
        await page.emulate_media(media="screen")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
        return pdf_path
    except Exception:
        return None

def periodo_to_sijpdj(periodo: Optional[str]) -> Optional[str]:
    if not periodo or not re.fullmatch(r"\d{6}", periodo):
        return None
    year = periodo[:4]
    month = periodo[4:]
    return f"SIJPDJ{month}/{year[2:]}"


def build_periodo_variants(periodo: str) -> List[str]:
    periodo = (periodo or "").strip()
    if not periodo:
        return []

    if not re.fullmatch(r"\d{6}", periodo):
        return [periodo]

    year = periodo[:4]
    month = periodo[4:]
    return [
        periodo,
        f"{month}/{year}",
        f"{year}/{month}",
        f"{month}-{year}",
        f"{year}-{month}",
        f"{month}.{year}",
    ]


async def wait_for_vep_rows(page, timeout_ms: int = 15000) -> int:
    """Espera a que la tabla SPA de VEPs (SETI) renderice filas."""
    rows = page.locator("table tbody tr")
    waited = 0
    while waited < timeout_ms:
        try:
            count = await rows.count()
        except Exception:
            count = 0
        if count > 0:
            return count
        await page.wait_for_timeout(400)
        waited += 400
    try:
        return await rows.count()
    except Exception:
        return 0


async def find_vep_row(page, periodo: Optional[str], nro_vep: Optional[str] = None):
    """Devuelve la fila del listado de VEPs que corresponde al periodo.

    Un periodo puede tener varias filas (por ejemplo el volante vencido y el
    comprobante pagado que lo reemplazo). Primero se busca el VEP informado en
    el detalle y, si no se conoce su numero, se prioriza la fila pagada: el PDF
    que se descarga tiene que ser el mismo VEP que se informa.
    """
    rows = page.locator("table tbody tr")

    if nro_vep:
        row_by_nro = rows.filter(has_text=re.compile(re.escape(nro_vep)))
        if await row_by_nro.count() > 0:
            return row_by_nro.first

    candidatas = None
    descriptor = periodo_to_sijpdj(periodo)
    if descriptor:
        row_by_desc = rows.filter(has_text=re.compile(re.escape(descriptor), re.I))
        if await row_by_desc.count() > 0:
            candidatas = row_by_desc

    if candidatas is None:
        for variant in build_periodo_variants(periodo or ""):
            row_by_variant = rows.filter(has_text=re.compile(re.escape(variant), re.I))
            if await row_by_variant.count() > 0:
                candidatas = row_by_variant
                break

    if candidatas is None:
        if await rows.count() == 1:
            return rows.first
        return None

    for idx in range(await candidatas.count()):
        fila = candidatas.nth(idx)
        try:
            texto = await fila.inner_text()
        except Exception:
            continue
        if es_vep_pagado(texto):
            return fila
    return candidatas.first


async def export_vep_pdf_from_row(page, row, timeout_ms: int = 15000):
    """Abre el menu 'ver opciones' de la fila y deja visible 'Exportar a PDF'.

    En SETI el link de exportacion vive dentro de un dropdown Bootstrap que
    permanece cerrado (tamano 0x0) hasta que se acciona el boton de la fila,
    por lo que Playwright nunca lo considera visible ni clickeable.
    Devuelve el locator del link listo para clickear, o None.
    """
    toggle = row.locator("button[data-bs-toggle='dropdown']")
    if await toggle.count() == 0:
        toggle = row.locator("span[title='ver opciones']")
    if await toggle.count() == 0:
        return None

    pdf_link = row.locator("a[title*='PDF']")
    if await pdf_link.count() == 0:
        return None

    try:
        await toggle.first.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    for click_kwargs in ({"timeout": 5000}, {"timeout": 5000, "force": True}):
        try:
            await toggle.first.click(**click_kwargs)
            break
        except Exception:
            continue
    else:
        return None

    try:
        await pdf_link.first.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return None
    return pdf_link.first


async def download_vep_pdf_from_page(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
    nro_vep: Optional[str] = None,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    try:
        await wait_for_vep_rows(page)
        target_row = await find_vep_row(page, periodo, nro_vep)
        if target_row is None:
            return None

        export_link = await export_vep_pdf_from_row(page, target_row)
        if export_link is None:
            return None

        download_path = download_dir / _build_archivo_nombre(
            cuit_representado=cuit_representado,
            tipo="VEP",
            periodo=periodo_safe,
            representado_nombre=representado_nombre,
            extension=".pdf",
        )
        async with page.expect_download(timeout=30000) as download_info:
            await export_link.click(timeout=15000)
        download = await download_info.value
        await download.save_as(str(download_path))
        return download_path
    except Exception:
        return None

async def save_ddjj_pdf(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    playwright: Playwright,
    context,
    download_dir: Path,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    pdf_path = download_dir / _build_archivo_nombre(
        cuit_representado=cuit_representado,
        tipo="DDJJ",
        periodo=periodo_safe,
        representado_nombre=representado_nombre,
        extension=".pdf",
    )
    try:
        await page.emulate_media(media="screen")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
        return pdf_path
    except Exception:
        pass
    return None

def save_ddjj_results_json(
    fields: Dict[str, str],
    sections: Dict[str, Dict[str, str]],
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    vep_info: Optional[Dict[str, str]] = None,
    download_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    periodo_safe = periodo if periodo else "sin_periodo"
    payload = {
        "periodo": periodo,
        "scraped_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "vep": vep_info or {},
        "datos": fields,
        "tablas": sections,
    }
    if download_dir:
        output_path = download_dir / _build_archivo_nombre(
            cuit_representado=cuit_representado,
            tipo="DDJJ",
            periodo=periodo_safe,
            representado_nombre=representado_nombre,
            extension=".json",
        )
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

async def bot_declaracion_en_linea(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str] = None,
    representado_nombre: Optional[str] = None,
    periodo_desde: Optional[str] = None,
    periodo_hasta: Optional[str] = None,
    proxy_request: bool = False,
) -> tuple[dict[str, object], list[str], bool]:
    results: dict[str, object] = {}
    error_logs: list[str] = []
    
    if not cuit_representante or not clave_representante:
        error_logs.append("Faltan parametros requeridos.")
        return results, error_logs, False
    if not periodo_desde or not periodo_hasta:
        error_logs.append("Periodo desde y hasta requeridos.")
        return results, error_logs, False

    representado_value = (cuit_representado or cuit_representante or "").strip()
    if not representado_value:
        representado_value = cuit_representante
    
    # Directorio de descarga
    output_dir = DOWNLOAD_DIR / "declaracion_en_linea" / representado_value
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    range_start, range_end = normalize_periodo_range(periodo_desde or "", periodo_hasta or "")
    if not range_start or not range_end:
        error_logs.append("Periodo invalido.")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, proxy=proxy)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        
        try:
            # Login
            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            if not login_result.success:
                return results, error_logs, False
            
            # Abrir servicio
            page1 = await open_arca_service(
                page,
                service_name="DECLARACIÓN EN LÍNEA",
                service_matchers=(
                    re.compile(r"DECLARACI[ÓO]N\s+EN\s+L[ÍI]NEA", re.I),
                    re.compile(r"DECLARACI[ÓO]N\s+EN\s+L[ÍI]NEA\s+Confecci", re.I),
                ),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de DECLARACIÓN EN LÍNEA",
            )
            if page1 is None:
                return results, error_logs, False

            # Seleccion de representado (si aplica) antes del submit inicial.
            has_representado_select, selected_representado_cuit, representado_options = await select_representado_cuit(
                page1,
                representado_value,
            )
            if has_representado_select:
                if not selected_representado_cuit:
                    options_text = ", ".join(
                        (option.get("label") or option.get("value") or "").strip()
                        for option in representado_options
                        if (option.get("label") or option.get("value"))
                    )
                    error_logs.append(
                        f"No se encontró el CUIT representado {representado_value} en el selector de representados. "
                        f"Opciones disponibles: {options_text or 'sin opciones'}"
                    )
                    return results, error_logs, False
                representado_value = selected_representado_cuit

            # Navegacion inicial dentro del servicio
            await page1.get_by_role("button", name="Submit").click()
            await wait_for_ready(page1, "submit")
            
            # Ir a Declaraciones Juradas
            try:
                ingrese = page1.locator("a").filter(has_text=re.compile(r"INGRESE", re.I)).first
                if await ingrese.count() == 0:
                    ingrese = page1.locator("td").filter(has_text=re.compile(r"^INGRESE\.\.\.$")).first
                await ingrese.hover(timeout=5000)

                consultas = page1.locator("a").filter(has_text=re.compile(r"para consultar", re.I)).first
                await consultas.hover(timeout=10000)

                ddjj_link = page1.locator("a").filter(
                    has_text=re.compile(r"Declaraciones Juradas (presentadas|generadas)", re.I)
                )
                if await ddjj_link.count() == 0:
                    ddjj_link = page1.locator("a").filter(has_text=re.compile(r"Declaraciones Juradas", re.I))
                await ddjj_link.first.click(timeout=10000)
            except Exception:
                 # Fallback directo
                direct_url = build_absolute_url(page1.url, "/djproforma/app/consultar/dj_generadas.aspx")
                await goto_with_retry(page1, direct_url, "listado_ddjj_directo")
            
            await wait_for_ready(page1, "listado_ddjj")

            ddjj_rows = await collect_ddjj_rows(page1)
            selected_rows = [
                row_info
                for row_info in ddjj_rows
                if is_periodo_in_range(row_info.get("periodo"), range_start, range_end)
            ]
            if not selected_rows:
                error_logs.append("No se encontraron DDJJ en el rango solicitado.")
                return results, error_logs, False

            # Preparar popup de VEPs (se abre uno global usualmente)
            primary_row = selected_rows[0]
            page3 = None
            target_url = None
            vep_status_map: Dict[str, Dict[str, str]] = {}
            vep_button = primary_row["row"].locator("[id*='ImageButton3']")
            if await vep_button.count() == 0:
                vep_button = page1.locator("#ctl00_ContentPlaceHolder1_RptDJGeneradas_ctl01_ImageButton3")
            try:
                async with page1.expect_popup() as page3_info:
                    await vep_button.first.click()
                page3 = await page3_info.value
                await wait_for_ready(page3, "vep_popup")
                base_url = page3.url.split("#")[0]
                if "setiweb" in base_url:
                    target_url = f"{base_url}#/pago/consulta-veps"
                else:
                    target_url = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
                await goto_with_retry(page3, target_url, "vep_consulta_inicial")
                vep_status_map = await fetch_vep_status_map(page3, target_url)
            except Exception:
                pass

            periodos_data = {}
            for row_info in selected_rows:
                row_periodo = row_info.get("periodo") or "desconocido"
                
                # Descargar Excel Nomina
                ddjj_excel_path = await download_ddjj_excel_from_row(
                    page1,
                    row_info,
                    row_periodo,
                    representado_value,
                    representado_nombre or representado_value,
                    output_dir,
                )

                # Abrir DDJJ Detalle
                ddjj_url = await build_ddjj_url(page1, row_info)
                if ddjj_url:
                    page2 = await context.new_page()
                    await goto_with_retry(page2, ddjj_url, f"ddjj_abierta_{row_periodo}")
                    
                    # Abrir VEP Detalle
                    vep_info = {}
                    vep_pdf_path = None
                    vep_url = await build_vep_url(page1, row_info)
                    if vep_url:
                        vep_page = await context.new_page()
                        await goto_with_retry(vep_page, vep_url, f"vep_detalle_{row_periodo}")
                        vep_html = await vep_page.content()
                        vep_info = extract_vep_info_from_html(vep_html, row_periodo)
                        vep_pdf_path = await download_vep_pdf_from_page(
                            vep_page,
                            row_periodo,
                            representado_value,
                            representado_nombre or representado_value,
                            output_dir,
                            nro_vep=(vep_info or {}).get("nro_vep"),
                        )
                        if not vep_pdf_path:
                            vep_pdf_path = await save_vep_pdf(
                                vep_page,
                                row_periodo,
                                representado_value,
                                representado_nombre or representado_value,
                                playwright,
                                context,
                                output_dir,
                            )
                        await vep_page.close()

                    # Merge con VEP info global
                    if page3 and target_url:
                        vep_info = merge_vep_info(
                            vep_info,
                            vep_status_map.get(row_periodo, {}),
                        )

                    # Click en Borrador (visualizar DDJJ completa)
                    try:
                        await page2.locator("#ucF931_dvBorrador").click()
                        await wait_for_ready(page2, "ddjj_borrador")
                    except Exception:
                        pass

                    await wait_for_ddjj_content(page2)

                    ddjj_html = await get_ddjj_html(page2)
                    ddjj_fields = extract_ddjj_header_fields(ddjj_html)
                    ddjj_sections = extract_ddjj_sections(ddjj_html)
                    
                    save_ddjj_results_json(
                        ddjj_fields,
                        ddjj_sections,
                        row_periodo,
                        representado_value,
                        representado_nombre or representado_value,
                        vep_info,
                        download_dir=output_dir,
                    )
                    
                    ddjj_pdf_path = await save_ddjj_pdf(
                        page2,
                        row_periodo,
                        representado_value,
                        representado_nombre or representado_value,
                        playwright,
                        context,
                        output_dir,
                    )
                    
                    periodos_data[row_periodo] = {
                        "ddjj_pdf": str(ddjj_pdf_path) if ddjj_pdf_path else None,
                        "ddjj_excel": str(ddjj_excel_path) if ddjj_excel_path else None,
                        "vep_pdf": str(vep_pdf_path) if vep_pdf_path else None,
                    }
                    await page2.close()

            results["periodos"] = periodos_data
            
        except Exception as e:
            error_logs.append(
                "Error general: "
                + safe_public_message(
                    e,
                    fallback=str(e),
                    context="consulta",
                    stage="consulta",
                )
            )
            return results, error_logs, False
        finally:
            await context.close()
            await browser.close()

    return results, error_logs, True

async def main():
    # Test local
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    cuit = os.getenv("DECLARACION_EN_LINEA_CUIT_REPRESENTANTE", "")
    cuit_representado = os.getenv("DECLARACION_EN_LINEA_CUIT_REPRESENTADO", "")
    clave = os.getenv("DECLARACION_EN_LINEA_CLAVE_REPRESENTANTE", "")
    
    if cuit and clave:
        res = await bot_declaracion_en_linea(
            cuit, 
            clave, 
            cuit_representado=cuit_representado or cuit, 
            periodo_desde="202401", 
            periodo_hasta="202402"
        )
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
