import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from playwright_stealth import Stealth

from shared.arca_login import launch_arca_browser_context, login_arca, open_arca_service, wait_page_ready
from portal_iva_ddjj_y_libros.portal_iva_ddjj_y_libros_logger import (
    BOT_NAME,
    PORTAL_IVA_DIRECT_URL,
    PORTAL_IVA_URL_PATTERN,
    PortalIvaDdjjYLibrosConfig,
    _env_flag,
    _env_first,
    _is_blank_page,
    _portal_iva_init_url_with_key,
    download_ddjj_presentadas,
    download_libro_iva_xml,
    select_representado,
)


DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)


class CleanRuntime:
    def __init__(self) -> None:
        self.descargas_dir = DOWNLOAD_DIR

    def log(self, message: str, *args) -> None:
        logging.info(message, *args)

    def log_error(self, message: str, *args) -> None:
        logging.error(message, *args)

    async def capture(self, page: Optional[Page], step: str) -> None:
        return


async def login_and_open_portal_clean(
    playwright: Playwright,
    config: PortalIvaDdjjYLibrosConfig,
) -> tuple[Browser, BrowserContext, Page]:
    browser, context, page = await launch_arca_browser_context(
        playwright,
        headless=config.headless,
        context_kwargs={
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/154.0.8037.97 Safari/537.36",
        },
    )
    await Stealth().apply_stealth_async(page)
    page.set_default_timeout(35_000)

    login_errors: list[str] = []
    login_result = await login_arca(
        page,
        config.cuit_representante,
        config.clave,
        error_logs=login_errors,
        fail_on_captcha=True,
    )
    for line in login_errors:
        logging.info("arca_login=%s", line)
    if login_result.captcha_detected:
        raise RuntimeError("Captcha detectado")
    if login_result.password_change_required:
        raise RuntimeError("Se requiere cambiar la clave fiscal")
    if not login_result.success:
        raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesion en ARCA")

    open_errors: list[str] = []
    portal_page = await open_arca_service(
        page,
        service_name="Portal IVA",
        service_matchers=(
            re.compile(r"PORTAL\s+IVA", re.I),
            "PORTAL IVA Presentación del",
            "siap_web_iva",
        ),
        error_logs=open_errors,
        click_ver_todos=True,
        service_unavailable_message="No se encuentra habilitado el servicio de Portal IVA",
        popup_timeout_ms=15_000,
    )
    for line in open_errors:
        logging.info("arca_service=%s", line)
    if portal_page is None:
        raise RuntimeError(open_errors[-1] if open_errors else "No se encuentra habilitado el servicio de Portal IVA")

    portal_page.set_default_timeout(35_000)
    try:
        await wait_page_ready(portal_page, timeout_ms=25_000)
        await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=25_000)
    except PlaywrightTimeoutError:
        await portal_page.goto(PORTAL_IVA_DIRECT_URL, wait_until="domcontentloaded", timeout=25_000)
        await wait_page_ready(portal_page, timeout_ms=25_000)
        await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=25_000)

    if await _is_blank_page(portal_page):
        await portal_page.goto(_portal_iva_init_url_with_key(), wait_until="domcontentloaded", timeout=25_000)
        await wait_page_ready(portal_page, timeout_ms=25_000)

    await portal_page.wait_for_timeout(3_000)
    return browser, context, portal_page


async def run_with_config(playwright: Playwright, config: PortalIvaDdjjYLibrosConfig) -> bool:
    runtime = CleanRuntime()
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    try:
        browser, context, portal_page = await login_and_open_portal_clean(playwright, config)
        await select_representado(portal_page, config, runtime)

        if config.descargar_libros_iva:
            await download_libro_iva_xml(portal_page, config, runtime)
        if config.descargar_ddjj:
            await download_ddjj_presentadas(portal_page, config, runtime)

        logging.info("Proceso completado exitosamente")
        return True
    except Exception as exc:
        logging.error("run_failed error=%s", exc)
        return False
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cuit_representante = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTANTE",
        "PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE",
        "PORTAL_IVA_CUIT_REPRESENTANTE",
    )
    clave = _env_first("PORTAL_IVA_DDJJ_LIBROS_CLAVE", "PORTAL_IVA_LIBROS_CLAVE", "PORTAL_IVA_CLAVE")
    cuit_representado = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTADO",
        "PORTAL_IVA_LIBROS_CUIT_REPRESENTADO",
        "PORTAL_IVA_CUIT_REPRESENTADO",
    )
    periodos_str = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_PERIODOS",
        "PORTAL_IVA_LIBROS_PERIODOS",
        "PORTAL_IVA_DDJJ_LIBROS_PERIODO",
        "PORTAL_IVA_LIBROS_PERIODO",
    )
    headless = _env_flag("PORTAL_IVA_HEADLESS", default=True)
    descargar_libros_iva = _env_flag("PORTAL_IVA_DDJJ_LIBROS_DESCARGAR_LIBROS_IVA", default=True)
    descargar_ddjj = _env_flag("PORTAL_IVA_DDJJ_LIBROS_DESCARGAR_DDJJ", default=True)
    denominacion = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_DENOMINACION",
        "PORTAL_IVA_LIBROS_DENOMINACION",
        "PORTAL_IVA_DENOMINACION",
    )

    if not cuit_representante or not clave or not cuit_representado:
        print(
            "Faltan variables de entorno: PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTANTE, "
            "PORTAL_IVA_DDJJ_LIBROS_CLAVE, PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTADO"
        )
        return

    periodos = [p.strip() for p in periodos_str.split(",") if p.strip()]
    if not periodos:
        print("No se especificaron periodos en PORTAL_IVA_DDJJ_LIBROS_PERIODOS")
        return

    config = PortalIvaDdjjYLibrosConfig(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        periodos=periodos,
        descargar_libros_iva=descargar_libros_iva,
        descargar_ddjj=descargar_ddjj,
        headless=headless,
        denominacion=denominacion,
    )

    print(f"Iniciando {BOT_NAME} limpio")
    print(f"Descargas: {DOWNLOAD_DIR.resolve()}")

    async with async_playwright() as playwright:
        success = await run_with_config(playwright, config)
        if success:
            print("Proceso finalizado correctamente.")
        else:
            print("El proceso finalizo con errores.")


if __name__ == "__main__":
    asyncio.run(main())
