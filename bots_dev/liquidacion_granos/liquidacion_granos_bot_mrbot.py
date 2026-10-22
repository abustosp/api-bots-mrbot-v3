import logging
import os
import tempfile
import re
import unicodedata
from html import unescape
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import (
    Download,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
SERVICE_NAME = "LIQUIDACIÓN PRIMARIA DE GRANOS"
SERVICE_NAME_PATTERN = re.compile(
    r"LIQUIDACI[ÓO]N\s+PRIMARIA\s+DE(?:\s+GRANOS)?",
    re.I,
)
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")
BASE_DOWNLOAD_LINK_SELECTOR = (
    "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, "
    "a[onclick*='.pdf'], a[href*='.pdf'], "
    "a[onclick*='descarg'], a[href*='descarg'], a[title*='Descargar']"
)
BASE_BS4_DOWNLOAD_SELECTORS = (
    "a[onclick*='pd.jsp']",
    "a[href*='pd.jsp']",
    "a.imprimir",
    "a[onclick*='.pdf']",
    "a[href*='.pdf']",
    "a[onclick*='descarg']",
    "a[href*='descarg']",
    "a[title*='Descargar']",
)

LOGGER = logging.getLogger("liquidacion_granos_bot")


@dataclass
class ComprobanteRecord:
    row_index: int
    row_text: str
    columns: Dict[str, str]
    cuit_vendedor: str
    coe: str
    download_key: str


@dataclass
class CertificadoDescargaRecord:
    tipo_certificado: str
    cee_codigo: str
    coe: str


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


def comprobante_label(consulta: str) -> str:
    normalized = normalize_text(consulta).lower()
    if "emit" in normalized:
        return "Emitida"
    if "recib" in normalized:
        return "Recibida"
    return normalize_text(consulta) or "Comprobante"


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
        (r"certificado(?:Id)?=(\d+)", "certificadoId"),
        (r"codigo=(\d+)", "codigo"),
        (r"id=(\d+)", "id"),
        (r"\b(\d{6,})\b", "numero"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return f"{label}={match.group(1)}"
    return normalize_text(value)[:160]


def extract_pd_and_report_paths(raw_value: str) -> Tuple[str, str]:
    value = unescape(raw_value or "")
    if not value:
        return "", ""

    pd_match = re.search(r"pd\.jsp\?[^'\";\s]+", value, flags=re.I)
    pd_path = pd_match.group(0).strip() if pd_match else ""

    report_match = re.search(r"generarReporte(?:DebitoCredito)?\.do\?[^'\";\s]+", value, flags=re.I)
    report_path = report_match.group(0).strip() if report_match else ""

    if pd_path:
        pd_query = urlparse(f"https://dummy/{pd_path.lstrip('/')}").query
        action_values = parse_qs(pd_query).get("action", [])
        if action_values:
            report_path = unescape(action_values[0]).strip()

    return pd_path, report_path


def extract_http_saved_id_from_html(html_content: str) -> str:
    if not html_content:
        return ""
    patterns = (
        r"name\s*=\s*['\"]HTTP_SAVED_ID['\"][^>]*value\s*=\s*['\"]([^'\"]+)['\"]",
        r"value\s*=\s*['\"]([^'\"]+)['\"][^>]*name\s*=\s*['\"]HTTP_SAVED_ID['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, html_content, flags=re.I)
        if match:
            return normalize_text(match.group(1))
    return ""


def decode_response_text(body: bytes, content_type: str) -> str:
    if not body:
        return ""

    encodings: List[str] = []
    charset_match = re.search(r"charset\s*=\s*([a-zA-Z0-9._-]+)", content_type or "", flags=re.I)
    if charset_match:
        encodings.append(charset_match.group(1))
    encodings.extend(("utf-8", "latin-1"))

    tried: set[str] = set()
    for encoding in encodings:
        normalized = normalize_text(encoding).lower()
        if not normalized or normalized in tried:
            continue
        tried.add(normalized)
        try:
            return body.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("latin-1", errors="ignore")


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
    cuit_vendedor = only_digits(
        find_column_value(
            columns,
            (
                "cuit vendedor",
                "vendedor cuit",
                "cuit del vendedor",
                "cuit vendedor primario",
            ),
        )
    )
    if len(cuit_vendedor) != 11:
        cuit_vendedor = ""

    coe = only_digits(find_column_value(columns, ("coe", "nro coe", "número coe", "numero coe")))
    if len(coe) < 12:
        coe = ""

    digits = re.findall(r"\b\d{11,14}\b", row_text)
    if not coe:
        for token in digits:
            if len(token) >= 12:
                coe = token
                break
    if not cuit_vendedor:
        for token in digits:
            if len(token) == 11:
                cuit_vendedor = token
                break

    if not cuit_vendedor:
        cuit_vendedor = "SIN_CUIT_VENDEDOR"
    if not coe:
        coe = "SIN_COE"
    return cuit_vendedor, coe


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


def parse_table_records(
    html_content: str,
    bs4_download_selectors: Sequence[str] = BASE_BS4_DOWNLOAD_SELECTORS,
) -> Tuple[List[str], List[ComprobanteRecord]]:
    soup = BeautifulSoup(html_content, "html.parser")
    candidate_by_table: Dict[int, Dict[str, object]] = {}
    headers_cache: Dict[Tuple[int, int], List[str]] = {}

    for row_idx, tr in enumerate(soup.find_all("tr")):
        link = None
        for selector in bs4_download_selectors:
            link = tr.select_one(selector)
            if link is not None:
                break
        if link is None:
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
        cuit_vendedor, coe = infer_row_identifiers(mapped, row_text)
        onclick_or_href = (link.get("onclick") or link.get("href") or "").strip()
        record = ComprobanteRecord(
            row_index=row_idx,
            row_text=row_text,
            columns=mapped,
            cuit_vendedor=cuit_vendedor,
            coe=coe,
            download_key=extract_download_key(onclick_or_href),
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
    df["cuit_vendedor_detectado"] = [record.cuit_vendedor for record in records]
    df["coe_detectado"] = [record.coe for record in records]
    return df


def parse_certificados_descarga_records(html_content: str) -> List[CertificadoDescargaRecord]:
    soup = BeautifulSoup(html_content, "html.parser")
    seen: set[Tuple[str, str, str]] = set()
    records: List[CertificadoDescargaRecord] = []

    for link in soup.select("a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]"):
        tipo_certificado = normalize_text((link.get("data-tipocertificado") or "")).upper()
        cee_codigo = only_digits(link.get("data-liqcodigo") or "")
        coe = only_digits(link.get("data-coe") or "")
        if not tipo_certificado or not cee_codigo or not coe:
            continue
        key = (tipo_certificado, cee_codigo, coe)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            CertificadoDescargaRecord(
                tipo_certificado=tipo_certificado,
                cee_codigo=cee_codigo,
                coe=coe,
            )
        )
    return records


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


async def capture_table_and_save(
    page: Page,
    tipo: str,
    consulta: str,
    download_dir: Path,
    bs4_download_selectors: Sequence[str] = BASE_BS4_DOWNLOAD_SELECTORS,
) -> Tuple[List[ComprobanteRecord], Path]:
    html_content = await page.content()
    headers, records = parse_table_records(
        html_content,
        bs4_download_selectors=bs4_download_selectors,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = safe_filename(f"tabla_{tipo}_{consulta}_{stamp}")

    download_dir.mkdir(parents=True, exist_ok=True)
    if records:
        output_path = download_dir / f"{output_base}.xlsx"
        df = records_to_dataframe(headers, records)
        df.to_excel(output_path, index=False)
    else:
        output_path = download_dir / f"{output_base}.txt"
        output_path.write_text("Sin registros de comprobantes", encoding="utf-8")
    return records, output_path


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
        raise RuntimeError(f"No se encontró {role} con patrón {pattern.pattern} en {step_name}")
    await locator.first.click(timeout=timeout)
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass


async def fill_fechas(page: Page, desde: str, hasta: str, step_name: str) -> None:
    await page.locator("input[name='fechaStr']").click()
    await page.locator("input[name='fechaStr']").fill(desde)
    await page.locator("input[name='fechaHastaStr']").click()
    await page.locator("input[name='fechaHastaStr']").fill(hasta)

    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consultar\s+Por\s+Criterio", re.I),
        f"{step_name}_consultar",
    )


async def download_from_link(
    page: Page,
    link_locator,
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
        return None
    except PlaywrightError as exc:
        LOGGER.warning("descarga error (%s): %s", label, exc)
        return None


async def _get_http_saved_id(page: Page) -> str:
    selectors = (
        "form[name='frmGo'] input[name='HTTP_SAVED_ID']",
        "form[name='form'] input[name='HTTP_SAVED_ID']",
        "input[name='HTTP_SAVED_ID']",
    )
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() == 0:
            continue
        value = (await locator.first.get_attribute("value")) or ""
        value = value.strip()
        if value:
            return value
    return ""


def _is_pdf_response(content_type: str, body: bytes) -> bool:
    return "pdf" in (content_type or "").lower() or body.startswith(b"%PDF")


async def _request_with_optional_form(
    page: Page,
    url: str,
    headers: Dict[str, str],
    form_payload: Dict[str, str],
    timeout_ms: int,
):
    if form_payload:
        return await page.request.post(
            url,
            headers=headers,
            form=form_payload,
            timeout=timeout_ms,
        )
    return await page.request.post(
        url,
        headers=headers,
        timeout=timeout_ms,
    )


async def download_comprobante_direct(
    page: Page,
    link_raw_value: str,
    target_path: Path,
    label: str,
    timeout_ms: int = 30000,
) -> Optional[Path]:
    base_url = page.url or "https://serviciosjava2.afip.gob.ar/lpg/jsp/"
    pd_path, report_path = extract_pd_and_report_paths(link_raw_value)
    if not report_path:
        LOGGER.warning("descarga directa sin report url (%s)", label)
        return None

    pd_url = urljoin(base_url, pd_path) if pd_path else ""
    report_url = urljoin(base_url, report_path)
    parsed_base = urlparse(base_url)

    base_headers = {"Referer": base_url}
    if parsed_base.scheme and parsed_base.netloc:
        base_headers["Origin"] = f"{parsed_base.scheme}://{parsed_base.netloc}"

    http_saved_id = ""
    if pd_url:
        try:
            pre_response = await page.request.get(
                pd_url,
                headers=base_headers,
                timeout=timeout_ms,
            )
            if pre_response.status >= 400:
                LOGGER.warning(
                    "descarga directa pd status error (%s): %s - %s",
                    label,
                    pre_response.status,
                    pd_url,
                )
                return None
            pre_body = await pre_response.body()
            pre_content_type = (pre_response.headers or {}).get("content-type", "")
            pre_html = decode_response_text(pre_body, pre_content_type)
            http_saved_id = extract_http_saved_id_from_html(pre_html)
        except PlaywrightTimeoutError:
            LOGGER.warning("descarga directa pd timeout (%s): %s", label, pd_url)
            return None
        except PlaywrightError as exc:
            LOGGER.warning("descarga directa pd error (%s): %s", label, exc)
            return None

    if not http_saved_id:
        http_saved_id = await _get_http_saved_id(page)

    post_headers = dict(base_headers)
    post_headers["Referer"] = pd_url or base_url

    form_payload: Dict[str, str] = {}
    if http_saved_id:
        form_payload["HTTP_SAVED_ID"] = http_saved_id

    try:
        response = await _request_with_optional_form(
            page=page,
            url=report_url,
            headers=post_headers,
            form_payload=form_payload,
            timeout_ms=timeout_ms,
        )
        content_type = (response.headers or {}).get("content-type", "")
        body = await response.body()
        if response.status >= 400:
            LOGGER.warning("descarga directa status error (%s): %s - %s", label, response.status, report_url)
            return None
        if not _is_pdf_response(content_type, body):
            LOGGER.warning(
                "descarga directa no pdf (%s): status=%s content-type=%s url=%s",
                label,
                response.status,
                content_type,
                report_url,
            )
            return None

        final_path = ensure_unique_path(target_path)
        final_path.write_bytes(body)
        return final_path
    except PlaywrightTimeoutError:
        LOGGER.warning("descarga directa timeout (%s): %s", label, report_url)
        return None
    except PlaywrightError as exc:
        LOGGER.warning("descarga directa error (%s): %s", label, exc)
        return None


async def download_certificado_direct(
    page: Page,
    certificado: CertificadoDescargaRecord,
    target_path: Path,
    label: str,
    timeout_ms: int = 30000,
) -> Optional[Path]:
    base_url = page.url or "https://serviciosjava2.afip.gob.ar/lpg/jsp/consultarCertificadosEmitidos.do"
    query = urlencode(
        {
            "tipo": certificado.tipo_certificado,
            "ceeCodigo": certificado.cee_codigo,
            "coe": certificado.coe,
        }
    )
    report_url = urljoin(base_url, f"generarReporteCEG.do?{query}")
    http_saved_id = await _get_http_saved_id(page)

    headers = {"Referer": base_url}
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

    form_payload: Dict[str, str] = {}
    if http_saved_id:
        form_payload["HTTP_SAVED_ID"] = http_saved_id

    try:
        response = await _request_with_optional_form(
            page=page,
            url=report_url,
            headers=headers,
            form_payload=form_payload,
            timeout_ms=timeout_ms,
        )
        content_type = (response.headers or {}).get("content-type", "")
        body = await response.body()
        if response.status >= 400:
            LOGGER.warning("descarga directa status error (%s): %s - %s", label, response.status, report_url)
            return None
        if not _is_pdf_response(content_type, body):
            LOGGER.warning(
                "descarga directa no pdf (%s): status=%s content-type=%s url=%s",
                label,
                response.status,
                content_type,
                report_url,
            )
            return None

        final_path = ensure_unique_path(target_path)
        final_path.write_bytes(body)
        return final_path
    except PlaywrightTimeoutError:
        LOGGER.warning("descarga directa timeout (%s): %s", label, report_url)
        return None
    except PlaywrightError as exc:
        LOGGER.warning("descarga directa error (%s): %s", label, exc)
        return None


async def descargar_comprobantes(
    page: Page,
    tipo: str,
    consulta: str,
    cuit_representante: str,
    download_dir: Path,
    download_link_selector: str = BASE_DOWNLOAD_LINK_SELECTOR,
    bs4_download_selectors: Sequence[str] = BASE_BS4_DOWNLOAD_SELECTORS,
) -> List[dict]:
    records, tabla_path = await capture_table_and_save(
        page,
        tipo,
        consulta,
        download_dir,
        bs4_download_selectors=bs4_download_selectors,
    )
    records_by_key = {record.download_key: record for record in records if record.download_key}

    rows = page.locator("tr").filter(
        has=page.locator(download_link_selector),
    )
    row_count = await rows.count()

    descargados: List[dict] = []
    descargados.append(
        {
            "categoria": "tabla",
            "tipo": tipo,
            "consulta": consulta,
            "archivo": tabla_path.name,
            "local_path": str(tabla_path),
            "cuit_vendedor": None,
            "coe": None,
        }
    )

    if row_count == 0:
        return descargados

    processed_keys: set[str] = set()

    for idx in range(row_count):
        row = rows.nth(idx)
        if await row.locator("table").count() > 0:
            continue

        link = row.locator(download_link_selector).first
        onclick = ""
        href = ""
        try:
            onclick = (await link.get_attribute("onclick")) or ""
            href = (await link.get_attribute("href")) or ""
        except Exception:
            pass

        key = extract_download_key(onclick or href)
        if key and key in processed_keys:
            continue
        if key:
            processed_keys.add(key)

        tds = row.locator("xpath=./td")
        td_count = await tds.count()
        cells: List[str] = []
        for td_idx in range(td_count):
            text = normalize_text(await tds.nth(td_idx).inner_text())
            cells.append(text)
        row_text = normalize_text(" ".join(cells))

        record = records_by_key.get(key)
        if record is not None:
            cuit_vendedor = only_digits(record.cuit_vendedor) or "SIN_CUIT_VENDEDOR"
            coe = only_digits(record.coe) or "SIN_COE"
        else:
            cuit_vendedor, coe = infer_row_identifiers({}, row_text)
            cuit_vendedor = only_digits(cuit_vendedor) or "SIN_CUIT_VENDEDOR"
            coe = only_digits(coe) or "SIN_COE"

        comprobante_tipo = comprobante_label(consulta)
        file_name = safe_filename(
            f"{tipo} - {comprobante_tipo} - {cuit_representante} - {cuit_vendedor} - {coe}.pdf"
        )
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

        target = download_dir / file_name
        saved_path = None
        raw_for_direct = onclick or href
        if raw_for_direct:
            saved_path = await download_comprobante_direct(
                page,
                raw_for_direct,
                target,
                label=f"{tipo}_{consulta}_{idx + 1}_{key}_direct",
            )
        if saved_path is None:
            saved_path = await download_from_link(
                page,
                link,
                target,
                label=f"{tipo}_{consulta}_{idx + 1}_{key}_fallback_click",
            )
        if saved_path is None:
            continue

        descargados.append(
            {
                "categoria": "comprobante",
                "tipo": tipo,
                "consulta": consulta,
                "archivo": saved_path.name,
                "local_path": str(saved_path),
                "cuit_vendedor": cuit_vendedor,
                "coe": coe,
            }
        )

    return descargados


async def open_service(page: Page) -> Page:
    service_page = await open_arca_service(
        page,
        service_name=SERVICE_NAME,
        service_matchers=(SERVICE_NAME_PATTERN,),
        service_unavailable_message=f"No se encuentra habilitado el servicio de {SERVICE_NAME}",
    )
    if service_page is None:
        raise RuntimeError(f"No se encuentra habilitado el servicio de {SERVICE_NAME}")

    try:
        await service_page.wait_for_load_state("domcontentloaded", timeout=12000)
    except PlaywrightTimeoutError:
        pass
    try:
        await service_page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass

    if not re.search(r"lpg|liquidaci", service_page.url or "", flags=re.I):
        menu_principal = service_page.get_by_role("button", name=re.compile(r"Men[úu]\s+principal", re.I))
        if await menu_principal.count() == 0:
            raise RuntimeError(f"No se encuentra habilitado el servicio de {SERVICE_NAME}")

    return service_page


async def seleccionar_denominacion(page: Page, denominacion: str) -> None:
    target_text = normalize_text(denominacion)
    if not target_text:
        return
    target_norm = normalize_name_for_match(target_text)
    service_menu_locator = page.get_by_role(
        "button",
        name=re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I),
    )
    if await service_menu_locator.count() > 0:
        # Ya estamos dentro del servicio para el representado activo.
        return

    async def click_first_visible(locator) -> bool:
        count = await locator.count()
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=8000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                return True
            except Exception:
                continue
        return False

    if await click_first_visible(page.get_by_role("button", name=target_text, exact=True)):
        return
    insensitive_pattern = re.compile(rf"^{re.escape(target_text)}$", re.I)
    if await click_first_visible(page.get_by_role("button", name=insensitive_pattern)):
        return

    buttons = page.get_by_role("button")
    button_count = await buttons.count()
    candidates: List[Dict[str, object]] = []

    for idx in range(button_count):
        button = buttons.nth(idx)
        try:
            if not await button.is_visible():
                continue
        except Exception:
            continue

        button_text = normalize_text(await button.inner_text() or await button.get_attribute("value") or "")
        if not button_text:
            continue

        button_norm = normalize_name_for_match(button_text)
        exact_normalized = button_norm == target_norm
        score = 2.0 if exact_normalized else similarity_score(target_norm, button_norm)
        len_penalty = abs(len(button_norm) - len(target_norm)) * 0.002
        final_score = score - len_penalty
        candidates.append(
            {
                "idx": idx,
                "raw_text": button_text,
                "exact_normalized": exact_normalized,
                "score": final_score,
            }
        )

    if not candidates:
        if await service_menu_locator.count() > 0:
            return
        raise RuntimeError(
            f"Representado no encontrado: no se detectaron opciones visibles para la denominación '{target_text}'."
        )

    candidates.sort(
        key=lambda item: (
            1 if item["exact_normalized"] else 0,
            item["score"],
        ),
        reverse=True,
    )
    min_similarity = 0.90
    for item in candidates:
        idx = int(item["idx"])
        is_exact_norm = bool(item["exact_normalized"])
        score = float(item["score"])
        if not is_exact_norm and score < min_similarity:
            break
        button = buttons.nth(idx)
        try:
            await button.click(timeout=8000)
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except PlaywrightTimeoutError:
                pass
            return
        except Exception:
            continue

    top_candidates = ", ".join(
        f"{str(item['raw_text'])} ({float(item['score']):.3f})" for item in candidates[:3]
    )
    message = (
        f"Representado no encontrado para la denominación '{target_text}'. "
        f"No hubo coincidencia exacta ni fuzzy confiable (umbral {min_similarity:.2f})."
    )
    if top_candidates:
        message = f"{message} Mejores candidatos: {top_candidates}."
    if await service_menu_locator.count() > 0:
        return
    raise RuntimeError(message)


async def volver_menu_principal(page: Page, step_name: str = "menu_principal") -> None:
    await click_by_role_name(page, "button", re.compile(r"Men[úu]\s+principal", re.I), step_name)


async def try_volver_menu_principal(page: Page, step_name: str) -> bool:
    menu_principal = page.get_by_role("button", name=re.compile(r"Men[úu]\s+principal", re.I))
    if await menu_principal.count() == 0:
        LOGGER.info("menu_principal_no_visible step=%s url=%s", step_name, page.url)
        return False
    await menu_principal.first.click(timeout=8000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    return True


async def click_menu_option_flexible(
    page: Page,
    pattern: re.Pattern[str],
    step_name: str,
    timeout: int = 10000,
) -> None:
    last_error: Optional[Exception] = None
    candidates = (
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
        page.locator("button, a, [role='button'], .usarManito").filter(has_text=pattern),
    )
    for locator in candidates:
        count = await locator.count()
        if count == 0:
            continue
        try:
            await locator.first.click(timeout=timeout)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            return
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise RuntimeError(
            f"No se pudo hacer click en opción de menú con patrón {pattern.pattern} en {step_name}"
        ) from last_error
    raise RuntimeError(f"No se encontró opción de menú con patrón {pattern.pattern} en {step_name}")


async def preparar_menu_para_certificados(page: Page) -> None:
    certificados_pattern = re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I)
    if await page.get_by_role("button", name=certificados_pattern).count() > 0:
        return
    if await page.get_by_role("link", name=certificados_pattern).count() > 0:
        return
    if await page.locator("button, a, [role='button'], .usarManito").filter(has_text=certificados_pattern).count() > 0:
        return

    menu_principal = page.get_by_role("button", name=re.compile(r"Men[úu]\s+principal", re.I))
    if await menu_principal.count() == 0:
        LOGGER.info("menu_principal_no_visible_para_certificados")
        return

    await menu_principal.first.click(timeout=8000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass


async def fill_fechas_certificados_deposito(
    page: Page,
    desde: str,
    hasta: str,
    tipo_consulta_valor: str,
) -> None:
    tipo_consulta = page.locator("#tipoConsulta")
    if await tipo_consulta.count() == 0:
        raise RuntimeError("No se encontró el selector de tipo de consulta para Certificados de Depósito")
    await tipo_consulta.first.select_option(tipo_consulta_valor)

    fecha_desde = page.locator("#fechaDesde")
    fecha_hasta = page.locator("#fechaHasta")
    if await fecha_desde.count() == 0 or await fecha_hasta.count() == 0:
        raise RuntimeError("No se encontraron los campos de fecha para Certificados de Depósito")

    await fecha_desde.first.click()
    await fecha_desde.first.fill(desde)
    await fecha_hasta.first.click()
    await fecha_hasta.first.fill(hasta)

    boton_consultar = page.get_by_role("button", name=re.compile(r"Consultar", re.I))
    if await boton_consultar.count() > 0:
        await boton_consultar.first.click()
    else:
        input_consultar = page.locator("input[type='button'][value*='Consultar'], input[type='submit'][value*='Consultar']")
        if await input_consultar.count() == 0:
            raise RuntimeError("No se encontró el botón Consultar para Certificados de Depósito")
        await input_consultar.first.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass


async def obtener_tipos_consulta_certificados(page: Page) -> List[Tuple[str, str]]:
    tipo_consulta = page.locator("#tipoConsulta")
    if await tipo_consulta.count() == 0:
        raise RuntimeError("No se encontró el selector de tipo de consulta para Certificados de Depósito")

    options = await tipo_consulta.first.evaluate(
        """el => Array.from(el.options).map(o => ({
            value: (o.value || '').trim(),
            text: (o.textContent || '').trim()
        }))"""
    )

    tipos: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for option in options:
        value = normalize_text(str(option.get("value", "")))
        text = normalize_text(str(option.get("text", "")))
        if not value or value == "-1":
            continue
        if value in seen:
            continue
        seen.add(value)
        tipos.append((value, text or value))

    if not tipos:
        raise RuntimeError("No se encontraron opciones válidas de Tipo De Consulta")
    return tipos


async def descargar_certificados_deposito(
    page: Page,
    cuit_representante: str,
    tipo_consulta: str,
    download_dir: Path,
) -> List[dict]:
    _, tabla_path = await capture_table_and_save(
        page,
        "CD",
        f"deposito_{tipo_consulta}",
        download_dir,
        bs4_download_selectors=("a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]",),
    )
    descargados: List[dict] = [
        {
            "categoria": "tabla",
            "tipo": "CD",
            "consulta": tipo_consulta,
            "archivo": tabla_path.name,
            "local_path": str(tabla_path),
            "cuit_vendedor": None,
            "coe": None,
        }
    ]

    html_content = await page.content()
    certificados = parse_certificados_descarga_records(html_content)
    if not certificados:
        return descargados

    for idx, certificado in enumerate(certificados, start=1):
        file_name = safe_filename(
            f"CD - {tipo_consulta} - deposito - {cuit_representante} - SIN_CUIT_VENDEDOR - {certificado.coe}.pdf"
        )
        target = download_dir / file_name
        label = (
            f"CD_{tipo_consulta}_direct_{idx}_{certificado.tipo_certificado}_{certificado.cee_codigo}_{certificado.coe}"
        )
        saved_path = await download_certificado_direct(
            page,
            certificado,
            target,
            label=label,
        )

        if saved_path is None:
            fallback_link = page.locator(
                "a.imprimir"
                f"[data-tipocertificado='{certificado.tipo_certificado}']"
                f"[data-liqcodigo='{certificado.cee_codigo}']"
                f"[data-coe='{certificado.coe}']"
            )
            if await fallback_link.count() > 0:
                saved_path = await download_from_link(
                    page,
                    fallback_link.first,
                    target,
                    label=f"{label}_fallback_click",
                )

        if saved_path is None:
            continue

        descargados.append(
            {
                "categoria": "certificado_deposito",
                "tipo": "CD",
                "consulta": tipo_consulta,
                "archivo": saved_path.name,
                "local_path": str(saved_path),
                "cuit_vendedor": "SIN_CUIT_VENDEDOR",
                "coe": certificado.coe,
            }
        )

    return descargados


async def procesar_certificados_deposito(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> List[dict]:
    await preparar_menu_para_certificados(page)
    await click_menu_option_flexible(
        page,
        re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I),
        "certificacion_electronica_menu",
    )
    await click_menu_option_flexible(
        page,
        re.compile(r"Consulta\s+de\s+Certificados\s+de", re.I),
        "consulta_certificados_menu",
    )

    tipos_consulta = await obtener_tipos_consulta_certificados(page)
    descargados: List[dict] = []
    for tipo_valor, tipo_texto in tipos_consulta:
        LOGGER.info("tipo_consulta_inicio value=%s text=%s", tipo_valor, tipo_texto)
        await fill_fechas_certificados_deposito(page, desde, hasta, tipo_valor)
        descargados.extend(
            await descargar_certificados_deposito(
                page=page,
                cuit_representante=cuit_representante,
                tipo_consulta=tipo_valor,
                download_dir=download_dir,
            )
        )
        LOGGER.info("tipo_consulta_fin value=%s text=%s", tipo_valor, tipo_texto)
    return descargados


async def procesar_lpg_emitidas(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> List[dict]:
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I),
        "lpg_menu",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones", re.I),
        "lpg_consulta_emitidas",
    )
    await fill_fechas(page, desde, hasta, "lpg_emitidas")
    return await descargar_comprobantes(page, "LPG", "emitidas", cuit_representante, download_dir)


async def procesar_lpg_recibidas(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> List[dict]:
    await volver_menu_principal(page, "menu_principal_lpg_1")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I),
        "lpg_menu_recibidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+Liquidaciones\s+Recibidas", re.I),
        "lpg_consulta_recibidas",
    )
    await fill_fechas(page, desde, hasta, "lpg_recibidas")
    return await descargar_comprobantes(page, "LPG", "recibidas", cuit_representante, download_dir)


async def procesar_lsg_emitidas(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> List[dict]:
    await volver_menu_principal(page, "menu_principal_lsg_1")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I),
        "lsg_menu_emitidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones\s+Emitidas", re.I),
        "lsg_consulta_emitidas",
    )
    await fill_fechas(page, desde, hasta, "lsg_emitidas")
    return await descargar_comprobantes(page, "LSG", "emitidas", cuit_representante, download_dir)


async def procesar_lsg_recibidas(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
) -> List[dict]:
    await volver_menu_principal(page, "menu_principal_lsg_2")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I),
        "lsg_menu_recibidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones\s+Recibidas", re.I),
        "lsg_consulta_recibidas",
    )
    await fill_fechas(page, desde, hasta, "lsg_recibidas")
    return await descargar_comprobantes(page, "LSG", "recibidas", cuit_representante, download_dir)


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


async def liquidacion_granos(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    headless: bool,
    download_dir: Path,
    error_logs: List[str],
    proxy: Optional[dict] = None,
) -> List[dict]:
    browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
    context = await browser.new_context(accept_downloads=True)
    page = await context.new_page()

    try:
        await ensure_login(page, cuit_representante, clave, error_logs)
        service_page = await open_service(page)
        await seleccionar_denominacion(service_page, denominacion)

        resultados: List[dict] = []
        resultados.extend(await procesar_lpg_emitidas(service_page, cuit_representante, desde, hasta, download_dir))
        resultados.extend(await procesar_lpg_recibidas(service_page, cuit_representante, desde, hasta, download_dir))
        resultados.extend(await procesar_lsg_emitidas(service_page, cuit_representante, desde, hasta, download_dir))
        resultados.extend(await procesar_lsg_recibidas(service_page, cuit_representante, desde, hasta, download_dir))
        resultados.extend(
            await procesar_certificados_deposito(
                service_page,
                cuit_representante,
                desde,
                hasta,
                download_dir,
            )
        )
        await try_volver_menu_principal(service_page, "menu_principal_final")

        return resultados
    finally:
        await context.close()
        await browser.close()


async def descargar_liquidacion_granos(
    desde: str,
    hasta: str,
    cuit_representante: str,
    clave: str,
    denominacion: str,
    cuit_representado: Optional[str] = None,
    eliminar_descargas: bool = True,

    proxy_request: bool = False,
    headless: bool = True,
) -> Tuple[List[dict], List[str], bool]:
    del cuit_representado

    error_logs: List[str] = []
    proxy = get_proxy_config() if proxy_request else None
    temp_dir = tempfile.TemporaryDirectory(prefix='liquidacion_granos_') if eliminar_descargas else None

    download_root = (
        Path(temp_dir.name)
        if temp_dir is not None
        else Path(os.getcwd()) / "descargas" / "liquidacion_granos" / safe_filename(denominacion or cuit_representante)
    )
    download_root.mkdir(parents=True, exist_ok=True)

    try:
        async with limit_browser(), async_playwright() as playwright:
            resultados = await liquidacion_granos(
                playwright=playwright,
                cuit_representante=cuit_representante,
                clave=clave,
                denominacion=denominacion,
                desde=desde,
                hasta=hasta,
                headless=headless,
                download_dir=download_root,
                error_logs=error_logs,
                proxy=proxy,
            )

        respuesta: List[dict] = []
        for item in resultados:
            clean_item = {
                "categoria": item.get("categoria"),
                "tipo": item.get("tipo"),
                "consulta": item.get("consulta"),
                "archivo": item.get("archivo"),
                "cuit_vendedor": item.get("cuit_vendedor"),
                "coe": item.get("coe"),
            }
            respuesta.append(clean_item)

        return respuesta, error_logs, True
    except Exception as exc:
        LOGGER.exception("bot_error_liquidacion_granos")
        error_logs.append(str(exc))
        return [], error_logs, False
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
