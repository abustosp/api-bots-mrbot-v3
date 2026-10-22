import asyncio
import os
import re
import tempfile
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import Playwright, async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

EXPAND_CUOTAS_SCRIPT = r"""
(selector) => {
    const table = document.querySelector(selector);
    if (!table) {
        return false;
    }

    const rows = table.querySelectorAll("tbody tr[id^='ContentPlaceHolder1_rptDetallePlanPago_trRpt_']");
    for (const row of rows) {
        row.style.display = "table-row";
    }

    return true;
}
"""

EXPAND_OBLIGACIONES_SCRIPT = r"""
(selector) => {
    if (!document.getElementById("codex-obligaciones-style")) {
        const style = document.createElement("style");
        style.id = "codex-obligaciones-style";
        style.textContent = `
            table[id^="tableDetalleDeuda"] {
                width: 100% !important;
                table-layout: auto !important;
                font-size: 9px !important;
            }
            table[id^="tableDetalleDeuda"] th,
            table[id^="tableDetalleDeuda"] td {
                padding: 2px 4px !important;
                vertical-align: middle !important;
            }
        `;
        document.head.appendChild(style);
    }

    const table = document.querySelector(selector);
    if (!table) {
        return false;
    }

    table.style.width = "100%";
    table.style.tableLayout = "auto";

    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
        row.style.display = "table-row";
    }

    const cells = table.querySelectorAll("th, td");
    for (const cell of cells) {
        cell.style.display = "table-cell";
        cell.style.visibility = "visible";
    }

    const hidden = table.querySelectorAll(".hidden-print");
    for (const el of hidden) {
        el.classList.remove("hidden-print");
    }

    let parent = table.parentElement;
    while (parent) {
        const style = window.getComputedStyle(parent);
        const overflowValues = [style.overflow, style.overflowX, style.overflowY];
        if (overflowValues.some((value) => value === "auto" || value === "scroll" || value === "hidden")) {
            parent.style.overflow = "visible";
            parent.style.overflowX = "visible";
            parent.style.overflowY = "visible";
            parent.style.height = "auto";
            parent.style.maxHeight = "none";
        }
        parent = parent.parentElement;
    }

    return true;
}
"""

def normalize_text(text: str) -> str:
    return " ".join((text or "").split())

def normalize_cuit(cuit: Optional[str]) -> str:
    return re.sub(r"\D", "", cuit or "")

def extract_cuit_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    cleaned = " ".join(text.split())
    patterns = [
        r"Contribuyente\s*:?\s*(\d{2}-?\d{8}-?\d)",
        r"Representado\s*:?\s*(\d{2}-?\d{8}-?\d)",
        r"C\.?U\.?I\.?T\.?\s*:?\s*(\d{2}-?\d{8}-?\d)",
        r"CUIT\s*:?\s*(\d{2}-?\d{8}-?\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return match.group(1)
    fallback = re.search(r"\b\d{2}-?\d{8}-?\d\b", cleaned)
    return fallback.group(0) if fallback else None

def extract_cuit_from_html(html_content: str) -> Optional[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(" ", strip=True)
    return extract_cuit_from_text(text)

def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "reporte"

def is_hidden_cell(cell) -> bool:
    style = (cell.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    class_list = cell.get("class") or []
    return "hidden-print" in class_list

def is_hidden_element(element) -> bool:
    if not element:
        return True
    style = (element.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    class_list = element.get("class") or []
    hidden_classes = {"hidden", "d-none", "visually-hidden", "hidden-print"}
    return any(class_name in hidden_classes for class_name in class_list)


async def navigate_back_to_plan_list(
    page,
    max_retries: int = 3,
    timeout_per_attempt: int = 15000,
) -> bool:
    """
    Navega desde el detalle de un plan hacia el listado de planes.
    Prueba multiples estrategias en orden con reintentos.
    Si ya estamos en el listado, retorna True inmediatamente.
    """
    if await verify_on_plan_list_page(page):
        return True

    for attempt in range(max_retries):
        # --- Estrategia 1: boton Volver con ID especifico ---
        btn_volver = page.locator("#ContentPlaceHolder1_btnVolver")
        try:
            if await btn_volver.count() > 0 and await btn_volver.is_visible(timeout=3000):
                await btn_volver.click(timeout=timeout_per_attempt)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass

        # --- Estrategia 2: cualquier elemento role=button con texto "Volver" ---
        try:
            volver_button = page.get_by_role("button", name="Volver", exact=False).first
            if await volver_button.count() > 0 and await volver_button.is_visible(timeout=2000):
                await volver_button.click(timeout=timeout_per_attempt)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass

        # --- Estrategia 3: cualquier enlace con texto "Volver" ---
        try:
            volver_link = page.get_by_role("link", name="Volver", exact=False).first
            if await volver_link.count() > 0 and await volver_link.is_visible(timeout=2000):
                await volver_link.click(timeout=timeout_per_attempt)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass

        # --- Estrategia 4: CSS selector generico para Volver ---
        for css_selector in [
            "a:has-text('Volver')",
            "button:has-text('Volver')",
            "input[value='Volver']",
            ".btnVolver",
            "#ContentPlaceHolder1_btnCancelar",
            "[id*='btnVolver']",
            "[id*='btn_volver']",
        ]:
            try:
                el = page.locator(css_selector).first
                if await el.count() > 0 and await el.is_visible(timeout=1000):
                    await el.click(timeout=timeout_per_attempt)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass

        # --- Estrategia 5: JavaScript history.back() ---
        try:
            went_back = await page.evaluate(
                "() => { window.history.back(); return true; }"
            )
            if went_back:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
                current_url = page.url
                if "detalle" not in current_url.lower() and "plan" not in current_url.lower():
                    return True
        except Exception:
            pass

        # --- Estrategia 6: navegacion directa a la URL del listado ---
        try:
            current_url = page.url
            list_url = re.sub(r"[?&]id=[^&]*", "", current_url)
            list_url = re.sub(r"/Detalle[^/]*", "", list_url)
            if list_url and list_url != current_url:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

        if attempt < max_retries - 1:
            await asyncio.sleep(2)

    return False


async def verify_on_plan_detail_page(page) -> bool:
    """Verifica que la pagina actual es la de detalle de un plan."""
    try:
        ver_pagos = page.get_by_role("button", name="Ver Pagos")
        if await ver_pagos.count() > 0:
            return True

        plan_de_pago = page.get_by_role("button", name="Plan de Pago")
        if await plan_de_pago.count() > 0:
            return True

        cabecera_plan = page.locator("#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_nroPlan")
        if await cabecera_plan.count() > 0:
            return True

        btn_volver = page.locator("#ContentPlaceHolder1_btnVolver")
        if await btn_volver.count() > 0 and await btn_volver.is_visible():
            return True

        return False
    except Exception:
        return False


async def verify_on_plan_list_page(page) -> bool:
    """Verifica que la pagina actual es el listado de planes."""
    try:
        search_table = page.locator("table.searchTable")
        if await search_table.count() > 0 and await search_table.first.is_visible():
            return True

        rpt_detalle = page.locator("[id^='ContentPlaceHolder1_rpt_detallePlan_']")
        if await rpt_detalle.count() > 0:
            return True

        return False
    except Exception:
        return False


async def ensure_on_plan_detail(
    page,
    detalle_id: Optional[str],
    *,
    navigate_back: bool = True,
    logger=None,
) -> bool:
    """Ensure we are on the plan detail page, recovering if necessary."""
    if await verify_on_plan_detail_page(page):
        return True
    if logger:
        logger.log("Recuperando pagina de detalle")
    if navigate_back:
        await navigate_back_to_plan_list(page)
    if detalle_id:
        return await open_plan_detail(page, detalle_id)
    return False


async def wait_loaded(page, timeout: int = 15000) -> None:
    """Wait for page load and a brief stabilization pause."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    await page.wait_for_timeout(500)


def inject_base_tag(html_content: str, base_url: str) -> str:
    if re.search(r"<base\\b", html_content, re.IGNORECASE):
        return html_content
    base_tag = f'<base href="{escape(base_url)}">'
    match = re.search(r"<head[^>]*>", html_content, re.IGNORECASE)
    if match:
        idx = match.end()
        return f"{html_content[:idx]}{base_tag}{html_content[idx:]}"
    return f"<head>{base_tag}</head>{html_content}"

async def expand_cuotas_for_export(page, table_selector: str) -> None:
    try:
        await page.evaluate(EXPAND_CUOTAS_SCRIPT, table_selector)
        await page.wait_for_timeout(300)
    except Exception:
        pass

async def expand_obligaciones_for_export(page, table_selector: str) -> None:
    try:
        await page.evaluate(EXPAND_OBLIGACIONES_SCRIPT, table_selector)
        await page.wait_for_timeout(300)
    except Exception:
        pass

async def try_select_cuit_option(page, cuit_target: str) -> bool:
    dropdown = page.locator("#ContentPlaceHolder1_ddlCUIT")
    if not cuit_target:
        return False
    try:
        await dropdown.select_option(cuit_target)
        return True
    except Exception:
        normalized_target = normalize_cuit(cuit_target)
        options = dropdown.locator("option")
        option_count = await options.count()
        for idx in range(option_count):
            option = options.nth(idx)
            value = (await option.get_attribute("value")) or ""
            label = (await option.text_content()) or ""
            if normalize_cuit(value) == normalized_target or normalize_cuit(label) == normalized_target:
                try:
                    if value:
                        await dropdown.select_option(value)
                    else:
                        await dropdown.select_option(label=label)
                    return True
                except Exception:
                    return False
    return False

async def get_available_cuits(page) -> Optional[List[str]]:
    dropdown = page.locator("#ContentPlaceHolder1_ddlCUIT")
    if await dropdown.count() == 0:
        return None
    try:
        await dropdown.wait_for(state="visible", timeout=5000)
    except Exception:
        pass
    options = dropdown.locator("option")
    option_count = await options.count()
    cuits: List[str] = []
    for idx in range(option_count):
        option = options.nth(idx)
        value = (await option.get_attribute("value")) or ""
        label = (await option.text_content()) or ""
        cuit = extract_cuit_from_text(value) or extract_cuit_from_text(label)
        if cuit and cuit not in cuits:
            cuits.append(cuit)
    return cuits

async def select_cuit_if_available(
    page,
    cuit_representado: Optional[str],
) -> bool:
    dropdown = page.locator("#ContentPlaceHolder1_ddlCUIT")
    if await dropdown.count() == 0:
        return False
    try:
        await dropdown.wait_for(state="visible", timeout=5000)
    except Exception:
        pass
    if cuit_representado:
        await try_select_cuit_option(page, cuit_representado)

    aceptar = page.get_by_role("button", name="Aceptar")
    if await aceptar.count() > 0:
        await aceptar.click()
        await page.wait_for_load_state("domcontentloaded")
    return True

async def get_selected_cuit_from_dropdown(page) -> Optional[str]:
    dropdown = page.locator("#ContentPlaceHolder1_ddlCUIT")
    if await dropdown.count() == 0:
        return None
    try:
        selected = dropdown.locator("option:checked")
        if await selected.count() == 0:
            selected = dropdown.locator("option[selected]")
        if await selected.count() == 0:
            return None
        value = (await selected.first.get_attribute("value")) or ""
        label = (await selected.first.text_content()) or ""
        return extract_cuit_from_text(value) or extract_cuit_from_text(label)
    except Exception:
        return None

async def verify_contribuyente_cuit(
    page,
    expected_cuit: Optional[str],
) -> Tuple[Optional[str], bool]:
    html_content = await page.content()
    actual_cuit = extract_cuit_from_html(html_content)
    if not actual_cuit:
        actual_cuit = await get_selected_cuit_from_dropdown(page)
    if not actual_cuit:
        return None, False
    if expected_cuit:
        if normalize_cuit(actual_cuit) != normalize_cuit(expected_cuit):
            return actual_cuit, False
    return actual_cuit, True

def extract_table_rows(section) -> List[List[str]]:
    if not section:
        return []
    rows: List[List[str]] = []
    span_map: Dict[Tuple[int, int], str] = {}
    if section.name == "table":
        trs = section.find_all("tr")
    else:
        trs = section.find_all("tr", recursive=False)
    for row_idx, tr in enumerate(trs):
        row: List[str] = []
        col_idx = 0
        cells = tr.find_all(["td", "th"], recursive=False)
        for cell in cells:
            if is_hidden_cell(cell):
                continue
            while (row_idx, col_idx) in span_map:
                row.append(span_map.pop((row_idx, col_idx)))
                col_idx += 1
            value = normalize_text(cell.get_text(" ", strip=True))
            rowspan = int(cell.get("rowspan") or 1)
            colspan = int(cell.get("colspan") or 1)
            for span_col in range(colspan):
                cell_value = value if span_col == 0 else ""
                row.append(cell_value)
                if rowspan > 1:
                    for span_row in range(1, rowspan):
                        span_map[(row_idx + span_row, col_idx)] = cell_value
                col_idx += 1
        while (row_idx, col_idx) in span_map:
            row.append(span_map.pop((row_idx, col_idx)))
            col_idx += 1
        if any(row):
            rows.append(row)
    return rows

def extract_table_from_html(html_content: str, table_selector: str) -> Tuple[List[str], List[List[str]]]:
    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.select_one(table_selector)
    if not table:
        return [], []

    header_rows = extract_table_rows(table.find("thead"))
    headers: List[str] = []
    if header_rows:
        max_cols = max(len(row) for row in header_rows)
        merged: List[str] = []
        for col_idx in range(max_cols):
            parts = []
            for row in header_rows:
                if col_idx < len(row) and row[col_idx]:
                    parts.append(row[col_idx])
            merged.append(" - ".join(parts).strip())
        headers = merged

    body_section = table.find("tbody") or table
    rows = extract_table_rows(body_section)
    if not headers and rows:
        headers = [f"col_{idx + 1}" for idx in range(max(len(row) for row in rows))]

    if headers:
        for row in rows:
            if len(row) < len(headers):
                row.extend([""] * (len(headers) - len(row)))
            elif len(row) > len(headers):
                del row[len(headers) :]
    return headers, rows

def find_deuda_table_selector(
    html_content: str,
    preferred_selectors: List[str],
) -> Optional[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    for selector in preferred_selectors:
        table = soup.select_one(selector)
        if table and not is_hidden_element(table):
            return selector
    for table in soup.select("table[id^=tableDetalleDeuda]"):
        if not is_hidden_element(table):
            table_id = table.get("id")
            if table_id:
                return f"#{table_id}"
    return None

def extract_plan_entries(html_content: str) -> List[Dict[str, Optional[str]]]:
    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.select_one("table.searchTable")
    if not table:
        return []

    entries: List[Dict[str, Optional[str]]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 7:
            continue
        plan_num = normalize_text(cells[1].get_text(" ", strip=True))
        denominacion = normalize_text(cells[3].get_text(" ", strip=True))
        situacion = normalize_text(cells[6].get_text(" ", strip=True))
        detalle_input = tr.select_one("input[id^=ContentPlaceHolder1_rpt_detallePlan_]")
        detalle_id = detalle_input.get("id") if detalle_input else None
        entries.append(
            {
                "plan_num": plan_num,
                "denominacion": denominacion,
                "situacion": situacion,
                "detalle_id": detalle_id,
            }
        )
    return entries

def parse_fecha(texto: Optional[str]) -> Optional[date]:
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None

def extract_fecha_consolidacion_from_detail(html_content: str) -> Optional[date]:
    soup = BeautifulSoup(html_content, "html.parser")
    fecha = soup.select_one("#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_fechaConsolidacion")
    return parse_fecha(normalize_text(fecha.get_text(" ", strip=True))) if fecha else None

def extract_plan_num_from_detail(html_content: str) -> Optional[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    plan_num = soup.select_one("#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_nroPlan")
    return normalize_text(plan_num.get_text(" ", strip=True)) if plan_num else None

def build_report_path(
    fin_cuit: str,
    cuit_representado: str,
    label: str,
    plan_num: str,
    denominacion: str,
    extension: str,
    base_dir: Optional[Path] = None,
) -> Path:
    # Cleanup invalid characters from inputs to avoid path issues
    safe_cuit = safe_filename(cuit_representado)
    safe_label = safe_filename(label)
    safe_plan_num = safe_filename(plan_num)
    safe_denominacion = safe_filename(denominacion)
    
    base = f"{fin_cuit} - {cuit_representado} - {label} - {plan_num} - {denominacion}"
    filename = f"{safe_filename(base)}{extension}"
    target_dir = base_dir or DOWNLOAD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / filename

def save_tables_to_excel(excel_path: Path, tables: List[Tuple[str, List[str], List[List[str]]]]) -> None:
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for sheet_name, headers, rows in tables:
            df = pd.DataFrame(rows, columns=headers if headers else None)
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

async def save_pdf_from_page(
    page,
    pdf_path: Path,
    playwright: Playwright,
    context,
    label: Optional[str] = None,
) -> None:
    html_content = await page.content()
    pdf_html = inject_base_tag(html_content, page.url)
    storage_state = await context.storage_state()
    pdf_browser = await playwright.chromium.launch(headless=True)
    pdf_context = await pdf_browser.new_context(storage_state=storage_state)
    pdf_page = await pdf_context.new_page()
    try:
        await pdf_page.set_content(pdf_html, wait_until="domcontentloaded")
        try:
            await pdf_page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        if label == "cuotas":
            table_selector = "#tableDetallePlanPago"
            try:
                await pdf_page.wait_for_selector(table_selector, timeout=5000)
            except Exception:
                pass
            await expand_cuotas_for_export(pdf_page, table_selector)
        elif label in {"obligaciones", "obligaciones_previsionales"}:
            table_selector = "table[id^='tableDetalleDeuda']"
            try:
                await pdf_page.wait_for_selector(table_selector, timeout=5000)
            except Exception:
                pass
            await expand_obligaciones_for_export(pdf_page, table_selector)
        await pdf_page.emulate_media(media="screen")
        pdf_kwargs = {"path": str(pdf_path), "format": "A4", "print_background": True}
        if label in {"obligaciones", "obligaciones_previsionales"}:
            pdf_kwargs.update(
                {
                    "landscape": True,
                    "scale": 0.8,
                    "margin": {"top": "8mm", "bottom": "8mm", "left": "5mm", "right": "5mm"},
                }
            )
        await pdf_page.pdf(**pdf_kwargs)
        if pdf_path.exists() and pdf_path.stat().st_size < 1024:
            await pdf_page.emulate_media(media="print")
            await pdf_page.pdf(**pdf_kwargs)
    except Exception:
        pass
    finally:
        await pdf_context.close()
        await pdf_browser.close()

async def collect_table(
    page,
    table_selector: str,
) -> Tuple[List[str], List[List[str]]]:
    try:
        await page.locator(table_selector).wait_for(state="visible", timeout=20000)
        await page.locator(f"{table_selector} tbody tr").first.wait_for(state="visible", timeout=20000)
    except Exception:
        pass
    html_content = await page.content()
    headers, rows = extract_table_from_html(html_content, table_selector)
    return headers, rows

async def open_plan_detail(
    page,
    detalle_id: str,
    max_retries: int = 3,
) -> bool:
    """Intenta abrir el detalle de un plan con reintentos y verificacion."""
    for attempt in range(max_retries):
        try:
            locator = page.locator(f"#{detalle_id}")
            if await locator.count() == 0:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.7)
                    continue
                return False
            
            try:
                await locator.scroll_into_view_if_needed(timeout=10000)
            except Exception:
                pass
            
            clicked = False
            try:
                await locator.click(timeout=15000)
                clicked = True
            except Exception:
                try:
                    await locator.click(force=True, timeout=15000)
                    clicked = True
                except Exception:
                    try:
                        clicked = await page.evaluate(
                            "(detalleId) => { const el = document.getElementById(detalleId); if (!el) return false; el.click(); return true; }",
                            detalle_id,
                        )
                    except Exception:
                        clicked = False
            
            if not clicked:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.7)
                    continue
                return False
            
            await wait_loaded(page)
            
            if await verify_on_plan_detail_page(page):
                return True
            
            if attempt < max_retries - 1:
                await asyncio.sleep(0.7)
                continue
            return False
                
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.7)
                continue
            return False
    
    return False

async def scrape_optional_obligaciones(
    page,
    playwright: Playwright,
    context,
    *,
    button_name: str,
    sheet_name: str,
    file_label: str,
    table_selectors: List[str],
    fin_cuit: str,
    cuit_representado: str,
    plan_num: str,
    denominacion_base: str,
    base_dir: Path,
) -> Optional[Tuple[Tuple[str, List[str], List[List[str]]], Path]]:
    button = page.get_by_role("button", name=button_name).first
    try:
        if await button.count() == 0 or not await button.is_visible(timeout=2000):
            return None
    except Exception:
        return None

    opened = False
    try:
        await button.click()
        opened = True
        await wait_loaded(page)

        html_content = await page.content()
        table_selector = find_deuda_table_selector(html_content, table_selectors)
        if table_selector:
            headers, rows = await collect_table(page, table_selector)
        else:
            headers, rows = [], []

        pdf_path = build_report_path(
            fin_cuit,
            cuit_representado,
            file_label,
            plan_num,
            denominacion_base,
            ".pdf",
            base_dir=base_dir,
        )
        await save_pdf_from_page(page, pdf_path, playwright, context, label=file_label)
        return (sheet_name, headers, rows), pdf_path
    finally:
        if opened:
            try:
                await page.get_by_role("link", name="Volver").click()
                await wait_loaded(page)
                if not await verify_on_plan_detail_page(page):
                    await navigate_back_to_plan_list(page)
            except Exception:
                pass


async def bot_mis_facilidades(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str] = None,
    denominacion: Optional[str] = None,
    situacion_excluyente: Optional[List[str]] = None,
    fecha_consolidacion_desde: Optional[str] = None,
    fecha_consolidacion_hasta: Optional[str] = None,
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True
    fuera_de_rango = 0
    fecha_desde = parse_fecha(fecha_consolidacion_desde)
    fecha_hasta = parse_fecha(fecha_consolidacion_hasta)
    if (fecha_consolidacion_desde and not fecha_desde) or (fecha_consolidacion_hasta and not fecha_hasta):
        return results, ["Las fechas de consolidación deben tener formato dd/mm/aaaa"], False
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        return results, ["fecha_consolidacion_desde no puede ser posterior a fecha_consolidacion_hasta"], False
    found_plan: Optional[bool] = None

    proxy = get_proxy_config() if proxy_request else None

    # Determine base directory
    base_dir = DOWNLOAD_DIR / "mis_facilidades"
    if cuit_representado:
        base_dir = base_dir / safe_filename(cuit_representado)
    else:
        base_dir = base_dir / safe_filename(cuit_representante)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = base_dir / timestamp
    base_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context()
            page = await context.new_page()

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            if not login_result.success:
                await context.close()
                await browser.close()
                return results, error_logs, False

            page1 = await open_arca_service(
                page,
                service_name="MIS FACILIDADES",
                service_matchers=(re.compile("MIS FACILIDADES", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se pudo abrir Mis Facilidades",
            )
            if page1 is None:
                await context.close()
                await browser.close()
                return results, error_logs, False
            await page1.wait_for_load_state("domcontentloaded")

            available_cuits = await get_available_cuits(page1)
            if not available_cuits:
                page_cuit = extract_cuit_from_html(await page1.content())
                if page_cuit:
                    available_cuits = [page_cuit]
                else:
                    available_cuits = [cuit_representante] if cuit_representante else []
            results["cuits_disponibles"] = available_cuits

            try:
                await select_cuit_if_available(page1, cuit_representado)
            except Exception as exc:
                error_logs.append(f"No se pudo seleccionar el representado: {exc}")
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            expected_cuit = cuit_representado or cuit_representante
            actual_cuit, cuit_ok = await verify_contribuyente_cuit(page1, expected_cuit)
            if not cuit_ok and actual_cuit is None and available_cuits:
                if len(available_cuits) == 1:
                    actual_cuit = available_cuits[0]
                    if normalize_cuit(actual_cuit) == normalize_cuit(expected_cuit):
                        cuit_ok = True
            if not cuit_ok:
                error_logs.append(
                    "CUIT seleccionado invalido o distinto al esperado"
                    f" (esperado: {expected_cuit}, actual: {actual_cuit})."
                )
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            cuit_objetivo = actual_cuit or expected_cuit or ""
            if not cuit_objetivo:
                error_logs.append("No se pudo determinar el CUIT del contribuyente.")
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            try:
                await page1.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pass

            safe_representado = safe_filename(cuit_objetivo)
            fin_cuit = (cuit_objetivo or "")[-1:]
            plan_entries = extract_plan_entries(await page1.content())
            found_plan = False
            for entry in plan_entries:
                plan_num = entry.get("plan_num") or "plan"
                situacion = entry.get("situacion") or ""
                detalle_id = entry.get("detalle_id")

                if "Plan Cancelado" in situacion:
                    continue
                if situacion_excluyente and any(excl.lower() in situacion.lower() for excl in situacion_excluyente):
                    continue
                found_plan = True
                if not detalle_id:
                    continue

                plan_denominacion = denominacion or entry.get("denominacion") or "denominacion"
                plan_result = {
                    "plan_num": plan_num,
                    "denominacion": plan_denominacion,
                }
                
                # Create subfolder for plan
                plan_dir = base_dir / safe_filename(plan_num)
                plan_dir.mkdir(parents=True, exist_ok=True)

                try:
                    opened = await open_plan_detail(page1, detalle_id)
                    if not opened:
                        raise RuntimeError("No se pudo abrir el detalle del plan")
                except Exception as exc:
                    error_logs.append(f"Error al abrir detalle del plan {plan_num}: {exc}")
                    success = False
                    continue

                if fecha_desde or fecha_hasta:
                    fecha_consolidacion = extract_fecha_consolidacion_from_detail(await page1.content())
                    if fecha_consolidacion is None:
                        error_logs.append(f"No se pudo leer la fecha de consolidación del plan {plan_num}")
                        success = False
                    if fecha_consolidacion and (fecha_desde and fecha_consolidacion < fecha_desde or fecha_hasta and fecha_consolidacion > fecha_hasta):
                        fuera_de_rango += 1
                    if fecha_consolidacion is None or (fecha_desde and fecha_consolidacion < fecha_desde) or (
                        fecha_hasta and fecha_consolidacion > fecha_hasta
                    ):
                        plan_dir.rmdir()
                        if not await navigate_back_to_plan_list(page1):
                            error_logs.append("No se pudo volver al listado de planes")
                            success = False
                            break
                        continue

                detail_plan_num = extract_plan_num_from_detail(await page1.content())
                if detail_plan_num:
                    plan_num = detail_plan_num
                    plan_result["plan_num"] = plan_num
                    # Update plan dir if plan num changed
                    new_plan_dir = base_dir / safe_filename(plan_num)
                    new_plan_dir.mkdir(parents=True, exist_ok=True)
                    plan_dir = new_plan_dir

                pagos_headers: List[str] = []
                pagos_rows: List[List[str]] = []
                cuotas_headers: List[str] = []
                cuotas_rows: List[List[str]] = []
                obligaciones_tables: List[Tuple[str, List[str], List[List[str]]]] = []

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id):
                        error_logs.append(f"No se pudo asegurar pagina de detalle antes de Ver Pagos (plan {plan_num})")
                        raise RuntimeError("No se pudo asegurar pagina de detalle")
                    await page1.get_by_role("button", name="Ver Pagos").click()
                    await wait_loaded(page1)
                    pagos_headers, pagos_rows = await collect_table(page1, "#tableDetallePagos")
                    pagos_pdf = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "pagos",
                        plan_num,
                        plan_denominacion,
                        ".pdf",
                        base_dir=plan_dir,
                    )
                    await save_pdf_from_page(page1, pagos_pdf, playwright, context)
                    plan_result["pagos_pdf"] = pagos_pdf.name
                    await page1.get_by_role("link", name="Volver").click()
                    await wait_loaded(page1)
                except Exception as exc:
                    error_logs.append(f"Error al obtener pagos del plan {plan_num}: {exc}")
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id):
                        error_logs.append(f"No se pudo asegurar pagina de detalle antes de Plan de Pago (plan {plan_num})")
                        raise RuntimeError("No se pudo asegurar pagina de detalle")
                    await page1.get_by_role("button", name="Plan de Pago").click()
                    await wait_loaded(page1)
                    cuotas_headers, cuotas_rows = await collect_table(page1, "#tableDetallePlanPago")
                    cuotas_pdf = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "cuotas",
                        plan_num,
                        plan_denominacion,
                        ".pdf",
                        base_dir=plan_dir,
                    )
                    await save_pdf_from_page(page1, cuotas_pdf, playwright, context, label="cuotas")
                    plan_result["cuotas_pdf"] = cuotas_pdf.name
                    await page1.get_by_role("link", name="Volver").click()
                    await wait_loaded(page1)
                except Exception as exc:
                    error_logs.append(f"Error al obtener cuotas del plan {plan_num}: {exc}")
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, navigate_back=False):
                        error_logs.append(f"No se pudo asegurar pagina de detalle antes de Obligaciones (plan {plan_num})")
                    oblig_impositivas = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Impositivas",
                        sheet_name="Obligaciones Impositivas",
                        file_label="obligaciones",
                        table_selectors=["#tableDetalleDeuda_2", "#tableDetalleDeuda_1"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_objetivo,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        base_dir=plan_dir,
                    )
                    if oblig_impositivas:
                        table_data, oblig_pdf = oblig_impositivas
                        obligaciones_tables.append(table_data)
                        plan_result["obligaciones_pdf"] = oblig_pdf.name
                except Exception as exc:
                    error_logs.append(
                        f"Error al obtener obligaciones impositivas del plan {plan_num}: {exc}"
                    )
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, navigate_back=False):
                        error_logs.append(f"No se pudo asegurar pagina de detalle antes de Obligaciones Previsionales (plan {plan_num})")
                    oblig_previsionales = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Previsionales",
                        sheet_name="Obligaciones Previsionales",
                        file_label="obligaciones_previsionales",
                        table_selectors=["#tableDetalleDeuda_1", "#tableDetalleDeuda_2"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_objetivo,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        base_dir=plan_dir,
                    )
                    if oblig_previsionales:
                        table_data, oblig_pdf = oblig_previsionales
                        obligaciones_tables.append(table_data)
                        plan_result["obligaciones_previsionales_pdf"] = oblig_pdf.name
                except Exception as exc:
                    error_logs.append(
                        f"Error al obtener obligaciones previsionales del plan {plan_num}: {exc}"
                    )
                    success = False

                try:
                    excel_path = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "tablas",
                        plan_num,
                        plan_denominacion,
                        ".xlsx",
                        base_dir=plan_dir,
                    )
                    save_tables_to_excel(
                        excel_path,
                        [
                            ("Pagos", pagos_headers, pagos_rows),
                            ("Cuotas", cuotas_headers, cuotas_rows),
                            *obligaciones_tables,
                        ],
                    )
                    plan_result["tablas_excel"] = excel_path.name
                except Exception as exc:
                    error_logs.append(f"Error al generar tablas del plan {plan_num}: {exc}")
                    success = False

                if not await navigate_back_to_plan_list(page1):
                    error_logs.append("No se pudo volver al listado de planes")
                    success = False
                    results["archivos"].append(plan_result)
                    continue

                results["archivos"].append(plan_result)

            if not found_plan:
                error_logs.append("No se encontraron planes disponibles")

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot mis facilidades: {exc}")
        success = False

    if not results["archivos"] and found_plan is not False and not fuera_de_rango:
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("MIS_FACILIDADES_CUIT_REPRESENTANTE", "")
    clave = os.getenv("MIS_FACILIDADES_CLAVE_REPRESENTANTE", "")
    if cuit and clave:
        res = await bot_mis_facilidades(
            cuit, clave, denominacion="MI EMPRESA"
        )
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
