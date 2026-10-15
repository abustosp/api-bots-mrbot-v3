import asyncio
import base64
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional

import pdfplumber
from playwright.async_api import Locator, Page, async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "rcel"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")

# --- Helpers (Duplicated) ---

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

async def seleccionar_representado_por_denominacion(
    page: Page,
    denominacion: str,
) -> bool:
    target_text = normalize_text(denominacion)
    if not target_text:
        return False
    target_norm = normalize_name_for_match(target_text)
    target_is_digits = target_norm.isdigit()

    async def build_contexts():
        contexts = [page]
        try:
            for frame in page.frames:
                if frame != page.main_frame:
                    contexts.append(frame)
        except Exception:
            pass
        return contexts

    async def has_consultas_action(contexts) -> bool:
        consultas_pattern = re.compile(r"Consulta(?:s)?", re.I)
        for ctx in contexts:
            locators = [
                ctx.get_by_role("button", name=consultas_pattern),
                ctx.get_by_role("link", name=consultas_pattern),
                ctx.locator(
                    "input[type='button'][value*='Consulta' i], "
                    "input[value*='Consulta' i], "
                    "button:has-text('Consulta'), "
                    "a:has-text('Consulta')"
                ),
            ]
            for locator in locators:
                try:
                    if await locator.count() > 0:
                        return True
                except Exception:
                    continue
        return False

    async def click_candidate(candidate: Locator) -> bool:
        try:
            await candidate.scroll_into_view_if_needed()
        except Exception:
            pass

        click_attempts = (
            {"timeout": 5000},
            {"timeout": 5000, "force": True},
        )
        for kwargs in click_attempts:
            try:
                await candidate.click(**kwargs)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                return True
            except Exception:
                continue

        try:
            handle = await candidate.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                return True
        except Exception:
            pass

        return False

    async def click_first_match(locator: Locator) -> bool:
        count = await locator.count()
        visibles = []
        ocultos = []
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

    max_attempts = 3
    min_similarity = 0.90

    for attempt in range(1, max_attempts + 1):
        contexts = await build_contexts()

        exact_locators = []
        for ctx in contexts:
            exact_locators.extend(
                [
                    ctx.get_by_role("button", name=target_text, exact=True),
                    ctx.get_by_role("link", name=target_text, exact=True),
                ]
            )
        for locator in exact_locators:
            if await click_first_match(locator):
                return True

        insensitive_pattern = re.compile(rf"^{re.escape(target_text)}$", re.I)
        ci_locators = []
        for ctx in contexts:
            ci_locators.extend(
                [
                    ctx.get_by_role("button", name=insensitive_pattern),
                    ctx.get_by_role("link", name=insensitive_pattern),
                ]
            )
        for locator in ci_locators:
            if await click_first_match(locator):
                return True

        candidate_locators = []
        for ctx in contexts:
            candidate_locators.extend(
                [
                    ctx.get_by_role("button"),
                    ctx.get_by_role("link"),
                    ctx.locator(
                        "input[type='button'], input[role='button'], input[value], "
                        "[role='button'], button, a, [onclick]"
                    ),
                ]
            )
        candidates = []

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
                    for attr in ("aria-label", "title", "value", "name"):
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
                if target_is_digits and target_norm and target_norm in candidate_norm:
                    exact_normalized = True
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
            if await has_consultas_action(contexts):
                return True

            if attempt < max_attempts:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                await asyncio.sleep(0.6 * attempt)
                continue
            return False

        candidates.sort(
            key=lambda item: (
                1 if item["exact_normalized"] else 0,
                item["score"],
                1 if item["is_visible"] else 0,
            ),
            reverse=True,
        )

        if target_is_digits and len(candidates) == 1:
            if await click_candidate(candidates[0]["candidate"]):
                return True

        for item in candidates:
            is_exact_norm = bool(item["exact_normalized"])
            score = float(item["score"])
            if not is_exact_norm and score < min_similarity:
                break

            if await click_candidate(item["candidate"]):
                return True

        if attempt < max_attempts:
            await asyncio.sleep(0.4 * attempt)
            continue

    return False


async def click_consultas_action(page: Page) -> bool:
    consultas_pattern = re.compile(r"Consulta(?:s)?", re.I)
    contexts = [page]
    try:
        for frame in page.frames:
            if frame != page.main_frame:
                contexts.append(frame)
    except Exception:
        pass

    for ctx in contexts:
        locators = [
            ctx.get_by_role("button", name=consultas_pattern),
            ctx.get_by_role("link", name=consultas_pattern),
            ctx.locator(
                "input[type='button'][value*='Consulta' i], "
                "input[value*='Consulta' i], "
                "button:has-text('Consulta'), "
                "a:has-text('Consulta')"
            ),
        ]
        for locator in locators:
            try:
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                return True
            except Exception:
                continue
    return False


async def try_select_cuit_from_select(page: Page, cuit_value: str) -> bool:
    cuit_digits = re.sub(r"\D", "", cuit_value or "")
    if not cuit_digits:
        return False

    contexts = [page]
    try:
        for frame in page.frames:
            if frame != page.main_frame:
                contexts.append(frame)
    except Exception:
        pass

    for ctx in contexts:
        try:
            selects = ctx.locator("select")
            select_count = await selects.count()
        except Exception:
            continue

        for idx in range(select_count):
            select = selects.nth(idx)
            try:
                options = select.locator("option")
                option_count = await options.count()
            except Exception:
                continue

            for jdx in range(option_count):
                option = options.nth(jdx)
                try:
                    value = (await option.get_attribute("value")) or ""
                    label = (await option.text_content()) or ""
                    if re.sub(r"\D", "", value) == cuit_digits or re.sub(r"\D", "", label) == cuit_digits:
                        await select.select_option(value if value else label)
                        return True
                except Exception:
                    continue

    return False


# --- Robust representado selection helpers ---

async def leer_representado_desde_encabezado(page: Page) -> tuple:
    """
    Lee el CUIT y nombre del representado desde la tabla de encabezado.
    Busca la fila 'Representando a' en la tabla con id='encabezado_usuario'.
    Returns: (cuit, nombre) o ("", "") si no se encuentra.
    """
    try:
        table = page.locator("table#encabezado_usuario")
        if await table.count() == 0:
            table = page.locator("table[id*='encabezado']")
        if await table.count() == 0:
            return "", ""

        rows = table.locator("tr")
        count = await rows.count()
        for i in range(count):
            row_text = (await rows.nth(i).inner_text()) or ""
            if "representando" in row_text.lower():
                lines = row_text.split("\n")
                for line in lines:
                    match = re.search(r"(\d{11})\s*-\s*(.*)", line.strip())
                    if match:
                        return match.group(1), match.group(2).strip()
                match = re.search(r"(\d{11})", row_text)
                if match:
                    return match.group(1), ""
        return "", ""
    except Exception:
        return "", ""


async def verificar_seleccion_encabezado(
    page: Page,
    cuit_esperado: str = "",
    nombre_esperado: str = ""
) -> bool:
    """
    Verifica que el representado seleccionado coincida con el CUIT y/o nombre esperado
    leyendo la tabla de encabezado 'Representando a'.
    """
    cuit, nombre = await leer_representado_desde_encabezado(page)
    if not cuit and not nombre:
        return False

    if cuit_esperado:
        cuit_digits_expected = re.sub(r"\D", "", cuit_esperado)
        cuit_digits_found = re.sub(r"\D", "", cuit)
        if cuit_digits_expected and cuit_digits_found == cuit_digits_expected:
            return True

    if nombre_esperado and nombre:
        norm_expected = normalize_name_for_match(nombre_esperado)
        norm_found = normalize_name_for_match(nombre)
        if norm_expected and norm_found == norm_expected:
            return True
        score = similarity_score(nombre_esperado, nombre)
        if score >= 0.90:
            return True

    return False


async def volver_a_seleccion_contribuyentes(page: Page, debug=None) -> bool:
    """
    Vuelve a la pantalla de selección de contribuyentes
    (https://fe.afip.gob.ar/rcel/jsp/index_bis.jsp).
    """
    try:
        volver_patterns = [
            re.compile(r"Volver", re.I),
            re.compile(r"Seleccionar\s+otro", re.I),
            re.compile(r"Cambiar\s+contribuyente", re.I),
            re.compile(r"Seleccionar\s+contribuyente", re.I),
            re.compile(r"Cambiar", re.I),
        ]
        for pattern in volver_patterns:
            for role in ("button", "link"):
                try:
                    locator = page.get_by_role(role, name=pattern)
                    if await locator.count() > 0:
                        await locator.first.click(timeout=5000)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue

            try:
                locator = page.locator(f"input[type='button'][value*='{pattern.pattern}' i]")
                if await locator.count() > 0:
                    await locator.first.click(timeout=5000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    return True
            except Exception:
                continue

        if debug:
            debug.log("No se encontró botón Volver, navegando directamente")
        await page.goto(
            "https://fe.afip.gob.ar/rcel/jsp/index_bis.jsp",
            wait_until="domcontentloaded",
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        return True
    except Exception:
        if debug:
            debug.log("Error en volver_a_seleccion_contribuyentes")
        return False


async def iterar_contribuyentes(
    page: Page,
    nombre_busqueda: str,
    cuit_busqueda: str = "",
    debug=None
) -> bool:
    """
    Itera sobre todos los botones btn_empresa en la pantalla de selección,
    prueba cada uno y verifica el CUIT/nombre en el encabezado tras cada selección.
    Si no coincide, vuelve a la pantalla de selección y prueba el siguiente.
    """
    target_norm = normalize_name_for_match(nombre_busqueda) if nombre_busqueda else ""
    cuit_digits = re.sub(r"\D", "", cuit_busqueda) if cuit_busqueda else ""

    btn_locator = page.locator(
        "input.btn_empresa, button.btn_empresa, [class*='btn_empresa'], "
        "input[type='button'][class*='ui-button']"
    )
    count = await btn_locator.count()

    if count == 0:
        if debug:
            debug.log("No se encontraron botones de contribuyentes para iterar")
        return False

    if debug:
        debug.log(f"Iterando sobre {count} contribuyentes")

    for i in range(count):
        btn = btn_locator.nth(i)
        try:
            value = (await btn.get_attribute("value")) or ""
            text = (await btn.text_content()) or ""
            btn_text = normalize_text(value or text)
        except Exception:
            continue

        if not btn_text:
            continue

        if debug:
            debug.log(f"Probando [{i+1}/{count}]: {btn_text}")

        try:
            await btn.click(timeout=5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        except Exception as e:
            if debug:
                debug.log(f"Error click [{i+1}]: {e}")
            continue

        # Read header to verify
        cuit_header, nombre_header = await leer_representado_desde_encabezado(page)
        if debug:
            debug.log(f"Header: CUIT={cuit_header}, Nombre={nombre_header}")

        # Verify by CUIT
        if cuit_digits:
            header_digits = re.sub(r"\D", "", cuit_header)
            if header_digits and header_digits == cuit_digits:
                if debug:
                    debug.log(f"¡CUIT coincide! {cuit_header}")
                return True

        # Verify by name
        if not cuit_digits and target_norm and nombre_header:
            header_norm = normalize_name_for_match(nombre_header)
            if header_norm and header_norm == target_norm:
                if debug:
                    debug.log(f"¡Nombre coincide! {nombre_header}")
                return True

        # No match, go back to selection
        if debug:
            debug.log("No coincide, volviendo a selección...")
        if not await volver_a_seleccion_contribuyentes(page, debug):
            if debug:
                debug.log("Error al volver, abortando iteración")
            return False

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

    if debug:
        debug.log("No se encontró el contribuyente buscado entre todos los disponibles")
    return False


async def extraer_y_procesar_pdf(
    pdf_data: bytes,
    incluir_b64: bool = False,
    ) -> dict:
    """
    Extrae y procesa texto desde un archivo PDF.
    """
    info_factura = {
        "CUIT del Emisor": "",
        "COD": "",
        "Punto de Venta": "",
        "Número de Factura": "",
        "Domicilio Vendedor": "",
        "Condición Vendedor": "",
        "Fecha": "",
        "Desde": "",
        "Hasta": "",
        "CUIT del Receptor": "",
        "Nombre del Receptor": "",
        "Condición Comprador": "",
        "Domicilio Cliente": "",
        "Importe Neto Gravado": "",
        "IVA 0%": "",
        "IVA 2,5%": "",
        "IVA 5%": "",
        "IVA 10,5%": "",
        "IVA 21%": "",
        "IVA 27%": "",
        "Otros Tributos": "",
        "Importe Total": "",
        "CAE": "",
        "Vencimiento CAE": "",
        "AUX": ""
    }
    
    regex_patterns = {
        "COD": r"(?:CÓD\.|COD\.)\s*(\d+)",
        "CUIT del Emisor": r"CUIT:\s*(\d+)",
        "Punto de Venta": r"Punto de Venta:\s*(\d+)",
        "Número de Factura": r"Comp\. Nro:\s*(\d+)",
        "Domicilio Vendedor": r"Domicilio Comercial:\s*([A-Za-z0-9\s:.,-]+?)\s*CUIT:",
        "Condición Vendedor": r"Condición frente al IVA:\s*([A-Za-z0-9\s:.,-]+?)\s*Fecha",
        "Fecha": r"Fecha de Emisión:\s*(\d{2}/\d{2}/\d{4})",
        "Desde": r"Desde:\s*(\d{2}/\d{2}/\d{4})",
        "Hasta": r"Hasta:\s*(\d{2}/\d{2}/\d{4})",
        "CUIT del Receptor": r"([0-9]{8,11})\sApellido y Nombre",
        "Nombre del Receptor": r"Apellido y Nombre \/ Razón Social:\s*(.+\n)",
        "Condición Comprador": r"Condición frente al IVA:\s*([A-Za-z0-9\s:.,-]+?)\s*Domicilio",
        "Domicilio Cliente": r"Domicilio:\s*(.+)",
        "Importe Neto Gravado": r"Importe Neto Gravado:\s*\$ ([\d,]+)",
        "IVA 0%": r"IVA\s*0%:\s*\$ ([\d,]+)",
        "IVA 2,5%": r"IVA\s*2,5%:\s*\$ ([\d,]+)",
        "IVA 5%": r"IVA\s*5%:\s*\$ ([\d,]+)",
        "IVA 10,5%": r"IVA\s*10,5%:\s*\$ ([\d,]+)",
        "IVA 21%": r"IVA\s*21%:\s*\$ ([\d,]+)",
        "IVA 27%": r"IVA\s*27%:\s*\$ ([\d,]+)",
        "Otros Tributos": r"Otros Tributos:\s*\$ ([\d,]+)",
        "Importe Total": r"Importe Total:\s*\$ ([\d,]+)",
        "CAE": r"CAE N°:\s*(\d+)",
        "Vencimiento CAE": r"Fecha de Vto\. de CAE:\s*([\d/]+)"
    }

    try:
        # Use simple synchronous code since this is CPU bound, or leave as is if not blocking much
        loop = asyncio.get_running_loop()
        def parse_pdf():
            with pdfplumber.open(BytesIO(pdf_data)) as pdf:
                if not pdf.pages:
                    raise ValueError("El PDF no contiene páginas.")
                return pdf.pages[0].extract_text()
        
        texto = await loop.run_in_executor(None, parse_pdf)

        if texto:
            for key, pattern in regex_patterns.items():
                match = re.search(pattern, texto)
                info_factura[key] = match.group(1) if match else ""
                
            #si el Domicilio Cliente no se encuentra en el PDF, se intenta extraer la segunda coincidencia luego de "Domicilio Comercial:"
            if info_factura["Domicilio Cliente"] == "":
                match = re.search(r"Domicilio Comercial:\s*([A-Za-z0-9\s:.,-]+?)\s*CUIT:", texto)
                if match:
                    match = re.search(r"Comercial:\s*(.+)", texto[match.end():])
                    info_factura["Domicilio Cliente"] = match.group(1) if match else ""


            try:
                info_factura["AUX"] = f"{info_factura['CUIT del Emisor']}-{info_factura['COD']}-{info_factura['Punto de Venta']}-{info_factura['Número de Factura']}"
            except:
                from datetime import datetime
                info_factura["AUX"] = f"AUX_NO_DISPONIBLE{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            if incluir_b64:
                # agregar B64 del PDF completo
                info_factura["B64"] = base64.b64encode(pdf_data).decode("utf-8")

    except Exception as e:
        raise ValueError(f"Error procesando el PDF: {e}")

    return info_factura

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


async def bot_rcel_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    desde: str,
    hasta: str,
    b64_PDF_data: bool = False,
    proxy_request: bool = False,
    **kwargs
    ) -> tuple[list, list, bool]:
    # Map runner args to bot args
    desde_fac = desde
    hasta_fac = hasta
    cuit_inicio_sesion = cuit_representante
    contrasena = clave_representante
    nombre_rcel = cuit_representado # Runner passes Name/CUIT here. Bot expects Name.

    '''
    Bot para descargas las facturas desde el servicio de Comprobantes en Línea (RECEL) de AFIP
    '''
    
    error_logs = []
    facturas = []
    success = True
    
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot RCEL Logger")
    
    proxy = get_proxy_config() if proxy_request==True else None
    
    # Use debug directory for downloads
    base_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True,
                                                       proxy=proxy)
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
                    str(cuit_inicio_sesion),
                    contrasena,
                    error_logs=error_logs,
                )
                await debug.capture(page, "post_login")
                if not login_result.success:
                    debug.log(f"Login failed: {error_logs}")
                    await context.close()
                    await browser.close()
                    return facturas, error_logs, False

                page1 = await open_arca_service(
                    page,
                    service_name="COMPROBANTES EN LÍNEA",
                    service_matchers=(
                        re.compile(r"COMPROBANTES\s+EN\s+L[ÍI]NEA", re.I),
                        "COMPROBANTES EN LÍNEA Sistema de emisión de comprobantes electrónicos",
                    ),
                    error_logs=error_logs,
                    service_unavailable_message="No se encuentra habilitado el servicio de COMPROBANTES EN LÍNEA",
                )
                if page1 is None:
                    debug.log("No se pudo abrir servicio")
                    await context.close()
                    await browser.close()
                    return facturas, error_logs, False
                if page1 is not page and not page.is_closed():
                    await page.close()
                
                await debug.capture(page1, "servicio_abierto")

                # --- Robust representado selection with verification ---
                cuit_para_verificar = re.sub(r"\D", "", cuit_representado) if re.search(r"\d{6,}", cuit_representado) else ""

                representado_seleccionado = await seleccionar_representado_por_denominacion(
                    page1,
                    nombre_rcel,
                )

                if representado_seleccionado:
                    await debug.capture(page1, "representado_clickeado")
                    if not await verificar_seleccion_encabezado(page1, cuit_para_verificar, nombre_rcel):
                        debug.log("Verificación falló tras selección por denominación, reintentando...")
                        await volver_a_seleccion_contribuyentes(page1, debug)
                        representado_seleccionado = False
                else:
                    await debug.capture(page1, "seleccion_denominacion_fallo")

                if not representado_seleccionado:
                    representado_seleccionado = await try_select_cuit_from_select(page1, nombre_rcel)
                    if representado_seleccionado:
                        await debug.capture(page1, "representado_select")
                        if not await verificar_seleccion_encabezado(page1, cuit_para_verificar, nombre_rcel):
                            debug.log("Verificación falló tras selección por dropdown, reintentando...")
                            await volver_a_seleccion_contribuyentes(page1, debug)
                            representado_seleccionado = False

                if not representado_seleccionado:
                    debug.log("Iniciando iteración de contribuyentes...")
                    representado_seleccionado = await iterar_contribuyentes(
                        page1, nombre_rcel, cuit_para_verificar, debug
                    )
                    await debug.capture(page1, "iteracion_contribuyentes")

                if not representado_seleccionado:
                    error_logs.append(
                        f"Error al seleccionar representado {nombre_rcel}: no se encontró en la lista"
                    )
                    await debug.capture(page1, "error_seleccion_representado")
                    await context.close()
                    await browser.close()
                    return facturas, error_logs, False

                await debug.capture(page1, "representado_verificado")

                if not await click_consultas_action(page1):
                    error_logs.append("No se pudo acceder al menu Consultas")
                    await debug.capture(page1, "error_menu_consultas")
                    await context.close()
                    await browser.close()
                    return facturas, error_logs, False

                await page1.get_by_label("Desde").fill(f"{desde_fac}")
                await page1.get_by_label("Hasta").fill(f"{hasta_fac}")
                await debug.capture(page1, "formulario_busqueda")
                await page1.get_by_role("button", name="Buscar").click()

                await page1.wait_for_load_state("networkidle")
                await debug.capture(page1, "resultados_busqueda")
            
                # Obtener todos los elementos de facturas
                elementos_facturas = await page1.query_selector_all("[title='Ver el documento original para imprimir o reimprimir']")
                debug.log(f"Encontradas {len(elementos_facturas)} facturas para descargar")
            
                # Descargar todos los PDFs primero (secuencialmente para evitar conflictos en Playwright)
                pdfs_descargados = []
                for i, elemento in enumerate(elementos_facturas):
                    try:
                        async with page1.expect_download() as download_info:
                            await elemento.click()
                        download = await download_info.value
                        nombre = download.suggested_filename
                        path_de_descarga = base_dir / nombre
                        await download.save_as(path=path_de_descarga)
                        pdfs_descargados.append((path_de_descarga, nombre))
                        if i % 5 == 0:
                            debug.log(f"Descargada factura {i+1}/{len(elementos_facturas)}")
                    except Exception as e:
                        error_logs.append(f"Error al descargar factura {i}: {e}")
                        debug.log(f"Error descarga factura {i}: {e}")
            
                # Crear semáforo para limitar a 30 workers concurrentes en el procesamiento
                semaphore = asyncio.Semaphore(30)
            
                async def procesar_pdf(path_de_descarga, nombre):
                    """Procesa un PDF descargado con control de concurrencia"""
                    async with semaphore:
                        try:
                            # transformar el PDF a bytes
                            with open(path_de_descarga, "rb") as f:
                                pdf_data = f.read()
                                pdf_extraction = await extraer_y_procesar_pdf(pdf_data, b64_PDF_data)
                                if pdf_extraction:
                                    return pdf_extraction
                        except Exception as e:
                            errMsg = f"Error al extraer la información del PDF {nombre}: {e}"
                            error_logs.append(errMsg)
                            debug.log(errMsg)
                            return None
            
                # Procesar PDFs con máximo 30 workers concurrentes
                tareas = [procesar_pdf(path, nombre) for path, nombre in pdfs_descargados]
                resultados = await asyncio.gather(*tareas, return_exceptions=True)
            
                # Filtrar resultados válidos
                for resultado in resultados:
                    if resultado and not isinstance(resultado, Exception):
                        facturas.append(resultado)
                
                debug.log(f"Procesadas {len(facturas)} facturas exitosamente")
                   
            except Exception as e:
                error_logs.append(f"Error durante el proceso: {e}")
                debug.log(f"Excepcion principal: {e}")
                # success = False
        
                await context.close()
                await browser.close()
        
                return facturas, error_logs, False
 
            await context.close()
            await browser.close()
        
            return facturas, error_logs, success
    except Exception as e:
        debug.log(f"Critical error: {e}")
        error_logs.append(f"Critical error: {e}")
        return facturas, error_logs, False

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    def _fmt_period(val: str) -> str:
        """Convert YYYYMMDD or YYYYMM to DD/MM/YYYY if needed."""
        val = val.strip()
        # YYYYMMDD (8 digits)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", val)
        if m:
            return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
        # YYYYMM (6 digits)
        m = re.match(r"^(\d{4})(\d{2})$", val)
        if m:
            return f"01/{m.group(2)}/{m.group(1)}"
        return val

    cuit_rep = os.getenv("RCEL_CUIT_REPRESENTANTE", "")
    clave = os.getenv("RCEL_CLAVE_REPRESENTANTE", "")
    cuit_repado = os.getenv("RCEL_CUIT_REPRESENTADO", "")
    nombre = os.getenv("RCEL_NOMBRE_REPRESENTADO", cuit_repado)
    desde = _fmt_period(os.getenv("RCEL_DESDE", "01/05/2025"))
    hasta = _fmt_period(os.getenv("RCEL_HASTA", "30/06/2025"))
    
    if cuit_rep and clave:
        res = await bot_rcel_logger(
            cuit_representante=cuit_rep,
            clave_representante=clave,
            cuit_representado=cuit_repado,
            desde=desde,
            hasta=hasta,
        )
        print("Facturas encontradas:", len(res[0]))

if __name__ == "__main__":
    asyncio.run(main())
