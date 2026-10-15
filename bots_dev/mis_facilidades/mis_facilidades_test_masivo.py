import asyncio
import os
import random
import re
import traceback
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import Playwright, async_playwright

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)

LOG_DIR = Path("./logs_masivo")
LOG_DIR.mkdir(exist_ok=True)

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

# Semáforo para limitar a 5 workers concurrentes
MAX_WORKERS = 3
semaphore = asyncio.Semaphore(MAX_WORKERS)

if load_dotenv is not None:
    load_dotenv()


def parse_env_bool(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_datacenter_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if value.startswith("user-"):
        return value
    if value.startswith("customer-"):
        return f"user-{value[len('customer-'):]}"
    return f"user-{value}"


def get_datacenter_proxy_from_env() -> Optional[Dict[str, str]]:
    host = (
        os.getenv("PROXY_URL")
        or os.getenv("ENTRY_POINT_PROXY")
        or os.getenv("SERVER_PROXY_HOST")
        or ""
    ).strip()
    username = (
        os.getenv("DATACENTER_USERNAME")
        or os.getenv("DATACENTER_PROXY_USERNAME")
        or ""
    ).strip()
    password = (
        os.getenv("DATACENTER_PASSWORD")
        or os.getenv("DATACENTER_PROXY_PASSWORD")
        or ""
    ).strip()
    start_port_raw = (os.getenv("STARTING_PORT_PROXY") or "").strip()
    end_port_raw = (os.getenv("ENDING_PORT_PROXY") or "").strip()

    if not host or not username or not password or not start_port_raw or not end_port_raw:
        return None

    try:
        start_port = int(start_port_raw)
        end_port = int(end_port_raw)
    except ValueError:
        return None

    if start_port <= 0 or end_port <= 0:
        return None
    if start_port > end_port:
        start_port, end_port = end_port, start_port

    clean_host = re.sub(r"^https?://", "", host).strip().strip("/")
    if ":" in clean_host:
        clean_host = clean_host.rsplit(":", 1)[0]

    random_port = random.randint(start_port, end_port)
    return {
        "server": f"https://{clean_host}:{random_port}",
        "username": normalize_datacenter_username(username),
        "password": password,
    }


def get_proxy_from_env() -> Optional[Dict[str, str]]:
    enabled = os.getenv("PROXY_PROPIA_ACTIVA")
    if enabled is not None and not parse_env_bool(enabled):
        return None

    host = (os.getenv("PROXY_PROPIA_IP") or "").strip()
    port = (os.getenv("PROXY_PROPIA_PUERTO") or "").strip()

    # Si no hay configuración de proxy propia en .env, se ejecuta sin proxy.
    if not host and not port:
        return None
    if not host or not port:
        return None

    clean_host = re.sub(r"^https?://", "", host).strip().strip("/")
    proxy: Dict[str, str] = {"server": f"http://{clean_host}:{port}"}

    username = (os.getenv("PROXY_PROPIA_USUARIO") or "").strip()
    password = (os.getenv("PROXY_PROPIA_CLAVE") or "").strip()
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password

    return proxy


PROXY_CONFIG = get_proxy_from_env()


def get_runtime_proxy_config() -> Tuple[Optional[Dict[str, str]], str]:
    # Prioriza Datacenter con puerto aleatorio por ejecución y usa proxy propia como fallback.
    datacenter_proxy = get_datacenter_proxy_from_env()
    if datacenter_proxy:
        return datacenter_proxy, "datacenter"
    if PROXY_CONFIG:
        return PROXY_CONFIG, "proxy_propia"
    return None, "directa"


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def is_plan_vigente(situacion: Optional[str]) -> bool:
    if not situacion:
        return False
    normalized = normalize_text(situacion).lower()
    if "vigente" not in normalized:
        return False
    return not re.search(r"\bno[\s-]+vigente(s)?\b", normalized)


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
    ubicacion_descarga: Optional[str] = None,
) -> Path:
    base = f"{fin_cuit} - {cuit_representado} - {label} - {plan_num} - {denominacion}"
    filename = f"{safe_filename(base)}{extension}"
    
    # Si hay ubicación de descarga, crear subcarpeta
    if ubicacion_descarga:
        target_dir = DOWNLOAD_DIR / ubicacion_descarga
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / filename
    
    return DOWNLOAD_DIR / filename


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
    proxy: Optional[Dict[str, str]] = None,
) -> None:
    html_content = await page.content()
    pdf_html = inject_base_tag(html_content, page.url)
    storage_state = await context.storage_state()
    pdf_browser = await playwright.chromium.launch(headless=True, proxy=proxy)
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
    """
    Intenta abrir el detalle de un plan con reintentos en caso de fallo.
    """
    for attempt in range(max_retries):
        try:
            locator = page.locator(f"#{detalle_id}")
            if await locator.count() == 0:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return False
            
            try:
                await locator.scroll_into_view_if_needed(timeout=10000)
            except Exception:
                pass
            
            try:
                await locator.click(timeout=15000)
            except Exception:
                try:
                    await locator.click(force=True, timeout=15000)
                except Exception:
                    try:
                        clicked = await page.evaluate(
                            "(detalleId) => { const el = document.getElementById(detalleId); if (!el) return false; el.click(); return true; }",
                            detalle_id,
                        )
                        if not clicked:
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2)
                                continue
                            return False
                    except Exception:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        return False
            
            await page.wait_for_load_state("domcontentloaded")
            
            try:
                await page.get_by_role("button", name="Ver Pagos").wait_for(state="visible", timeout=20000)
                return True
            except Exception:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return False
                
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            return False
    
    return False


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
    ubicacion_descarga: Optional[str] = None,
    proxy: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, List[str], List[List[str]]]]:
    button = page.get_by_role("button", name=button_name).first
    try:
        if not await button.is_visible(timeout=2000):
            return None
    except Exception:
        return None

    await button.click()
    await page.wait_for_load_state("domcontentloaded")

    html_content = await page.content()
    table_selector = find_deuda_table_selector(html_content, table_selectors)
    if table_selector:
        headers, rows = await collect_table(page, table_selector)
    else:
        headers, rows = [], []

    pdf_path = build_report_path(
        fin_cuit, cuit_representado, file_label, plan_num, denominacion_base, ".pdf", ubicacion_descarga
    )
    await save_pdf_from_page(page, pdf_path, playwright, context, label=file_label, proxy=proxy)
    await page.get_by_role("link", name="Volver").click()
    await page.wait_for_load_state("domcontentloaded")
    return (sheet_name, headers, rows)


async def bot_mis_facilidades(
    playwright: Playwright,
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str],
    denominacion: Optional[str],
    ubicacion_descarga: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ejecuta el bot de Mis Facilidades y retorna un diccionario con los resultados y logs.
    """
    result = {
        "cuit_representante": cuit_representante,
        "cuit_representado": cuit_representado,
        "denominacion": denominacion,
        "ubicacion_descarga": ubicacion_descarga,
        "success": False,
        "logs": [],
        "errors": [],
        "cuits_disponibles": [],
        "planes_procesados": 0,
        "planes_no_accesibles": 0,
        "archivos_generados": [],
    }

    browser = None
    context = None
    page = None

    try:
        runtime_proxy, proxy_source = get_runtime_proxy_config()
        result["logs"].append(f"[{datetime.now()}] Iniciando bot para {cuit_representante}")
        if runtime_proxy:
            result["logs"].append(
                f"[{datetime.now()}] Proxy habilitado ({proxy_source}) para Playwright: {runtime_proxy.get('server')}"
            )
        else:
            result["logs"].append(f"[{datetime.now()}] Proxy no configurado, usando conexión directa")
        
        browser = await playwright.chromium.launch(headless=True, proxy=runtime_proxy)
        context = await browser.new_context()
        page = await context.new_page()

        # Login
        try:
            result["logs"].append(f"[{datetime.now()}] Accediendo a página de login")
            await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
            await page.get_by_role("spinbutton").click()
            await page.get_by_role("spinbutton").fill(cuit_representante)
            await page.get_by_role("button", name="Siguiente").click()
            await page.get_by_role("textbox", name="TU CLAVE").click()
            await page.get_by_role("textbox", name="TU CLAVE").fill(clave_representante)
            await page.get_by_role("button", name="Ingresar").click()
            await page.wait_for_load_state("domcontentloaded")
            result["logs"].append(f"[{datetime.now()}] Login completado exitosamente")
        except Exception:
            result["errors"].append("Error en login: No se pudo completar el inicio de sesión (verifique CUIT y clave)")
            result["logs"].append(f"[{datetime.now()}] Falló el login")
            return result

        # Verificar si hay captcha
        try:
            await page.get_by_role("img", name="Captcha").click(timeout=1000)
            result["errors"].append("Captcha detectado")
            return result
        except Exception:
            pass

        # Verificar cambio de clave
        try:
            heading = page.get_by_role("heading", name=re.compile("Cambiar Clave Fiscal", re.I))
            if await heading.is_visible():
                result["errors"].append("Se requiere cambiar la clave fiscal")
                return result
        except Exception:
            pass

        # Abrir Mis Facilidades
        try:
            result["logs"].append(f"[{datetime.now()}] Accediendo a Mis Facilidades")
            await page.get_by_role("link", name="Ver todos").click()
            async with page.expect_popup() as page1_info:
                await page.get_by_role("button", name="MIS FACILIDADES Planes de").click()
            page1 = await page1_info.value
            await page1.wait_for_load_state("domcontentloaded")
            result["logs"].append(f"[{datetime.now()}] Mis Facilidades abierto exitosamente")
        except Exception:
            result["errors"].append("Error al abrir Mis Facilidades: No se pudo acceder al módulo")
            result["logs"].append(f"[{datetime.now()}] Falló la apertura de Mis Facilidades")
            return result

        # Obtener CUITs disponibles
        available_cuits = await get_available_cuits(page1)
        if not available_cuits:
            page_cuit = extract_cuit_from_html(await page1.content())
            if page_cuit:
                available_cuits = [page_cuit]
            else:
                available_cuits = [cuit_representante] if cuit_representante else []
        
        result["cuits_disponibles"] = available_cuits
        result["logs"].append(f"[{datetime.now()}] CUITs disponibles: {', '.join(available_cuits)}")

        # Seleccionar representado
        try:
            await select_cuit_if_available(page1, cuit_representado)
            result["logs"].append(f"[{datetime.now()}] Representado seleccionado: {cuit_representado or 'sin representado'}")
        except Exception:
            result["errors"].append("Error al seleccionar representado: No se pudo seleccionar el CUIT")
            result["logs"].append(f"[{datetime.now()}] Falló la selección de representado")
            return result

        # Verificar CUIT
        expected_cuit = cuit_representado or cuit_representante
        actual_cuit, cuit_ok = await verify_contribuyente_cuit(page1, expected_cuit)
        
        if not cuit_ok and actual_cuit is None and available_cuits:
            if len(available_cuits) == 1:
                actual_cuit = available_cuits[0]
                if normalize_cuit(actual_cuit) == normalize_cuit(expected_cuit):
                    cuit_ok = True

        if not cuit_ok:
            result["errors"].append(
                f"CUIT seleccionado inválido o distinto al esperado "
                f"(esperado: {expected_cuit}, actual: {actual_cuit})"
            )
            return result

        cuit_contribuyente = actual_cuit or expected_cuit or ""
        fin_cuit = normalize_cuit(cuit_contribuyente)[-1:] if cuit_contribuyente else ""
        
        result["logs"].append(f"[{datetime.now()}] CUIT verificado: {cuit_contribuyente}")

        # Extraer planes
        plan_entries = extract_plan_entries(await page1.content())
        result["logs"].append(f"[{datetime.now()}] Planes encontrados: {len(plan_entries)}")

        # Procesar cada plan
        for entry in plan_entries:
            plan_num = entry.get("plan_num") or "plan"
            situacion = entry.get("situacion") or ""
            detalle_id = entry.get("detalle_id")
            plan_denominacion = denominacion or entry.get("denominacion") or "denominacion"

            if not is_plan_vigente(situacion):
                situacion_label = situacion or "sin situacion"
                result["logs"].append(
                    f"[{datetime.now()}] Plan {plan_num} no vigente ({situacion_label}) - omitido"
                )
                continue
            
            if not detalle_id:
                result["logs"].append(f"[{datetime.now()}] Plan {plan_num} sin detalle_id - omitido")
                continue

            result["logs"].append(f"[{datetime.now()}] Procesando plan {plan_num}")

            try:
                if not await open_plan_detail(page1, detalle_id):
                    result["errors"].append(f"No se pudo abrir el detalle del plan {plan_num}")
                    result["logs"].append(f"[{datetime.now()}] Error: Plan {plan_num} no accesible")
                    result["planes_no_accesibles"] += 1
                    continue

                detail_plan_num = extract_plan_num_from_detail(await page1.content())
                if detail_plan_num:
                    plan_num = detail_plan_num

                # Pagos
                try:
                    await page1.get_by_role("button", name="Ver Pagos").click()
                    await page1.wait_for_load_state("domcontentloaded")
                    pagos_headers, pagos_rows = await collect_table(page1, "#tableDetallePagos")
                    pagos_pdf = build_report_path(
                        fin_cuit, cuit_contribuyente, "pagos", plan_num, plan_denominacion, ".pdf", ubicacion_descarga
                    )
                    await save_pdf_from_page(page1, pagos_pdf, playwright, context, proxy=runtime_proxy)
                    result["archivos_generados"].append(str(pagos_pdf))
                    result["logs"].append(f"[{datetime.now()}] ✓ Archivo generado: Pagos - {pagos_pdf.name}")
                    await page1.get_by_role("link", name="Volver").click()
                    await page1.wait_for_load_state("domcontentloaded")
                except Exception:
                    result["errors"].append(f"No se pudo obtener la sección Pagos del plan {plan_num}")
                    result["logs"].append(f"[{datetime.now()}] ✗ Error al obtener Pagos del plan {plan_num}")

                # Plan de Pago / Cuotas
                try:
                    await page1.get_by_role("button", name="Plan de Pago").click()
                    await page1.wait_for_load_state("domcontentloaded")
                    cuotas_headers, cuotas_rows = await collect_table(page1, "#tableDetallePlanPago")
                    cuotas_pdf = build_report_path(
                        fin_cuit, cuit_contribuyente, "cuotas", plan_num, plan_denominacion, ".pdf", ubicacion_descarga
                    )
                    await save_pdf_from_page(
                        page1,
                        cuotas_pdf,
                        playwright,
                        context,
                        label="cuotas",
                        proxy=runtime_proxy,
                    )
                    result["archivos_generados"].append(str(cuotas_pdf))
                    result["logs"].append(f"[{datetime.now()}] ✓ Archivo generado: Cuotas - {cuotas_pdf.name}")
                    await page1.get_by_role("link", name="Volver").click()
                    await page1.wait_for_load_state("domcontentloaded")
                except Exception:
                    result["errors"].append(f"No se pudo obtener la sección Plan de Pago del plan {plan_num}")
                    result["logs"].append(f"[{datetime.now()}] ✗ Error al obtener Plan de Pago del plan {plan_num}")

                # Obligaciones Impositivas
                try:
                    oblig_impositivas = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Impositivas",
                        sheet_name="Obligaciones Impositivas",
                        file_label="obligaciones",
                        table_selectors=["#tableDetalleDeuda_2", "#tableDetalleDeuda_1"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_contribuyente,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        ubicacion_descarga=ubicacion_descarga,
                        proxy=runtime_proxy,
                    )
                    if oblig_impositivas:
                        result["logs"].append(f"[{datetime.now()}] ✓ Archivo generado: Obligaciones Impositivas")
                    else:
                        result["logs"].append(f"[{datetime.now()}] - Obligaciones Impositivas no disponible")
                except Exception:
                    oblig_impositivas = None
                    result["logs"].append(f"[{datetime.now()}] ✗ Error al obtener Obligaciones Impositivas")

                # Obligaciones Previsionales
                try:
                    oblig_previsionales = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Previsionales",
                        sheet_name="Obligaciones Previsionales",
                        file_label="obligaciones_previsionales",
                        table_selectors=["#tableDetalleDeuda_1", "#tableDetalleDeuda_2"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_contribuyente,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        ubicacion_descarga=ubicacion_descarga,
                        proxy=runtime_proxy,
                    )
                    if oblig_previsionales:
                        result["logs"].append(f"[{datetime.now()}] ✓ Archivo generado: Obligaciones Previsionales")
                    else:
                        result["logs"].append(f"[{datetime.now()}] - Obligaciones Previsionales no disponible")
                except Exception:
                    oblig_previsionales = None
                    result["logs"].append(f"[{datetime.now()}] ✗ Error al obtener Obligaciones Previsionales")

                # Excel
                try:
                    excel_path = build_report_path(
                        fin_cuit, cuit_contribuyente, "tablas", plan_num, plan_denominacion, ".xlsx", ubicacion_descarga
                    )
                    tables = [
                        ("Pagos", pagos_headers, pagos_rows),
                        ("Cuotas", cuotas_headers, cuotas_rows),
                    ]
                    if oblig_impositivas:
                        tables.append(oblig_impositivas)
                    if oblig_previsionales:
                        tables.append(oblig_previsionales)
                    save_tables_to_excel(excel_path, tables)
                    result["archivos_generados"].append(str(excel_path))
                    result["logs"].append(f"[{datetime.now()}] ✓ Archivo generado: Excel consolidado - {excel_path.name}")
                except Exception:
                    result["errors"].append(f"No se pudo generar el Excel del plan {plan_num}")
                    result["logs"].append(f"[{datetime.now()}] ✗ Error al generar Excel del plan {plan_num}")

                try:
                    await page1.locator("#ContentPlaceHolder1_btnVolver").click()
                    await page1.wait_for_load_state("domcontentloaded")
                except Exception:
                    result["logs"].append(f"[{datetime.now()}] Advertencia: No se pudo volver al listado de planes")

                result["planes_procesados"] += 1
                result["logs"].append(f"[{datetime.now()}] ✓ Plan {plan_num} procesado completamente")

            except Exception:
                result["errors"].append(f"Error al procesar plan {plan_num}")
                result["logs"].append(f"[{datetime.now()}] ✗ Falló el procesamiento del plan {plan_num}")

        # Determinar éxito: 
        # - Si hay planes que no se pudieron abrir, es FALLIDO
        # - Si no se procesó ningún plan y hay errores, es FALLIDO
        # - Si no se procesó ningún plan y no hay errores, es EXITOSO (no había planes activos)
        # - Si se procesó al menos un plan y no hay planes no accesibles, es EXITOSO
        if result["planes_no_accesibles"] > 0:
            result["success"] = False
            result["logs"].append(
                f"[{datetime.now()}] ✗ Proceso FALLIDO - {result['planes_no_accesibles']} plan(es) no accesible(s)"
            )
        elif result["planes_procesados"] == 0:
            if len(result["errors"]) > 0:
                result["success"] = False
                result["logs"].append(
                    f"[{datetime.now()}] ✗ Proceso FALLIDO - No se pudo procesar ningún plan"
                )
            else:
                result["success"] = True
                result["logs"].append(
                    f"[{datetime.now()}] ✓ Proceso completado - No se encontraron planes activos"
                )
        else:
            result["success"] = True
            result["logs"].append(
                f"[{datetime.now()}] ✓ Proceso completado - Planes procesados: {result['planes_procesados']}"
            )

    except Exception:
        result["errors"].append("Error general: Fallo inesperado en el procesamiento")
        result["logs"].append(f"[{datetime.now()}] Error general: Fallo inesperado en el proceso")
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()

    return result


async def process_single_task(
    task: Dict[str, Any],
    task_id: int,
    general_log_file: Path,
) -> Dict[str, Any]:
    """
    Procesa una tarea individual con semáforo para limitar concurrencia.
    """
    async with semaphore:
        print(f"[Worker {task_id}] Iniciando tarea: {task.get('cuit_representante')}")
        
        async with async_playwright() as playwright:
            result = await bot_mis_facilidades(
                playwright,
                cuit_representante=task.get("cuit_representante", ""),
                clave_representante=task.get("clave", ""),
                cuit_representado=task.get("cuit_representado"),
                denominacion=task.get("denominacion"),
                ubicacion_descarga=task.get("ubicacion_descarga"),
            )
        
        # Guardar log individual (usar CUIT del representado si existe, sino el representante)
        cuit_para_log = result.get("cuit_representado") or result.get("cuit_representante", "sin_cuit")
        cuit_log = normalize_cuit(cuit_para_log)
        individual_log_file = LOG_DIR / f"log_{cuit_log}_{task_id}.txt"
        
        with open(individual_log_file, "w", encoding="utf-8") as f:
            f.write(f"=== LOG INDIVIDUAL - TAREA {task_id} ===\n")
            f.write(f"CUIT Representante: {result['cuit_representante']}\n")
            f.write(f"CUIT Representado: {result['cuit_representado']}\n")
            f.write(f"Denominación: {result['denominacion']}\n")
            f.write(f"Estado: {'EXITOSO' if result['success'] else 'FALLIDO'}\n")
            f.write(f"CUITs Disponibles: {', '.join(result['cuits_disponibles'])}\n")
            f.write(f"Planes Procesados: {result['planes_procesados']}\n")
            f.write(f"Planes No Accesibles: {result['planes_no_accesibles']}\n")
            f.write("\n=== LOGS ===\n")
            for log in result["logs"]:
                f.write(f"{log}\n")
            if result["errors"]:
                f.write("\n=== ERRORES ===\n")
                for error in result["errors"]:
                    f.write(f"{error}\n")
            if result["archivos_generados"]:
                f.write("\n=== ARCHIVOS GENERADOS ===\n")
                for archivo in result["archivos_generados"]:
                    f.write(f"{archivo}\n")
        
        # Escribir en log general
        with open(general_log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[Tarea {task_id}] {result['cuit_representante']} - ")
            f.write(f"{'EXITOSO' if result['success'] else 'FALLIDO'}\n")
            f.write(f"CUITs disponibles: {', '.join(result['cuits_disponibles'])}\n")
            f.write(f"Planes procesados: {result['planes_procesados']}\n")
            f.write(f"Planes no accesibles: {result['planes_no_accesibles']}\n")
            if result["errors"]:
                f.write(f"Errores: {len(result['errors'])}\n")
                for error in result["errors"][:3]:  # Solo primeros 3 errores en log general
                    f.write(f"  - {error}\n")
            f.write(f"Log individual: {individual_log_file.name}\n")
        
        print(f"[Worker {task_id}] Completado: {task.get('cuit_representante')} - "
              f"{'EXITOSO' if result['success'] else 'FALLIDO'}")
        
        return result


async def main():
    """
    Función principal que lee el Excel y ejecuta las tareas en paralelo.
    """
    excel_path = Path("/home/abp/Desktop/SantiagoS/Mis Facilidades para procesar.xlsx")
    
    if not excel_path.exists():
        print(f"Error: No se encontró el archivo {excel_path}")
        return
    
    print(f"Leyendo Excel: {excel_path}")
    df = pd.read_excel(excel_path)
    
    # Validar columnas necesarias
    required_columns = ["cuit_login", "clave"]
    for col in required_columns:
        if col not in df.columns:
            print(f"Error: El Excel debe tener la columna '{col}'")
            return
    
    # Filtrar solo las filas con procesar='SI'
    if "procesar" in df.columns:
        df = df[df["procesar"].str.upper() == "SI"]
        print(f"Filas filtradas con 'procesar=SI': {len(df)}")
    
    # Preparar tareas
    tasks = []
    for idx, row in df.iterrows():
        task = {
            "cuit_representante": str(row.get("cuit_login", "")),
            "clave": str(row.get("clave", "")),
            "cuit_representado": str(row.get("cuit_representado", "")) if pd.notna(row.get("cuit_representado")) else None,
            "denominacion": str(row.get("denominacion", "")) if pd.notna(row.get("denominacion")) else None,
            "ubicacion_descarga": str(row.get("ubicacion_descarga", "")) if pd.notna(row.get("ubicacion_descarga")) else None,
        }
        tasks.append((task, idx + 1))
    
    print(f"Total de tareas a procesar: {len(tasks)}")
    print(f"Workers concurrentes: {MAX_WORKERS}")
    
    # Crear archivo de log general
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    general_log_file = LOG_DIR / f"log_general_{timestamp}.txt"
    
    with open(general_log_file, "w", encoding="utf-8") as f:
        f.write(f"=== LOG GENERAL - PROCESAMIENTO MASIVO ===\n")
        f.write(f"Fecha: {datetime.now()}\n")
        f.write(f"Total tareas: {len(tasks)}\n")
        f.write(f"Workers: {MAX_WORKERS}\n")
        f.write(f"Excel: {excel_path}\n")
        f.write(f"{'='*80}\n")
    
    # Ejecutar tareas en paralelo
    print("\nIniciando procesamiento...\n")
    start_time = datetime.now()
    
    results = await asyncio.gather(
        *[process_single_task(task, task_id, general_log_file) for task, task_id in tasks],
        return_exceptions=True,
    )
    
    end_time = datetime.now()
    duration = end_time - start_time
    
    # Resumen final
    successful = sum(1 for r in results if isinstance(r, dict) and r.get("success", False))
    failed = len(results) - successful
    
    summary = f"""
{'='*80}
=== RESUMEN FINAL ===
{'='*80}
Total procesadas: {len(results)}
Exitosas: {successful}
Fallidas: {failed}
Tiempo total: {duration}
Log general: {general_log_file}
Logs individuales: {LOG_DIR}
Archivos descargados: {DOWNLOAD_DIR}
{'='*80}
"""
    
    print(summary)
    
    with open(general_log_file, "a", encoding="utf-8") as f:
        f.write(summary)
    
    print(f"\n✅ Proceso completado. Revisa los logs en: {LOG_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
