"""Orquestador unico para variantes logger / clean.

- Manda el flujo definido por el usuario (callable async).
- En logger: crea context con HAR embebido, DebugCapture y DebugCapture.log.
- En clean: usa NullDebug, sin HAR, sin HTML, sin screenshots.
- Login ARCA y apertura de servicio delegados a arca_login (ya existente).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .debug import DebugCapture, NullDebug
from .env import env_bool, get_env

_log = logging.getLogger("bot_builder.runner")

FlowFn = Callable[["BotContext"], Awaitable[tuple[dict, list[str], bool]]]


@dataclass
class BotConfig:
    """Configuracion minima. Si falta, se lee de .env con prefijo."""
    bot: str
    prefix: str = ""                          # prefijo para variables de entorno
    service_name: str = ""                    # servicio ARCA a abrir
    service_matchers: tuple = ()              # matchers adicionales (regex)
    headless: bool = True
    proxy: bool = False                       # si True, usa get_proxy_config()
    logger: bool = True                       # True = variante logger, False = clean
    login_url: str = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"

    @classmethod
    def from_env(cls, bot: str, prefix: str, *, service_name: str = "") -> "BotConfig":
        return cls(
            bot=bot,
            prefix=prefix,
            service_name=service_name or get_env("SERVICE_NAME", prefix=prefix),
            headless=not env_bool("HEADED", prefix=prefix),
            proxy=env_bool("PROXY", prefix=prefix),
        )


@dataclass
class BotContext:
    """Lo que ve el flujo del usuario."""
    page: Page
    browser: Browser
    context: BrowserContext
    cfg: BotConfig
    debug: Any                                # DebugCapture o NullDebug
    error_logs: list[str] = field(default_factory=list)
    results: dict = field(default_factory=lambda: {"archivos": []})


async def run_bot(cfg: BotConfig, flow: FlowFn) -> tuple[dict, list[str], bool]:
    """Entry point. Eligé logger/clean segun cfg.logger."""
    cuit = get_env("CUIT_LOGIN", prefix=cfg.prefix)
    clave = get_env("CLAVE", prefix=cfg.prefix)
    if not cuit or not clave:
        raise SystemExit(f"Faltan credenciales ({cfg.prefix}_CUIT_LOGIN / {cfg.prefix}_CLAVE).")

    debug = DebugCapture(cfg.bot) if cfg.logger else NullDebug()
    proxy = None
    if cfg.proxy:
        try:
            from proxy import get_proxy_config
            proxy = get_proxy_config()
        except Exception as exc:
            _log.warning("proxy deshabilitado: %s", exc)

    error_logs: list[str] = []
    success = True
    debug.log("Iniciando bot %s (logger=%s)", cfg.bot, cfg.logger)

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    try:
        async with async_playwright() as pw:
            try:
                browser_kwargs: dict[str, Any] = {"headless": cfg.headless}
                if proxy:
                    browser_kwargs["proxy"] = proxy

                browser = await pw.chromium.launch(**browser_kwargs)
                ctx_kwargs: dict[str, Any] = {"locale": "es-AR"}
                if isinstance(debug, DebugCapture):
                    ctx_kwargs["record_har_path"] = str(debug.har_path)
                    ctx_kwargs["record_har_content"] = "embed"
                context = await browser.new_context(**ctx_kwargs)
                page = await context.new_page()

                from arca_login import login_arca, open_arca_service

                login_result = await login_arca(
                    page, cuit, clave, error_logs=error_logs
                )
                await debug.capture(page, "post_login")
                if not login_result.success:
                    debug.log("login_failed: %s", error_logs)
                    success = False
                    return {"archivos": []}, error_logs, success

                if cfg.service_name:
                    matchers = tuple(cfg.service_matchers) or (
                        re.compile(cfg.service_name, re.I),
                    )
                    service_page = await open_arca_service(
                        page,
                        service_name=cfg.service_name,
                        service_matchers=matchers,
                        error_logs=error_logs,
                    )
                    if service_page is None:
                        debug.log("servicio_no_habilitado")
                        success = False
                        return {"archivos": []}, error_logs, success
                    page = service_page

                await debug.capture(page, "servicio_abierto")

                bot_ctx = BotContext(
                    page=page, browser=browser, context=context,
                    cfg=cfg, debug=debug, error_logs=error_logs,
                )
                results, error_logs, success = await flow(bot_ctx)
                return results, error_logs, success
            except asyncio.CancelledError:
                # La cancelación debe conservarse, pero el finally igualmente
                # ejecuta el cierre antes de propagarla al worker.
                raise
            except Exception as exc:
                _log.exception("error general: %s", exc)
                error_logs.append(f"Error en bot {cfg.bot}: {exc}")
                return {}, error_logs, False
            finally:
                await _safe_close(context, browser)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log.exception("error general: %s", exc)
        error_logs.append(f"Error en bot {cfg.bot}: {exc}")
        return {}, error_logs, False


async def _safe_close(
    context: Optional[BrowserContext], browser: Optional[Browser]
) -> None:
    """Cierra context y browser sin fallar ni duplicar cierres.

    Playwright expone ``is_closed``/``is_connected`` para consultar el estado
    real. El marcador cubre mocks y dobles simples que no implementan esos
    métodos, y también hace idempotente una segunda invocación explícita.
    """

    async def close_once(
        resource: Any, *, state_method: str, closed_when: bool
    ) -> None:
        if resource is None:
            return

        if getattr(resource, "_mrbot_safe_close_done", False) is True:
            return

        status = getattr(resource, state_method, None)
        if callable(status):
            try:
                if status() is closed_when:
                    try:
                        setattr(resource, "_mrbot_safe_close_done", True)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        try:
            setattr(resource, "_mrbot_safe_close_done", True)
        except Exception:
            pass
        try:
            await resource.close()
        except Exception:
            pass

    await close_once(context, state_method="is_closed", closed_when=True)
    await close_once(browser, state_method="is_connected", closed_when=False)


def main(cfg: BotConfig, flow: FlowFn) -> tuple[dict, list[str], bool]:
    """Helper sincronico para el `if __name__ == "__main__"`."""
    return asyncio.run(run_bot(cfg, flow))
