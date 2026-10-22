import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from playwright.async_api import Playwright, async_playwright, expect


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "mis_retenciones") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.downloads_dir = self.run_dir / "descargas"
        self.har_dir = self.run_dir / "har"
        for path in (self.screenshots_dir, self.html_dir, self.downloads_dir, self.har_dir):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.run_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.impuesto_options_logged = False
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page, step: str) -> None:
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


INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "download"


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
    debug: DebugCapture,
    cuit_representado: str,
    wait_timeout_ms: int = 4000,
) -> bool:
    """Verify that the representado is selected by checking if their CUIT is visible in the navbar."""
    formatted = format_cuit(cuit_representado)
    digits_only = re.sub(r"\D", "", cuit_representado or "")
    if not formatted and not digits_only:
        debug.log("representado_not_verified cuit=missing")
        return False

    navbar = page.locator("#navBarMisRetenciones")
    try:
        await navbar.wait_for(state="visible", timeout=wait_timeout_ms)
    except Exception:
        debug.log("representado_navbar_not_visible")

    cuit_locator = page.locator("#navBarMisRetenciones-relationCuil")
    label_locator = page.locator("#navBarMisRetenciones-relationLabel")
    for name, locator in (("relationCuil", cuit_locator), ("relationLabel", label_locator)):
        try:
            count = await locator.count()
            if count:
                text_content = await locator.first.text_content()
                debug.log(f"verify_check locator={name} count={count} text={repr((text_content or '')[:80])}")
                if _text_has_cuit(text_content or "", formatted, digits_only):
                    debug.log(f"representado_verified cuit={formatted} found_in_navbar")
                    return True
        except Exception as exc:
            debug.log(f"verify_check_error locator={name} error={str(exc)[:120]}")
            continue

    try:
        nav_text = await navbar.text_content()
        debug.log(f"verify_check locator=navbar text={repr((nav_text or '')[:120])}")
        if _text_has_cuit(nav_text or "", formatted, digits_only):
            debug.log(f"representado_verified cuit={formatted} found_in_navbar_text")
            return True
    except Exception:
        pass

    debug.log(f"representado_not_verified cuit={formatted} not_found_in_navbar")
    return False


async def select_representado(page, debug: DebugCapture, cuit_representado: str) -> None:
    formatted = format_cuit(cuit_representado)
    cuit_pattern = cuit_regex(cuit_representado)
    try:
        await page.locator(".e-relation__card").first.wait_for(state="visible", timeout=20000)
    except Exception:
        debug.log("representado_cards_not_visible")
    attempts = [
        (
            "card_text_formatted",
            page.locator(".e-relation__card", has=page.get_by_text(formatted)),
        ),
        (
            "card_text_regex",
            page.locator(".e-relation__card", has=page.get_by_text(cuit_pattern)),
        ),
        (
            "relation_div_formatted",
            page.locator("div[id^=selectorRelaciones_relation]", has_text=formatted),
        ),
        ("text_formatted", page.get_by_text(formatted)),
        ("text_regex", page.get_by_text(cuit_pattern)),
    ]
    for label, locator in attempts:
        try:
            await locator.first.click(timeout=3000)
            debug.log(f"representado_selected mode={label} cuit={formatted}")
            await debug.capture(page, f"selected_representado_{label}")
            return
        except Exception:
            continue
    debug.log(f"representado_select_failed cuit={formatted}")
    await debug.capture(page, "selected_representado_failed")


async def log_impuesto_options(page, debug: DebugCapture) -> None:
    options = page.locator("#selectImpuestos-dropdown li[role='option']")
    count = await options.count()
    codes = []
    labels = {}
    seen_codes = set()
    for idx in range(count):
        option = options.nth(idx)
        option_id = await option.get_attribute("id") or ""
        label = (await option.get_attribute("aria-label")) or ""
        match = re.search(r"[_-](IMP|SS|ADU|SIR)_(\d+)", option_id)
        if match:
            code = match.group(2)
            if code not in seen_codes:
                seen_codes.add(code)
                codes.append(code)
            if label and code not in labels:
                labels[code] = label
    if codes:
        debug.log(f"impuesto_options_imp_codes={','.join(codes)}")
    expected = [item["code"] for item in IMPUESTOS_CONFIG]
    missing = [code for code in expected if code not in codes]
    if missing:
        debug.log(f"impuesto_options_missing={','.join(missing)}")
    for code in expected:
        label = labels.get(code)
        if label:
            debug.log(f"impuesto_option_label code={code} label={label}")


async def wait_for_ready(page, debug: DebugCapture, label: str, timeout_ms: int = 30000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        debug.log(f"wait_networkidle_timeout label={label}")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            debug.log(f"wait_domcontentloaded_timeout label={label}")


async def wait_for_results_state(page, debug: DebugCapture, label: str, timeout_ms: int = 30000) -> str:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            # Check for SIRE modal first
            if await page.locator("#modal-sire").is_visible(timeout=500):
                return "sire"
        except Exception:
            pass
            
        try:
            # Check for error modal
            if await page.locator("#modal-confirmar").is_visible(timeout=500):
                return "error"
        except Exception:
            pass
            
        try:
            # Check for export processing modal
            if await page.locator("#modal-sinresultados").is_visible(timeout=500):
                return "export_processing"
        except Exception:
            pass
        
        # Check for results page with export button
        export_btn = page.locator("#btnExportarOtrosFormatos")
        try:
            if await export_btn.count() > 0 and await export_btn.is_visible(timeout=500):
                return "results"
        except Exception:
            pass
            
        # Check for results table
        try:
            table = page.locator("#tablaResultados")
            if await table.count() > 0 and await table.is_visible(timeout=500):
                return "results"
        except Exception:
            pass
            
        # Check URL for resultados
        if re.search(r"/resultados", page.url, re.I):
            await asyncio.sleep(1)  # Give page time to fully load
            if await export_btn.count() > 0:
                return "results"
                
        await asyncio.sleep(0.5)
        
    debug.log(f"resultados_timeout label={label} url={page.url}")
    return "no_results"


async def open_representado_selector(page, debug: DebugCapture) -> None:
    cards = page.locator(".e-relation__card")
    if await cards.count():
        debug.log("representado_cards_visible")
        await debug.capture(page, "representado_cards_visible")
        return
    try:
        await page.locator("#navBarMisRetenciones").wait_for(state="visible", timeout=20000)
    except Exception:
        debug.log("representado_navbar_not_visible")
    relation_link = page.locator("#navBarMisRetenciones-relationLabel")
    relation_cuit = page.locator("#navBarMisRetenciones-relationCuil")
    for locator in (relation_link, relation_cuit):
        if await locator.count():
            try:
                await locator.click(timeout=3000)
                await debug.capture(page, "clicked_relation_link")
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
            debug.log("representado_dropdown_toggle_not_visible")
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
                    debug.log("representado_dropdown_toggle_not_found")
    dropdown = page.locator(".dropdown-menu.show")
    try:
        await dropdown.wait_for(state="visible", timeout=5000)
    except Exception:
        debug.log("representado_dropdown_not_visible")
    await debug.capture(page, "clicked_navbar_dropdown")
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
            await debug.capture(page, "clicked_seleccionar_representado")
            try:
                await cards.first.wait_for(state="visible", timeout=5000)
            except Exception:
                debug.log("representado_cards_not_visible_after_click")
            return
        except Exception:
            continue
    debug.log("seleccionar_representado_not_found")
    await debug.capture(page, "clicked_seleccionar_representado_failed")


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

async def ensure_date_range(page, debug: DebugCapture, desde: str, hasta: str) -> bool:
    if not desde or not hasta:
        debug.log("fecha_range_missing")
        return False
    fecha_desde_input = page.locator("#fechaRetencionDesde__input")
    fecha_hasta_input = page.locator("#fechaRetencionHasta__input")
    try:
        await fecha_desde_input.wait_for(state="visible", timeout=5000)
        await fecha_hasta_input.wait_for(state="visible", timeout=5000)
    except Exception:
        debug.log("fecha_inputs_not_visible")
        return False
    try:
        current_desde = (await fecha_desde_input.input_value()) or ""
        current_hasta = (await fecha_hasta_input.input_value()) or ""
    except Exception:
        current_desde = ""
        current_hasta = ""
    if _fecha_mostrada_ok(current_desde, desde) and _fecha_mostrada_ok(current_hasta, hasta):
        debug.log("fecha_range_already_set")
        return True
    try:
        await fecha_desde_input.click()
        await fecha_desde_input.fill(desde)
        await fecha_desde_input.press("Enter")
    except Exception as exc:
        debug.log(f"fecha_desde_set_failed error={str(exc)[:100]}")
        return False
    try:
        await fecha_hasta_input.click()
        await fecha_hasta_input.fill(hasta)
        await fecha_hasta_input.press("Enter")
    except Exception as exc:
        debug.log(f"fecha_hasta_set_failed error={str(exc)[:100]}")
        return False
    await debug.capture(page, "fecha_range_set")
    await asyncio.sleep(0.3)
    try:
        final_desde = (await fecha_desde_input.input_value()) or ""
        final_hasta = (await fecha_hasta_input.input_value()) or ""
    except Exception:
        return True
    return _fecha_mostrada_ok(final_desde, desde) and _fecha_mostrada_ok(final_hasta, hasta)


async def ensure_inicio(
    page,
    debug: DebugCapture,
    cuit_representado: str,
    timeout_ms: int = 20000,
) -> None:
    async def _click_nueva_consulta() -> None:
        nueva_tab = page.locator("#tabNuevaConsulta-tab")
        if await nueva_tab.count():
            try:
                selected = await nueva_tab.get_attribute("aria-selected")
                if selected != "true":
                    await nueva_tab.click(timeout=5000)
            except Exception:
                debug.log("nueva_consulta_tab_click_failed")

    async def _ensure_relation() -> None:
        if cuit_representado:
            if not await verify_representado_selected(page, debug, cuit_representado):
                debug.log(f"representado_not_selected_reselecting cuit={cuit_representado}")
                await open_representado_selector(page, debug)
                await select_representado(page, debug, cuit_representado)

    # Fast path: el formulario ya está disponible, no navegar (un reload resetea la relación).
    await _click_nueva_consulta()
    await _ensure_relation()
    impuesto_input = page.locator("#selectImpuestos")
    try:
        await impuesto_input.wait_for(state="visible", timeout=5000)
        return
    except Exception:
        pass

    # El formulario no está visible: navegar a inicio y rearmar todo.
    # IMPORTANTE: la ruta real del formulario (HomeView) es la raíz '/', NO '/inicio'.
    # '/inicio' no existe en el router, por eso el formulario nunca se renderiza.
    debug.log("impuesto_input_not_visible_navigating_inicio")
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
    await _ensure_relation()
    try:
        await page.locator("#selectImpuestos").wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        debug.log("impuesto_input_not_visible")


async def open_impuesto_dropdown(page, debug: DebugCapture, label: str, code: str) -> None:
    safe_label = _safe_step_name(label or code or "impuesto")
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
            debug.log("impuesto_combobox_not_found")
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
        debug.log("impuesto_dropdown_not_visible")
    try:
        selected_options = page.locator("#selectImpuestos-multiselect-options li.multiselect-option.is-selected")
        selected_count = await selected_options.count()
        for idx in range(selected_count):
            option = selected_options.nth(idx)
            option_id = (await option.get_attribute("id")) or ""
            try:
                await option.click(timeout=2000)
                debug.log(f"impuesto_deselected id={option_id}")
            except Exception:
                debug.log(f"impuesto_deselect_failed id={option_id}")
    except Exception:
        debug.log("impuesto_deselect_scan_failed")
    await debug.capture(page, f"impuesto_combobox_opened_{safe_label}")
    if not debug.impuesto_options_logged:
        await log_impuesto_options(page, debug)
        debug.impuesto_options_logged = True


async def select_impuesto(page, debug: DebugCapture, label: str, code: str) -> bool:
    safe_label = _safe_step_name(label or code)
    if await is_impuesto_selected(page, label, code):
        debug.log(f"impuesto_already_selected label={label}")
        return True
    await open_impuesto_dropdown(page, debug, label, code)
    if code:
        prefixes = ("IMP", "SS", "ADU", "SIR")
        for prefix in prefixes:
            option_id = f"#selectImpuestos-multiselect-option-{prefix}_{code}"
            option = page.locator(option_id)
            if await option.count():
                try:
                    await option.first.click(timeout=2000)
                    debug.log(f"impuesto_selected_id label={label} id={option_id}")
                    await debug.capture(page, f"impuesto_selected_{safe_label}")
                    return True
                except Exception:
                    debug.log(f"impuesto_select_id_failed label={label} id={option_id}")
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
                debug.log(f"impuesto_selected label={label} candidate={candidate}")
                await debug.capture(page, f"impuesto_selected_{safe_label}")
                return True
            except Exception:
                continue
        for pattern in _impuesto_patterns(label, code):
            try:
                await scope.get_by_role("option", name=pattern).first.click(timeout=2000)
                debug.log(f"impuesto_selected_regex label={label} pattern={pattern.pattern}")
                await debug.capture(page, f"impuesto_selected_{safe_label}")
                return True
            except Exception:
                continue
    debug.log(f"impuesto_select_failed label={label}")
    await debug.capture(page, f"impuesto_select_failed_{safe_label}")
    return False


async def select_tipo(page, debug: DebugCapture, tipo: str) -> bool:
    if not tipo:
        debug.log("tipo_select_failed tipo=missing")
        return False
    safe_tipo = _safe_step_name(tipo)
    container = page.locator("#tipoOperacion")
    try:
        await container.wait_for(state="visible", timeout=5000)
    except Exception:
        debug.log(f"tipo_container_not_visible tipo={tipo}")
        await debug.capture(page, f"tipo_container_not_visible_{safe_tipo}")
        return False
    labels = container.locator("label")
    try:
        label_count = await labels.count()
    except Exception:
        label_count = 0
    if label_count == 0:
        debug.log(f"tipo_options_empty tipo={tipo}")
        await debug.capture(page, f"tipo_options_empty_{safe_tipo}")
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
            await debug.capture(page, f"tipo_{safe_tipo}_selected")
            return True
    except Exception:
        pass
    try:
        await container.get_by_label(pattern).first.check(timeout=3000)
        await debug.capture(page, f"tipo_{safe_tipo}_selected")
        return True
    except Exception:
        pass
    try:
        options = []
        limit = min(label_count, 5)
        for idx in range(limit):
            text = (await labels.nth(idx).inner_text()) or ""
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                options.append(text)
        if options:
            debug.log(f"tipo_options_available tipo={tipo} options={','.join(options)}")
    except Exception:
        pass
    debug.log(f"tipo_select_failed tipo={tipo}")
    await debug.capture(page, f"tipo_select_failed_{safe_tipo}")
    return False


async def download_csv(page, debug: DebugCapture, impuesto_code: str, impuesto_label: str, tipo: str) -> None:
    safe_impuesto = safe_filename(impuesto_code or impuesto_label or "impuesto")
    safe_tipo = safe_filename(tipo)

    # Multiple strategies to click Consultar button
    consultar_clicked = False
    consultar_attempts = [
        ("id_consultar_retenciones", page.locator("#btnConsultarRetenciones")),
        ("role_button", page.get_by_role("button", name=re.compile("Consultar", re.I))),
        ("button_text", page.locator("button:has-text('Consultar')")),
    ]

    for attempt_name, locator in consultar_attempts:
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=2000):
                await locator.first.click(timeout=5000)
                consultar_clicked = True
                debug.log(f"consultar_clicked method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"consultar_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not consultar_clicked:
        debug.log(f"consultar_not_found {safe_impuesto}_{safe_tipo}")
        await debug.capture(page, f"consultar_not_found_{safe_impuesto}_{safe_tipo}")
        return
        
    await debug.capture(page, f"clicked_consultar_{safe_impuesto}_{safe_tipo}")
    await wait_for_ready(page, debug, f"consultar_{safe_impuesto}_{safe_tipo}")
    state = await wait_for_results_state(page, debug, f"{safe_impuesto}_{safe_tipo}")
    debug.log(f"consulta_state {safe_impuesto}_{safe_tipo}={state}")
    if state == "sire":
        try:
            await page.locator("#modal-sire_btnOK").click(timeout=3000)
            await debug.capture(page, f"sire_redirect_{safe_impuesto}_{safe_tipo}")
        except Exception:
            debug.log("sire_modal_ok_not_found")
        return
    if state == "error":
        try:
            await page.locator("#modal-confirmar_btnOK").click(timeout=3000)
            await debug.capture(page, f"consulta_error_ack_{safe_impuesto}_{safe_tipo}")
        except Exception:
            debug.log("consulta_error_modal_ok_not_found")
        return
    if state != "results":
        debug.log(f"consulta_no_results {safe_impuesto}_{safe_tipo}")
        await debug.capture(page, f"consulta_no_results_{safe_impuesto}_{safe_tipo}")
        return

    # Check if there are no results
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
                debug.log(f"no_results_found {safe_impuesto}_{safe_tipo}")
                await debug.capture(page, f"no_results_{safe_impuesto}_{safe_tipo}")
                return
        except Exception:
            pass
    
    # Try multiple strategies to find the export button
    export_clicked = False
    export_button = page.locator("#btnExportarOtrosFormatos")
    try:
        await export_button.wait_for(state="visible", timeout=8000)
        await export_button.click(timeout=5000)
        export_clicked = True
        debug.log("export_button_clicked method=id_primary")
    except Exception as e:
        debug.log(f"export_button_attempt_failed method=id_primary error={str(e)[:100]}")
        export_attempts = [
            ("button_exportar", page.get_by_role("button", name=re.compile("Exportar", re.I))),
            ("any_exportar_button", page.locator("button:has-text('Exportar')")),
        ]
        for attempt_name, locator in export_attempts:
            try:
                if await locator.count() > 0 and await locator.first.is_visible(timeout=3000):
                    await locator.first.click(timeout=5000)
                    export_clicked = True
                    debug.log(f"export_button_clicked method={attempt_name}")
                    break
            except Exception as exc:
                debug.log(f"export_button_attempt_failed method={attempt_name} error={str(exc)[:100]}")
                continue

    if not export_clicked:
        debug.log(f"export_button_not_found {safe_impuesto}_{safe_tipo} - possibly no results")
        await debug.capture(page, f"export_button_not_found_{safe_impuesto}_{safe_tipo}")
        return
    
    await debug.capture(page, f"clicked_exportar_{safe_impuesto}_{safe_tipo}")

    # Wait for dropdown and select export format (CSV preferred, Excel fallback)
    format_clicked = False
    dropdown = page.locator(".dropdown-menu.show")
    try:
        await dropdown.wait_for(state="visible", timeout=5000)
        for fmt in (".CSV", ".xls"):
            option = dropdown.get_by_text(fmt, exact=False)
            if await option.count() > 0:
                await option.first.click(timeout=5000)
                format_clicked = True
                debug.log(f"export_format_selected method=dropdown fmt={fmt}")
                break
    except Exception as e:
        debug.log(f"export_format_attempt_failed method=dropdown error={str(e)[:100]}")

    if not format_clicked:
        for fmt in (".CSV", ".xls"):
            try:
                fallback = page.get_by_text(fmt, exact=False)
                if await fallback.count() > 0 and await fallback.first.is_visible(timeout=2000):
                    await fallback.first.click(timeout=5000)
                    format_clicked = True
                    debug.log(f"export_format_selected method=text fmt={fmt}")
                    break
            except Exception as e:
                debug.log(f"export_format_attempt_failed method=text fmt={fmt} error={str(e)[:100]}")

    if not format_clicked:
        debug.log(f"export_format_not_found {safe_impuesto}_{safe_tipo}")
        await debug.capture(page, f"export_format_not_found_{safe_impuesto}_{safe_tipo}")
        return
    
    await debug.capture(page, f"selected_format_{safe_impuesto}_{safe_tipo}")
    
    # Confirm export if needed, then open "Ver archivo"
    export_all_ok = page.locator("#tablaResultadosexport_all_btnOK")
    try:
        if await export_all_ok.is_visible(timeout=4000):
            await export_all_ok.click(timeout=5000)
            debug.log("export_all_clicked")
            await debug.capture(page, f"clicked_export_all_{safe_impuesto}_{safe_tipo}")
    except Exception as e:
        debug.log(f"export_all_attempt_failed error={str(e)[:100]}")

    exportar_archivo_clicked = False
    # Modal actual: "Exportación en proceso" (Ver archivo). Fallback: modal histórico.
    ver_archivo = page.locator("#modal-exportar-consulta_btnOK, #modal-sinresultados_btnOK")
    try:
        await ver_archivo.first.wait_for(state="visible", timeout=15000)
        await ver_archivo.first.click(timeout=5000)
        exportar_archivo_clicked = True
        debug.log("exportar_archivo_clicked method=modal_ver_archivo")
        await debug.capture(page, f"clicked_exportar_archivo_{safe_impuesto}_{safe_tipo}")
    except Exception as e:
        debug.log(f"exportar_archivo_attempt_failed error={str(e)[:100]}")

    if not exportar_archivo_clicked:
        debug.log(f"exportar_archivo_not_found {safe_impuesto}_{safe_tipo}")
        await debug.capture(page, f"exportar_archivo_not_found_{safe_impuesto}_{safe_tipo}")
        return

    # Wait for the exported-consultas table and start download
    download_link = None
    link_attempts = [
        ("newest_row", page.locator('#tablaExportarAplicativo .ag-row[row-index="0"] a[title="Descargar Archivo"]').first),
        ("any_row", page.locator("#tablaExportarAplicativo a[title='Descargar Archivo']").first),
        ("legacy", page.locator("a.boton_mt[download], a[download][href*='descarga']").first),
    ]
    for attempt_name, locator in link_attempts:
        try:
            await locator.wait_for(state="visible", timeout=8000)
            download_link = locator
            debug.log(f"download_link_visible method={attempt_name}")
            break
        except Exception:
            continue
    if download_link is None:
        debug.log(f"download_link_not_visible {safe_impuesto}_{safe_tipo}")
        await debug.capture(page, f"download_link_not_visible_{safe_impuesto}_{safe_tipo}")
        return

    await debug.capture(page, f"downloads_page_{safe_impuesto}_{safe_tipo}")

    download = None
    try:
        async with page.expect_download(timeout=20000) as download_info:
            await download_link.click(timeout=5000)
        download = await download_info.value
        debug.log("download_started method=download_link")
    except Exception as e:
        debug.log(f"download_attempt_failed method=download_link error={str(e)[:100]}")

    if not download:
        debug.log(f"download_not_available {safe_impuesto}_{safe_tipo}")
        return

    suggested = download.suggested_filename or f"retenciones_{safe_impuesto}_{safe_tipo}.csv"
    suffix = Path(suggested).suffix or ".csv"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = safe_filename(f"{stamp}_{safe_impuesto}_{safe_tipo}{suffix}")
    download_path = debug.downloads_dir / filename
    await download.save_as(str(download_path))
    debug.log(f"download_saved {download_path}")
    await debug.capture(page, f"download_saved_{safe_impuesto}_{safe_tipo}")


async def bot_mis_retenciones(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    desde: str,
    hasta: str,
    impuestos_filter: Optional[Sequence[str]] = None,
    ) -> None:
    debug = DebugCapture()
    impuestos = IMPUESTOS_CONFIG
    if impuestos_filter:
        wanted = {str(code).strip() for code in impuestos_filter if str(code).strip()}
        impuestos = [item for item in IMPUESTOS_CONFIG if item["code"] in wanted]
        debug.log(f"impuestos_filter applied codes={','.join(item['code'] for item in impuestos)}")
    debug.log("launch_browser headless=False")
    browser = await playwright.chromium.launch(headless=False)
    debug.log("new_context")
    save_har = _env_flag("SAVE_HAR", default=True)
    har_path = debug.har_dir / "network.har" if save_har else None
    context_kwargs = {"accept_downloads": True}
    if har_path is not None:
        debug.log(f"har_path={har_path}")
        context_kwargs["record_har_path"] = str(har_path)
        context_kwargs["record_har_content"] = "embed"
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    await debug.capture(page, "new_page")
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await debug.capture(page, "login_page_loaded")
    
    # Multiple strategies to find and fill CUIT field
    cuit_filled = False
    cuit_attempts = [
        ("spinbutton", page.get_by_role("spinbutton")),
        ("id_F1_login", page.locator("#F1\\:login")),
        ("input_number", page.locator("input[type='number']")),
        ("placeholder_cuit", page.locator("input[placeholder*='CUIT'], input[placeholder*='cuit']")),
    ]
    
    for attempt_name, locator in cuit_attempts:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await debug.capture(page, f"cuit_field_click_{attempt_name}")
                await locator.first.fill(cuit_representante)
                await debug.capture(page, f"cuit_filled_{attempt_name}")
                cuit_filled = True
                debug.log(f"cuit_filled method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"cuit_fill_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not cuit_filled:
        debug.log("cuit_fill_failed_all_methods")
        await debug.capture(page, "cuit_fill_failed")
        return
        
    # Multiple strategies to click Siguiente button
    siguiente_clicked = False
    siguiente_attempts = [
        ("role_button", page.get_by_role("button", name="Siguiente")),
        ("role_button_contains", page.get_by_role("button", name=re.compile("Siguiente", re.I))),
        ("text", page.get_by_text("Siguiente", exact=True)),
        ("id_siguiente", page.locator("#siguiente")),
        ("button_text", page.locator("button:has-text('Siguiente')")),
    ]
    
    for attempt_name, locator in siguiente_attempts:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await debug.capture(page, f"clicked_siguiente_{attempt_name}")
                siguiente_clicked = True
                debug.log(f"siguiente_clicked method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"siguiente_click_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not siguiente_clicked:
        debug.log("siguiente_click_failed_all_methods")
        await debug.capture(page, "siguiente_click_failed")
        return
        
    await asyncio.sleep(1)  # Wait for password field to appear
    
    # Multiple strategies to find and fill password field
    clave_filled = False
    clave_attempts = [
        ("textbox_tuclave", page.get_by_role("textbox", name="TU CLAVE")),
        ("textbox_clave", page.get_by_role("textbox", name=re.compile("CLAVE|clave", re.I))),
        ("id_F1_password", page.locator("#F1\\:password")),
        ("input_password", page.locator("input[type='password']")),
        ("name_password", page.locator("input[name*='password'], input[name*='clave']")),
    ]
    
    for attempt_name, locator in clave_attempts:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await debug.capture(page, f"clave_field_click_{attempt_name}")
                await locator.first.fill(clave)
                await debug.capture(page, f"clave_filled_{attempt_name}")
                clave_filled = True
                debug.log(f"clave_filled method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"clave_fill_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not clave_filled:
        debug.log("clave_fill_failed_all_methods")
        await debug.capture(page, "clave_fill_failed")
        return
        
    # Multiple strategies to click Ingresar button
    ingresar_clicked = False
    ingresar_attempts = [
        ("role_button", page.get_by_role("button", name="Ingresar")),
        ("role_button_contains", page.get_by_role("button", name=re.compile("Ingresar|INGRESAR", re.I))),
        ("text", page.get_by_text("Ingresar", exact=True)),
        ("id_ingresar", page.locator("#ingresar")),
        ("button_submit", page.locator("button[type='submit']")),
    ]
    
    for attempt_name, locator in ingresar_attempts:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await debug.capture(page, f"clicked_ingresar_{attempt_name}")
                ingresar_clicked = True
                debug.log(f"ingresar_clicked method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"ingresar_click_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not ingresar_clicked:
        debug.log("ingresar_click_failed_all_methods")
        await debug.capture(page, "ingresar_click_failed")
        return
        
    await wait_for_ready(page, debug, "after_login", 20000)
    
    # Multiple strategies to click Ver todos link
    ver_todos_clicked = False
    ver_todos_attempts = [
        ("role_link", page.get_by_role("link", name="Ver todos")),
        ("role_link_contains", page.get_by_role("link", name=re.compile("Ver todos|VER TODOS", re.I))),
        ("text", page.get_by_text("Ver todos", exact=False)),
        ("link_text", page.locator("a:has-text('Ver todos')")),
    ]
    
    for attempt_name, locator in ver_todos_attempts:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=10000)
                await debug.capture(page, f"clicked_ver_todos_{attempt_name}")
                ver_todos_clicked = True
                debug.log(f"ver_todos_clicked method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"ver_todos_click_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not ver_todos_clicked:
        debug.log("ver_todos_not_found")
        await debug.capture(page, "ver_todos_not_found")
        
    await asyncio.sleep(1)
    
    # Multiple strategies to click MIS RETENCIONES button  
    page1 = None
    mis_retenciones_attempts = [
        ("role_button", page.get_by_role("button", name=re.compile("MIS RETENCIONES", re.I))),
        ("text_contains", page.get_by_text(re.compile("MIS RETENCIONES|Mis Retenciones", re.I))),
        ("link_retenciones", page.locator("a:has-text('MIS RETENCIONES'), button:has-text('MIS RETENCIONES')")),
    ]
    
    for attempt_name, locator in mis_retenciones_attempts:
        try:
            if await locator.count() > 0:
                async with page.expect_popup() as page1_info:
                    await locator.first.click(timeout=10000)
                page1 = await page1_info.value
                debug.log(f"mis_retenciones_opened method={attempt_name}")
                break
        except Exception as e:
            debug.log(f"mis_retenciones_attempt_failed method={attempt_name} error={str(e)[:100]}")
            continue
    
    if not page1:
        debug.log("mis_retenciones_not_found")
        await debug.capture(page, "mis_retenciones_not_found")
        return
    await wait_for_ready(page1, debug, "mis_retenciones_popup")
    await debug.capture(page1, "mis_retenciones_popup_opened")
    # La ruta real del formulario (HomeView) es la raíz '/', NO '/inicio' (inexistente).
    await page1.goto("https://mirequa-web.arca.gob.ar/")
    await debug.capture(page1, "inicio_loaded")
    await ensure_inicio(page1, debug, cuit_representado)
    await debug.capture(page1, "impuesto_input_visible")
    for impuesto in impuestos:
        code = impuesto["code"]
        label = impuesto["label"]
        tipos = impuesto["tipos"]
        debug.log(f"impuesto_start code={code} label={label}")
        for tipo in tipos:
            await ensure_inicio(page1, debug, cuit_representado)
            selected = await select_impuesto(page1, debug, label, code)
            if not selected:
                continue
            if not await select_tipo(page1, debug, tipo):
                continue
            if not await ensure_date_range(page1, debug, desde, hasta):
                continue
            debug.log(f"consulta_start code={code} tipo={tipo}")
            await download_csv(page1, debug, code, label, tipo)

    # ---------------------
    await context.close()
    if har_path is not None:
        try:
            if har_path.exists():
                debug.log(f"har_saved path={har_path} size={har_path.stat().st_size}")
            else:
                debug.log(f"har_missing path={har_path}")
        except Exception as exc:
            debug.log(f"har_check_failed error={exc}")
    await browser.close()


async def main() -> None:
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    cuit_representante = os.getenv("CUIT_REPRESENTANTE")
    clave = os.getenv("CLAVE")
    cuit_representado = os.getenv("CUIT_REPRESENTADO")
    desde = os.getenv("DESDE")
    hasta = os.getenv("HASTA")
    impuestos_filter = [code for code in os.getenv("IMPUESTOS", "").split(",") if code.strip()]

    async with async_playwright() as playwright:
        await bot_mis_retenciones(
            playwright,
            cuit_representante,
            clave,
            cuit_representado,
            desde,
            hasta,
            impuestos_filter=impuestos_filter,
        )


asyncio.run(main())
