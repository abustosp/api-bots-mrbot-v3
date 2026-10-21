import re
import time
import asyncio
import os
from dataclasses import dataclass, field
from typing import List, Optional, Pattern, Sequence, Union

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from shared.arca_captcha import get_arca_capmonster_config, try_solve_arca_captcha
from shared.public_errors import safe_public_message, safe_public_text

ARCA_LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
ServiceMatcher = Union[str, Pattern[str]]

STEALTH_LAUNCH_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
]

STEALTH_CONTEXT_DEFAULTS = {
        "locale": "es-AR",
        "timezone_id": "America/Argentina/Buenos_Aires",
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
        ),
}

STEALTH_INIT_SCRIPT = """
(() => {
    const override = (obj, prop, value) => {
        try {
            Object.defineProperty(obj, prop, {
                get: () => value,
                configurable: true,
            });
        } catch (_) {}
    };

    override(Navigator.prototype, 'webdriver', undefined);
    override(Navigator.prototype, 'languages', ['es-AR', 'es', 'en-US', 'en']);
    override(Navigator.prototype, 'platform', 'Win32');
    override(Navigator.prototype, 'hardwareConcurrency', 8);
    override(Navigator.prototype, 'deviceMemory', 8);
    override(Navigator.prototype, 'plugins', [1, 2, 3, 4, 5]);

    try {
        if (!window.chrome) {
            Object.defineProperty(window, 'chrome', {
                value: { runtime: {} },
                configurable: true,
            });
        }
    } catch (_) {}

    try {
        const originalQuery = window.navigator.permissions?.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => {
                if (parameters?.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return originalQuery.call(window.navigator.permissions, parameters);
            };
        }
    } catch (_) {}

    try {
        const webglGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return webglGetParameter.call(this, parameter);
        };
    } catch (_) {}

    try {
        const webgl2GetParameter = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return webgl2GetParameter.call(this, parameter);
        };
    } catch (_) {}
})();
"""


def _merge_launch_args(launch_args: Optional[Sequence[str]]) -> list[str]:
        merged = list(STEALTH_LAUNCH_ARGS)
        for arg in list(launch_args or []):
                if arg not in merged:
                        merged.append(arg)
        return merged


def _merge_context_kwargs(context_kwargs: Optional[dict]) -> dict:
        merged = dict(STEALTH_CONTEXT_DEFAULTS)
        merged.update(dict(context_kwargs or {}))
        return merged


@dataclass
class ArcaLoginResult:
    success: bool
    error_logs: List[str] = field(default_factory=list)
    captcha_detected: bool = False
    password_change_required: bool = False


@dataclass
class ArcaSession:
    browser: Browser
    context: BrowserContext
    page: Page
    login_result: ArcaLoginResult


async def wait_page_ready(page: Page, timeout_ms: int = 12_000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass


async def launch_arca_browser_context(
    playwright: Playwright,
    *,
    headless: bool = True,
    proxy: Optional[dict] = None,
    launch_args: Optional[Sequence[str]] = None,
    context_kwargs: Optional[dict] = None,
) -> tuple[Browser, BrowserContext, Page]:
    browser_kwargs: dict = {
        "headless": headless,
        "args": _merge_launch_args(launch_args),
        "ignore_default_args": ["--enable-automation"],
    }

    browser_channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()
    if browser_channel:
        browser_kwargs["channel"] = browser_channel

    if proxy is not None:
        browser_kwargs["proxy"] = proxy

    browser = await playwright.chromium.launch(**browser_kwargs)
    context = await browser.new_context(**_merge_context_kwargs(context_kwargs))
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    page = await context.new_page()
    return browser, context, page


async def _safe_close_session(browser: Optional[Browser], context: Optional[BrowserContext]) -> None:
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


async def start_arca_session(
    playwright: Playwright,
    cuit_representante: str,
    clave_representante: str,
    *,
    error_logs: Optional[List[str]] = None,
    headless: bool = True,
    proxy: Optional[dict] = None,
    launch_args: Optional[Sequence[str]] = None,
    context_kwargs: Optional[dict] = None,
    login_url: str = ARCA_LOGIN_URL,
    fail_on_captcha: bool = False,
    solve_arca_captcha: bool = False,
) -> Optional[ArcaSession]:
    browser = None
    context = None
    try:
        browser, context, page = await launch_arca_browser_context(
            playwright,
            headless=headless,
            proxy=proxy,
            launch_args=launch_args,
            context_kwargs=context_kwargs,
        )
        login_result = await login_arca(
            page,
            cuit_representante,
            clave_representante,
            error_logs=error_logs,
            login_url=login_url,
            fail_on_captcha=fail_on_captcha,
            solve_arca_captcha=solve_arca_captcha,
        )
        if not login_result.success:
            await _safe_close_session(browser, context)
            return None
        return ArcaSession(
            browser=browser,
            context=context,
            page=page,
            login_result=login_result,
        )
    except Exception:
        await _safe_close_session(browser, context)
        raise


async def _fill_first_available(
    locators: Sequence[Locator],
    value: str,
    timeout_ms: int = 5_000,
) -> Optional[Locator]:
    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            target = locator.first
            try:
                await target.click(timeout=1_500)
            except Exception:
                pass
            await target.fill(str(value), timeout=timeout_ms)
            return target
        except Exception:
            continue
    return None


async def _click_first_available(
    locators: Sequence[Locator],
    timeout_ms: int = 5_000,
    max_items: int = 6,
    total_timeout_ms: Optional[int] = None,
) -> Optional[Locator]:
    deadline = time.monotonic() + ((total_timeout_ms or timeout_ms) / 1000)
    while True:
        for locator_group in locators:
            try:
                count = await locator_group.count()
            except Exception:
                continue

            if count <= 0:
                continue

            for idx in range(min(count, max_items)):
                candidate = locator_group.nth(idx)
                try:
                    await candidate.click(timeout=timeout_ms)
                    return candidate
                except Exception:
                    continue

        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.2)
    return None


async def _has_visible_locator(locators: Sequence[Locator], timeout_ms: int = 1_500) -> bool:
    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            target = locator.first
            try:
                await target.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                if await target.is_visible():
                    return True
        except Exception:
            continue
    return False


async def _get_login_feedback_message(page: Page) -> str:
    candidates = [
        page.locator("#F1\\:msg"),
        page.locator("span[id$=':msg']"),
        page.locator("p.text-danger"),
    ]
    for locator in candidates:
        try:
            if await locator.count() == 0:
                continue
            text = (await locator.first.inner_text(timeout=1_500) or "").strip()
            if text:
                return re.sub(r"\s+", " ", text)
        except Exception:
            continue
    return ""


def _is_incorrect_password_message(message: str) -> bool:
    return bool(re.search(r"clave\s+o\s+usuario\s+incorrecto", message, re.I))


def _is_incorrect_captcha_message(message: str) -> bool:
    return bool(re.search(r"captcha\s+ingresado\s+es\s+incorrecto", message, re.I))


def _build_ver_todos_locators(page: Page) -> list[Locator]:
    return [
        page.get_by_role("link", name="Ver todos"),
        page.get_by_role("link", name=re.compile(r"Ver\s+todos|VER\s+TODOS", re.I)),
        page.get_by_role("button", name=re.compile(r"Ver\s+todos|VER\s+TODOS", re.I)),
        page.get_by_text(re.compile(r"Ver\s+todos", re.I)),
        page.locator("a:has-text('Ver todos')"),
        page.locator("button:has-text('Ver todos')"),
    ]


def _build_service_locators(page: Page, matchers: Sequence[ServiceMatcher]) -> list[Locator]:
    locators: list[Locator] = []
    for matcher in matchers:
        locators.append(page.get_by_role("button", name=matcher))
        locators.append(page.get_by_role("link", name=matcher))
        locators.append(page.locator("button, [role='button'], a").filter(has_text=matcher))
    return locators


async def _find_first_visible(
    locators: Sequence[Locator],
    *,
    total_timeout_ms: int = 12_000,
    max_items: int = 8,
) -> Optional[Locator]:
    deadline = time.monotonic() + (total_timeout_ms / 1000)
    while True:
        fallback: Optional[Locator] = None
        for locator_group in locators:
            try:
                count = await locator_group.count()
            except Exception:
                continue
            if count <= 0:
                continue
            if fallback is None:
                fallback = locator_group.first

            for idx in range(min(count, max_items)):
                candidate = locator_group.nth(idx)
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue

        if time.monotonic() >= deadline:
            return fallback
        await asyncio.sleep(0.2)


async def login_arca(
    page: Page,
    cuit_representante: str,
    clave_representante: str,
    *,
    error_logs: Optional[List[str]] = None,
    login_url: str = ARCA_LOGIN_URL,
    login_error_prefix: str = "No se pudo iniciar sesión en ARCA. Verifique los datos ingresados",
    fail_on_captcha: bool = False,
    solve_arca_captcha: bool = False,
    click_ver_todos: bool = False,
    ver_todos_required: bool = False,
    ver_todos_error_message: str = "No se pudo abrir 'Ver todos' en ARCA",
) -> ArcaLoginResult:
    logs = error_logs if error_logs is not None else []
    result = ArcaLoginResult(success=False, error_logs=logs)

    try:
        await page.goto(login_url, timeout=45_000)
        await wait_page_ready(page)
    except Exception as exc:
        logs.append(
            safe_public_message(
                exc,
                context="authentication",
                stage="el inicio de sesión",
                secrets=[clave_representante],
            )
        )
        return result

    cuit_locator = await _fill_first_available(
        [
            page.get_by_role("spinbutton"),
            page.locator("#F1\\:login"),
            page.locator("input[type='number']"),
            page.locator("input[placeholder*='CUIT' i], input[placeholder*='cuit' i]"),
            page.locator("input[name*='cuit' i], input[id*='cuit' i]"),
        ],
        cuit_representante,
    )
    if cuit_locator is None:
        logs.append("No se pudo cargar el CUIT en ARCA")
        return result

    siguiente_clicked = await _click_first_available(
        [
            page.get_by_role("button", name="Siguiente"),
            page.get_by_role("button", name=re.compile(r"Siguiente", re.I)),
            page.get_by_text("Siguiente", exact=True),
            page.locator("#siguiente"),
            page.locator("button:has-text('Siguiente')"),
        ]
    )
    if siguiente_clicked is None:
        try:
            await cuit_locator.press("Enter")
        except Exception:
            logs.append("No se pudo avanzar al ingreso de clave en ARCA")
            return result

    await wait_page_ready(page)

    captcha_detected = await _has_visible_locator(
        [
            page.get_by_role("img", name=re.compile(r"Captcha", re.I)),
            page.locator("img[alt*='captcha' i], img[src*='captcha' i]"),
            page.locator("iframe[src*='captcha' i]"),
            page.locator("[id*='captcha' i], [class*='captcha' i]"),
        ]
    )
    if captcha_detected:
        logs.append("Captcha detectado")
        result.captcha_detected = True

    captcha_cfg = get_arca_capmonster_config()
    max_captcha_attempts = max(1, int(captcha_cfg.get("max_attempts") or 5))
    captcha_retry = 0
    feedback_message = ""

    while True:
        # Resolver el captcha apenas aparece (con reintentos) antes de enviar la clave.
        if captcha_detected and solve_arca_captcha:
            try:
                resolved = await try_solve_arca_captcha(
                    page,
                    error_logs=logs,
                    max_attempts=1,
                    announce=False,
                )
            except Exception:
                resolved = False
            if resolved:
                result.captcha_detected = False
            elif fail_on_captcha:
                return result

        clave_locator = await _fill_first_available(
            [
                page.get_by_role("textbox", name="TU CLAVE"),
                page.get_by_label("TU CLAVE"),
                page.get_by_label(re.compile(r"CLAVE", re.I)),
                page.get_by_role("textbox", name=re.compile(r"CLAVE|clave", re.I)),
                page.locator("#F1\\:password"),
                page.locator("input[type='password']"),
                page.locator("input[name*='password' i], input[name*='clave' i]"),
                page.get_by_role("textbox"),
            ],
            clave_representante,
        )
        if clave_locator is None:
            logs.append("No se pudo completar la clave fiscal")
            return result

        ingresar_clicked = await _click_first_available(
            [
                page.get_by_role("button", name="Ingresar"),
                page.get_by_role("button", name=re.compile(r"Ingresar|INGRESAR", re.I)),
                page.get_by_text("Ingresar", exact=True),
                page.locator("#ingresar"),
                page.locator("button[type='submit']"),
            ]
        )
        if ingresar_clicked is None:
            try:
                await clave_locator.press("Enter")
            except Exception:
                logs.append("No se pudo completar el ingreso a ARCA")
                return result

        await wait_page_ready(page)

        password_change_required = await _has_visible_locator(
            [
                page.get_by_role("heading", name=re.compile(r"Cambiar\s+Clave\s+Fiscal", re.I)),
                page.locator("text=/Cambiar\\s+Clave\\s+Fiscal/i"),
            ]
        )
        if password_change_required:
            logs.append("Se requiere cambiar la clave fiscal")
            result.password_change_required = True
            return result

        feedback_message = await _get_login_feedback_message(page)

        if (
            captcha_detected
            and solve_arca_captcha
            and _is_incorrect_captcha_message(feedback_message)
        ):
            captcha_retry += 1
            if captcha_retry >= max_captcha_attempts:
                logs.append("No se pudo resolver el captcha de ARCA con CapMonster")
                result.captcha_detected = True
                return result
            logs.append(
                f"Captcha incorrecto, reintento {captcha_retry + 1}/{max_captcha_attempts}"
            )
            captcha_detected = await _has_visible_locator(
                [
                    page.locator("img[src*='base64']"),
                    page.locator("img[alt*='captcha' i], img[src*='captcha' i]"),
                    page.locator("[id*='captcha' i], [class*='captcha' i]"),
                ]
            )
            if not captcha_detected:
                captcha_detected = True
            continue

        break

    if _is_incorrect_password_message(feedback_message):
        logs.append("Clave Incorrecta")
        return result

    if feedback_message:
        # El texto del organismo puede interpolar la clave del login si el
        # llamador lo incluye en el prefijo: se tapa antes de salir de aca.
        logs.append(
            safe_public_text(
                f"{login_error_prefix}: {feedback_message}",
                context="login",
                stage="login",
                secrets=[clave_representante],
            )
        )
        return result

    if click_ver_todos:
        ver_todos_clicked = await _click_first_available(_build_ver_todos_locators(page))
        if ver_todos_clicked is None:
            logs.append(ver_todos_error_message)
            if ver_todos_required:
                return result
        else:
            await wait_page_ready(page)

    result.success = True
    return result


async def open_arca_service(
    page: Page,
    *,
    service_name: str,
    service_matchers: Optional[Sequence[ServiceMatcher]] = None,
    error_logs: Optional[List[str]] = None,
    click_ver_todos: bool = True,
    ver_todos_required: bool = False,
    ver_todos_error_message: str = "No se pudo abrir 'Ver todos' en ARCA",
    service_unavailable_message: Optional[str] = None,
    popup_timeout_ms: int = 10_000,
) -> Optional[Page]:
    logs = error_logs if error_logs is not None else []
    matchers = tuple(service_matchers or (service_name,))
    unavailable_message = (
        service_unavailable_message
        if service_unavailable_message
        else f"No se encuentra habilitado el servicio de {service_name}"
    )

    if click_ver_todos:
        clicked = await _click_first_available(
            _build_ver_todos_locators(page),
            timeout_ms=1_500,
            total_timeout_ms=8_000,
        )
        if clicked is None:
            logs.append(ver_todos_error_message)
            if ver_todos_required:
                return None
        else:
            await wait_page_ready(page)

    service_candidates = _build_service_locators(page, matchers)
    service_button = await _find_first_visible(
        service_candidates,
        total_timeout_ms=20_000,
        max_items=10,
    )

    if service_button is None:
        logs.append(unavailable_message)
        return None

    try:
        await service_button.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass

    try:
        async with page.expect_popup(timeout=popup_timeout_ms) as popup_info:
            await service_button.click(timeout=8_000)
        service_page = await popup_info.value
    except PlaywrightTimeoutError:
        # Mantiene el criterio previo: si no abre popup, reintenta click y usa la misma pestaña.
        try:
            await service_button.click(timeout=3_000)
        except Exception:
            try:
                handle = await service_button.element_handle()
                if handle is not None:
                    await handle.evaluate("el => el.click()")
            except Exception:
                pass
        service_page = page
    except Exception as exc:
        try:
            handle = await service_button.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                service_page = page
            else:
                logs.append(f"{unavailable_message}: {exc}")
                return None
        except Exception:
            logs.append(f"{unavailable_message}: {exc}")
            return None

    await wait_page_ready(service_page)
    return service_page
