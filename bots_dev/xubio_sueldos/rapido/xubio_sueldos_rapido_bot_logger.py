"""
Bot logger (nuevo, separado): descarga rápida de sueldos Xubio vía APIs de payroll
con evidencias completas por ejecución.

Totalmente independiente de xubio_sueldos_bot.py / xubio_sueldos_bot_logger.py:
no los toca ni los importa.

Misma lógica funcional que xubio_sueldos_rapido_bot.py (paridad), solo cambia la
instrumentación:
- Estructura de evidencias por ejecución: ./temp/xubio_sueldos_rapido_{YYYYMMDD_HHMMSS}/
  - logs/, screenshots/, html/, har/, descargas/
- Captura (screenshot + html) por paso, HAR de la sesión completa.

Variables de entorno (`.env`) — prefijo propio con fallback a los viejos:
- XUBIO_RAPIDO_USUARIO / XUBIO_RAPIDO_CLAVE
- XUBIO_RAPIDO_CONTRIBUYENTE / XUBIO_RAPIDO_CONTRIBUYENTES  (| ; , como separador)
- XUBIO_RAPIDO_PERIODO, XUBIO_RAPIDO_NRO_LIQUIDACION (0-10)
- XUBIO_RAPIDO_HEADLESS, XUBIO_RAPIDO_PROXY
Si no están XUBIO_RAPIDO_*, se usan las XUBIO_SUELDOS_* equivalentes.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from xubio_sueldos_api import RapiConfig, load_rapi_config, run_rapido


BOT_NAME = "xubio_sueldos_rapido"
TEMP_DIR = Path("./temp")


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugLogger:
    """Logger con estructura de evidencias ./temp/{bot}_{timestamp}/ (patrón repo)."""

    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (
            self.logs_dir,
            self.screenshots_dir,
            self.html_dir,
            self.har_dir,
            self.descargas_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.step = 0
        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

        file_handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        self.log("run_dir=%s", self.run_dir)

    # --- interfaz compatible con logger del core ---
    def log(self, message: str, *args) -> None:
        self.logger.info(message, *args)

    def log_error(self, message: str, *args) -> None:
        self.logger.error(message, *args)

    def error(self, message: str, *args) -> None:
        self.logger.error(message, *args)

    # --- capturas por paso ---
    async def capture(self, page: Optional[Page], step: str) -> None:
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


async def run_logger_with_config(playwright: Playwright, config: RapiConfig, debug: DebugLogger) -> bool:
    """Igual que run_rapido pero con evidencias por paso (login, selección, descargas)."""
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None

    try:
        from proxy import get_proxy_config
        from arca_login import launch_arca_browser_context

        proxy = get_proxy_config() if config.proxy else None
        har_path = debug.har_dir / f"{BOT_NAME}.har"
        browser, context, page = await launch_arca_browser_context(
            playwright,
            headless=config.headless,
            proxy=proxy,
            context_kwargs={
                "accept_downloads": True,
                "record_har_path": str(har_path),
                "record_har_content": "embed",
            },
        )
        page.set_default_timeout(20_000)
        debug.log("browser_launched url=%s", page.url)

        await _step(debug, page, "login_visma", lambda: _from_core("login_visma")(page, config, debug.log))
        debug.log("contribuyentes_a_procesar=%s", config.contribuyentes)

        resultados: dict[str, dict] = {}
        for contribuyente in config.contribuyentes:
            debug.log("contribuyente_procesar=%s", contribuyente or "(empresa activa)")

            empresa = await _step(
                debug, page, "seleccionar_contribuyente",
                lambda c=contribuyente: _from_core("seleccionar_contribuyente")(page, c, debug.log),
            )
            debug.log("empresa_efectiva=%s", empresa)

            descargas = await _step(
                debug, page, "descarga_rapida_apis",
                lambda e=empresa: _from_core("descargar_rapido")(page, config, e, debug.descargas_dir, logger=debug.log),
            )
            resultados[empresa] = descargas

        success = all(
            p.exists() and p.stat().st_size > 0
            for descargas in resultados.values()
            for p in descargas.values()
        )
        debug.log("resultado success=%s contribuyentes=%s", success, resultados)
        return success
    except Exception as exc:
        debug.log_error("run_failed error=%s", exc)
        await debug.capture(page, "run_failed")
        return False
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception as exc:
                debug.log_error("context_close_failed error=%s", exc)
        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                debug.log_error("browser_close_failed error=%s", exc)

        har_path = debug.har_dir / f"{BOT_NAME}.har"
        if har_path.exists():
            debug.log("har_saved path=%s size=%s", har_path, har_path.stat().st_size)
        else:
            debug.log("har_missing path=%s", har_path)

        print(f"Logs y evidencias: {debug.run_dir}")


async def _step(debug: DebugLogger, page: Optional[Page], step_name: str, action):
    debug.log("step_start=%s url=%s", step_name, getattr(page, "url", ""))
    try:
        result = await action()
        await debug.capture(page, step_name)
        debug.log("step_end=%s status=OK url=%s", step_name, getattr(page, "url", ""))
        return result
    except Exception as exc:
        debug.log_error("step_end=%s status=ERROR url=%s error=%s", step_name, getattr(page, "url", ""), exc)
        await debug.capture(page, f"{step_name}_error")
        raise


def _from_core(name: str):
    """Devuelve la función async del core xubio_sueldos_api."""
    import xubio_sueldos_api as core

    return getattr(core, name)


async def main() -> bool:
    config = load_rapi_config()
    print("Iniciando Xubio Sueldos Rápido Logger (separado del bot original)")
    print(f"Contribuyentes: {config.contribuyentes}")
    print(f"Período: {config.periodo} | Nro Liquidación: {config.nro_liquidacion} | Headless: {config.headless}")

    debug = DebugLogger(prefix=BOT_NAME)
    debug.log(
        "config periodo=%s nro_liquidacion=%s contribuyentes=%s headless=%s proxy=%s",
        config.periodo, config.nro_liquidacion, config.contribuyentes,
        config.headless, config.proxy,
    )

    async with async_playwright() as playwright:
        return await run_logger_with_config(playwright, config, debug)


if __name__ == "__main__":
    try:
        load_dotenv()
    except Exception:
        pass

    ok = asyncio.run(main())
    print("OK" if ok else "ERROR")
    raise SystemExit(0 if ok else 1)