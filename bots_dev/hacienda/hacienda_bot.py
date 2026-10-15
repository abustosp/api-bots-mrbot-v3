import asyncio
import logging
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import (
    Download,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

# Stub for subir_archivo_a_minio (not needed for local testing)
async def subir_archivo_a_minio(**kwargs):
    return "minio_disabled"

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
COMPROBANTES_SERVICE_NAME = "COMPROBANTES EN LINEA"
HACIENDA_SERVICE_NAME = "Hacienda y Carne - Liquidacion"
COMPROBANTES_SERVICE_PATTERN = re.compile(r"COMPROBANTES\s+EN\s+L[ÍI]NEA", re.I)
HACIENDA_SERVICE_PATTERN = re.compile(r"Hacienda\s+y\s+Carne\s*-\s*Liquidaci", re.I)
QUERY_EMISOR_PATTERN = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Emisor",
    re.I,
)
QUERY_RECEPTOR_PATTERN = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Receptor",
    re.I,
)
QUERY_LINK_PATTERN = re.compile(r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones", re.I)
NEXT_PAGE_PATTERN = re.compile(r"^\s*(Siguiente|Sig\.?|Next|>|>>|›|»)\s*$", re.I)
RESULT_TABLE_ROW_SELECTOR = "#resultTable tbody tr"
DOWNLOAD_ACTION_SELECTOR = (
    "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, "
    "a[onclick*='.pdf'], a[href*='.pdf'], a[title*='Imprimir'], "
    "a.btnImprimir, a[class*='btnImprimir'], "
    "a[onclick*='descarg'], a[href*='descarg'], a[title*='Descargar'], "
    "button[onclick*='pd.jsp'], button[onclick*='.pdf'], button[title*='Imprimir'], "
    "button.btnImprimir, button[class*='btnImprimir'], "
    "button[onclick*='descarg'], button[title*='Descargar'], "
    "[role='button'][onclick*='pd.jsp'], [role='button'][onclick*='.pdf'], "
    "[role='button'][onclick*='descarg']"
)
DOWNLOAD_ICON_SELECTOR = (
    "i[class*='download'], span[class*='download'], "
    "i[class*='descarg'], span[class*='descarg'], "
    "i[class*='Descarg'], span[class*='Descarg'], "
    "i[class*='imprimir'], span[class*='imprimir'], "
    "i[class*='Imprimir'], span[class*='Imprimir'], "
    "i[class*='print'], span[class*='print'], "
    "i[class*='pdf'], span[class*='pdf'], "
    "i.glyphicon-file, span.glyphicon-file, a.glyphicon-file, "
    "svg[class*='download'], svg[class*='descarg'], svg[class*='pdf']"
)
DOWNLOAD_ROW_SELECTOR = f"{DOWNLOAD_ACTION_SELECTOR}, {DOWNLOAD_ICON_SELECTOR}"
BS4_DOWNLOAD_SELECTORS = (
    "a[onclick*='pd.jsp']",
    "a[href*='pd.jsp']",
    "a.imprimir",
    "a.btnImprimir",
    "a[class*='btnImprimir']",
    "a[onclick*='.pdf']",
    "a[href*='.pdf']",
    "a[title*='Imprimir']",
    "a[onclick*='descarg']",
    "a[href*='descarg']",
    "a[title*='Descargar']",
    "button[onclick*='pd.jsp']",
    "button[onclick*='.pdf']",
    "button[title*='Imprimir']",
    "button[onclick*='descarg']",
    "button[title*='Descargar']",
    "i[class*='download']",
    "span[class*='download']",
    "i[class*='descarg']",
    "span[class*='descarg']",
    "i[class*='imprimir']",
    "span[class*='imprimir']",
    "i[class*='print']",
    "span[class*='print']",
    "i[class*='pdf']",
    "span[class*='pdf']",
)
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

LOGGER = logging.getLogger("hacienda_bot")

minio_url_interna = os.getenv("MINIO_URL_INTERNA")
minio_url_externa = os.getenv("MINIO_URL_EXTERNA")
minio_ssl = os.getenv("MINIO_SSL", "False").lower() == "true"
minio_interno = os.getenv("MINIO_INTERNO", "False").lower() == "true"

minio_bucket_hacienda = os.getenv("MINIO_BUCKET_HACIENDA", "hacienda")
minio_access_key_hacienda = os.getenv("MINIO_MRBOT_ACCESS_KEY")
minio_secret_key_hacienda = os.getenv("MINIO_MRBOT_SECRET_KEY")


@dataclass
class ComprobanteRecord:
    row_index: int
    row_text: str
    columns: Dict[str, str]
    cuit_referencia: str
    codigo_referencia: str
    download_key: str
    data_id: str
    tipo_comprobante: str
    punto_venta: str
    numero_comprobante: str


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def normalize_name_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized_ascii = re.sub(r"[^A-Za-z0-9]+", " ", without_accents).upper()
    return normalize_text(normalized_ascii)


def _directional_token_coverage(source_text: str, target_text: str) -> float:
    source_tokens = [token for token in source_text.split() if token]
    target_tokens = [token for token in target_text.split() if token]
    if not source_tokens or not target_tokens:
        return 0.0

    total = 0.0
    for source_token in source_tokens:
        best = 0.0
        for target_token in target_tokens:
            if source_token == target_token:
                best = 1.0
                break
            ratio = SequenceMatcher(None, source_token, target_token).ratio()
            if ratio > best:
                best = ratio
        total += best
    return total / len(source_tokens)


def similarity_score(left: str, right: str) -> float:
    # Fuzzy siempre case-insensitive: normaliza ambos lados internamente.
    left_norm = normalize_name_for_match(left)
    right_norm = normalize_name_for_match(right)
    if not left_norm or not right_norm:
        return 0.0

    forward_coverage = _directional_token_coverage(left_norm, right_norm)
    backward_coverage = _directional_token_coverage(right_norm, left_norm)
    coverage_balance = min(forward_coverage, backward_coverage)
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()

    return (
        (coverage_balance * 0.45)
        + (forward_coverage * 0.25)
        + (backward_coverage * 0.20)
        + (ratio * 0.10)
    )


def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "archivo"


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def extract_download_key(raw_value: str) -> str:
    value = raw_value or ""
    patterns = (
        (r"liqCodigo=(\d+)", "liqCodigo"),
        (r"coe=(\d+)", "coe"),
        (r"codigo=(\d+)", "codigo"),
        (r"id=(\d+)", "id"),
        (r"comprobante(?:Id)?=(\d+)", "comprobanteId"),
        (r"liquidacion(?:Id)?=(\d+)", "liquidacionId"),
        (r'data-id[=:\\"\s]+(\d+)', "dataId"),
        (r'data-idcomprobante[=:\\"\s]+(\d+)', "dataIdComprobante"),
        (r"\b(\d{6,})\b", "numero"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return f"{label}={match.group(1)}"
    return normalize_text(value)[:160]


def extract_data_id(raw_value: str) -> str:
    value = raw_value or ""
    patterns = (
        r'data-id(?:comprobante)?[=:\\"\s]+(\d+)',
        r"getPdf\?id=(\d+)",
        r"[?&](?:id|idComprobante|idLiquidacion)=(\d+)",
        r"\b(?:id|idComprobante|idLiquidacion)\s*[:=]\s*(\d+)\b",
        r"^\s*(\d{6,})\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return only_digits(match.group(1))
    return ""


def _join_key_parts(parts: Sequence[str]) -> str:
    return " ".join(normalize_text(part) for part in parts if part)


def find_bs4_download_element(tr) -> Optional[object]:
    for selector in BS4_DOWNLOAD_SELECTORS:
        try:
            node = tr.select_one(selector)
        except Exception:
            node = None
        if node is not None:
            return node

    for node in tr.find_all(attrs={"onclick": True}):
        onclick = normalize_text(node.get("onclick", ""))
        if re.search(r"pd\.jsp|\.pdf|descarg|imprim", onclick, flags=re.I):
            return node

    for node in tr.find_all(attrs={"href": True}):
        href = normalize_text(node.get("href", ""))
        if re.search(r"pd\.jsp|\.pdf|descarg|imprim", href, flags=re.I):
            return node
    return None


def find_column_value(columns: Dict[str, str], patterns: Sequence[str]) -> str:
    normalized_items = [
        (normalize_text(key).lower(), normalize_text(value))
        for key, value in columns.items()
    ]
    for pattern in patterns:
        token = pattern.lower()
        for key, value in normalized_items:
            if token in key and value:
                return value
    return ""


def infer_row_identifiers(columns: Dict[str, str], row_text: str) -> Tuple[str, str]:
    cuit_referencia = only_digits(
        find_column_value(
            columns,
            (
                "cuit vendedor",
                "cuit receptor",
                "cuit emisor",
                "cuit comprador",
                "cuit",
            ),
        )
    )
    if len(cuit_referencia) != 11:
        cuit_referencia = ""

    codigo_referencia = only_digits(
        find_column_value(
            columns,
            (
                "coe",
                "codigo",
                "código",
                "nro",
                "numero",
                "liquidacion",
                "liquidación",
            ),
        )
    )
    if len(codigo_referencia) < 6:
        codigo_referencia = ""

    digits = re.findall(r"\b\d{6,20}\b", row_text)
    if not codigo_referencia:
        for token in digits:
            if len(token) >= 8:
                codigo_referencia = token
                break
    if not cuit_referencia:
        for token in digits:
            if len(token) == 11:
                cuit_referencia = token
                break

    if not cuit_referencia:
        cuit_referencia = "SIN_CUIT"
    if not codigo_referencia:
        codigo_referencia = "SIN_CODIGO"
    return cuit_referencia, codigo_referencia


def infer_comprobante_parts(columns: Dict[str, str], row_text: str) -> Tuple[str, str, str]:
    tipo = normalize_text(
        find_column_value(
            columns,
            (
                "tipo comprobante",
                "tipo de comprobante",
                "tipo comp",
            ),
        )
    )
    tipo_digits = only_digits(tipo)
    if tipo_digits:
        tipo = tipo_digits

    numero_raw = normalize_text(
        find_column_value(
            columns,
            (
                "numero de comprobante",
                "nro de comprobante",
                "número de comprobante",
                "nro comprobante",
            ),
        )
    )

    punto = ""
    numero = ""
    if numero_raw:
        match = re.search(r"(\d{1,6})\s*[-/]\s*(\d{1,12})", numero_raw)
        if match:
            punto = match.group(1)
            numero = match.group(2)
        else:
            pieces = re.findall(r"\d+", numero_raw)
            if len(pieces) >= 2:
                punto, numero = pieces[0], pieces[1]
            elif len(pieces) == 1:
                numero = pieces[0]

    if not tipo or not numero:
        fallback_match = re.search(r"\b(\d{2,4})\s+(\d{1,6})\s*-\s*(\d{1,12})\b", row_text)
        if fallback_match:
            if not tipo:
                tipo = fallback_match.group(1)
            if not punto:
                punto = fallback_match.group(2)
            if not numero:
                numero = fallback_match.group(3)

    return only_digits(tipo), only_digits(punto), only_digits(numero)


def build_codigo_segment(record: Optional[ComprobanteRecord], row_text: str) -> str:
    if record is not None:
        tipo = only_digits(record.tipo_comprobante)
        punto = only_digits(record.punto_venta)
        numero = only_digits(record.numero_comprobante)
        if tipo and punto and numero:
            return f"{tipo} - {punto.zfill(5)} - {numero.zfill(8)}"
        if tipo and numero:
            return f"{tipo} - {numero.zfill(8)}"
        if numero:
            return numero

        codigo_raw = normalize_text(record.codigo_referencia)
        if " - " in codigo_raw:
            return codigo_raw
        codigo_digits = only_digits(codigo_raw)
        if codigo_digits:
            return codigo_digits

    fallback = re.search(r"\b(\d{2,4})\s+(\d{1,6})\s*-\s*(\d{1,12})\b", row_text or "")
    if fallback:
        return (
            f"{only_digits(fallback.group(1))} - "
            f"{only_digits(fallback.group(2)).zfill(5)} - "
            f"{only_digits(fallback.group(3)).zfill(8)}"
        )
    return "SIN_CODIGO"


def _extract_best_headers_for_row(table, data_cell_count: int) -> List[str]:
    header_rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        ths = tr.find_all("th", recursive=False)
        if not ths:
            continue
        row_headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]
        row_headers = [item for item in row_headers if item]
        if len(row_headers) >= 2:
            header_rows.append(row_headers)

    if not header_rows:
        return []

    best_under_or_equal: Optional[Tuple[int, List[str]]] = None
    best_any: Optional[Tuple[int, List[str]]] = None
    for row_headers in header_rows:
        diff = abs(len(row_headers) - data_cell_count)
        if best_any is None or diff < best_any[0]:
            best_any = (diff, row_headers)
        if len(row_headers) <= data_cell_count:
            under_diff = data_cell_count - len(row_headers)
            if best_under_or_equal is None or under_diff < best_under_or_equal[0]:
                best_under_or_equal = (under_diff, row_headers)

    if best_under_or_equal is not None:
        return best_under_or_equal[1]
    return best_any[1] if best_any is not None else []


def parse_table_records(html_content: str) -> Tuple[List[str], List[ComprobanteRecord]]:
    soup = BeautifulSoup(html_content, "html.parser")
    candidate_by_table: Dict[int, Dict[str, object]] = {}
    headers_cache: Dict[Tuple[int, int], List[str]] = {}

    for row_idx, tr in enumerate(soup.find_all("tr")):
        download_node = find_bs4_download_element(tr)
        if download_node is None:
            continue
        if tr.find("table") is not None:
            continue

        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
        if not any(cells):
            continue

        row_text = normalize_text(" ".join(cells))
        if not row_text:
            continue

        row_parent_table = tr.find_parent("table")
        if row_parent_table is None:
            continue

        table_key = id(row_parent_table)
        header_cache_key = (table_key, len(cells))
        headers = headers_cache.get(header_cache_key)
        if headers is None:
            headers = _extract_best_headers_for_row(row_parent_table, len(cells))
            headers_cache[header_cache_key] = headers

        mapped = {
            headers[i] if i < len(headers) and headers[i] else f"col_{i + 1}": cells[i]
            for i in range(len(cells))
        }
        cuit_referencia, codigo_referencia = infer_row_identifiers(mapped, row_text)
        tipo_comprobante, punto_venta, numero_comprobante = infer_comprobante_parts(mapped, row_text)
        parent_clickable = None
        try:
            parent_clickable = download_node.find_parent(["a", "button"])
        except Exception:
            parent_clickable = None
        key_source = _join_key_parts(
            [
                str(download_node.get("onclick") or ""),
                str(download_node.get("href") or ""),
                str(download_node.get("title") or ""),
                str(download_node.get("aria-label") or ""),
                str(download_node.get("id") or ""),
                (
                    f"data-id={download_node.get('data-id')}"
                    if download_node.get("data-id")
                    else ""
                ),
                (
                    f"data-idcomprobante={download_node.get('data-idcomprobante')}"
                    if download_node.get("data-idcomprobante")
                    else ""
                ),
                " ".join(download_node.get("class") or []),
                str(parent_clickable.get("onclick") if parent_clickable else ""),
                str(parent_clickable.get("href") if parent_clickable else ""),
                str(parent_clickable.get("title") if parent_clickable else ""),
                str(parent_clickable.get("aria-label") if parent_clickable else ""),
                str(parent_clickable.get("id") if parent_clickable else ""),
                (
                    f"data-id={parent_clickable.get('data-id')}"
                    if parent_clickable and parent_clickable.get("data-id")
                    else ""
                ),
                (
                    f"data-idcomprobante={parent_clickable.get('data-idcomprobante')}"
                    if parent_clickable and parent_clickable.get("data-idcomprobante")
                    else ""
                ),
                " ".join((parent_clickable.get("class") or []) if parent_clickable else []),
                row_text,
            ]
        )
        data_id_attr = only_digits(str(download_node.get("data-id") or ""))
        data_id_comp_attr = only_digits(str(download_node.get("data-idcomprobante") or ""))
        parent_data_id = only_digits(
            str(parent_clickable.get("data-id") if parent_clickable else "")
        )
        parent_data_id_comp = only_digits(
            str(parent_clickable.get("data-idcomprobante") if parent_clickable else "")
        )
        data_id_source = _join_key_parts(
            [
                f"data-id={data_id_attr}" if data_id_attr else "",
                f"data-idcomprobante={data_id_comp_attr}" if data_id_comp_attr else "",
                str(download_node.get("onclick") or ""),
                str(download_node.get("href") or ""),
                f"data-id={parent_data_id}" if parent_data_id else "",
                (
                    f"data-idcomprobante={parent_data_id_comp}"
                    if parent_data_id_comp
                    else ""
                ),
                str(parent_clickable.get("onclick") if parent_clickable else ""),
                str(parent_clickable.get("href") if parent_clickable else ""),
            ]
        )
        record = ComprobanteRecord(
            row_index=row_idx,
            row_text=row_text,
            columns=mapped,
            cuit_referencia=cuit_referencia,
            codigo_referencia=codigo_referencia,
            download_key=extract_download_key(key_source),
            data_id=extract_data_id(data_id_source),
            tipo_comprobante=tipo_comprobante,
            punto_venta=punto_venta,
            numero_comprobante=numero_comprobante,
        )

        if table_key not in candidate_by_table:
            candidate_by_table[table_key] = {"headers": headers, "records": []}
        candidate_by_table[table_key]["records"].append(record)

    if not candidate_by_table:
        return [], []

    candidates = []
    for candidate in candidate_by_table.values():
        headers = candidate["headers"]
        records = candidate["records"]
        unique_keys = {item.download_key for item in records if item.download_key}
        score = (len(unique_keys), len(records))
        candidates.append((score, headers, records))

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, headers, records = candidates[0]
    return list(headers), list(records)


def records_to_dataframe(headers: List[str], records: List[ComprobanteRecord]) -> pd.DataFrame:
    rows = [record.columns for record in records]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if headers:
        base_headers = [header for header in headers if header]
        for header in base_headers:
            if header not in df.columns:
                df[header] = ""
        final_columns = base_headers + [col for col in df.columns if col not in base_headers]
        df = df[final_columns]

    df["cuit_referencia_detectado"] = [record.cuit_referencia for record in records]
    df["codigo_referencia_detectado"] = [record.codigo_referencia for record in records]
    df["tipo_comprobante_detectado"] = [record.tipo_comprobante for record in records]
    df["punto_venta_detectado"] = [record.punto_venta for record in records]
    df["numero_comprobante_detectado"] = [record.numero_comprobante for record in records]
    df["data_id_detectado"] = [record.data_id for record in records]
    return df


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


async def click_and_get_target_page(
    host_page: Page,
    click_target: Locator,
    timeout_ms: int = 12000,
) -> Page:
    popup_waiter = asyncio.create_task(host_page.wait_for_event("popup", timeout=timeout_ms))
    popup_page: Optional[Page] = None
    try:
        await click_target.click()
        try:
            popup_page = await popup_waiter
        except PlaywrightTimeoutError:
            popup_page = None
    finally:
        if not popup_waiter.done():
            popup_waiter.cancel()

    target_page = popup_page or host_page
    try:
        await target_page.wait_for_load_state("domcontentloaded", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    try:
        await target_page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    return target_page


async def click_by_role_name(
    page: Page,
    role: str,
    pattern: re.Pattern[str],
    step_name: str,
    timeout: int = 10000,
) -> None:
    locator = page.get_by_role(role, name=pattern)
    count = await locator.count()
    if count == 0:
        raise RuntimeError(f"No se encontro {role} con patron {pattern.pattern} en {step_name}")
    await locator.first.click(timeout=timeout)
    try:
        await page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError:
        pass


async def fill_first_available(page: Page, locators: List[Locator], value: str, field_name: str) -> None:
    for locator in locators:
        if await locator.count() == 0:
            continue
        candidate = locator.first
        try:
            await candidate.click(timeout=2000)
            await candidate.fill("")
            await candidate.fill(value)
            return
        except Exception:
            continue
    raise RuntimeError(f"No se pudo completar el campo {field_name}")


async def click_first_available(page: Page, locators: List[Locator], step_name: str) -> None:
    for locator in locators:
        if await locator.count() == 0:
            continue
        for idx in range(await locator.count()):
            candidate = locator.nth(idx)
            try:
                await candidate.click(timeout=4000)
                return
            except Exception:
                continue
    raise RuntimeError(f"No se pudo hacer click en {step_name}")


async def fill_fechas_y_buscar(
    page: Page,
    desde: str,
    hasta: str,
    consulta_key: str,
) -> None:
    await fill_first_available(
        page,
        [
            page.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+desde", re.I)),
            page.locator("input[name='fechaStr']"),
            page.locator("input[id*='fecha'][id*='desde' i]"),
            page.locator("input[placeholder*='desde' i]"),
        ],
        desde,
        field_name="fecha_desde",
    )
    await fill_first_available(
        page,
        [
            page.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+hasta", re.I)),
            page.locator("input[name='fechaHastaStr']"),
            page.locator("input[id*='fecha'][id*='hasta' i]"),
            page.locator("input[placeholder*='hasta' i]"),
        ],
        hasta,
        field_name="fecha_hasta",
    )
    await click_first_available(
        page,
        [
            page.get_by_role("button", name=re.compile(r"Buscar|Consultar", re.I)),
            page.locator("button:has-text('Buscar')"),
            page.locator("a:has-text('Buscar')"),
        ],
        f"{consulta_key}_buscar",
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass
    await set_max_records_per_page(page, consulta_key)


async def _menu_is_open(page: Page) -> bool:
    wrapper = page.locator("#wrapper")
    if await wrapper.count() > 0:
        wrapper_class = ((await wrapper.first.get_attribute("class")) or "").lower()
        if "toggled" in wrapper_class:
            return True

    sidebar_button = page.locator("#buttonSidebar")
    if await sidebar_button.count() > 0:
        sidebar_class = ((await sidebar_button.first.get_attribute("class")) or "").lower()
        if "toggled" in sidebar_class:
            return True

    hamburger = page.locator("#buttonSidebar button.hamburger, button.hamburger")
    if await hamburger.count() > 0:
        burger_class = ((await hamburger.first.get_attribute("class")) or "").lower()
        if "is-open" in burger_class:
            return True
    return False


async def set_max_records_per_page(page: Page, _consulta_key: str) -> None:
    try:
        select_candidates = [
            page.locator("#cantPages"),
            page.locator("select[id*='cantPages' i]"),
            page.locator("select[name='displayLength']"),
        ]
        select_locator: Optional[Locator] = None
        for candidate in select_candidates:
            if await candidate.count() > 0:
                select_locator = candidate.first
                break

        if select_locator is None:
            return

        option_locator = select_locator.locator("option")
        option_count = await option_locator.count()
        numeric_values: List[int] = []
        for idx in range(option_count):
            option = option_locator.nth(idx)
            raw = (await option.get_attribute("value")) or normalize_text(await option.inner_text())
            digits = re.sub(r"\D", "", raw or "")
            if digits:
                numeric_values.append(int(digits))

        if not numeric_values:
            return

        max_value = max(numeric_values)
        current_value = normalize_text(await select_locator.input_value())
        if current_value == str(max_value):
            return

        await select_locator.select_option(str(max_value))
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=12000)
        except PlaywrightTimeoutError:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except PlaywrightTimeoutError:
            pass

    except Exception:
        pass


async def open_menu_if_needed(page: Page) -> None:
    menu_candidates = [
        page.locator("#buttonSidebar button.hamburger[data-toggle='offcanvas']"),
        page.locator("button.hamburger[data-toggle='offcanvas']"),
        page.locator("button[data-toggle='offcanvas']"),
        page.get_by_role("button", name=re.compile(r"Men[úu]|Menu", re.I)),
        page.locator("button.navbar-toggle"),
        page.locator("button:has(i.fa-bars)"),
        page.locator("button:has-text('☰')"),
    ]

    if await _menu_is_open(page):
        return

    for locator in menu_candidates:
        count = await locator.count()
        if count == 0:
            continue

        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:
                continue

            try:
                await candidate.scroll_into_view_if_needed()
            except Exception:
                pass

            clicked = False
            try:
                await candidate.click(timeout=3000)
                clicked = True
            except Exception:
                try:
                    await candidate.click(timeout=3000, force=True)
                    clicked = True
                except Exception:
                    pass

            if not clicked:
                try:
                    handle = await candidate.element_handle()
                    if handle is not None:
                        await handle.evaluate("el => el.click()")
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                continue

            await asyncio.sleep(0.35)
            if await _menu_is_open(page):
                return

    try:
        toggled_by_js = await page.evaluate(
            """
            () => {
              const btn =
                document.querySelector('#buttonSidebar button.hamburger[data-toggle="offcanvas"]') ||
                document.querySelector('button.hamburger[data-toggle="offcanvas"]') ||
                document.querySelector('button[data-toggle="offcanvas"]');
              if (btn) {
                btn.click();
                return true;
              }
              if (window.$) {
                const jq = window.$('[data-toggle="offcanvas"]').first();
                if (jq.length) {
                  jq.trigger('click');
                  return true;
                }
              }
              return false;
            }
            """
        )
        if toggled_by_js:
            await asyncio.sleep(0.35)
            if await _menu_is_open(page):
                return
    except Exception:
        pass


async def open_query_section(
    page: Page,
    consulta_key: str,
    query_pattern: re.Pattern[str],
) -> None:
    if re.search(r"inicioPerfil\.htm|/lsp-web/inicio$", page.url or "", re.I):
        await open_menu_if_needed(page)

    link = page.get_by_role("link", name=query_pattern)
    if await link.count() == 0:
        await open_menu_if_needed(page)
        link = page.get_by_role("link", name=query_pattern)

    if await link.count() == 0:
        raise RuntimeError(f"No se encontro la opcion de consulta para {consulta_key}")

    try:
        await link.first.click(timeout=10000)
    except Exception:
        await open_menu_if_needed(page)
        link = page.get_by_role("link", name=query_pattern)
        if await link.count() == 0:
            raise RuntimeError(f"No se encontro la opcion de consulta para {consulta_key}")

        href = (await link.first.get_attribute("href")) or ""
        if href and not href.lower().startswith("javascript:"):
            target_url = urljoin(page.url or "", href)
            await page.goto(target_url, timeout=30000)
        else:
            try:
                await link.first.click(timeout=10000, force=True)
            except Exception:
                handle = await link.first.element_handle()
                if handle is None:
                    raise
                await handle.evaluate("el => el.click()")
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass


async def capture_table_records(page: Page) -> Tuple[List[str], List[ComprobanteRecord]]:
    html_content = await page.content()
    headers, records = parse_table_records(html_content)
    return headers, records


def build_table_dataframe(
    consulta_key: str,
    page_index: int,
    headers: List[str],
    records: List[ComprobanteRecord],
) -> Optional[pd.DataFrame]:
    if not records:
        return None

    df = records_to_dataframe(headers, records)
    df.insert(0, "pagina", page_index)
    df.insert(0, "consulta", consulta_key)
    return df


async def download_from_link(
    page: Page,
    link_locator: Locator,
    target_path: Path,
    label: str,
    timeout_ms: int = 30000,
) -> Optional[Path]:
    try:
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await link_locator.click()
        download: Download = await download_info.value
        final_path = ensure_unique_path(target_path)
        await download.save_as(str(final_path))
        return final_path
    except PlaywrightTimeoutError:
        LOGGER.warning("descarga timeout: %s", label)
    except PlaywrightError as exc:
        LOGGER.warning("descarga error (%s): %s", label, exc)

    popup_waiter = asyncio.create_task(page.wait_for_event("popup", timeout=8000))
    popup_page: Optional[Page] = None
    current_page_url_before = page.url
    try:
        await link_locator.click()
        try:
            popup_page = await popup_waiter
        except PlaywrightTimeoutError:
            popup_page = None
    except Exception:
        pass
    finally:
        if not popup_waiter.done():
            popup_waiter.cancel()

    candidate_pages = [popup_page] if popup_page is not None else [page]
    for candidate_page in candidate_pages:
        try:
            try:
                await candidate_page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            file_url = candidate_page.url or ""
            if not file_url or file_url == "about:blank":
                continue
            response = await candidate_page.context.request.get(file_url)
            if response.status != 200:
                continue

            content = await response.body()
            if not content:
                continue

            content_type = (response.headers.get("content-type") or "").lower()
            is_pdf = (
                "pdf" in content_type
                or file_url.lower().endswith(".pdf")
                or "pd.jsp" in file_url.lower()
                or content.startswith(b"%PDF")
            )
            if not is_pdf:
                continue

            final_path = ensure_unique_path(target_path)
            final_path.write_bytes(content)
            if popup_page is not None:
                try:
                    await popup_page.close()
                except Exception:
                    pass
            elif page.url != current_page_url_before:
                try:
                    await page.go_back()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            return final_path
        except Exception:
            continue

    if popup_page is not None:
        try:
            await popup_page.close()
        except Exception:
            pass
    return None


async def download_pdf_by_data_id(
    page: Page,
    data_id: str,
    target_path: Path,
    label: str,
) -> Optional[Path]:
    if not data_id:
        return None

    data_id_digits = only_digits(data_id)
    if not data_id_digits:
        return None

    base_url = page.url or ""
    pdf_url = urljoin(base_url, f"/lsp-web/consultaLiquidacion/getPdf?id={data_id_digits}")
    try:
        response = await page.context.request.get(pdf_url)
        if response.status != 200:
            return None

        content = await response.body()
        if not content:
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        is_pdf = (
            "pdf" in content_type
            or content.startswith(b"%PDF")
            or "application/octet-stream" in content_type
        )
        if not is_pdf:
            return None

        final_path = ensure_unique_path(target_path)
        final_path.write_bytes(content)
        return final_path
    except Exception as exc:
        LOGGER.debug("Error en descarga por data_id (%s): %s", label, exc)
        return None


async def get_result_rows(page: Page, retries: int = 3):
    for i in range(retries):
        try:
            result_rows = page.locator(RESULT_TABLE_ROW_SELECTOR)
            if await result_rows.count() > 0:
                return result_rows
            
            fallback = page.locator("tr").filter(has=page.locator(DOWNLOAD_ROW_SELECTOR))
            if await fallback.count() > 0:
                return fallback
            
            return result_rows
        except Exception as e:
            if "destroyed" in str(e).lower() and i < retries - 1:
                await asyncio.sleep(1)
                continue
            raise


async def get_row_download_targets(row: Locator) -> List[Tuple[Locator, str]]:
    targets: List[Tuple[Locator, str]] = []
    seen_signatures: Set[str] = set()

    source_candidates: List[Tuple[Locator, int]] = []
    actions = row.locator(DOWNLOAD_ACTION_SELECTOR)
    action_count = await actions.count()
    if action_count > 0:
        for idx in range(action_count):
            source_candidates.append((actions.nth(idx), idx + 1))
    else:
        icons = row.locator(DOWNLOAD_ICON_SELECTOR)
        icon_count = await icons.count()
        for idx in range(icon_count):
            icon = icons.nth(idx)
            parent_clickable = icon.locator("xpath=ancestor::a[1] | ancestor::button[1]")
            if await parent_clickable.count() > 0:
                source_candidates.append((parent_clickable.first, idx + 1))
            else:
                source_candidates.append((icon, idx + 1))

    for candidate, pos in source_candidates:
        try:
            if not await candidate.is_visible():
                continue
        except Exception:
            continue

        onclick = (await candidate.get_attribute("onclick")) or ""
        href = (await candidate.get_attribute("href")) or ""
        title = (await candidate.get_attribute("title")) or ""
        aria_label = (await candidate.get_attribute("aria-label")) or ""
        element_id = (await candidate.get_attribute("id")) or ""
        data_id = (await candidate.get_attribute("data-id")) or ""
        class_attr = (await candidate.get_attribute("class")) or ""
        inner_text = normalize_text(await candidate.inner_text())

        signature = _join_key_parts(
            [
                onclick,
                href,
                title,
                aria_label,
                element_id,
                f"data-id={data_id}" if data_id else "",
                class_attr,
                inner_text,
            ]
        )
        if not signature:
            signature = f"candidate_{pos}"
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        download_key = extract_download_key(signature)
        if not download_key:
            download_key = f"candidate_{pos}"
        targets.append((candidate, download_key))
    return targets


async def get_page_signature(page: Page, retries: int = 3) -> str:
    for i in range(retries):
        try:
            rows = await get_result_rows(page)
            count = await rows.count()
            break
        except Exception as e:
            if "destroyed" in str(e).lower() and i < retries - 1:
                await asyncio.sleep(1)
                continue
            raise
    else:
        return "empty"
    keys: List[str] = []
    for idx in range(count):
        row = rows.nth(idx)
        if await row.locator("table").count() > 0:
            continue
        targets = await get_row_download_targets(row)
        for _, key in targets:
            if key:
                keys.append(key)

    unique_keys = sorted(set(keys))
    if unique_keys:
        return "|".join(unique_keys)

    try:
        table_text = normalize_text(await page.locator("table").first.inner_text())
    except Exception:
        table_text = normalize_text(await page.inner_text("body"))
    return table_text[:400]


async def wait_for_signature_change(
    page: Page,
    previous_signature: str,
    timeout_ms: int = 10000,
) -> str:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        await asyncio.sleep(0.4)
        current_signature = await get_page_signature(page)
        if current_signature != previous_signature:
            return current_signature
    return previous_signature


async def locator_is_disabled(locator: Locator) -> bool:
    disabled = await locator.get_attribute("disabled")
    aria_disabled = (await locator.get_attribute("aria-disabled") or "").strip().lower()
    css_class = (await locator.get_attribute("class") or "").lower()
    parent_class = ""
    try:
        parent_class = (
            (await locator.locator("xpath=..").first.get_attribute("class") or "").lower()
        )
    except Exception:
        parent_class = ""
    return bool(disabled) or aria_disabled == "true" or "disabled" in css_class or "disabled" in parent_class


async def go_to_next_results_page(
    page: Page,
    consulta_key: str,
    visited_signatures: Set[str],
) -> bool:
    previous_signature = await get_page_signature(page)

    candidate_locators = [
        page.get_by_role("link", name=NEXT_PAGE_PATTERN),
        page.get_by_role("button", name=NEXT_PAGE_PATTERN),
        page.locator("li.next:not(.disabled) a"),
        page.locator("a[aria-label*='Siguiente'], button[aria-label*='Siguiente']"),
        page.locator("a:has-text('Siguiente'), button:has-text('Siguiente')"),
    ]

    for locator in candidate_locators:
        count = await locator.count()
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:
                continue

            if await locator_is_disabled(candidate):
                continue

            try:
                await candidate.scroll_into_view_if_needed()
            except Exception:
                pass

            try:
                await candidate.click(timeout=5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                current_signature = await wait_for_signature_change(page, previous_signature, timeout_ms=10000)
                if current_signature == previous_signature:
                    continue
                if current_signature in visited_signatures:
                    return False
                visited_signatures.add(current_signature)
                return True
            except Exception:
                continue

    LOGGER.debug("No se encontro boton de siguiente para %s", consulta_key)
    return False


async def descargar_comprobantes_pagina(
    page: Page,
    consulta_key: str,
    cuit_representante: str,
    page_index: int,
    processed_keys: Set[str],
    download_dir: Path,
    table_frames: List[pd.DataFrame],
) -> List[Path]:
    headers, records = await capture_table_records(page)
    table_df = build_table_dataframe(consulta_key, page_index, headers, records)
    if table_df is not None:
        table_frames.append(table_df)
    records_by_key = {record.download_key: record for record in records if record.download_key}
    records_by_row_text = {normalize_text(record.row_text): record for record in records}

    downloaded_paths: List[Path] = []

    for rec_idx, record in enumerate(records, start=1):
        stable_key = extract_download_key(f"{record.download_key} {record.row_text}") or safe_filename(
            f"{consulta_key}_{page_index}_{rec_idx}"
        )
        if stable_key in processed_keys:
            continue

        data_id = extract_data_id(
            _join_key_parts(
                [
                    record.data_id,
                    record.download_key,
                    record.row_text,
                ]
            )
        )
        if not data_id:
            continue

        cuit_ref = only_digits(record.cuit_referencia) or "SIN_CUIT"
        codigo_segment = build_codigo_segment(record, record.row_text)
        file_name = safe_filename(
            (
                f"HACIENDA - {consulta_key.upper()} - {cuit_representante} - "
                f"{cuit_ref} - {codigo_segment}.pdf"
            )
        )
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

        label = (
            f"{consulta_key}_pagina_{page_index}_record_{rec_idx}_"
            f"{stable_key}"
        )
        saved_path = await download_pdf_by_data_id(
            page,
            data_id=data_id,
            target_path=download_dir / file_name,
            label=label,
        )
        if saved_path is not None:
            processed_keys.add(stable_key)
            downloaded_paths.append(saved_path)

    rows = await get_result_rows(page)
    row_count = await rows.count()

    for idx in range(row_count):
        row = rows.nth(idx)
        if await row.locator("table").count() > 0:
            continue

        tds = row.locator("xpath=./td")
        td_count = await tds.count()
        cells: List[str] = []
        for td_idx in range(td_count):
            cells.append(normalize_text(await tds.nth(td_idx).inner_text()))
        row_text = normalize_text(" ".join(cells))

        row_targets = await get_row_download_targets(row)
        if not row_targets:
            continue

        for target_pos, (download_target, target_key) in enumerate(row_targets, start=1):
            key = target_key or f"fila_{page_index}_{idx + 1}_target_{target_pos}"
            stable_key = extract_download_key(f"{key} {row_text}") or safe_filename(
                f"{consulta_key}_{page_index}_{idx + 1}_{target_pos}"
            )
            if stable_key in processed_keys:
                continue

            record = (
                records_by_key.get(key)
                or records_by_key.get(stable_key)
                or records_by_row_text.get(row_text)
            )
            if record is not None:
                cuit_ref = only_digits(record.cuit_referencia) or "SIN_CUIT"
                codigo_segment = build_codigo_segment(record, row_text)
            else:
                cuit_ref, codigo_ref = infer_row_identifiers({}, row_text)
                cuit_ref = only_digits(cuit_ref) or "SIN_CUIT"
                if codigo_ref and re.search(r"\s-\s", codigo_ref):
                    codigo_segment = codigo_ref
                else:
                    codigo_segment = only_digits(codigo_ref) or "SIN_CODIGO"

            file_name = safe_filename(
                (
                    f"HACIENDA - {consulta_key.upper()} - {cuit_representante} - "
                    f"{cuit_ref} - {codigo_segment}.pdf"
                )
            )
            if not file_name.lower().endswith(".pdf"):
                file_name += ".pdf"

            processed_keys.add(stable_key)
            target = download_dir / file_name
            label = (
                f"{consulta_key}_pagina_{page_index}_fila_{idx + 1}_"
                f"target_{target_pos}_{stable_key}"
            )
            onclick = (await download_target.get_attribute("onclick")) or ""
            href = (await download_target.get_attribute("href")) or ""
            data_id = (
                (await download_target.get_attribute("data-id"))
                or (await download_target.get_attribute("data-idcomprobante"))
                or (record.data_id if record is not None else "")
                or extract_data_id(_join_key_parts([onclick, href, key, stable_key]))
                or ""
            )
            saved_path = await download_pdf_by_data_id(
                page,
                data_id=data_id,
                target_path=target,
                label=label,
            )
            if saved_path is None:
                saved_path = await download_from_link(
                    page,
                    download_target,
                    target,
                    label=label,
                )
            if saved_path is not None:
                downloaded_paths.append(saved_path)

    return downloaded_paths


async def descargar_todas_las_paginas(
    page: Page,
    consulta_key: str,
    cuit_representante: str,
    download_dir: Path,
) -> Tuple[List[Path], List[pd.DataFrame]]:
    page_index = 1
    processed_keys: Set[str] = set()
    
    # Wait for page to stabilize before querying
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    
    visited_signatures: Set[str] = {await get_page_signature(page)}
    downloaded: List[Path] = []
    table_frames: List[pd.DataFrame] = []

    while True:
        page_downloads = await descargar_comprobantes_pagina(
            page,
            consulta_key,
            cuit_representante,
            page_index,
            processed_keys,
            download_dir,
            table_frames,
        )
        downloaded.extend(page_downloads)

        moved_next = await go_to_next_results_page(page, consulta_key, visited_signatures)
        if not moved_next:
            break
        page_index += 1

    return downloaded, table_frames


async def ensure_login(page: Page, cuit_representante: str, clave: str, error_logs: List[str]) -> None:
    login_result = await login_arca(
        page,
        cuit_representante,
        clave,
        error_logs=error_logs,
        login_url=LOGIN_URL,
    )
    if not login_result.success:
        raise RuntimeError(error_logs[-1] if error_logs else "No se pudo iniciar sesión en ARCA")


async def open_comprobantes_service(page: Page) -> Page:
    service_page = await open_arca_service(
        page,
        service_name=COMPROBANTES_SERVICE_NAME,
        service_matchers=(COMPROBANTES_SERVICE_PATTERN,),
        service_unavailable_message=f"No se encuentra habilitado el servicio de {COMPROBANTES_SERVICE_NAME}",
    )
    if service_page is None:
        raise RuntimeError(f"No se encuentra habilitado el servicio de {COMPROBANTES_SERVICE_NAME}")
    return service_page


async def seleccionar_denominacion(
    page: Page,
    denominacion: str,
    step_name: str,
) -> None:
    target_text = normalize_text(denominacion)
    if not target_text:
        return
    target_norm = normalize_name_for_match(target_text)

    async def click_candidate(candidate: Locator) -> bool:
        try:
            await candidate.scroll_into_view_if_needed()
        except Exception:
            pass

        click_attempts = (
            {"timeout": 8000},
            {"timeout": 8000, "force": True},
        )
        for kwargs in click_attempts:
            try:
                await candidate.click(**kwargs)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                return True
            except Exception:
                continue

        try:
            handle = await candidate.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                return True
        except Exception:
            pass

        return False

    async def click_first_match(locator: Locator) -> bool:
        count = await locator.count()
        visibles: List[Locator] = []
        ocultos: List[Locator] = []
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if await candidate.is_visible():
                    visibles.append(candidate)
                else:
                    ocultos.append(candidate)
            except Exception:
                ocultos.append(candidate)

        for candidate in [*visibles, *ocultos]:
            if await click_candidate(candidate):
                return True
        return False

    # 1) Exacto 100% case-sensitive
    exact_locators = [
        page.get_by_role("button", name=target_text, exact=True),
        page.get_by_role("link", name=target_text, exact=True),
    ]
    for locator in exact_locators:
        if await click_first_match(locator):
            return

    # 2) Exacto case-insensitive
    insensitive_pattern = re.compile(rf"^{re.escape(target_text)}$", re.I)
    ci_locators = [
        page.get_by_role("button", name=insensitive_pattern),
        page.get_by_role("link", name=insensitive_pattern),
    ]
    for locator in ci_locators:
        if await click_first_match(locator):
            return

    # 3) Fuzzy por similitud de texto/token (fallback final)
    candidate_locators = [
        page.get_by_role("button"),
        page.get_by_role("link"),
        page.locator("[role='button'], button, a, [onclick]"),
    ]
    candidates: List[Dict[str, object]] = []
    for group_locator in candidate_locators:
        count = await group_locator.count()
        for idx in range(count):
            candidate = group_locator.nth(idx)
            try:
                is_visible = await candidate.is_visible()
            except Exception:
                is_visible = False

            raw_text = ""
            text_extractors = (
                lambda: candidate.inner_text(),
                lambda: candidate.text_content(),
            )
            for extractor in text_extractors:
                try:
                    raw_text = normalize_text((await extractor()) or "")
                except Exception:
                    raw_text = ""
                if raw_text:
                    break

            if not raw_text:
                for attr in ("aria-label", "title", "value"):
                    try:
                        raw_text = normalize_text((await candidate.get_attribute(attr)) or "")
                    except Exception:
                        raw_text = ""
                    if raw_text:
                        break

            if not raw_text:
                continue

            candidate_norm = normalize_name_for_match(raw_text)
            if not candidate_norm:
                continue

            exact_normalized = candidate_norm == target_norm
            score = 2.0 if exact_normalized else similarity_score(target_norm, candidate_norm)
            len_penalty = abs(len(candidate_norm) - len(target_norm)) * 0.002
            final_score = score - len_penalty

            candidates.append(
                {
                    "candidate": candidate,
                    "raw_text": raw_text,
                    "exact_normalized": exact_normalized,
                    "score": final_score,
                    "is_visible": is_visible,
                }
            )

    if not candidates:
        raise RuntimeError("No se encontraron candidatos para representado")

    candidates.sort(
        key=lambda item: (
            1 if item["exact_normalized"] else 0,
            item["score"],
            1 if item["is_visible"] else 0,
        ),
        reverse=True,
    )
    min_similarity = 0.90
    for item in candidates:
        is_exact_norm = bool(item["exact_normalized"])
        score = float(item["score"])
        if not is_exact_norm and score < min_similarity:
            break

        candidate = item["candidate"]
        if await click_candidate(candidate):
            return

    raise RuntimeError(
        f"{step_name}: No se encontro coincidencia suficientemente confiable para representado '{target_text}'"
    )


async def open_hacienda_service_twice(
    page: Page,
    denominacion: str,
) -> Page:
    service_button = page.get_by_role("button", name=HACIENDA_SERVICE_PATTERN)
    try:
        await service_button.first.wait_for(state="visible", timeout=12000)
    except Exception:
        pass

    if await service_button.count() == 0:
        raise RuntimeError(f"No se encuentra habilitado el servicio de {HACIENDA_SERVICE_NAME}")

    first_open_page = await click_and_get_target_page(page, service_button.first, timeout_ms=12000)

    if first_open_page is not page:
        try:
            await first_open_page.close()
        except Exception:
            pass

    second_open_page = await click_and_get_target_page(page, service_button.first, timeout_ms=12000)
    await seleccionar_denominacion(second_open_page, denominacion, "hacienda_denominacion_segunda")

    if re.search(r"inicioPerfil\.htm", second_open_page.url or "", re.I):
        await open_menu_if_needed(second_open_page)

    query_link = second_open_page.get_by_role("link", name=QUERY_LINK_PATTERN)
    if await query_link.count() == 0:
        await open_menu_if_needed(second_open_page)
        query_link = second_open_page.get_by_role("link", name=QUERY_LINK_PATTERN)
    if await query_link.count() == 0:
        raise RuntimeError(f"No se encuentra habilitado el servicio de {HACIENDA_SERVICE_NAME}")
    return second_open_page


async def procesar_consulta(
    page: Page,
    consulta_key: str,
    query_pattern: re.Pattern[str],
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> Tuple[List[Path], List[pd.DataFrame]]:
    await open_query_section(page, consulta_key, query_pattern)
    await fill_fechas_y_buscar(page, desde, hasta, consulta_key)
    return await descargar_todas_las_paginas(page, consulta_key, cuit_representante, download_dir)


async def hacienda(
    playwright: Playwright,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    headless: bool,
    download_dir: Path,
    error_logs: List[str],
    proxy: Optional[dict] = None,
) -> Dict[str, List[Path]]:
    del cuit_representado

    browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
    context = await browser.new_context(accept_downloads=True)
    page = await context.new_page()

    try:
        await ensure_login(page, cuit_representante, clave, error_logs)
        comprobantes_page = await open_comprobantes_service(page)
        await seleccionar_denominacion(
            comprobantes_page,
            denominacion,
            "comprobantes_denominacion",
        )

        hacienda_page = await open_hacienda_service_twice(comprobantes_page, denominacion)

        emisor_dir = download_dir / "por_emisor"
        receptor_dir = download_dir / "por_receptor"
        emisor_dir.mkdir(parents=True, exist_ok=True)
        receptor_dir.mkdir(parents=True, exist_ok=True)

        emisor_files, emisor_frames = await procesar_consulta(
            hacienda_page,
            "por_emisor",
            QUERY_EMISOR_PATTERN,
            cuit_representante,
            desde,
            hasta,
            emisor_dir,
        )
        receptor_files, receptor_frames = await procesar_consulta(
            hacienda_page,
            "por_receptor",
            QUERY_RECEPTOR_PATTERN,
            cuit_representante,
            desde,
            hasta,
            receptor_dir,
        )

        excel_consolidado = save_consolidated_excel(
            table_frames_by_query={
                "por_emisor": emisor_frames,
                "por_receptor": receptor_frames,
            },
            output_dir=download_dir,
            cuit_representante=cuit_representante,
            denominacion=denominacion,
            desde=desde,
            hasta=hasta,
        )

        return {
            "por_emisor": emisor_files,
            "por_receptor": receptor_files,
            "excel_consolidado": [excel_consolidado],
        }
    finally:
        await context.close()
        await browser.close()


def save_consolidated_excel(
    table_frames_by_query: Dict[str, List[pd.DataFrame]],
    output_dir: Path,
    cuit_representante: str,
    denominacion: str,
    desde: str,
    hasta: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = safe_filename(
        (
            f"HACIENDA - TABLA CONSOLIDADA - {cuit_representante} - "
            f"{denominacion or 'SIN_DENOMINACION'} - {desde} - {hasta} - {stamp}.xlsx"
        )
    )
    if not output_name.lower().endswith(".xlsx"):
        output_name += ".xlsx"
    output_path = ensure_unique_path(output_dir / output_name)

    all_frames: List[pd.DataFrame] = []
    for frames in table_frames_by_query.values():
        all_frames.extend(frames)

    with pd.ExcelWriter(output_path) as writer:
        if all_frames:
            consolidated_df = pd.concat(all_frames, ignore_index=True, sort=False)
        else:
            consolidated_df = pd.DataFrame(
                [{"consulta": "sin_datos", "mensaje": "Sin registros de comprobantes"}]
            )
        consolidated_df.to_excel(writer, index=False, sheet_name="consolidado")

        for consulta_key in ("por_emisor", "por_receptor"):
            frames = table_frames_by_query.get(consulta_key, [])
            if not frames:
                continue
            query_df = pd.concat(frames, ignore_index=True, sort=False)
            query_df.to_excel(writer, index=False, sheet_name=consulta_key[:31])

    return output_path


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _is_excel(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".xlsx"


def _is_uploadable_file(path: Path) -> bool:
    return _is_pdf(path) or _is_excel(path)


async def _subir_archivos_a_minio(
    archivos: List[Path],
    cuit_representante: str,
    denominacion: str,
    error_logs: List[str],
) -> Dict[str, str]:
    urls_por_archivo: Dict[str, str] = {}

    for archivo in archivos:
        if not archivo.exists() or not _is_uploadable_file(archivo):
            continue

        fecha_segmento = datetime.utcnow().strftime("%Y/%m/%d")
        nombre_objeto = "/".join(
            [
                "hacienda",
                safe_filename(cuit_representante),
                safe_filename(denominacion) if denominacion else "sin_denominacion",
                fecha_segmento,
                safe_filename(archivo.name),
            ]
        )
        try:
            url = await subir_archivo_a_minio(
                minio_url_interna=minio_url_interna,
                minio_url_externa=minio_url_externa,
                minio_ssl=minio_ssl,
                minio_access_key=minio_access_key_hacienda,
                minio_secret_key=minio_secret_key_hacienda,
                minio_bucket=minio_bucket_hacienda,
                minio_interno=minio_interno,
                archivo_a_subir=str(archivo),
                nombre_archivo_en_bucket=nombre_objeto,
            )
            if url:
                urls_por_archivo[str(archivo)] = url
            else:
                error_logs.append(f"No se pudo subir a MinIO: {archivo.name}")
        except Exception as exc:
            error_logs.append(f"Error subiendo a MinIO {archivo.name}: {exc}")

    return urls_por_archivo


async def descargar_hacienda(
    desde: str,
    hasta: str,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    denominacion: str,
    eliminar_descargas: bool = True,
    subir_a_minio: bool = False,
    proxy_request: bool = False,
    headless: bool = True,
) -> Tuple[List[dict], List[str], bool]:
    error_logs: List[str] = []
    success = True
    proxy = get_proxy_config() if proxy_request else None
    temp_dir = tempfile.TemporaryDirectory(prefix="hacienda_") if eliminar_descargas else None

    download_root = (
        Path(temp_dir.name)
        if temp_dir is not None
        else Path(os.getcwd()) / "descargas" / "hacienda" / safe_filename(denominacion or cuit_representado)
    )
    download_root.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as playwright:
            downloaded_by_query = await hacienda(
                playwright=playwright,
                cuit_representante=cuit_representante,
                cuit_representado=cuit_representado,
                clave=clave,
                denominacion=denominacion,
                desde=desde,
                hasta=hasta,
                headless=headless,
                download_dir=download_root,
                error_logs=error_logs,
                proxy=proxy,
            )

        all_pdfs: List[Path] = []
        for consulta_key in ("por_emisor", "por_receptor"):
            for path in downloaded_by_query.get(consulta_key, []):
                if _is_pdf(path):
                    all_pdfs.append(path)
        consolidated_excels: List[Path] = []
        for path in downloaded_by_query.get("excel_consolidado", []):
            if _is_excel(path):
                consolidated_excels.append(path)

        minio_urls: Dict[str, str] = {}
        if subir_a_minio:
            minio_urls = await _subir_archivos_a_minio(
                archivos=[*all_pdfs, *consolidated_excels],
                cuit_representante=cuit_representante,
                denominacion=denominacion,
                error_logs=error_logs,
            )

        results: List[dict] = []
        for archivo in consolidated_excels:
            result_item = {
                "consulta": "consolidado",
                "archivo": archivo.name,
            }
            if subir_a_minio:
                result_item["url_minio"] = minio_urls.get(str(archivo))
            results.append(result_item)

        for consulta_key in ("por_emisor", "por_receptor"):
            for archivo in downloaded_by_query.get(consulta_key, []):
                if not _is_pdf(archivo):
                    continue
                result_item = {
                    "consulta": consulta_key,
                    "archivo": archivo.name,
                }
                if subir_a_minio:
                    result_item["url_minio"] = minio_urls.get(str(archivo))
                results.append(result_item)

        return results, error_logs, success
    except Exception as exc:
        LOGGER.exception("bot_error_hacienda")
        error_logs.append(str(exc))
        return [], error_logs, False
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

# Main function for standalone testing
async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    
    cuit = os.getenv("HACIENDA_CUIT_REPRESENTANTE", "")
    clave = os.getenv("HACIENDA_CLAVE", "")
    denominacion = os.getenv("HACIENDA_DENOMINACION", "")
    desde = os.getenv("HACIENDA_DESDE", "01/01/2020")
    hasta = os.getenv("HACIENDA_HASTA", "31/12/2020")
    cuit_representado = os.getenv("HACIENDA_REPRESENTADO_CUIT", cuit)
    
    if not cuit or not clave:
        print("ERROR: Faltan HACIENDA_CUIT_REPRESENTANTE y/o HACIENDA_CLAVE en .env")
        return
    
    print(f"Ejecutando bot Hacienda para CUIT: {cuit}")
    print(f"Denominación: {denominacion}")
    print(f"Período: {desde} - {hasta}")
    
    results, errors, success = await descargar_hacienda(
        desde=desde,
        hasta=hasta,
        cuit_representante=cuit,
        cuit_representado=cuit_representado,
        clave=clave,
        denominacion=denominacion,
        eliminar_descargas=False,  # Keep downloads for debugging
        subir_a_minio=False,
        proxy_request=False,
        headless=False,  # Visible mode for debugging
    )
    print(f"Success: {success}")
    print(f"Results: {results}")
    if errors:
        print(f"Errors: {errors}")

if __name__ == "__main__":
    asyncio.run(main())


async def bot_hacienda(
    desde: str,
    hasta: str,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    denominacion: str,
    proxy_request: bool = False,
    **kwargs,
) -> Tuple[List[dict], List[str], bool]:
    return await descargar_hacienda(
        desde=desde,
        hasta=hasta,
        cuit_representante=cuit_representante,
        cuit_representado=cuit_representado,
        clave=clave,
        denominacion=denominacion,
        eliminar_descargas=False,
        proxy_request=proxy_request,
    )
