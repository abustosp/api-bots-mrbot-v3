import asyncio
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

from minio.error import S3Error
from playwright.async_api import async_playwright

from shared.arca_login import login_arca, open_arca_service
from app.utils.bucket import subir_archivo_a_minio
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

# Constants and Helpers
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

minio_url_interna = os.getenv("MINIO_URL_INTERNA")
minio_url_externa = os.getenv("MINIO_URL_EXTERNA")
minio_ssl = os.getenv("MINIO_SSL", "False").lower() == "true"
minio_interno = os.getenv("MINIO_INTERNO", "False").lower() == "true"

minio_bucket_mis_retenciones = os.getenv("MINIO_BUCKET_MISRETENCIONES", "misretenciones")
minio_access_key_mis_retenciones = os.getenv("MINIO_MRBOT_ACCESS_KEY")
minio_secret_key_mis_retenciones = os.getenv("MINIO_MRBOT_SECRET_KEY")

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
        text_only = re.sub(rf"^{re.escape(code)}\s*-\s*", "", normalized, flags=re.I).strip()
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
        normalized = re.sub(rf"^{re.escape(code)}\s*-\s*", "", normalized, flags=re.I).strip()
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
        pattern = rf"{digits[:2]}\D*{digits[2:10]}\D*{digits[10:]}"
    else:
        pattern = r"\D*".join(list(digits))
    return re.compile(pattern)

IMPUESTOS_CONFIG = [
    {"code": "216", "label": "216 - SIRE - IVA", "tipos": ["Retencion"]},
    {"code": "217", "label": "217 - SICORE-IMPTO.A LAS GANANCIAS", "tipos": ["Retencion", "Percepcion"]},
    {"code": "219", "label": "219 - SICORE-IMPTO.S/ BS PERSONALES", "tipos": ["Retencion", "Percepcion"]},
    {"code": "353", "label": "353 - RETENCIONES CONTRIB.SEG.SOCIAL", "tipos": ["Retencion", "Percepcion"]},
    {"code": "767", "label": "767 - SICORE - RETENCIONES Y PERCEPC", "tipos": ["Retencion", "Percepcion"]},
    {"code": "787", "label": "787 - RET ART 79 LEY GCIAS INC A,ByC", "tipos": ["Retencion"]},
]

def _text_has_cuit(text: str, formatted: str, digits_only: str) -> bool:
    if not text:
        return False
    cleaned = text.replace("[", "").replace("]", "")
    return (formatted and formatted in cleaned) or (digits_only and digits_only in cleaned)

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

async def wait_for_ready(page, timeout_ms: int = 18000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 9000))
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
            await asyncio.sleep(0.35)
            if await export_btn.count() > 0:
                return "results"
                
        await asyncio.sleep(0.2)
        
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

def _fecha_mostrada_ok(mostrada: str, valor: str) -> bool:
    """El datepicker de ARCA reescribe ``dd/mm/aaaa`` como ``mm/dd/aaaa``."""
    mostrada = (mostrada or "").strip()
    partes = valor.split("/")
    if len(partes) != 3:
        return mostrada == valor
    return mostrada in {valor, f"{partes[1]}/{partes[0]}/{partes[2]}"}

async def _set_datepicker_value(page, input_locator, value: str, label: str) -> bool:
    """Set a datepicker value with multiple strategies: fill, type, and JS fallback.
    The input may be hidden (Angular controlled), so we don't require visibility."""
    if not value:
        return False
    for attempt in range(3):
        try:
            await input_locator.wait_for(state="attached", timeout=5000)
        except Exception:
            await asyncio.sleep(0.5)
            continue
        # Try to focus/click (may fail if hidden, that's ok)
        try:
            await input_locator.click(timeout=2000, force=True)
        except Exception:
            pass
        # Strategy 1: fill + Enter
        try:
            await input_locator.fill(value, force=True)
            await input_locator.press("Enter")
            await asyncio.sleep(0.3)
            current = (await input_locator.input_value()) or ""
            if _fecha_mostrada_ok(current, value):
                return True
        except Exception:
            pass
        # Strategy 2: type char by char + Enter
        if attempt == 1:
            try:
                await input_locator.click(timeout=2000, force=True)
                await input_locator.fill("", force=True)
                await input_locator.type(value, delay=50)
                await input_locator.press("Enter")
                await asyncio.sleep(0.3)
                current = (await input_locator.input_value()) or ""
                if _fecha_mostrada_ok(current, value):
                    return True
            except Exception:
                pass
        # Strategy 3: JS value injection + dispatch events
        if attempt == 2:
            try:
                el = await input_locator.element_handle()
                if el:
                    await el.evaluate(
                        "(el, val) => { el.value = val; "
                        "el.dispatchEvent(new Event('input', {bubbles:true})); "
                        "el.dispatchEvent(new Event('change', {bubbles:true})); "
                        "el.dispatchEvent(new Event('blur', {bubbles:true})); }",
                        value,
                    )
                    await asyncio.sleep(0.3)
                    current = (await input_locator.input_value()) or ""
                    if _fecha_mostrada_ok(current, value):
                        return True
            except Exception:
                pass
        await asyncio.sleep(0.4)
    return False

async def ensure_date_range(page, desde: str, hasta: str) -> bool:
    if not desde or not hasta:
        return False
    # Datepickers reales de la pantalla de consulta (Siap pair deprecated: ya no existe)
    datepicker_pairs = [
        ("#fechaRetencionDesde__input", "#fechaRetencionHasta__input"),
    ]
    for desde_sel, hasta_sel in datepicker_pairs:
        fecha_desde_input = page.locator(desde_sel)
        fecha_hasta_input = page.locator(hasta_sel)
        try:
            await fecha_desde_input.wait_for(state="attached", timeout=5000)
            await fecha_hasta_input.wait_for(state="attached", timeout=5000)
        except Exception:
            continue
        try:
            current_desde = (await fecha_desde_input.input_value()) or ""
            current_hasta = (await fecha_hasta_input.input_value()) or ""
        except Exception:
            current_desde = ""
            current_hasta = ""
        if _fecha_mostrada_ok(current_desde, desde) and _fecha_mostrada_ok(current_hasta, hasta):
            return True
        if not await _set_datepicker_value(page, fecha_desde_input, desde, "desde"):
            continue
        await asyncio.sleep(0.3)
        if not await _set_datepicker_value(page, fecha_hasta_input, hasta, "hasta"):
            continue
        await asyncio.sleep(0.3)
        # Final verification
        try:
            final_desde = (await fecha_desde_input.input_value()) or ""
            final_hasta = (await fecha_hasta_input.input_value()) or ""
            return _fecha_mostrada_ok(final_desde, desde) and _fecha_mostrada_ok(final_hasta, hasta)
        except Exception:
            return True
    return False

async def ensure_inicio(
    page,
    cuit_representado: str,
    timeout_ms: int = 20000,
) -> bool:
    async def _click_nueva_consulta() -> None:
        nueva_tab = page.locator("#tabNuevaConsulta-tab")
        if await nueva_tab.count():
            try:
                selected = await nueva_tab.get_attribute("aria-selected")
                if selected != "true":
                    await nueva_tab.click(timeout=5000)
            except Exception:
                pass

    async def _ensure_relation() -> bool:
        if cuit_representado:
            if not await verify_representado_selected(page, cuit_representado):
                await open_representado_selector(page)
                await select_representado(page, cuit_representado)
                return await verify_representado_selected(page, cuit_representado)
        return True

    # Fast path: el formulario ya esta disponible, no navegar (un reload resetea la relacion).
    await _click_nueva_consulta()
    representado_ok = await _ensure_relation()
    impuesto_input = page.locator("#selectImpuestos")
    try:
        await impuesto_input.wait_for(state="visible", timeout=5000)
        return representado_ok
    except Exception:
        pass

    # El formulario no esta visible: navegar a inicio (recarga completa) y rearmar todo.
    # IMPORTANTE: la ruta real del formulario (HomeView) es la raíz '/', NO '/inicio'.
    # '/inicio' no existe en el router de la SPA, por eso el formulario nunca se renderiza.
    inicio_url = "https://mirequa-web.arca.gob.ar/"
    breadcrumb = page.locator("#bcMisRetenciones-li-0-routerLink")
    if await breadcrumb.count():
        try:
            await breadcrumb.first.click(timeout=5000)
        except Exception:
            await page.goto(inicio_url, timeout=timeout_ms)
    else:
        await page.goto(inicio_url, timeout=timeout_ms)

    await _click_nueva_consulta()
    representado_ok = await _ensure_relation()
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
    try:
        selected_options = page.locator("#selectImpuestos-multiselect-options li.multiselect-option.is-selected")
        selected_count = await selected_options.count()
        for idx in range(selected_count):
            option = selected_options.nth(idx)
            try:
                await option.click(timeout=2000)
            except Exception:
                pass
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

async def _click_with_retry(
    locator,
    timeout_ms: int = 5000,
    retries: int = 3,
    force: bool = False,
) -> bool:
    """Try to click a locator with retries, handling overlays and stale elements."""
    for attempt in range(retries):
        try:
            if await locator.count() == 0:
                await asyncio.sleep(0.3)
                continue
            try:
                if await locator.first.is_visible(timeout=1000):
                    await locator.first.click(timeout=timeout_ms, force=force)
                    return True
            except Exception:
                pass
            # Try force-click on later attempts
            if attempt >= 1:
                try:
                    await locator.first.click(timeout=timeout_ms, force=True)
                    return True
                except Exception:
                    pass
            await asyncio.sleep(0.4)
        except Exception:
            await asyncio.sleep(0.3)
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

    # --- Click "Consultar" with retries ---
    consultar_clicked = False
    for attempt in range(3):
        consultar_attempts = [
            page.locator("#btnConsultarRetenciones"),
            page.get_by_role("button", name=re.compile("Consultar", re.I)),
            page.locator("button:has-text('Consultar')"),
        ]
        for locator in consultar_attempts:
            if await _click_with_retry(locator, timeout_ms=5000, retries=2, force=(attempt > 0)):
                consultar_clicked = True
                break
        if consultar_clicked:
            break
        await asyncio.sleep(0.5)

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

    no_results_messages = [
        page.locator("text=/no se encontraron resultados/i"),
        page.locator("text=/sin resultados/i"),
        page.locator(".alert:has-text('No se encontraron')"),
        page.locator("#mensajeSinResultados"),
        page.locator("text=/no se encontraron registros/i"),
    ]

    for no_results_locator in no_results_messages:
        try:
            if await no_results_locator.count() > 0 and await no_results_locator.is_visible(timeout=1000):
                error_logs.append(f"No se encontraron resultados para {contexto}")
                return None
        except Exception:
            pass

    # --- Click "Exportar" with retries ---
    export_clicked = False
    for attempt in range(3):
        export_button = page.locator("#btnExportarOtrosFormatos")
        if await _click_with_retry(export_button, timeout_ms=5000, retries=2, force=(attempt > 0)):
            export_clicked = True
            break
        # Fallbacks
        export_attempts = [
            page.get_by_role("button", name=re.compile("Exportar", re.I)),
            page.locator("button:has-text('Exportar')"),
        ]
        for locator in export_attempts:
            if await _click_with_retry(locator, timeout_ms=3000, retries=2, force=(attempt > 0)):
                export_clicked = True
                break
        if export_clicked:
            break
        await asyncio.sleep(0.5)

    if not export_clicked:
        error_logs.append(f"No se pudo exportar resultados para {contexto}")
        return None

    # --- Select export format with retries (CSV preferred, XLS fallback) ---
    format_clicked = False
    for attempt in range(3):
        dropdown = page.locator(".dropdown-menu.show")
        try:
            await dropdown.wait_for(state="visible", timeout=5000)
            for fmt in (".CSV", ".xls"):
                option = dropdown.get_by_text(fmt, exact=False)
                if await option.count() > 0 and await _click_with_retry(option, timeout_ms=5000, retries=2, force=(attempt > 0)):
                    format_clicked = True
                    break
        except Exception:
            pass
        if not format_clicked:
            for fmt in (".CSV", ".xls"):
                try:
                    fallback = page.get_by_text(fmt, exact=False)
                    if await fallback.count() > 0 and await _click_with_retry(fallback, timeout_ms=3000, retries=2, force=(attempt > 0)):
                        format_clicked = True
                        break
                except Exception:
                    pass
        if format_clicked:
            break
        await asyncio.sleep(0.4)

    if not format_clicked:
        error_logs.append(f"No se pudo seleccionar el formato de exportacion (CSV/XLS) para {contexto}")
        return None

    export_all_ok = page.locator("#tablaResultadosexport_all_btnOK")
    try:
        if await export_all_ok.is_visible(timeout=4000):
            await export_all_ok.click(timeout=5000)
    except Exception:
        pass

    # --- Confirm download with retries ---
    exportar_archivo_clicked = False
    ver_archivo = page.locator("#modal-exportar-consulta_btnOK, #modal-sinresultados_btnOK")
    for attempt in range(3):
        try:
            await ver_archivo.first.wait_for(state="visible", timeout=10000)
            if await _click_with_retry(ver_archivo, timeout_ms=5000, retries=2, force=(attempt > 0)):
                exportar_archivo_clicked = True
                break
        except Exception:
            pass
        await asyncio.sleep(0.4)

    if not exportar_archivo_clicked:
        error_logs.append(f"No se pudo confirmar la exportacion del archivo para {contexto}")
        return None

    # --- Wait for download link (pestaña "consultas exportadas", ag-grid) con fallbacks históricos ---
    download_link = None
    link_attempts = [
        page.locator('#tablaExportarAplicativo .ag-row[row-index="0"] a[title="Descargar Archivo"]').first,
        page.locator("#tablaExportarAplicativo a[title='Descargar Archivo']").first,
        page.locator("a.boton_mt[download], a[download][href*='descarga']").first,
    ]
    for locator in link_attempts:
        try:
            await locator.wait_for(state="visible", timeout=8000)
            download_link = locator
            break
        except Exception:
            continue
    if download_link is None:
        error_logs.append(f"No se encontro el link de descarga para {contexto}")
        return None

    download = None
    for attempt in range(3):
        try:
            async with page.expect_download(timeout=20000) as download_info:
                await download_link.click(timeout=5000, force=(attempt > 0))
            download = await download_info.value
            break
        except Exception:
            await asyncio.sleep(0.5)

    if not download:
        error_logs.append(f"No se pudo descargar el archivo exportado para {contexto}")
        return None

    filename = _sanitize_component(nombre_archivo) or f"retenciones_{safe_impuesto}_{safe_tipo}"
    stem = filename[:-4] if filename.lower().endswith(".csv") else filename
    suggested_suffix = Path(download.suggested_filename or "").suffix
    filename = f"{stem}{suggested_suffix or '.csv'}"

    base_dir.mkdir(parents=True, exist_ok=True)
    download_path = base_dir / filename

    try:
        await download.save_as(str(download_path))
    except Exception as exc:
        error_logs.append(f"No se pudo guardar el CSV para {contexto}: {exc}")
        return None

    return download_path

async def bot_mis_retenciones(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str],
    denominacion: str,
    desde: str,
    hasta: str,
    impuestos: Optional[List[str]] = None,
    carga_minio: bool = True,
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

    # Validar impuestos solicitados
    codigos_validos = {"216", "217", "219", "353", "767", "787"}
    impuestos_filtrados = IMPUESTOS_CONFIG
    
    if impuestos:
        # Filtrar solo los códigos válidos solicitados
        codigos_solicitados = [str(code).strip() for code in impuestos]
        codigos_validos_solicitados = [code for code in codigos_solicitados if code in codigos_validos]
        
        if not codigos_validos_solicitados:
            error_logs.append(
                f"Ninguno de los impuestos solicitados {codigos_solicitados} está en la lista válida: {sorted(codigos_validos)}"
            )
            return results, error_logs, False
        
        # Filtrar IMPUESTOS_CONFIG para solo los códigos válidos solicitados
        impuestos_filtrados = [imp for imp in IMPUESTOS_CONFIG if imp["code"] in codigos_validos_solicitados]
        
        # Log de impuestos disponibles y seleccionados
        codigos_disponibles = [imp["code"] for imp in IMPUESTOS_CONFIG]
        codigos_no_disponibles = [code for code in codigos_solicitados if code not in codigos_disponibles]
        
        if codigos_no_disponibles:
            error_logs.append(
                f"Impuestos no disponibles para selección: {codigos_no_disponibles}. "
                f"Solo se procesarán: {codigos_validos_solicitados}"
            )
        else:
            error_logs.append(f"Impuestos seleccionados para procesar: {codigos_validos_solicitados}")

    cuit_objetivo = cuit_representado or cuit_representante
    proxy = get_proxy_config() if proxy_request else None

    temp_dir = None
    base_root = Path("./descargas/mis_retenciones")
    if carga_minio:
        temp_dir = tempfile.TemporaryDirectory(prefix="mis_retenciones_")
        base_root = Path(temp_dir.name)

    stamp_dir = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_representado = safe_filename(cuit_objetivo or cuit_representante)
    output_dir = base_root / safe_representado / stamp_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    async def subir_minio(path: Path, nombre_archivo: str) -> str:
        if not carga_minio:
            return ""
        try:
            return await subir_archivo_a_minio(
                minio_url_interna=minio_url_interna,
                minio_url_externa=minio_url_externa,
                minio_ssl=minio_ssl,
                minio_interno=minio_interno,
                minio_access_key=minio_access_key_mis_retenciones,
                minio_secret_key=minio_secret_key_mis_retenciones,
                minio_bucket=minio_bucket_mis_retenciones,
                archivo_a_subir=str(path),
                nombre_archivo_en_bucket=nombre_archivo,
            )
        except S3Error as exc:
            error_logs.append(f"Error al subir {path.name} a MinIO: {exc}")
        except Exception as exc:
            error_logs.append(f"Error al subir {path.name} a MinIO: {exc}")
        return ""

    try:
        async with limit_browser(), async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            async def _cleanup_and_return(ok: bool) -> tuple[dict[str, Any], list[str], bool]:
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
                return results, error_logs, ok

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            if not login_result.success:
                return await _cleanup_and_return(False)

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
                return await _cleanup_and_return(False)

            await wait_for_ready(page1)
            # La ruta real del formulario (HomeView) es la raíz '/', NO '/inicio' (inexistente).
            await page1.goto("https://mirequa-web.arca.gob.ar/")
            if not await ensure_inicio(page1, cuit_objetivo):
                error_logs.append(
                    f"No se pudo seleccionar el representado {cuit_objetivo} en Mis Retenciones"
                )

            for impuesto in impuestos_filtrados:
                code = impuesto["code"]
                label = impuesto["label"]
                tipos = impuesto["tipos"]
                for tipo in tipos:
                    if not await ensure_inicio(page1, cuit_objetivo):
                        error_logs.append(
                            f"No se pudo confirmar el representado {cuit_objetivo} antes de consultar {label}"
                        )
                    selected = await select_impuesto(page1, label, code)
                    if not selected:
                        error_logs.append(f"No se pudo seleccionar el impuesto {label}")
                        continue
                    if not await select_tipo(page1, tipo):
                        error_logs.append(f"No se pudo seleccionar el tipo {tipo} para {label}")
                        continue
                    if not await ensure_date_range(page1, desde, hasta):
                        error_logs.append(
                            f"No se pudo cargar el rango de fechas {desde} - {hasta} para {label}"
                        )
                        continue
                    await asyncio.sleep(0.5)
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
                        object_key = (
                            await subir_minio(csv_path, csv_path.name) if carga_minio else ""
                        )
                        # V2: guardar object_key + bucket + size, sin URL
                        try:
                            _size = csv_path.stat().st_size if csv_path.exists() else 0
                        except Exception:
                            _size = 0
                        results["archivos"].append(
                            {
                                "impuesto_code": code,
                                "impuesto_label": label,
                                "tipo": tipo,
                                "name": csv_path.name,
                                "archivo": csv_path.name,
                                "object_key": object_key or None,
                                "bucket": minio_bucket_mis_retenciones if object_key else None,
                                "size": _size if object_key else 0,
                            }
                        )
                        if object_key and csv_path.exists():
                            try:
                                csv_path.unlink()
                            except Exception as exc:
                                error_logs.append(
                                    f"Error al eliminar el archivo local {csv_path.name}: {exc}"
                                )

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot Mis Retenciones: {exc}")
        success = False
    finally:
        if temp_dir:
            temp_dir.cleanup()

    if not results["archivos"]:
        #success = False
        if not error_logs:
            error_logs.append("No se encontraron retenciones para el periodo solicitado")

    return results, error_logs, success

async def main() -> None:
    from dotenv import load_dotenv
    
    load_dotenv()
    cuit_representante = os.getenv("CUIT_REPRESENTANTE")
    clave = os.getenv("CLAVE")
    cuit_representado = os.getenv("CUIT_REPRESENTADO")
    denominacion = os.getenv("DENOMINACION")
    desde = os.getenv("DESDE")
    hasta = os.getenv("HASTA")
    
    await bot_mis_retenciones(
        cuit_representante=cuit_representante,
        clave_representante=clave,
        cuit_representado=cuit_representado,
        denominacion=denominacion,
        desde=desde,
        hasta=hasta,
        carga_minio=False,
        proxy_request=False,
    )

if __name__ == "__main__":
    asyncio.run(main())
