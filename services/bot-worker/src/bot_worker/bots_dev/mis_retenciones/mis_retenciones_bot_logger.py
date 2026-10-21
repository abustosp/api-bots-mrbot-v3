import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

from playwright.async_api import async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "mis_retenciones"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")

# --- Helper Functions (Duplicated from common version for standalone execution) ---
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "download"

def _sanitize_component(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def _format_date_component(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).replace("/", "-")

def _tipo_to_abbr(tipo: str) -> str:
    lowered = (tipo or "").strip().lower()
    if lowered.startswith("ret"):
        return "ret"
    if lowered.startswith("per"):
        return "per"
    return _sanitize_component(tipo)

def build_retenciones_filename(
    cuit_representado: str,
    desde: str,
    hasta: str,
    impuesto_label: str,
    tipo: str,
    denominacion: str,
) -> str:
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else ""
    cuit_value = cuit_digits or (cuit_representado or "")
    parts = [
        fin_cuit,
        cuit_value,
        _format_date_component(desde),
        _format_date_component(hasta),
        impuesto_label,
        _tipo_to_abbr(tipo),
        denominacion,
    ]
    cleaned_parts = [_sanitize_component(part) for part in parts if part]
    nombre = " - ".join(cleaned_parts).strip()
    if not nombre.endswith(".csv"):
        nombre = f"{nombre}.csv" if nombre else "reporte.csv"
    return nombre

def _unique(values: Sequence[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered

def _impuesto_variants(label: str, code: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", (label or "").strip())
    variants: List[str] = [normalized] if normalized else []
    text_only = normalized
    if code:
        text_only = re.sub(rf"^{re.escape(code)}\\s*-\\s*", "", normalized, flags=re.I).strip()
        if normalized and not normalized.startswith(code):
            variants.append(f"{code} - {normalized}")
    if text_only:
        variants.append(text_only)
        variants.append(f"- {text_only}")
    no_punct = re.sub(r"[./]", " ", normalized)
    if no_punct:
        variants.append(no_punct)
    if text_only:
        text_only_no_punct = re.sub(r"[./]", " ", text_only)
        variants.append(text_only_no_punct)
        variants.append(f"- {text_only_no_punct}")
    if normalized:
        variants.extend([normalized.upper(), normalized.lower()])
    if text_only:
        variants.extend([text_only.upper(), text_only.lower()])
    cleaned = [re.sub(r"\s+", " ", value).strip() for value in variants if value]
    return _unique(cleaned)

def _impuesto_patterns(label: str, code: str) -> List[re.Pattern]:
    normalized = re.sub(r"\s+", " ", (label or "").strip())
    if code:
        normalized = re.sub(rf"^{re.escape(code)}\\s*-\\s*", "", normalized, flags=re.I).strip()
    normalized = re.sub(r"[\s\-./,]+", " ", normalized).strip()
    tokens = [re.escape(token) for token in normalized.split(" ") if token]
    if not tokens:
        return []
    flex = r"[\s\-./,]+".join(tokens)
    patterns = [re.compile(flex, re.I)]
    if code:
        patterns.insert(0, re.compile(rf"\b{re.escape(code)}\b[\s\-./,]*{flex}", re.I))
    return patterns

def format_cuit(cuit: str) -> str:
    digits = re.sub(r"\D", "", cuit or "")
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    return cuit or ""

def cuit_regex(cuit: str) -> re.Pattern:
    digits = re.sub(r"\D", "", cuit or "")
    if not digits:
        return re.compile(re.escape(cuit or ""))
    if len(digits) == 11:
        pattern = rf"{digits[:2]}\\D*{digits[2:10]}\\D*{digits[10:]}"
    else:
        pattern = r"\\D*".join(list(digits))
    return re.compile(pattern)

def _text_has_cuit(text: str, formatted: str, digits_only: str) -> bool:
    if not text:
        return False
    cleaned = text.replace("[", "").replace("]", "")
    return (formatted and formatted in cleaned) or (digits_only and digits_only in cleaned)

IMPUESTOS_CONFIG = [
    {"code": "216", "label": "216 - SIRE - IVA", "tipos": ["Retencion"]},
    {"code": "217", "label": "217 - SICORE-IMPTO.A LAS GANANCIAS", "tipos": ["Retencion", "Percepcion"]},
    {"code": "219", "label": "219 - SICORE-IMPTO.S/ BS PERSONALES", "tipos": ["Retencion", "Percepcion"]},
    {"code": "353", "label": "353 - RETENCIONES CONTRIB.SEG.SOCIAL", "tipos": ["Retencion", "Percepcion"]},
    {"code": "767", "label": "767 - SICORE - RETENCIONES Y PERCEPC", "tipos": ["Retencion", "Percepcion"]},
    {"code": "787", "label": "787 - RET ART 79 LEY GCIAS INC A,ByC", "tipos": ["Retencion"]},
]

# --- Debug Capture Class ---
def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page, step: str) -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None", step)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        self.logger.info("step=%s name=%s url=%s", self.step, step, getattr(page, "url", ""))

        screenshot_path = self.screenshots_dir / f"{prefix}.png"
        html_path = self.html_dir / f"{prefix}.html"

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            self.logger.info("screenshot=%s", screenshot_path)
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            self.logger.info("html=%s", html_path)
        except Exception:
            self.logger.exception("html_failed step=%s", step)

# --- Browser Interaction Functions (Duplicated) ---

async def verify_representado_selected(
    page,
    cuit_representado: str,
    wait_timeout_ms: int = 4000,
) -> bool:
    formatted = format_cuit(cuit_representado)
    digits_only = re.sub(r"\D", "", cuit_representado or "")
    if not formatted and not digits_only:
        return False

    navbar = page.locator("#navBarMisRetenciones")
    try:
        await navbar.wait_for(state="visible", timeout=wait_timeout_ms)
    except Exception:
        pass

    cuit_locator = page.locator("#navBarMisRetenciones-relationCuil")
    label_locator = page.locator("#navBarMisRetenciones-relationLabel")
    for locator in (cuit_locator, label_locator):
        try:
            if await locator.count():
                text_content = await locator.first.text_content()
                if _text_has_cuit(text_content or "", formatted, digits_only):
                    return True
        except Exception:
            continue

    try:
        nav_text = await navbar.text_content()
        if _text_has_cuit(nav_text or "", formatted, digits_only):
            return True
    except Exception:
        pass

    return False

async def select_representado(page, cuit_representado: str) -> None:
    formatted = format_cuit(cuit_representado)
    cuit_pattern = cuit_regex(cuit_representado)
    try:
        await page.locator(".e-relation__card").first.wait_for(state="visible", timeout=20000)
    except Exception:
        pass
    attempts = [
        page.locator(".e-relation__card", has=page.get_by_text(formatted)),
        page.locator(".e-relation__card", has=page.get_by_text(cuit_pattern)),
        page.locator("div[id^=selectorRelaciones_relation]", has_text=formatted),
        page.get_by_text(formatted),
        page.get_by_text(cuit_pattern),
    ]
    for locator in attempts:
        try:
            await locator.first.click(timeout=3000)
            return
        except Exception:
            continue

async def wait_for_ready(page, timeout_ms: int = 30000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

async def wait_for_results_state(page, timeout_ms: int = 30000) -> str:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            if await page.locator("#modal-sire").is_visible(timeout=500):
                return "sire"
        except Exception:
            pass
            
        try:
            if await page.locator("#modal-confirmar").is_visible(timeout=500):
                return "error"
        except Exception:
            pass
            
        try:
            if await page.locator("#modal-sinresultados").is_visible(timeout=500):
                return "export_processing"
        except Exception:
            pass
        
        export_btn = page.locator("#btnExportarOtrosFormatos")
        try:
            if await export_btn.count() > 0 and await export_btn.is_visible(timeout=500):
                return "results"
        except Exception:
            pass
            
        try:
            table = page.locator("#tablaResultados")
            if await table.count() > 0 and await table.is_visible(timeout=500):
                return "results"
        except Exception:
            pass
            
        if re.search(r"/resultados", page.url, re.I):
            await asyncio.sleep(1)
            if await export_btn.count() > 0:
                return "results"
                
        await asyncio.sleep(0.5)
        
    return "no_results"

async def open_representado_selector(page) -> None:
    cards = page.locator(".e-relation__card")
    if await cards.count():
        return
    try:
        await page.locator("#navBarMisRetenciones").wait_for(state="visible", timeout=20000)
    except Exception:
        pass
    relation_link = page.locator("#navBarMisRetenciones-relationLabel")
    relation_cuit = page.locator("#navBarMisRetenciones-relationCuil")
    for locator in (relation_link, relation_cuit):
        if await locator.count():
            try:
                await locator.click(timeout=3000)
                try:
                    await cards.first.wait_for(state="visible", timeout=5000)
                    return
                except Exception:
                    continue
            except Exception:
                continue
    toggle = page.locator("#e-navbar-dropdown-toggle")
    if await toggle.count():
        try:
            await toggle.wait_for(state="visible", timeout=5000)
            await toggle.click()
        except Exception:
            pass
    else:
        fallback_toggle = page.locator("#navBarMisRetenciones_avatar")
        if await fallback_toggle.count():
            await fallback_toggle.click()
        else:
            try:
                await page.locator("#navBarMisRetenciones [data-bs-toggle='dropdown']").first.click()
            except Exception:
                try:
                    await page.get_by_role("button", name=re.compile("menu|usuario|perfil", re.I)).click()
                except Exception:
                    pass
    dropdown = page.locator(".dropdown-menu.show")
    try:
        await dropdown.wait_for(state="visible", timeout=5000)
    except Exception:
        pass
    candidates = [
        page.locator("#navBarMisRetenciones-dropdown-changeRelation"),
        page.get_by_role("link", name=re.compile("Seleccionar representado", re.I)),
        page.get_by_role("link", name=re.compile("Cambiar representado", re.I)),
        page.get_by_text(re.compile("Seleccionar representado", re.I)),
        page.get_by_text(re.compile("Cambiar representado", re.I)),
    ]
    for locator in candidates:
        try:
            await locator.first.click(timeout=3000)
            try:
                await cards.first.wait_for(state="visible", timeout=5000)
            except Exception:
                pass
            return
        except Exception:
            continue

async def _get_selected_impuesto_text(page) -> str:
    candidates = [
        page.locator("#selectImpuestos-assist"),
        page.locator(".multiselect-single-label-text"),
    ]
    for locator in candidates:
        try:
            if await locator.count():
                text = (await locator.first.text_content()) or ""
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""

async def is_impuesto_selected(page, label: str, code: str) -> bool:
    current = await _get_selected_impuesto_text(page)
    if not current:
        return False
    if code and code in current:
        return True
    normalized_label = re.sub(r"\s+", " ", (label or "").strip())
    return bool(normalized_label and normalized_label in current)

async def ensure_date_range(page, desde: str, hasta: str) -> bool:
    if not desde or not hasta:
        return False
    fecha_desde_input = page.locator("#fechaRetencionDesde__input")
    fecha_hasta_input = page.locator("#fechaRetencionHasta__input")
    try:
        await fecha_desde_input.wait_for(state="visible", timeout=5000)
        await fecha_hasta_input.wait_for(state="visible", timeout=5000)
    except Exception:
        return False
    try:
        current_desde = (await fecha_desde_input.input_value()) or ""
        current_hasta = (await fecha_hasta_input.input_value()) or ""
    except Exception:
        current_desde = ""
        current_hasta = ""
    if current_desde.strip() == desde and current_hasta.strip() == hasta:
        return True
    try:
        await fecha_desde_input.click()
        await fecha_desde_input.fill(desde)
        await fecha_desde_input.press("Enter")
    except Exception:
        return False
    try:
        await fecha_hasta_input.click()
        await fecha_hasta_input.fill(hasta)
        await fecha_hasta_input.press("Enter")
    except Exception:
        return False
    return True

async def ensure_inicio(
    page,
    cuit_representado: str,
    timeout_ms: int = 20000,
) -> bool:
    inicio_url = "https://mirequa-web.arca.gob.ar/inicio"
    if not re.search(r"/inicio", page.url, re.I):
        breadcrumb = page.locator("#bcMisRetenciones-li-0-routerLink")
        if await breadcrumb.count():
            try:
                await breadcrumb.first.click(timeout=5000)
            except Exception:
                await page.goto(inicio_url, timeout=timeout_ms)
        else:
            await page.goto(inicio_url, timeout=timeout_ms)

    nueva_tab = page.locator("#tabNuevaConsulta-tab")
    if await nueva_tab.count():
        try:
            selected = await nueva_tab.get_attribute("aria-selected")
            if selected != "true":
                await nueva_tab.click(timeout=5000)
        except Exception:
            pass

    representado_ok = True
    if cuit_representado:
        if not await verify_representado_selected(page, cuit_representado):
            await open_representado_selector(page)
            await select_representado(page, cuit_representado)
            representado_ok = await verify_representado_selected(page, cuit_representado)

    try:
        await page.locator("#selectImpuestos").wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        pass

    return representado_ok

async def open_impuesto_dropdown(page, label: str, code: str) -> None:
    input_locator = page.locator("#selectImpuestos")
    try:
        await input_locator.wait_for(state="visible", timeout=20000)
        await input_locator.click()
        try:
            await input_locator.press("Control+A")
            await input_locator.press("Backspace")
        except Exception:
            pass
    except Exception:
        try:
            await page.get_by_role("combobox", name=re.compile("Impuesto", re.I)).click()
        except Exception:
            pass
    dropdown = page.locator("#selectImpuestos-dropdown")
    try:
        if not await dropdown.is_visible():
            wrapper = page.locator("#selectImpuestos").locator("..")
            if await wrapper.count():
                await wrapper.click()
        if not await dropdown.is_visible():
            caret = page.locator("#selectImpuestos_caret")
            if await caret.count():
                await caret.click()
        if not await dropdown.is_visible():
            multiselect = page.locator(".multiselect-wrapper")
            if await multiselect.count():
                await multiselect.click()
        await dropdown.wait_for(state="visible", timeout=5000)
    except Exception:
        pass

async def select_impuesto(page, label: str, code: str) -> bool:
    if await is_impuesto_selected(page, label, code):
        return True
    await open_impuesto_dropdown(page, label, code)
    if code:
        prefixes = ("IMP", "SS", "ADU", "SIR")
        for prefix in prefixes:
            option_id = f"#selectImpuestos-multiselect-option-{prefix}_{code}"
            option = page.locator(option_id)
            if await option.count():
                try:
                    await option.first.click(timeout=2000)
                    return True
                except Exception:
                    pass
    options_root = page.locator("#selectImpuestos-dropdown")
    group = options_root.locator('[role="group"][aria-label="Impositivas"]')
    scopes = []
    try:
        if await group.count():
            scopes.append(group)
    except Exception:
        pass
    scopes.append(options_root)
    for scope in scopes:
        for candidate in _impuesto_variants(label, code):
            try:
                await scope.get_by_role("option", name=candidate).first.click(timeout=2000)
                return True
            except Exception:
                continue
        for pattern in _impuesto_patterns(label, code):
            try:
                await scope.get_by_role("option", name=pattern).first.click(timeout=2000)
                return True
            except Exception:
                continue
    return False

async def select_tipo(page, tipo: str) -> bool:
    if not tipo:
        return False
    container = page.locator("#tipoOperacion")
    try:
        await container.wait_for(state="visible", timeout=5000)
    except Exception:
        return False
    labels = container.locator("label")
    try:
        label_count = await labels.count()
    except Exception:
        label_count = 0
    if label_count == 0:
        return False
    if tipo.lower().startswith("ret"):
        pattern = re.compile("retenci[o\\u00f3]n", re.I)
    elif tipo.lower().startswith("per"):
        pattern = re.compile("percepci[o\\u00f3]n", re.I)
    else:
        pattern = re.compile(re.escape(tipo), re.I)
    try:
        target = labels.filter(has_text=pattern)
        if await target.count():
            await target.first.click(timeout=3000)
            return True
    except Exception:
        pass
    try:
        await container.get_by_label(pattern).first.check(timeout=3000)
        return True
    except Exception:
        pass
    return False

async def download_csv(
    page,
    impuesto_code: str,
    impuesto_label: str,
    tipo: str,
    nombre_archivo: str,
    base_dir: Path,
    error_logs: list[str],
) -> Optional[Path]:
    safe_impuesto = safe_filename(impuesto_code or impuesto_label or "impuesto")
    safe_tipo = safe_filename(tipo or "tipo")
    contexto = " ".join([value for value in [impuesto_code, impuesto_label, tipo] if value]).strip()

    consultar_clicked = False
    consultar_attempts = [
        page.locator("#btnConsultarRetenciones"),
        page.get_by_role("button", name=re.compile("Consultar", re.I)),
        page.locator("button:has-text('Consultar')"),
    ]

    for locator in consultar_attempts:
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=2000):
                await locator.first.click(timeout=5000)
                consultar_clicked = True
                break
        except Exception:
            continue

    if not consultar_clicked:
        error_logs.append(f"No se pudo ejecutar la consulta para {contexto or 'Mis Retenciones'}")
        return None

    await wait_for_ready(page)
    state = await wait_for_results_state(page)
    if state == "sire":
        try:
            await page.locator("#modal-sire_btnOK").click(timeout=3000)
        except Exception:
            pass
        error_logs.append(f"El servicio SIRE requiere gestionar la consulta manualmente ({contexto})")
        return None
    if state == "error":
        try:
            await page.locator("#modal-confirmar_btnOK").click(timeout=3000)
        except Exception:
            pass
        error_logs.append(f"Se detecto un error al consultar {contexto}")
        return None
    if state != "results":
        error_logs.append(f"No se encontraron resultados para {contexto}")
        return None

    export_clicked = False
    export_button = page.locator("#btnExportarOtrosFormatos")
    try:
        await export_button.wait_for(state="visible", timeout=8000)
        await export_button.click(timeout=5000)
        export_clicked = True
    except Exception:
        export_attempts = [
            page.get_by_role("button", name=re.compile("Exportar", re.I)),
            page.locator("button:has-text('Exportar')"),
        ]
        for locator in export_attempts:
            try:
                if await locator.count() > 0 and await locator.first.is_visible(timeout=3000):
                    await locator.first.click(timeout=5000)
                    export_clicked = True
                    break
            except Exception:
                continue

    if not export_clicked:
        error_logs.append(f"No se pudo exportar resultados para {contexto}")
        return None

    csv_clicked = False
    dropdown = page.locator(".dropdown-menu.show")
    # El portal ofrece hoy ".xls" (antes ".CSV"): se prueba ".CSV" y luego ".xls",
    # igual que el plugin de la API (runtime/portals/mis_retenciones.py).
    for extension in (".CSV", ".xls"):
        if csv_clicked:
            break
        try:
            await dropdown.wait_for(state="visible", timeout=5000)
            await dropdown.get_by_text(extension, exact=False).first.click(timeout=5000)
            csv_clicked = True
        except Exception:
            pass
        if not csv_clicked:
            try:
                fallback = page.get_by_text(extension, exact=False)
                if await fallback.count() > 0 and await fallback.first.is_visible(timeout=2000):
                    await fallback.first.click(timeout=5000)
                    csv_clicked = True
            except Exception:
                pass

    if not csv_clicked:
        error_logs.append(f"No se pudo seleccionar el formato CSV para {contexto}")
        return None

    export_all_ok = page.locator("#tablaResultadosexport_all_btnOK")
    try:
        if await export_all_ok.is_visible(timeout=4000):
            await export_all_ok.click(timeout=5000)
    except Exception:
        pass

    # Flujo actual del portal (igual que runtime/portals/mis_retenciones.py de la API):
    # confirmar el modal de exportación y bajar el archivo desde la tabla de exportaciones.
    async def _fila_exportacion() -> str:
        try:
            return (await page.locator('#tablaExportarAplicativo .ag-row[row-index="0"]').first.inner_text(timeout=1500)).strip()
        except Exception:
            return ""

    exportar_archivo_clicked = False
    ver_archivo = page.locator("#modal-exportar-consulta_btnOK, #modal-sinresultados_btnOK")
    fila_previa = await _fila_exportacion()
    try:
        await ver_archivo.first.wait_for(state="visible", timeout=10000)
        await ver_archivo.first.click(timeout=5000)
        exportar_archivo_clicked = True
    except Exception:
        pass
    if exportar_archivo_clicked:
        # La tabla puede mostrar aún la exportación anterior: se espera la fila nueva
        # para no bajar el archivo de otro impuesto.
        for _ in range(30):
            actual = await _fila_exportacion()
            if actual and actual != fila_previa:
                break
            await asyncio.sleep(1)

    if not exportar_archivo_clicked:
        error_logs.append(f"No se pudo confirmar la descarga del CSV para {contexto}")
        return None

    download_link = None
    for candidato in (
        page.locator('#tablaExportarAplicativo .ag-row[row-index="0"] a[title="Descargar Archivo"]'),
        page.locator("#tablaExportarAplicativo a[title='Descargar Archivo']"),
        page.locator("a.boton_mt[download], a[download][href*='descarga']"),
    ):
        try:
            await candidato.first.wait_for(state="visible", timeout=8000)
            download_link = candidato.first
            break
        except Exception:
            continue
    if download_link is None:
        error_logs.append(f"No se encontro el link de descarga para {contexto}")
        return None

    # La grilla puede abrir la descarga en una pestaña emergente.
    loop = asyncio.get_running_loop()
    recibida: asyncio.Future = loop.create_future()

    def _al_descargar(d):
        if not recibida.done():
            recibida.set_result(d)

    def _al_abrir(p):
        p.on("download", _al_descargar)

    page.on("download", _al_descargar)
    page.context.on("page", _al_abrir)
    download = None
    try:
        await download_link.click(timeout=5000)
        download = await asyncio.wait_for(recibida, timeout=25)
    except Exception:
        pass
    finally:
        try:
            page.remove_listener("download", _al_descargar)
            page.context.remove_listener("page", _al_abrir)
        except Exception:
            pass

    if not download:
        error_logs.append(f"No se pudo descargar el CSV para {contexto}")
        return None

    filename = _sanitize_component(nombre_archivo) or f"retenciones_{safe_impuesto}_{safe_tipo}.csv"
    sugerido = (download.suggested_filename or "").lower()
    extension = ".xlsx" if sugerido.endswith(".xlsx") else ".xls" if sugerido.endswith(".xls") else ".csv"
    if not filename.lower().endswith(extension):
        filename = f"{os.path.splitext(filename)[0]}{extension}"

    base_dir.mkdir(parents=True, exist_ok=True)
    download_path = base_dir / filename

    try:
        await download.save_as(str(download_path))
    except Exception as exc:
        error_logs.append(f"No se pudo guardar el CSV para {contexto}: {exc}")
        return None

    return download_path

# --- Main Bot Logic ---

async def bot_mis_retenciones_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str],
    denominacion: str,
    desde: str,
    hasta: str,
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True

    if not cuit_representante:
        error_logs.append("El CUIT del representante es obligatorio")
        return results, error_logs, False
    if not (denominacion or "").strip():
        error_logs.append("La denominacion del representado es obligatoria")
        return results, error_logs, False

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Mis Retenciones Logger")

    cuit_objetivo = cuit_representado or cuit_representante
    proxy = get_proxy_config() if proxy_request else None
    
    # Use debug directories
    output_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed"
            )
            page = await context.new_page()
            await debug.capture(page, "inicio")

            try:
                login_result = await login_arca(
                    page,
                    cuit_representante,
                    clave_representante,
                    error_logs=error_logs,
                )
                await debug.capture(page, "post_login")
            except Exception as e:
                debug.log(f"Login failed: {e}")
                error_logs.append(f"Login failed: {e}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            if not login_result.success:
                debug.log(f"Login returned failure: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            await asyncio.sleep(1)

            page1 = await open_arca_service(
                page,
                service_name="MIS RETENCIONES",
                service_matchers=(
                    re.compile("MIS RETENCIONES", re.I),
                    re.compile("MIS\\s+RETENCIONES|Mis\\s+Retenciones", re.I),
                ),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se pudo abrir Mis Retenciones en ARCA",
            )
            if not page1:
                debug.log("Service open failed")
                await context.close()
                await browser.close()
                return results, error_logs, False
            
            await debug.capture(page1, "servicio_abierto")

            await wait_for_ready(page1)
            await page1.goto("https://mirequa-web.arca.gob.ar/inicio")
            
            await debug.capture(page1, "mirequa_inicio")

            if not await ensure_inicio(page1, cuit_objetivo):
                error_logs.append(
                    f"No se pudo seleccionar el representado {cuit_objetivo} en Mis Retenciones"
                )
                debug.log(f"Error selecting representado {cuit_objetivo}")

            for impuesto in IMPUESTOS_CONFIG:
                code = impuesto["code"]
                label = impuesto["label"]
                tipos = impuesto["tipos"]
                for tipo in tipos:
                    debug.log(f"Procesando {label} - {tipo}")
                    if not await ensure_inicio(page1, cuit_objetivo):
                        error_logs.append(
                            f"No se pudo confirmar el representado {cuit_objetivo} antes de consultar {label}"
                        )
                        debug.log(f"Re-check represented failed for {label}")
                    
                    selected = await select_impuesto(page1, label, code)
                    if not selected:
                        error_logs.append(f"No se pudo seleccionar el impuesto {label}")
                        debug.log(f"Select impuesto failed: {label}")
                        continue
                    
                    if not await select_tipo(page1, tipo):
                        error_logs.append(f"No se pudo seleccionar el tipo {tipo} para {label}")
                        debug.log(f"Select tipo failed: {tipo}")
                        continue
                    
                    if not await ensure_date_range(page1, desde, hasta):
                        error_logs.append(
                            f"No se pudo cargar el rango de fechas {desde} - {hasta} para {label}"
                        )
                        debug.log(f"Date range failed: {desde}-{hasta}")
                        continue
                    
                    await debug.capture(page1, f"antes_consulta_{code}_{tipo}")

                    nombre_archivo = build_retenciones_filename(
                        cuit_objetivo,
                        desde,
                        hasta,
                        label,
                        tipo,
                        denominacion,
                    )
                    
                    csv_path = await download_csv(
                        page1,
                        code,
                        label,
                        tipo,
                        nombre_archivo,
                        output_dir,
                        error_logs,
                    )

                    if csv_path:
                        debug.log(f"Downloaded: {csv_path.name}")
                        results["archivos"].append(
                            {
                                "impuesto_code": code,
                                "impuesto_label": label,
                                "tipo": tipo,
                                "archivo": csv_path.name,
                                "path": str(csv_path)
                            }
                        )
                    else:
                        debug.log(f"No download for {label} - {tipo}")

                    try:
                        await page1.goto("https://mirequa-web.arca.gob.ar/inicio", timeout=30000)
                        await wait_for_ready(page1, 10000)
                        await asyncio.sleep(2)
                    except Exception as e:
                         debug.log(f"Error returning to start: {e}")

            await context.close()
            await browser.close()
            return results, error_logs, success

    except Exception as e:
        error_logs.append(f"Error global en Mis Retenciones: {e}")
        debug.log(f"Critical error: {e}")
        return results, error_logs, False

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("MIS_RETENCIONES_CUIT_REPRESENTANTE", "")
    clave = os.getenv("MIS_RETENCIONES_CLAVE_REPRESENTANTE", "")
    cuit_rep = os.getenv("MIS_RETENCIONES_CUIT_REPRESENTADO", "")
    denom = os.getenv("MIS_RETENCIONES_DENOMINACION", "Bustos y Hope SH")
    desde = os.getenv("MIS_RETENCIONES_DESDE", "01/11/2024")
    hasta = os.getenv("MIS_RETENCIONES_HASTA", "31/12/2024")
    
    if cuit and clave:
        res = await bot_mis_retenciones_logger(
            cuit, clave, cuit_rep, denom, desde, hasta
        )
        print("Archivos:", len(res[0]["archivos"]))

if __name__ == "__main__":
    asyncio.run(main())
