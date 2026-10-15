# app/bot/carga_portal_iva_bot.py
"""
Bot exclusivo para la carga de TXTs (comprobantes + alícuotas) y CSVs de apertura
(DF, DF Restitución, CF, CF Restitución) en Portal IVA.
"""
import asyncio
import os
import re
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from app.bot.portal_iva_bot import (
    BOT_NAME,
    DOWNLOAD_DIR,
    PortalIvaConfig,
    RuntimeState,
    _env_flag,
    _human_pause,
    click_first,
    ensure_session_active,
    login_and_open_portal,
    maybe_click_processed,
    open_book,
    prepare_period,
    select_representado,
)
from shared.arca_login import wait_page_ready
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

LIVA_BASE = "https://liva.afip.gob.ar/liva/jsp"

# Timeout total del bot (segundos). Sobrescribible por variable de entorno.
BOT_TOTAL_TIMEOUT = int(os.getenv("PORTAL_IVA_BOT_TIMEOUT", "600"))

# Tiempos de espera por sección (segundos)
SECTION_TIMEOUT_TXT = int(os.getenv("PORTAL_IVA_SECTION_TIMEOUT_TXT", "300"))
SECTION_TIMEOUT_CSV = int(os.getenv("PORTAL_IVA_SECTION_TIMEOUT_CSV", "180"))

# Reintentos por sección independiente
SECTION_MAX_RETRIES = int(os.getenv("PORTAL_IVA_SECTION_RETRIES", "2"))
SECTION_RETRY_DELAY = int(os.getenv("PORTAL_IVA_SECTION_RETRY_DELAY", "3"))


# ---------------------------------------------------------------------------
#  Utilidades de archivo
# ---------------------------------------------------------------------------

def _url_in_liva(url: str) -> bool:
    return "liva.afip.gob.ar" in (url or "")


def _url_is_menu_presentacion(url: str) -> bool:
    return "menuPresentacion" in (url or "")


def _calcular_total_cf_csv(csv_path: str) -> Optional[str]:
    """Calcula el total de 'Credito Fiscal Computable' desde un CSV de apertura."""
    if not csv_path or not os.path.isfile(csv_path):
        return None
    try:
        total = 0.0
        with open(csv_path, encoding="utf-8-sig") as f:
            lines = f.readlines()
        if not lines:
            return None
        header = lines[0].strip().split(";")
        col_idx = next((i for i, h in enumerate(header) if "credito fiscal computable" in h.lower()), None)
        if col_idx is None:
            return None
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) > col_idx:
                val_str = parts[col_idx].strip()
                if val_str:
                    val_str = val_str.replace(".", "").replace(",", ".")
                    total += float(val_str)
        return f"{total:.2f}"
    except Exception:
        return None


def _detect_encoding(file_path: str) -> str:
    """Detecta encoding de un archivo probando utf-8-sig y latin-1."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, encoding=enc) as f:
                f.read()
            return enc
        except Exception:
            continue
    return "latin-1"


def _validar_archivo(path: str, tipo: str) -> tuple[bool, Optional[str]]:
    """Valida que el archivo exista, no esté vacío y tenga contenido mínimo.

    Retorna (ok, mensaje_error).
    """
    if not path:
        return False, f"Ruta vacía para {tipo}"
    if not os.path.isfile(path):
        return False, f"No existe el archivo {tipo}: {path}"
    try:
        size = os.path.getsize(path)
    except Exception as exc:
        return False, f"No se pudo leer el tamaño de {tipo}: {exc}"
    if size == 0:
        return False, f"Archivo vacío {tipo}: {path}"

    enc = _detect_encoding(path)
    try:
        with open(path, encoding=enc) as f:
            lines = [line for line in f if line.strip()]
    except Exception as exc:
        return False, f"No se pudo leer {tipo} con encoding {enc}: {exc}"

    if not lines:
        return False, f"Archivo {tipo} sin contenido legible"
    if tipo.startswith("CSV") and len(lines) <= 1:
        return False, f"CSV {tipo} solo contiene encabezado"
    return True, None


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except Exception:
        pass


DiagnosticCallback = Optional[Callable[[Page, str], Awaitable[None]]]


async def _maybe_diagnostic(page: Page, step: str, callback: DiagnosticCallback) -> None:
    if callback and page:
        try:
            await callback(page, step)
        except Exception:
            pass


async def _run_with_retry(
    label: str,
    coro_fn: Callable[[], Awaitable[Any]],
    error_logs: list[str],
    warning_logs: list[str],
    max_retries: int = SECTION_MAX_RETRIES,
    retry_delay: int = SECTION_RETRY_DELAY,
) -> Any:
    """Ejecuta una coroutine con reintentos. Registra warnings en lugar de errores
    si el último intento también falla."""
    last_exc: Optional[Exception] = None
    for intento in range(1, max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            msg = f"{label} intento {intento}/{max_retries} falló: {exc}"
            if intento < max_retries:
                warning_logs.append(msg)
                await asyncio.sleep(retry_delay)
            else:
                error_logs.append(msg)
    raise last_exc or RuntimeError(f"{label} falló tras {max_retries} intentos")


# ---------------------------------------------------------------------------
#  Funciones de navegación e importación específicas de carga
# ---------------------------------------------------------------------------

async def _handle_credito_fiscal_modal(page: Page, valor: Optional[str] = None) -> bool:
    """Rellena el modal de Crédito Fiscal de Prorrateo si está presente."""
    input_field = page.locator("#credFiscProrrateo")
    if await input_field.count() == 0:
        return False

    if valor is not None:
        await input_field.first.fill(valor)
        await _human_pause(page, 1_500, 500)
        return True

    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        body_text = ""
    numeros = re.findall(r'(?:\$?\s*)([\d]{1,3}(?:\.\d{3})*(?:,\d{2})?)', body_text)
    if not numeros:
        numeros = re.findall(r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)', body_text)
    if numeros:
        raw = numeros[-1]
        limpio = raw.replace(".", "").replace(",", ".")
        await input_field.first.fill(limpio)
        await _human_pause(page, 1_500, 500)
        return True
    return False


# ---------------------------------------------------------------------------
#  Constantes locales para tiempos de espera
# ---------------------------------------------------------------------------

UPLOAD_TO = 60_000
SHORT = 1_500
MEDIUM = 3_000
LONG = 5_000
NAV_TO = 15_000
CLICK_TO = 10_000


async def _esperar_procesando(page: Page) -> None:
    """Espera a que desaparezca el indicador 'Procesando' (hasta 60s)."""
    try:
        procesando = page.get_by_text("Procesando", exact=False)
        if await procesando.count() > 0:
            try:
                await procesando.first.wait_for(state="visible", timeout=2_000)
                await procesando.first.wait_for(state="hidden", timeout=UPLOAD_TO)
            except Exception:
                pass
        await page.wait_for_timeout(SHORT)
    except Exception:
        pass


async def _extraer_mensaje_error_visible(page: Page) -> Optional[str]:
    """Extrae texto de mensajes de error visibles en la página."""
    selectors = [
        ".alert-danger",
        ".text-danger",
        ".bootstrap-dialog-message",
        ".modal-body",
        ".help-block",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                text = (await loc.first.inner_text()).strip()
                if text:
                    return text[:500]
        except Exception:
            continue
    return None


async def _importar_txts(
    page: Page,
    book: str,
    comprobantes_path: str,
    alicuotas_path: str,
    error_logs: list[str],
    warning_logs: list[str],
) -> None:
    """Importa TXTs (comprobantes + alícuotas) con el flujo correcto:
       upload ambos archivos → click importar 1 vez → esperar procesamiento → cerrar.
       error_logs: lista para almacenar errores/diagnósticos.
    """
    await ensure_session_active(page)

    for path, label in ((comprobantes_path, f"{book} comprobantes"), (alicuotas_path, f"{book} alicuotas")):
        ok, msg = _validar_archivo(path, label)
        if not ok:
            raise RuntimeError(msg)

    comp_label = "Archivo Ventas *" if book == "ventas" else "Archivo Compras *"
    alic_label = "Archivo IVA Ventas *" if book == "ventas" else "Archivo IVA Compras *"

    # 0) Cerrar cualquier modal o notificación previa que pueda interferir
    for locator in (page.locator(".bootstrap-dialog .close"), page.locator("#btnTareasCerrar")):
        try:
            await click_first([locator], timeout=2_000)
        except Exception:
            pass
    await page.wait_for_timeout(SHORT)

    # 0.5) Asegurar que no haya overlay bloqueando
    try:
        await page.locator(".modal-backdrop").first.wait_for(state="hidden", timeout=3_000)
    except Exception:
        pass

    # 1) Abrir menú Importar
    print(f"DIAG_CARGA: {book} abriendo menu Importar", flush=True)
    opened = await click_first([
        page.locator("#btnDropdownImportar"),
        page.get_by_role("button", name=re.compile(r"Importar", re.I)),
    ], timeout=10_000)
    if not opened:
        raise RuntimeError(f"No se encontró Importar para {book}")
    await page.wait_for_timeout(MEDIUM)

    # 2) Click "Importar Archivos"
    print(f"DIAG_CARGA: {book} click Importar Archivos", flush=True)
    clicked = await click_first([
        page.get_by_role("link", name=re.compile(r"Importar\s+(?:desde\s+)?Archivos?", re.I)),
        page.locator("#lnkImportarArchivo"),
    ], timeout=12_000)
    if not clicked:
        raise RuntimeError(f"No se encontró 'Importar Archivos' para {book}")
    await page.wait_for_timeout(MEDIUM)

    # 3) Upload AMBOS archivos usando IDs específicos (#archivo, #archivoIVA)
    if book == "compras":
        # En compras: seleccionar CUIT del comprador explícitamente
        try:
            cuit_options = page.locator("#cuitArchivo option")
            if await cuit_options.count() > 0:
                first_value = await cuit_options.first.get_attribute("value")
                if first_value:
                    await page.locator("#cuitArchivo").select_option(first_value, timeout=5_000)
        except Exception as exc:
            warning_logs.append(f"No se pudo seleccionar CUIT comprador: {exc}")
        await page.wait_for_timeout(SHORT)

    await page.get_by_label(comp_label).set_input_files(comprobantes_path, timeout=15_000)
    await page.get_by_label(alic_label).set_input_files(alicuotas_path, timeout=15_000)
    print(f"DIAG_CARGA: {book} archivos subidos, clickeando importar", flush=True)

    await page.wait_for_timeout(SHORT)

    # 4) Click importar UNA SOLA vez usando evaluate (como bot de referencia)
    await page.locator("#btnImportarArchivosImportar").evaluate("el => el.click()")
    await page.wait_for_timeout(MEDIUM)

    # 5) Esperar procesamiento (indicador "Procesando" → oculto → "Procesada")
    print(f"DIAG_CARGA: {book} esperando procesamiento", flush=True)
    await _esperar_procesando(page)

    # 6) Esperar "Procesada" visible y cerrar (como bot de referencia)
    try:
        await page.get_by_text("Procesada", exact=False).first.wait_for(
            state="visible", timeout=UPLOAD_TO
        )
        print(f"DIAG_CARGA: {book} Procesada detectada", flush=True)
    except Exception as exc:
        error_text = await _extraer_mensaje_error_visible(page)
        diag = f"; mensaje en página: {error_text}" if error_text else ""
        error_logs.append(
            f"Error detectando 'Procesada' para {book}: {exc}{diag}"
        )
        # Intentar diagnosticar el estado de la página
        try:
            url_actual = page.url
            print(f"DIAG_CARGA: {book} error Procesada url={url_actual}", flush=True)
        except Exception:
            pass
        raise RuntimeError(
            f"No se detectó 'Procesada' tras importación de {book}{diag}"
        ) from exc

    try:
        await click_first([
            page.get_by_text("Procesada", exact=False),
            page.locator(".bootstrap-dialog .close"),
        ], timeout=LONG)
    except Exception:
        pass

    await page.wait_for_timeout(2_000)
    await click_first([page.locator("#btnTareasCerrar")], timeout=LONG)
    await page.wait_for_timeout(MEDIUM)


async def _eliminar_todo(page: Page) -> bool:
    """Elimina todos los registros del libro actual (versión local)."""
    await ensure_session_active(page)
    await page.wait_for_timeout(MEDIUM)
    deleted = await click_first([
        page.get_by_role("button", name=re.compile(r"Eliminar\s+Todos?\.\.\.?", re.I)),
        page.locator("#btnEliminarTodo"),
    ], timeout=8_000)
    if not deleted:
        return False
    await page.wait_for_timeout(SHORT)
    confirmed = await click_first([
        page.locator(".bootstrap-dialog .btn-danger").filter(has_text=re.compile(r"Eliminar", re.I)),
        page.locator(".modal.in .btn-danger"),
        page.get_by_role("button", name=re.compile(r"Eliminar", re.I)),
    ], timeout=LONG)
    if not confirmed:
        await click_first([page.locator(".bootstrap-dialog .close")], timeout=MEDIUM)
        return False
    await page.wait_for_timeout(MEDIUM)
    return True


async def _abrir_libro(page: Page, book: str) -> None:
    """Versión local simplificada de open_book (similar al bot de referencia)."""
    href = "verVentas.do" if book == "ventas" else "verCompras.do"
    label = "Libro Ventas" if book == "ventas" else "Libro Compras"

    await ensure_session_active(page)
    await _esperar_procesando(page)

    clicked = await click_first([
        page.locator(f"a[href*='{href}']"),
        page.get_by_role("link", name=re.compile(rf"{label}\s+Registr", re.I)),
        page.get_by_role("link", name=re.compile(label, re.I)),
        page.locator(f"#{'btnLibroVentas' if book == 'ventas' else 'btnLibroCompras'}"),
    ], timeout=NAV_TO)

    if not clicked:
        # Fallback: navegar a menuPresentacion primero, luego intentar open_book
        await _return_to_home(page)
        await _esperar_procesando(page)
        await open_book(page, book)
        return

    await page.wait_for_timeout(MEDIUM)
    await _esperar_procesando(page)


async def _ir_a_editar_credito_fiscal_en_compras(page: Page, csv_path: str = "") -> bool:
    """Navega a la sección de Crédito Fiscal Computable Global en Compras y la completa.

    Primero intenta navegación directa a verComprasCreditoFiscalComputableGlobal.do.
    Si falla, busca el link en la página de compras o en menuInfoAdicional.
    """
    valor = _calcular_total_cf_csv(csv_path) if csv_path else None

    # 1) Intentar navegación directa a la página CF Computable Global
    await _return_to_home(page)
    await _esperar_procesando(page)
    cf_url = f"{LIVA_BASE}/verComprasCreditoFiscalComputableGlobal.do"
    try:
        await page.goto(cf_url, wait_until="domcontentloaded", timeout=15_000)
        await wait_page_ready(page, timeout_ms=10_000)
        await _esperar_procesando(page)
        if await page.locator("#credFiscProrrateo").count() > 0:
            manejado = await _handle_credito_fiscal_modal(page, valor=valor)
            if manejado:
                await click_first([
                    page.locator("button[type='submit']"),
                    page.get_by_role("button", name=re.compile(r"Guardar", re.I)),
                ], timeout=8_000)
                await _human_pause(page, 2_000, 700)
                await _esperar_procesando(page)
                return True
    except Exception:
        pass

    # 2) Buscar en libro compras
    await open_book(page, "compras")
    await _esperar_procesando(page)
    clicked = await click_first([
        page.locator("a, button, span, div").filter(has_text=re.compile(r"Crédito\s+Fiscal\s+Computable\s+Global", re.I)),
        page.get_by_role("link", name=re.compile(r"Crédito\s+Fiscal\s+Computable\s+Global", re.I)),
        page.locator("a[href*='verComprasCreditoFiscalComputableGlobal']"),
        page.get_by_text(re.compile(r"CF\s+Computable\s+Global|Crédito\s+Fiscal\s+Computable", re.I), exact=False),
    ], timeout=8_000)

    # 3) Fallback: buscar en menuInfoAdicional
    if not clicked:
        await _return_to_home(page)
        await _esperar_procesando(page)
        try:
            if await page.locator("#btnAjustes").count() > 0:
                await page.locator("#btnAjustes").first.click(timeout=10_000)
                await _human_pause(page, 2_000, 700)
            if await page.locator("a[href*='menuInfoAdicional']").count() > 0:
                await page.locator("a[href*='menuInfoAdicional']").first.click(timeout=10_000)
                await _human_pause(page, 2_000, 700)
            else:
                await page.goto(f"{LIVA_BASE}/menuInfoAdicional.jsp", wait_until="domcontentloaded", timeout=15_000)
                await wait_page_ready(page, timeout_ms=10_000)
        except Exception:
            await page.goto(f"{LIVA_BASE}/menuInfoAdicional.jsp", wait_until="domcontentloaded", timeout=15_000)
            await wait_page_ready(page, timeout_ms=10_000)
        await _esperar_procesando(page)
        clicked = await click_first([
            page.locator("a, button, span, div").filter(has_text=re.compile(r"Crédito\s+Fiscal\s+Computable\s+Global", re.I)),
            page.get_by_role("link", name=re.compile(r"Crédito\s+Fiscal\s+Computable\s+Global", re.I)),
            page.locator("a[href*='verComprasCreditoFiscalComputableGlobal']"),
            page.get_by_text(re.compile(r"Crédito\s+Fiscal", re.I), exact=False),
            page.locator("a.btn-primary").filter(has_text=re.compile(r"Crédito\s+Fiscal", re.I)),
            page.locator("a[href*='creditoFiscal']"),
        ], timeout=8_000)

    if not clicked:
        return False

    await _human_pause(page, 2_000, 700)
    await _esperar_procesando(page)
    manejado = await _handle_credito_fiscal_modal(page, valor=valor)

    await click_first([
        page.locator("button[type='submit']"),
        page.get_by_role("button", name=re.compile(r"Guardar", re.I)),
    ], timeout=8_000)
    await _human_pause(page, 2_000, 700)
    await _esperar_procesando(page)

    # Volver a menuPresentacion para estado consistente
    try:
        await page.goto(f"{LIVA_BASE}/menuPresentacion.do", wait_until="domcontentloaded", timeout=15_000)
        await wait_page_ready(page, timeout_ms=10_000)
        await _esperar_procesando(page)
    except Exception:
        pass
    return manejado


async def _return_to_home(page: Page) -> None:
    """Vuelve a menuPresentacion.do navegando directamente por URL.
    NO usa clicks en elementos UI para evitar diálogos de confirmación
    que descarten la importación realizada."""
    await ensure_session_active(page)
    await _esperar_procesando(page)

    current = getattr(page, "url", "")
    if _url_is_menu_presentacion(current):
        return

    # Solo navegación directa por URL (evita diálogos de confirmación)
    try:
        await page.goto(
            f"{LIVA_BASE}/menuPresentacion.do",
            wait_until="domcontentloaded", timeout=NAV_TO,
        )
        await wait_page_ready(page, timeout_ms=10_000)
        await _esperar_procesando(page)
        if _url_is_menu_presentacion(getattr(page, "url", "")):
            return
    except Exception:
        pass

    # Fallback: intentar clicks UI si la navegación directa no funcionó
    try:
        for locators in (
            [page.locator("a[href*='menuPresentacion.do']")],
            [page.get_by_role("link", name=re.compile(r"Inicio", re.I))],
            [page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I))],
            [page.get_by_role("button", name=re.compile(r"Volver", re.I)), page.locator("#btnVolver")],
        ):
            if await click_first(locators, timeout=LONG):
                await _human_pause(page, 2_000, 600)
                await _esperar_procesando(page)
                if _url_is_menu_presentacion(getattr(page, "url", "")):
                    return
    except Exception:
        pass


async def _navegar_a_info_adicional(page: Page) -> None:
    """Navega a la sección de Información Adicional dentro de LIVA."""
    cur = getattr(page, "url", "")
    if "menuInfoAdicional" in cur:
        return

    await ensure_session_active(page)
    try:
        if await page.locator("#btnAjustes").count() > 0:
            await page.locator("#btnAjustes").first.click(timeout=10_000)
            await _human_pause(page, 2_000, 700)
        if await page.locator("a[href*='menuInfoAdicional']").count() > 0:
            await page.locator("a[href*='menuInfoAdicional']").first.click(timeout=10_000)
            await _human_pause(page, 2_000, 700)
        else:
            await page.goto(f"{LIVA_BASE}/menuInfoAdicional.jsp", wait_until="domcontentloaded", timeout=15_000)
            await wait_page_ready(page, timeout_ms=10_000)
    except Exception:
        await page.goto(f"{LIVA_BASE}/menuInfoAdicional.jsp", wait_until="domcontentloaded", timeout=15_000)
        await wait_page_ready(page, timeout_ms=10_000)
    await _human_pause(page, 2_000, 700)


async def cargar_apertura_csv(page: Page, seccion: str, csv_path: str) -> str:
    """Carga un archivo CSV de apertura (CF, CF Restitución, DF, DF Restitución).

    Retorna: 'ok', 'sin_archivo', 'sin_movimientos', o lanza RuntimeError.
    """
    if not csv_path or not os.path.isfile(csv_path):
        return "sin_archivo"
    if os.path.getsize(csv_path) == 0:
        return "sin_movimientos"
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            non_empty_lines = [line for line in f if line.strip()]
        if len(non_empty_lines) <= 1:
            return "sin_movimientos"
    except Exception:
        pass

    links_map = {
        "CF": re.compile(r"Crédito\s+Fiscal(?!\s+Comp)", re.I),
        "CF Restitucion": re.compile(r"Restituci[oó]n.*Crédito\s+Fiscal", re.I),
        "DF": re.compile(r"Débito\s+Fiscal(?!\s+Comp)", re.I),
        "DF Restitucion": re.compile(r"Restituci[oó]n.*Débito\s+Fiscal", re.I),
    }
    link_pattern = links_map.get(seccion)
    href_map = {
        "CF": "d=c", "CF Restitucion": "d=rc",
        "DF": "d=d", "DF Restitucion": "d=rd",
    }

    await _navegar_a_info_adicional(page)

    clicked = await click_first([
        page.locator(f".form-group a.btn-primary[href*='{href_map[seccion]}']"),
        page.locator("a.btn-primary").filter(has_text=link_pattern),
        page.get_by_role("link", name=link_pattern),
    ], timeout=15_000)
    if not clicked:
        raise RuntimeError(f"No se encontró link para {seccion}")
    await _human_pause(page, 2_000, 700)

    btn_importar = page.locator("#btnImportarArchivo")
    if await btn_importar.count() > 0:
        try:
            await btn_importar.first.click(timeout=10_000)
        except Exception:
            await btn_importar.first.evaluate("el => el.click()")
    else:
        await click_first([
            page.locator("button.btn-link").filter(has_text=re.compile(r"Importar", re.I)),
            page.get_by_role("button", name=re.compile(r"Importar.*CSV|Importar.*Archivo", re.I)),
        ], timeout=10_000)
    await _human_pause(page, 2_000, 700)

    file_input = page.locator("input[type='file']")
    if await file_input.count() > 0:
        await file_input.first.set_input_files(csv_path)
    await _human_pause(page, 1_500, 500)

    importar_btn = page.locator("#btnImportarArchivoImportar")
    if await importar_btn.count() > 0:
        try:
            await importar_btn.first.click(timeout=10_000)
        except Exception:
            try:
                await importar_btn.evaluate("el => el.click()")
            except Exception:
                pass

    # Esperar procesamiento robusto
    await _esperar_procesando(page)

    # Cerrar notificación "Procesada" que puede bloquear el botón Guardar
    try:
        await click_first([
            page.get_by_text("Procesada", exact=False),
            page.get_by_text("Procesando", exact=False),
        ], timeout=5_000)
    except Exception:
        pass
    await _human_pause(page, 2_000, 500)

    if seccion == "CF":
        try:
            await _handle_credito_fiscal_modal(page)
        except Exception:
            pass

    # Click Guardar para aplicar los cambios importados del CSV
    try:
        print(f"DIAG_CARGA: CSV {seccion} clickeando Guardar", flush=True)
        await click_first([
            page.locator("button[type='submit']"),
            page.get_by_role("button", name=re.compile(r"Guardar", re.I)),
        ], timeout=10_000)
        await _human_pause(page, 2_000, 700)
    except Exception as exc:
        print(f"DIAG_CARGA: CSV {seccion} Guardar falló: {exc}", flush=True)
    # Esperar a que termine el guardado antes de continuar
    await _esperar_procesando(page)

    try:
        await click_first([
            page.locator("a[href*='menuInfoAdicional']"),
            page.locator("#btnVolver"),
            page.get_by_text(re.compile(r"Volver", re.I)),
            page.locator("a[href*='menuPresentacion']"),
        ], timeout=8_000)
        await _human_pause(page, 2_000, 700)
    except Exception:
        pass

    return "ok"


async def _cargar_txt_seccion(
    page: Page,
    book: str,
    cbte_path: str,
    alic_path: str,
    error_logs: list[str],
    warning_logs: list[str],
) -> None:
    """Abre libro, elimina registros previos e importa TXTs (ventas/compras)."""
    await _abrir_libro(page, book)
    await _eliminar_todo(page)
    await _importar_txts(page, book, cbte_path, alic_path, error_logs, warning_logs)


async def _ejecutar_carga_completa(
    page: Page,
    config: "PortalIvaConfig",
    csv_paths: dict[str, str],
    diagnostic_callback: DiagnosticCallback = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str], list[str]]:
    """Ejecuta la carga completa de TXTs y CSVs de apertura sobre Portal IVA LIVA.

    Retorna (resultados_ventas, resultados_compras, resultados_aperturas, errors, warnings).
    """
    error_logs: list[str] = []
    warning_logs: list[str] = []

    await _maybe_diagnostic(page, "inicio_carga", diagnostic_callback)

    # --- Ventas TXTs ---
    resultados_ventas: dict[str, Any] = {
        "comprobantes": "no_enviado",
        "alicuotas": "no_enviado",
    }
    if config.importar_txt_ventas:
        cbte_path = config.liv_cbte or ""
        alic_path = config.liv_alicuota or ""
        cbte_ok, cbte_err = _validar_archivo(cbte_path, "LIV comprobantes")
        alic_ok, alic_err = _validar_archivo(alic_path, "LIV alicuotas")
        if not cbte_ok or not alic_ok:
            resultados_ventas["comprobantes"] = "error"
            resultados_ventas["alicuotas"] = "error"
            error_logs.append(f"Validación ventas fallida: {cbte_err or alic_err}")
        else:
            try:
                print(f"DIAG_CARGA: ventas cbte_size={os.path.getsize(cbte_path)} alic_size={os.path.getsize(alic_path)}", flush=True)
                await _run_with_retry(
                    "carga_TXT_ventas",
                    lambda: _cargar_txt_seccion(
                        page, "ventas", cbte_path, alic_path, error_logs, warning_logs
                    ),
                    error_logs,
                    warning_logs,
                )
                resultados_ventas["comprobantes"] = "ok"
                resultados_ventas["alicuotas"] = "ok"
            except Exception as e:
                resultados_ventas["comprobantes"] = "error"
                resultados_ventas["alicuotas"] = "error"
                error_logs.append(f"Error cargando TXTs ventas: {e}")
                print(f"DIAG_CARGA: ventas error={e}", flush=True)
        await _return_to_home(page)
        await _maybe_diagnostic(page, "post_ventas", diagnostic_callback)

    # --- Compras TXTs ---
    resultados_compras: dict[str, Any] = {
        "comprobantes": "no_enviado",
        "alicuotas": "no_enviado",
    }
    if config.importar_txt_compras:
        cbte_path = config.lic_cbte or ""
        alic_path = config.lic_alicuota or ""
        cbte_ok, cbte_err = _validar_archivo(cbte_path, "LIC comprobantes")
        alic_ok, alic_err = _validar_archivo(alic_path, "LIC alicuotas")
        if not cbte_ok or not alic_ok:
            resultados_compras["comprobantes"] = "error"
            resultados_compras["alicuotas"] = "error"
            error_logs.append(f"Validación compras fallida: {cbte_err or alic_err}")
        else:
            try:
                print(f"DIAG_CARGA: compras cbte_size={os.path.getsize(cbte_path)} alic_size={os.path.getsize(alic_path)}", flush=True)
                await _run_with_retry(
                    "carga_TXT_compras",
                    lambda: _cargar_txt_seccion(
                        page, "compras", cbte_path, alic_path, error_logs, warning_logs
                    ),
                    error_logs,
                    warning_logs,
                )
                resultados_compras["comprobantes"] = "ok"
                resultados_compras["alicuotas"] = "ok"
            except Exception as e:
                resultados_compras["comprobantes"] = "error"
                resultados_compras["alicuotas"] = "error"
                error_logs.append(f"Error cargando TXTs compras: {e}")
                print(f"DIAG_CARGA: compras error={e}", flush=True)
        await _return_to_home(page)
        await _maybe_diagnostic(page, "post_compras", diagnostic_callback)

    # --- Aperturas CSV ---
    # Generar CSVs cero para Restitución faltantes (LIVA los requiere aunque sea cero)
    for seccion_orig, key_orig, path_orig in [
        ("CF", "cf", csv_paths.get("cf", "")),
        ("DF", "df", csv_paths.get("df", "")),
    ]:
        if not (path_orig and os.path.isfile(path_orig)):
            continue
        rest_key = f"{key_orig}_restitucion"
        if csv_paths.get(rest_key):
            continue
        try:
            with open(path_orig, encoding="utf-8-sig") as f:
                orig_lines = [line.rstrip("\n\r") for line in f]
        except Exception:
            continue
        if not orig_lines:
            continue
        header_line = orig_lines[0]
        data_lines = [l for l in orig_lines[1:] if l.strip()]
        if not data_lines:
            continue
        numeric_cols: set[int] = set()
        for dl in data_lines[:3]:
            parts = dl.split(";")
            for i, p in enumerate(parts):
                p = p.strip().replace(".", "")
                if re.match(r'^\d+,\d{2}$', p):
                    numeric_cols.add(i)
        tmp_csv_path = DOWNLOAD_DIR / f"_{rest_key}.csv"
        tmp_csv = open(tmp_csv_path, "w", encoding="utf-8")
        tmp_csv.write(header_line + "\n")
        for dl in data_lines:
            parts = dl.split(";")
            row = []
            for i, p in enumerate(parts):
                if i in numeric_cols:
                    row.append("0,")
                else:
                    row.append(p.strip())
            tmp_csv.write(";".join(row) + "\n")
        tmp_csv.close()
        csv_paths[rest_key] = tmp_csv.name
        print(f"DIAG_CARGA: generado CSV cero para {seccion_orig} Restitucion: {tmp_csv.name} filas={len(data_lines)} num_cols={sorted(numeric_cols)}", flush=True)

    aperturas_map = [
        ("CF", "cf", csv_paths.get("cf", "")),
        ("CF Restitucion", "cf_restitucion", csv_paths.get("cf_restitucion", "")),
        ("DF", "df", csv_paths.get("df", "")),
        ("DF Restitucion", "df_restitucion", csv_paths.get("df_restitucion", "")),
    ]
    resultados_aperturas: list[dict[str, Any]] = []
    _tmp_csvs: list[str] = []

    for seccion, key, path in aperturas_map:
        if not path:
            continue
        resultado: dict[str, Any] = {
            "seccion": seccion,
            "key": key,
            "archivo": os.path.basename(path) if path else None,
        }
        if "__tmp_csv" in path.lower() or path.startswith("/tmp/"):
            _tmp_csvs.append(path)
        try:
            ok, msg = _validar_archivo(path, f"apertura {seccion}")
            if not ok:
                resultado["status"] = "sin_archivo" if "vacío" not in msg and "No existe" not in msg else "sin_movimientos"
                warning_logs.append(f"Apertura {seccion} omitida: {msg}")
            else:
                status = await _run_with_retry(
                    f"carga_CSV_{seccion}",
                    lambda: cargar_apertura_csv(page, seccion, path),
                    error_logs,
                    warning_logs,
                )
                resultado["status"] = status
                print(f"DIAG_CARGA: CSV {seccion} status={status}", flush=True)
                if status == "ok" and seccion == "CF" and config.prorrateo_global:
                    try:
                        await _ir_a_editar_credito_fiscal_en_compras(page, csv_path=path)
                    except Exception as exc:
                        warning_logs.append(f"Crédito fiscal compras no se pudo completar: {exc}")
                if status in ("ok", "error"):
                    try:
                        await _return_to_home(page)
                    except Exception:
                        pass
        except Exception as e:
            resultado["status"] = "error"
            error_logs.append(f"Error en apertura {seccion}: {e}")
            print(f"DIAG_CARGA: CSV {seccion} error={e}", flush=True)
            try:
                await _return_to_home(page)
            except Exception:
                pass
        resultados_aperturas.append(resultado)
        await _maybe_diagnostic(page, f"post_csv_{seccion}", diagnostic_callback)

    for p in _tmp_csvs:
        _safe_remove(p)

    await _maybe_diagnostic(page, "fin_carga", diagnostic_callback)
    return resultados_ventas, resultados_compras, resultados_aperturas, error_logs, warning_logs


# ---------------------------------------------------------------------------
#  Punto de entrada público
# ---------------------------------------------------------------------------

async def bot_portal_iva_carga(
    cuit_representante: str,
    clave_representante: str,
    periodo: str,
    cuit_representado: str = "",
    denominacion: str = "",
    operaciones_ng_o_e: bool = False,
    prorrateo_global: bool = False,
    prorrateo_asignacion_directa: bool = False,
    prorrateo_ambos: bool = False,
    importacion_definitiva_bienes: bool = False,
    importacion_servicios: bool = False,
    regimen_turiva: bool = False,
    bienes_usados: bool = False,
    ninguna_anteriores: bool = True,
    liv_cbte_path: str = "",
    liv_alicuota_path: str = "",
    lic_cbte_path: str = "",
    lic_alicuota_path: str = "",
    csv_cf_path: str = "",
    csv_cf_restitucion_path: str = "",
    csv_df_path: str = "",
    csv_df_restitucion_path: str = "",
    proxy_request: bool = False,
    headless: bool = True,
    diagnostic_callback: DiagnosticCallback = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str], list[str], list[dict[str, str]], bool]:
    """
    Ejecuta exclusivamente la carga de TXTs y CSVs de apertura en Portal IVA.

    Retorna:
        (resultados_ventas, resultados_compras, resultados_aperturas, errors, warnings, periodos_seleccionables, success)
    """
    error_logs: list[str] = []
    warning_logs: list[str] = []

    cuit_objetivo = (cuit_representado or cuit_representante or "").strip()

    if not (cuit_representante or "").strip():
        error_logs.append("El CUIT del representante es obligatorio")
        return {}, {}, [], error_logs, warning_logs, [], False
    if not (clave_representante or "").strip():
        error_logs.append("La clave fiscal es obligatoria")
        return {}, {}, [], error_logs, warning_logs, [], False
    if not cuit_objetivo:
        error_logs.append("El CUIT del representado es obligatorio")
        return {}, {}, [], error_logs, warning_logs, [], False
    if not (periodo or "").strip():
        error_logs.append("El periodo es obligatorio")
        return {}, {}, [], error_logs, warning_logs, [], False

    tiene_txt_ventas = bool(liv_cbte_path and liv_alicuota_path)
    tiene_txt_compras = bool(lic_cbte_path and lic_alicuota_path)
    tiene_csvs = bool(csv_cf_path or csv_cf_restitucion_path or csv_df_path or csv_df_restitucion_path)

    if not (tiene_txt_ventas or tiene_txt_compras or tiene_csvs):
        error_logs.append("Debe enviar al menos un par de TXTs (ventas/compras) o un CSV de apertura.")
        return {}, {}, [], error_logs, warning_logs, [], False

    proxy = get_proxy_config() if proxy_request else None

    config = PortalIvaConfig(
        cuit_representante=(cuit_representante or "").strip(),
        cuit_representado=cuit_objetivo,
        clave_representado=(clave_representante or "").strip(),
        periodo=(periodo or "").strip(),
        operaciones_ng_o_e=operaciones_ng_o_e,
        prorrateo_global=prorrateo_global,
        prorrateo_asignacion_directa=prorrateo_asignacion_directa,
        prorrateo_ambos=prorrateo_ambos,
        importacion_definitiva_bienes=importacion_definitiva_bienes,
        importacion_servicios=importacion_servicios,
        regimen_turiva=regimen_turiva,
        bienes_usados=bienes_usados,
        ninguna_anteriores=ninguna_anteriores,
        liv_cbte=liv_cbte_path,
        liv_alicuota=liv_alicuota_path,
        lic_cbte=lic_cbte_path,
        lic_alicuota=lic_alicuota_path,
        descarga_CSV_ventas=False,
        descarga_CSV_compras=False,
        importar_txt_ventas=tiene_txt_ventas,
        importar_txt_compras=tiene_txt_compras,
        denominacion=(denominacion or "").strip(),
        headless=headless,

        stealth_mode=_env_flag("PORTAL_IVA_STEALTH", False),
        proxy=proxy,
        output_dir=DOWNLOAD_DIR / BOT_NAME,
    )

    csv_paths: dict[str, str] = {}
    if csv_cf_path:
        csv_paths["cf"] = csv_cf_path
    if csv_cf_restitucion_path:
        csv_paths["cf_restitucion"] = csv_cf_restitucion_path
    if csv_df_path:
        csv_paths["df"] = csv_df_path
    if csv_df_restitucion_path:
        csv_paths["df_restitucion"] = csv_df_restitucion_path

    async def _ciclo_carga() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str], list[str], list[dict[str, str]], bool]:
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        state = RuntimeState()
        reached_load_phase = False
        try:
            async with limit_browser(), async_playwright() as playwright:
                browser, context, portal_page = await login_and_open_portal(playwright, config)
                await _maybe_diagnostic(portal_page, "post_login", diagnostic_callback)
                await select_representado(portal_page, config)
                await _maybe_diagnostic(portal_page, "post_representado", diagnostic_callback)

                try:
                    await prepare_period(portal_page, config, state)
                except Exception as prep_exc:
                    warning_logs.append(f"prepare_period falló (período ya configurado?): {prep_exc}")
                    print(f"DIAG_CARGA: prepare_period falló: {prep_exc}", flush=True)
                    await portal_page.goto(
                        f"{LIVA_BASE}/menuPresentacion.do",
                        wait_until="domcontentloaded", timeout=15_000,
                    )
                    await wait_page_ready(portal_page, timeout_ms=10_000)
                    await _esperar_procesando(portal_page)

                await _maybe_diagnostic(portal_page, "post_prepare_period", diagnostic_callback)

                # Se alcanzó la fase de carga: sesión + representado + período OK.
                # A partir de aquí, success es True aunque fallen los TXTs/CSVs,
                # porque el bot realizó su trabajo de preparación.
                reached_load_phase = True

                try:
                    ventas, compras, aperturas, carga_errors, carga_warnings = await _ejecutar_carga_completa(
                        portal_page, config, csv_paths, diagnostic_callback=diagnostic_callback,
                    )
                except Exception as carga_exc:
                    print(f"DIAG_CARGA: error durante la carga: {carga_exc}", flush=True)
                    error_logs.append(f"Error durante la carga: {carga_exc}")
                    ventas, compras, aperturas = {}, {}, []
                    carga_errors = []
                    carga_warnings = []

                error_logs.extend(carga_errors)
                warning_logs.extend(carga_warnings)
                success = reached_load_phase
                periodos_seleccionables = state.periodos_seleccionables or config.periodos_seleccionables
                return ventas, compras, aperturas, error_logs, warning_logs, periodos_seleccionables, success
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    MAX_RETRIES = 2
    last_exc: Optional[Exception] = None
    for intento in range(1, MAX_RETRIES + 1):
        print(f"DIAG_CARGA: intento global {intento}/{MAX_RETRIES}", flush=True)
        try:
            return await asyncio.wait_for(_ciclo_carga(), timeout=BOT_TOTAL_TIMEOUT)
        except asyncio.TimeoutError as toe:
            last_exc = toe
            error_logs.append(f"Timeout global en intento {intento}/{MAX_RETRIES}")
            print(f"DIAG_CARGA: timeout global intento {intento}", flush=True)
        except Exception as exc:
            last_exc = exc
            error_logs.append(f"Error Portal IVA Carga (intento {intento}): {exc}")
            print(f"DIAG_CARGA: error fatal intento {intento}: {exc}", flush=True)

        if intento < MAX_RETRIES:
            await asyncio.sleep(SECTION_RETRY_DELAY * 2)

    return {}, {}, [], error_logs, warning_logs, [], False
