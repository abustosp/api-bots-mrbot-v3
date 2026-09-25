"""Login fiscal ARCA compartido del worker.

La sesion usa el ``Page`` que crea la ``BrowserFactory``. No lanza un navegador,
lee configuracion de entorno ni conserva cookies fuera del contexto del job.
"""

from __future__ import annotations

import base64
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from bot_worker.bots.errors import (
    CaptchaUnsolvableError as BotCaptchaUnsolvableError,
    CredentialsRejectedError,
    TargetUnavailableError,
)
from bot_worker.runtime.captcha import CaptchaSolver
from bot_worker.runtime.context import FiscalCredentials

ARCA_LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"


class ArcaLoginError(CredentialsRejectedError):
    """El login fue rechazado o no se pudo completar de forma segura."""

    category = "CREDENTIALS_REJECTED"

    def __init__(
        self,
        diagnostico: str,
        *,
        diagnostic_code: str = "arca_login_rejected",
    ) -> None:
        super().__init__(diagnostico)
        self.diagnostic_code = diagnostic_code


class ArcaServicePage:
    """Adaptador de página para los bots heredados de Mis Comprobantes."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)

    async def seleccionar_representado(self, cuit: str) -> None:
        digits = re.sub(r"\D", "", str(cuit))
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        formatted = f"{digits[:2]}-{digits[2:10]}-{digits[10]}"
        for selector in (
            f"small.pull-right:has-text('{formatted}')",
            f"span:has-text('{formatted}')",
        ):
            try:
                option = self._page.locator(selector)
                if await option.count():
                    await option.first.click(timeout=3_000)
                    return
            except Exception:
                continue
        for selector in (
            "span.nombre-propio.nombre-activo.pull-right",
            "span.nombre-propio.nombre-activo.pull-righ",
        ):
            try:
                active = self._page.locator(selector)
                if not await active.count():
                    continue
                text = await active.first.text_content() or ""
                if re.sub(r"\D", "", text).endswith(digits):
                    return
            except Exception:
                continue
        raise TargetUnavailableError(
            "no se pudo seleccionar el CUIT representado",
            diagnostic_code="represented_cuit_not_selectable",
        )


class ArcaSession:
    """Sesión fiscal efímera sobre un ``Page`` gestionado por BrowserFactory."""

    def __init__(
        self,
        credentials: FiscalCredentials,
        captcha: CaptchaSolver | None = None,
        *,
        page: Any = None,
        context: Any = None,
    ) -> None:
        if credentials is None:
            raise ArcaLoginError("el plugin requiere credenciales fiscales")
        self._credentials: FiscalCredentials | None = credentials
        self._captcha = captcha
        self._page = page
        self._context = context
        self._logged_in = False

    @property
    def page(self) -> Any:
        if self._page is None:
            raise TargetUnavailableError(
                "página ARCA no disponible",
                diagnostic_code="arca_page_missing",
            )
        return self._page

    @staticmethod
    async def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() == 0:
                    continue
                await locator.first.fill(value, timeout=5_000)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _click_first(page: Any, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=5_000)
                return True
            except Exception:
                continue
        return False

    async def _captcha_image(self) -> bytes | None:
        for selector in (
            "#captcha img",
            "img[alt*='captcha' i]",
            "img[src*='captcha' i]",
            "img[src^='data:image']",
        ):
            try:
                locator = self.page.locator(selector)
                if not await locator.count():
                    continue
                image = locator.first
                src = await image.get_attribute("src")
                if src and src.startswith("data:image/") and "," in src:
                    return base64.b64decode(src.split(",", 1)[1], validate=False)
                return await image.screenshot(timeout=5_000)
            except Exception:
                continue
        return None

    async def _captcha_present(self) -> bool:
        for selector in (
            "#captcha",
            "img[alt*='captcha' i]",
            "img[src*='captcha' i]",
            "iframe[src*='captcha' i]",
            "input[id*='captcha' i]",
            "input[name*='captcha' i]",
        ):
            try:
                locator = self.page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _solve_captcha(self) -> None:
        if self._captcha is None or not self._captcha.enabled:
            raise BotCaptchaUnsolvableError(
                "ARCA requiere CAPTCHA y no hay solver habilitado"
            )
        image = await self._captcha_image()
        if not image:
            raise BotCaptchaUnsolvableError("no se pudo leer el CAPTCHA de ARCA")
        try:
            solution = (await self._captcha.solve_image(image)).strip()
        except Exception:
            # La excepción del proveedor puede incluir respuestas sensibles.
            # Se descarta su cadena causal para que no llegue a logs/reportes.
            raise BotCaptchaUnsolvableError(
                "no se pudo resolver el CAPTCHA de ARCA"
            ) from None
        if not solution:
            raise BotCaptchaUnsolvableError("no se pudo resolver el CAPTCHA de ARCA")
        selectors = (
            "input#F1\\:captchaSolutionInput",
            "input[name='F1:captchaSolutionInput']",
            "input[placeholder*='captcha' i]",
            "input[id*='captcha' i]",
        )
        if not await self._fill_first(self.page, selectors, solution):
            raise BotCaptchaUnsolvableError("no se encontró el campo CAPTCHA de ARCA")

    async def _feedback(self) -> str:
        for selector in ("#F1\\:msg", "span[id$=':msg']", "p.text-danger"):
            try:
                locator = self.page.locator(selector)
                if await locator.count():
                    text = (await locator.first.inner_text(timeout=1_500) or "").strip()
                    if text:
                        return re.sub(r"\s+", " ", text)
            except Exception:
                continue
        return ""

    async def _wait_ready(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass

    def _page_contexts(self) -> tuple[Any, ...]:
        """Devuelve la página y sus frames, sin duplicar el frame principal."""
        contexts = [self.page]
        try:
            frames = self.page.frames
        except Exception:
            frames = ()
        for frame in frames or ():
            if all(frame is not existing for existing in contexts):
                contexts.append(frame)
        return tuple(contexts)

    async def _login_form_visible(self) -> bool:
        """Comprueba si ARCA aún muestra el campo de clave tras enviar el login."""
        for selector in (
            "input#F1\\:password",
            "input[name='F1:password']",
            "input[type='password']",
            "#F1\\:password",
        ):
            try:
                locator = self.page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _click_first_in_contexts(
        self, selectors: tuple[str, ...]
    ) -> bool:
        for context in self._page_contexts():
            if await self._click_first(context, selectors):
                return True
        return False

    async def login(self) -> None:
        """Inicia sesión una sola vez con errores tipados y sin secretos."""
        if self._logged_in:
            return
        if self._page is None and self._context is not None:
            try:
                self._page = await self._context.new_page()
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo abrir una página ARCA",
                    diagnostic_code="arca_page_create_failed",
                ) from exc
        if self._page is None or self._credentials is None:
            raise TargetUnavailableError(
                "contexto de navegador ARCA no disponible",
                diagnostic_code="arca_browser_context_missing",
            )
        try:
            await self.page.goto(ARCA_LOGIN_URL, timeout=45_000)
            await self._wait_ready()
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo abrir el login de ARCA",
                diagnostic_code="arca_login_navigation_failed",
            ) from exc

        if not await self._fill_first(
            self.page,
            (
                "input#F1\\:username",
                "input[name='F1:username']",
                "input#F1\\:login",
                "input[type='number']",
                "input[placeholder*='CUIT' i]",
                "input[name*='cuit' i], input[id*='cuit' i]",
                "input[type='text']",
            ),
            self._credentials.cuit_representante,
        ):
            raise ArcaLoginError("no se encontró el campo CUIT en ARCA")
        if not await self._click_first(
            self.page,
            (
                "button:has-text('Siguiente')",
                "#siguiente",
                "button[type='submit']",
                "input#F1\\:btnSiguiente",
                "input[type='submit']",
            ),
        ):
            advanced = False
            for selector in (
                "input#F1\\:username",
                "input[name='F1:username']",
                "input#F1\\:login",
                "input[type='number']",
            ):
                try:
                    locator = self.page.locator(selector)
                    if await locator.count():
                        await locator.first.press("Enter")
                        advanced = True
                        break
                except Exception:
                    continue
            if not advanced:
                raise TargetUnavailableError(
                    "no se pudo avanzar en el login de ARCA",
                    diagnostic_code="arca_username_submit_failed",
                )
        await self._wait_ready()

        if await self._captcha_present():
            await self._solve_captcha()
        if not await self._fill_first(
            self.page,
            (
                "#F1\\:password",
                "input[type='password']",
                "input[name*='password' i], input[name*='clave' i]",
            ),
            self._credentials.clave,
        ):
            raise ArcaLoginError("no se encontró el campo de clave fiscal en ARCA")
        if not await self._click_first(
            self.page,
            (
                "button:has-text('Ingresar')",
                "#ingresar",
                "button[type='submit']",
                "input#F1\\:btnIngresar",
                "input[type='submit']",
            ),
        ):
            try:
                await self.page.locator("input[type='password']").press("Enter")
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo enviar el login de ARCA",
                    diagnostic_code="arca_password_submit_failed",
                ) from exc
        await self._wait_ready()

        feedback = (await self._feedback()).lower()
        if re.search(r"captcha\s+ingresado\s+es\s+incorrecto", feedback):
            raise BotCaptchaUnsolvableError("ARCA rechazó el CAPTCHA")
        if await self._captcha_present():
            await self._solve_captcha()
            if await self._login_form_visible():
                raise BotCaptchaUnsolvableError(
                    "ARCA requiere resolver un CAPTCHA antes de continuar"
                )
        if re.search(r"clave\s+o\s+usuario\s+incorrecto|clave incorrecta", feedback):
            raise ArcaLoginError(
                "ARCA rechazó las credenciales fiscales",
                diagnostic_code="arca_login_credentials_rejected",
            )
        if feedback:
            raise ArcaLoginError(
                "ARCA rechazó el inicio de sesión",
                diagnostic_code="arca_login_feedback_rejected",
            )
        try:
            if await self.page.locator("text=/Cambiar\\s+Clave\\s+Fiscal/i").count():
                raise ArcaLoginError(
                    "ARCA requiere cambiar la clave fiscal",
                    diagnostic_code="arca_password_change_required",
                )
        except ArcaLoginError:
            raise
        except Exception:
            pass
        if await self._login_form_visible():
            raise ArcaLoginError(
                "ARCA no completó el inicio de sesión",
                diagnostic_code="arca_login_not_completed",
            )
        self._logged_in = True

    async def _find_service(self, service: str) -> Any | None:
        matcher = re.compile(re.escape(service), re.I)
        for context in self._page_contexts():
            candidates = (
                context.get_by_role("button", name=matcher),
                context.get_by_role("link", name=matcher),
                context.locator("button, [role='button'], a").filter(
                    has_text=matcher
                ),
            )
            for group in candidates:
                try:
                    count = await group.count()
                except Exception:
                    continue
                for index in range(min(count, 10)):
                    candidate = group.nth(index)
                    try:
                        if await candidate.is_visible():
                            return candidate
                    except Exception:
                        continue
        return None

    async def open_service(self, service: str) -> Any:
        """Abre un servicio por nombre o una URL HTTPS del dominio fiscal."""
        if not self._logged_in:
            await self.login()
        value = str(service or "").strip()
        if not value:
            raise TargetUnavailableError(
                "nombre de servicio ARCA vacío",
                diagnostic_code="arca_service_name_missing",
            )
        parsed = urlparse(value)
        if parsed.scheme:
            host = (parsed.hostname or "").lower().rstrip(".")
            trusted_host = host.endswith(".afip.gob.ar") or host == "afip.gob.ar"
            trusted_host = trusted_host or host.endswith(".arca.gob.ar") or host == "arca.gob.ar"
            if (
                parsed.scheme != "https"
                or not trusted_host
                or parsed.username
                or parsed.password
            ):
                raise TargetUnavailableError(
                    "URL de servicio ARCA no permitida",
                    diagnostic_code="arca_service_url_rejected",
                )
            try:
                await self.page.goto(value, timeout=45_000)
                await self._wait_ready()
                return ArcaServicePage(self.page)
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo abrir el servicio ARCA",
                    diagnostic_code="arca_service_navigation_failed",
                ) from exc

        service_locator = await self._find_service(value)
        if service_locator is None:
            # El portal oculta el catálogo completo tras "Ver todos".
            await self._click_first_in_contexts(
                (
                    "a:has-text('Ver todos')",
                    "button:has-text('Ver todos')",
                    "[role='link']:has-text('Ver todos')",
                ),
            )
            await self._wait_ready()
            service_locator = await self._find_service(value)
        if service_locator is None:
            raise TargetUnavailableError(
                "servicio ARCA no habilitado o no visible",
                diagnostic_code="arca_service_not_visible",
            )

        before_url = getattr(self.page, "url", "")
        try:
            async with self.page.expect_popup(timeout=5_000) as popup:
                await service_locator.click(timeout=8_000)
            service_page = await popup.value
        except Exception as exc:
            service_page = self.page
            after_url = getattr(self.page, "url", "")
            if before_url == after_url and type(exc).__name__ not in {
                "TimeoutError",
                "PlaywrightTimeoutError",
            }:
                raise TargetUnavailableError(
                    "no se pudo abrir el servicio ARCA",
                    diagnostic_code="arca_service_open_failed",
                ) from exc
        try:
            await service_page.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass
        return ArcaServicePage(service_page)

    async def close(self) -> None:
        # BrowserFactory cierra el contexto. Reducir referencias a secretos aquí.
        self._credentials = None
        self._captcha = None
        self._page = None
        self._context = None


@asynccontextmanager
async def arca_session(
    credentials: FiscalCredentials | None,
    captcha: CaptchaSolver | None = None,
    *,
    page: Any = None,
    context: Any = None,
    **_: Any,
) -> AsyncIterator[ArcaSession]:
    session = ArcaSession(credentials, captcha, page=page, context=context)  # type: ignore[arg-type]
    try:
        await session.login()
        yield session
    finally:
        await session.close()
