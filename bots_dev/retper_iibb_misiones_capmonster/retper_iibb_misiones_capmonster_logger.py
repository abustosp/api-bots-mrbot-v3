import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
import pandas as pd
import random
from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "retper_iibb_misiones_capmonster"

CAPMONSTER_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
CAPMONSTER_GET_TASK_RESULT_URL = "https://api.capmonster.cloud/getTaskResult"

RETRYABLE_ERRORS = ("ERR_CONNECTION_RESET", "ECONNRESET", "net::ERR_", "read ECONNRESET", "Timeout")


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _normalize_cuit(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
        return value if value > 0 else default
    except Exception:
        return default


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME, worker_label: str = "") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        if worker_label:
            self.run_dir = self.run_dir / worker_label
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.session_dir = self.run_dir / "session"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (
            self.logs_dir,
            self.screenshots_dir,
            self.html_dir,
            self.har_dir,
            self.session_dir,
            self.descargas_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        logger_name = f"{prefix}.{run_id}" + (f".{worker_label}" if worker_label else "")
        self.logger = logging.getLogger(logger_name)
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

    async def capture(self, page: Optional[Page], step: str, result: str = "OK") -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None result=%s", step, result)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        self.logger.info(
            "step=%s name=%s result=%s url=%s",
            self.step,
            step,
            result,
            getattr(page, "url", ""),
        )

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


# ---------------------------------------------------------------------------
# Captcha utilities (shared logic with clean version)
# ---------------------------------------------------------------------------

def _http_post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Respuesta invalida de CapMonster en {url}")
        return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        raise RuntimeError(f"CapMonster HTTP {exc.code} en {url}. {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CapMonster URL error en {url}: {exc}") from exc


async def _detect_captcha(page: Page) -> bool:
    locators = [
        page.locator("iframe[src*='recaptcha']"),
        page.locator("iframe[title*='reCAPTCHA']"),
        page.locator("iframe[src*='google.com/recaptcha']"),
        page.locator("div.g-recaptcha"),
        page.locator("[id*='captcha' i], [class*='captcha' i]"),
    ]
    for locator in locators:
        try:
            if await locator.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _wait_for_captcha(page: Page, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _detect_captcha(page):
            return True
        await asyncio.sleep(0.5)
    return False


async def _is_captcha_valid(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                  try {
                    if (typeof captchaisvalid === 'function') return !!captchaisvalid();
                    if (typeof grecaptcha !== 'undefined' && grecaptcha && typeof grecaptcha.getResponse === 'function') {
                      return !!grecaptcha.getResponse();
                    }
                    const ta = document.querySelector("textarea#g-recaptcha-response, textarea[name='g-recaptcha-response']");
                    return !!(ta && ta.value && ta.value.trim().length > 0);
                  } catch (e) {
                    return false;
                  }
                }
                """
            )
        )
    except Exception:
        return False


async def _detect_recaptcha_sitekey(page: Page) -> Optional[str]:
    for selector in ["[data-sitekey]", ".g-recaptcha[data-sitekey]", "div[data-sitekey]"]:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                sitekey = await locator.first.get_attribute("data-sitekey")
                if sitekey:
                    return sitekey.strip()
        except Exception:
            continue
    for iframe_selector in ["iframe[src*='recaptcha']", "iframe[src*='captcha']"]:
        try:
            locator = page.locator(iframe_selector)
            if await locator.count() > 0:
                src = (await locator.first.get_attribute("src")) or ""
                parsed = urlparse(src)
                params = parse_qs(parsed.query)
                if "k" in params:
                    return params["k"][0].strip()
        except Exception:
            continue
    return None


async def _inject_recaptcha_token(page: Page, token: str) -> None:
    try:
        await page.evaluate(
            """
            (token) => {
              let ta = document.querySelector("textarea#g-recaptcha-response, textarea[name='g-recaptcha-response']");
              if (!ta) {
                if (!document.body) return;
                ta = document.createElement('textarea');
                ta.name = 'g-recaptcha-response';
                ta.id = 'g-recaptcha-response';
                ta.style.display = 'block';
                document.body.appendChild(ta);
              }
              ta.value = token;
              ta.innerHTML = token;
              ta.textContent = token;
              ta.dispatchEvent(new Event('input', { bubbles: true }));
              ta.dispatchEvent(new Event('change', { bubbles: true }));

              try {
                if (typeof window.grecaptcha === 'undefined' || !window.grecaptcha) {
                  window.grecaptcha = { getResponse: () => token, reset: () => {} };
                } else {
                  window.__copilot_recaptcha_token = token;
                  window.grecaptcha.getResponse = () => window.__copilot_recaptcha_token || '';
                }
              } catch (e) {}

              const callbackNodes = Array.from(document.querySelectorAll('[data-callback]'));
              for (const node of callbackNodes) {
                const cbName = node.getAttribute('data-callback');
                if (!cbName) continue;
                const callback = cbName.split('.').reduce((acc, key) => (acc ? acc[key] : undefined), window);
                if (typeof callback === 'function') {
                  try { callback(token); } catch (e) {}
                }
              }
            }
            """,
            token,
        )
    except Exception:
        pass


async def _solve_recaptcha_v2_capmonster_proxyless(
    api_key: str,
    website_url: str,
    site_key: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> Optional[str]:
    create_payload = {
        "clientKey": api_key,
        "task": {
            "type": "RecaptchaV2Task",
            "websiteURL": website_url,
            "websiteKey": site_key,
        },
    }
    try:
        data = await asyncio.to_thread(_http_post_json, CAPMONSTER_CREATE_TASK_URL, create_payload, 60)
    except Exception:
        return None
    if int(data.get("errorId", 1)) != 0 or "taskId" not in data:
        return None
    task_id = data["taskId"]
    result_payload = {"clientKey": api_key, "taskId": task_id}
    elapsed = 0
    while elapsed < timeout_seconds:
        await asyncio.sleep(max(1, poll_seconds))
        elapsed += max(1, poll_seconds)
        try:
            result = await asyncio.to_thread(_http_post_json, CAPMONSTER_GET_TASK_RESULT_URL, result_payload, 60)
        except Exception:
            continue
        if int(result.get("errorId", 1)) != 0:
            return None
        if result.get("status") == "ready":
            return (result.get("solution", {}) or {}).get("gRecaptchaResponse")
    return None


async def _retry_with_backoff(coro_factory, max_retries: int = 3, base_delay: float = 2.0, description: str = ""):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            err_str = str(e)
            is_retryable = any(r in err_str for r in RETRYABLE_ERRORS)
            if not is_retryable or attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            print(f"      Reintentando ({attempt}/{max_retries}) {description} en {delay:.1f}s: {err_str[:100]}")
            await asyncio.sleep(delay)


async def _solve_captcha_if_needed(
    page: Page,
    stage: str,
    api_key: str,
    timeout_s: int,
    poll_s: int,
    error_logs: list,
    dc: Optional[DebugCapture] = None,
) -> bool:
    captcha_present = await _wait_for_captcha(page, timeout=10.0)
    if not captcha_present:
        return True
    if await _is_captcha_valid(page):
        return True

    error_logs.append(f"Captcha detectado en {stage}.")
    if dc:
        await dc.capture(page, f"captcha_detectado_{stage}", "CAPTCHA")
        dc.log(f"Captcha detectado en {stage}")

    if not api_key:
        error_logs.append("No se definio RETPER_CAPMONSTER_API_KEY para resolver captcha.")
        return False

    sitekey = await _detect_recaptcha_sitekey(page)
    if not sitekey:
        msg = f"Captcha detectado en {stage}, pero no se pudo detectar sitekey"
        error_logs.append(msg)
        if dc:
            dc.log(msg)
        return False

    if dc:
        dc.log(f"Sitekey detectado: {sitekey}")

    task_candidates = []
    for candidate in (
        os.getenv("RETPER_CAPMONSTER_TASK_TYPE", "RecaptchaV2Task").strip(),
        "RecaptchaV2Task",
        "NoCaptchaTaskProxyless",
    ):
        if candidate and candidate not in task_candidates:
            task_candidates.append(candidate)

    for task_type in task_candidates:
        if dc:
            dc.log(f"Resolviendo captcha {stage} con task_type={task_type}")
        token = await _solve_recaptcha_v2_capmonster_proxyless(
            api_key=api_key,
            website_url=page.url,
            site_key=sitekey,
            timeout_seconds=timeout_s,
            poll_seconds=poll_s,
        )
        if not token:
            if dc:
                dc.log(f"task_type={task_type} no devolvio token")
            continue

        await _inject_recaptcha_token(page, token)
        await asyncio.sleep(1.2)
        if await _is_captcha_valid(page):
            error_logs.append(f"Captcha resuelto en {stage} con {task_type}.")
            if dc:
                dc.log(f"Captcha resuelto en {stage} con {task_type}")
            return True

        await _inject_recaptcha_token(page, token)
        await asyncio.sleep(0.8)
        if await _is_captcha_valid(page):
            error_logs.append(f"Captcha resuelto en {stage} con {task_type} (2do intento).")
            if dc:
                dc.log(f"Captcha resuelto en {stage} con {task_type} (2do intento)")
            return True

        if dc:
            dc.log(f"Validacion fallida para {task_type}")

    error_logs.append(f"No se pudo resolver captcha en {stage} con ningun task type.")
    return False


def _response_looks_like_captcha(payload: str) -> bool:
    text = (payload or "").lower()
    return any(marker in text for marker in ("recaptcha", "captcha", "g-recaptcha", "no soy un robot"))


def _period_to_site_format(periodo: str) -> str:
    """Convierte un periodo interno AAAAMM al formato AAAA/MM del sitio."""
    value = (periodo or "").strip()
    if len(value) == 6 and value.isdigit():
        return f"{value[:4]}/{value[4:6]}"
    return value


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------

async def descarga_retper_iibb(
    playwright: Any,
    usuario: str,
    contraseña: str,
    periodo_desde: str,
    periodo_hasta: str,
    pdf: bool,
    excel: bool,
    path_descarga: str = "descarga/",
    nombre_archivo: str = None,
    dgr: str = "",
    proxy_config: dict = None,
    capmonster_api_key: str = "",
    capmonster_timeout_seconds: int = 180,
    capmonster_poll_seconds: int = 5,
    dc: Optional[DebugCapture] = None,
) -> tuple[list, bool]:
    error_logs = []
    success = True
    cuit_log = _normalize_cuit(usuario)

    if dc:
        dc.log(f"Iniciando descarga: CUIT={cuit_log} DGR={dgr} Periodo={periodo_desde}-{periodo_hasta}")

    try:
        desde_f = _period_to_site_format(periodo_desde)
        hasta_f = _period_to_site_format(periodo_hasta)
        desde_ = desde_f.replace("/", "")
        hasta_ = hasta_f.replace("/", "")

        os.makedirs(path_descarga, exist_ok=True)

        if nombre_archivo is None:
            nombre_archivo = f"retper_iibb_misiones_{desde_}_{hasta_}_{usuario}"

        # ------------------------------------------------------------------
        # Launch browser
        # ------------------------------------------------------------------
        browser = await playwright.chromium.launch(headless=True)

        context_options: dict[str, Any] = {
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1920, "height": 1080},
            "locale": "es-AR",
            "timezone_id": "America/Argentina/Buenos_Aires",
        }
        if proxy_config:
            context_options["proxy"] = proxy_config

        context = await browser.new_context(**context_options)

        if dc:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = await context.new_page()

        # ------------------------------------------------------------------
        # Navigate to index
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "navegar_index", "NAVEGANDO")
        try:
            await page.goto("https://extranet.atmisiones.gob.ar/Extranet/index.php", timeout=60000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(random.uniform(1, 2))
        if dc:
            await dc.capture(page, "index_cargado", "OK")

        # ------------------------------------------------------------------
        # Click "INGRESE CON CLAVE FISCAL"
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "clic_ingrese_clave_fiscal", "NAVEGANDO")
        await page.get_by_role("button", name="INGRESE CON CLAVE FISCAL").click()
        await asyncio.sleep(random.uniform(0.5, 1.5))
        if dc:
            await dc.capture(page, "form_login_cargado", "OK")

        # ------------------------------------------------------------------
        # Fill credentials
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "completar_usuario", "COMPLETANDO")
        await page.get_by_role("textbox", name="Usuario").fill(usuario)
        await asyncio.sleep(random.uniform(0.3, 0.8))

        await page.get_by_placeholder("Contraseña").fill(contraseña)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        if dc:
            await dc.capture(page, "credenciales_completadas", "OK")

        # ------------------------------------------------------------------
        # Submit login
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "login_enviar", "ENVIANDO")
        await page.get_by_role("button", name="Ingresar").click()
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(random.uniform(1, 2))
        if dc:
            await dc.capture(page, "login_post_envio", "OK")

        # ------------------------------------------------------------------
        # Captcha on login
        # ------------------------------------------------------------------
        if not await _solve_captcha_if_needed(page, "login", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
            success = False
            await context.close()
            await browser.close()
            return error_logs, success

        current_url = page.url
        still_on_login = "index.php" in current_url
        if await _detect_captcha(page) and still_on_login:
            if dc:
                await dc.capture(page, "login_captcha_reintento", "CAPTCHA")
            await page.get_by_role("button", name="Ingresar").click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(1, 2))
            if dc:
                await dc.capture(page, "login_reintento_enviado", "OK")
            if not await _solve_captcha_if_needed(page, "login_reintento", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
                success = False
                await context.close()
                await browser.close()
                return error_logs, success

        # ------------------------------------------------------------------
        # Check login error
        # ------------------------------------------------------------------
        try:
            error_element = await page.query_selector(".alert-danger, .error, [class*='error']")
            if error_element:
                error_text = await error_element.text_content()
                error_logs.append(f"Error en login: {error_text}")
                if dc:
                    await dc.capture(page, "login_error", "ERROR")
                    dc.log(f"Error en login: {error_text}")
                success = False
                await context.close()
                await browser.close()
                return error_logs, success
        except Exception:
            pass

        if dc:
            await dc.capture(page, "login_exitoso", "OK")

        # ------------------------------------------------------------------
        # Navigate to Ingresos Brutos
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "navegar_ingresos_brutos", "NAVEGANDO")
        await page.get_by_role("link", name=" Ingresos Brutos ").click()
        await page.wait_for_load_state("networkidle")
        if dc:
            await dc.capture(page, "ingresos_brutos_cargado", "OK")

        # ------------------------------------------------------------------
        # Navigate to Consulta de Ret./Perc.
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "navegar_consulta_ret_perc", "NAVEGANDO")
        await page.get_by_role("link", name="Consulta de Ret./Perc.").click()
        await page.wait_for_load_state("networkidle")
        if dc:
            await dc.capture(page, "consulta_cargada", "OK")

        # ------------------------------------------------------------------
        # Fill periods
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "completar_periodos", "COMPLETANDO")
        await page.locator("#periodo_desde").click()
        await page.locator("#periodo_desde").fill(desde_f)
        await page.locator("#periodo_hasta").click()
        await page.locator("#periodo_hasta").fill(hasta_f)
        if dc:
            await dc.capture(page, "periodos_completados", "OK")

        # ------------------------------------------------------------------
        # Download Excel
        # ------------------------------------------------------------------
        if excel:
            if dc:
                await dc.capture(page, "descargar_excel", "DESCARGANDO")
            try:
                async with page.expect_download() as download_info:
                    await page.get_by_role("button", name="Generar Excel").click()
                download = await download_info.value
                excel_path = f"{path_descarga}{nombre_archivo}.xlsx"
                await download.save_as(excel_path)
                if dc:
                    dc.log(f"Excel descargado: {excel_path}")
            except Exception as e:
                error_logs.append(f"Error descargando Excel: {str(e)}")
                if dc:
                    dc.log(f"Error descargando Excel: {e}")

        # ------------------------------------------------------------------
        # Generate and download PDF
        # ------------------------------------------------------------------
        if pdf:
            pdf_path = f"{path_descarga}{nombre_archivo}.pdf"
            if dc:
                await dc.capture(page, "generar_reporte_pdf", "PROCESANDO")
                dc.log("Iniciando generacion de PDF")

            try:
                def save_debug(data: bytes, label: str, ext: str) -> None:
                    debug_path = f"{pdf_path}.{label}.{ext}"
                    with open(debug_path, "wb") as f:
                        f.write(data)

                def try_save_pdf(data: bytes, label: str) -> bool:
                    if not data:
                        return False
                    size = len(data)
                    if data[:4] != b"%PDF":
                        save_debug(data, label, "bin")
                        return False
                    if size < 100:
                        save_debug(data, label, "pdf")
                        return False
                    with open(pdf_path, "wb") as f:
                        f.write(data)
                    if dc:
                        dc.log(f"PDF guardado: {pdf_path} (label={label}, size={size})")
                    return True

                cookies = await context.cookies("https://extranet.atmisiones.gob.ar")
                cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                try:
                    user_agent = await page.evaluate("() => navigator.userAgent")
                except Exception:
                    pass

                id_session_header = None
                try:
                    id_session_header = await page.evaluate("""() => {
                        const direct = [
                            localStorage.getItem('IdSession'),
                            sessionStorage.getItem('IdSession'),
                            localStorage.getItem('id_session'),
                            sessionStorage.getItem('id_session'),
                            window.IdSession, window.id_session, window.ID_SESSION,
                        ];
                        for (const val of direct) { if (val) return val; }
                        for (const store of [localStorage, sessionStorage]) {
                            for (let i = 0; i < store.length; i += 1) {
                                const key = store.key(i);
                                if (key && key.toLowerCase().includes('idsession')) return store.getItem(key);
                            }
                        }
                        const meta = document.querySelector('meta[name="IdSession"], meta[name="id_session"]');
                        if (meta && meta.content) return meta.content;
                        const input = document.querySelector('input[name="IdSession"], input[name="id_session"], input[id="IdSession"], input[id="id_session"]');
                        if (input && input.value) return input.value;
                        return null;
                    }""")
                except Exception:
                    pass

                denominacion = ""
                try:
                    denominacion = await page.evaluate("""() => {
                        const selectors = [
                            '#denominacion', '#p_d_denominacion',
                            'input[name="denominacion"]', 'input[name="p_d_denominacion"]',
                            'input[name="P_D_DENOMINACION"]', 'input[id*="denominacion" i]',
                            '[data-denominacion]'
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) { const val = (el.value || el.textContent || '').trim(); if (val) return val; }
                        }
                        const labels = Array.from(document.querySelectorAll('label'));
                        const label = labels.find(l => /denominaci[oó]n/i.test(l.textContent || ''));
                        if (label) {
                            const forId = label.getAttribute('for');
                            if (forId) { const el = document.getElementById(forId); if (el) { const val = (el.value || el.textContent || '').trim(); if (val) return val; } }
                            const next = label.nextElementSibling;
                            if (next) { const val = (next.value || next.textContent || '').trim(); if (val) return val; }
                        }
                        return '';
                    }""")
                except Exception:
                    denominacion = ""
                if not denominacion and dgr:
                    denominacion = dgr

                parametros_raw = (
                    f"P_CUIT_CONTRIBUYENTE={usuario}&P_CUIT_AGENTE=null&P_DATOS_CONTRIB=N&"
                    f"P_C_REGIMEN=null&P_PERIODO_DESDE={desde_f}&P_PERIODO_HASTA={hasta_f}&"
                    f"P_SUBTRIBUTO=null&P_ID_CONTRIBUYENTE=null&"
                    f"P_TITULO_REPORTE=MIS RETENCIONES/PERCEPCIONES&P_C_TIPO_INFORME=TODO&"
                    f"P_D_DENOMINACION={denominacion}&P_USER={usuario}&P_TIPO_INGRESO=EXTRANET"
                )
                parametros_encoded = quote_plus(parametros_raw, safe="")
                post_data = (
                    "c_tipo_report=consultas_ret_perc&"
                    f"parametros={parametros_encoded}&"
                    "server_name=extranet.atmisiones.gob.ar&c_impresion=PDF"
                )

                headers = {
                    "User-Agent": user_agent,
                    "Accept": "*/*",
                    "Accept-Language": "es-ES,en-US;q=0.7,en;q=0.3",
                    "Referer": "https://extranet.atmisiones.gob.ar/Extranet/Aplicaciones/consultas_ret_perc.php",
                    "Origin": "https://extranet.atmisiones.gob.ar",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Id-Menu": "99999",
                    "X-Requested-With": "XMLHttpRequest",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                }
                if cookie_header:
                    headers["Cookie"] = cookie_header
                if id_session_header:
                    headers["IdSession"] = id_session_header

                    async def generar_reporte_pdf() -> tuple[Any | None, str]:
                        if not await _solve_captcha_if_needed(page, "generacion_reporte", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
                            return None, ""

                        async def _do_post():
                            resp = await context.request.post(
                                "https://extranet.atmisiones.gob.ar/Framework/Funciones/llamar_report.php",
                                data=post_data,
                                headers=headers,
                            )
                            resp_text = await resp.text()
                            return resp, resp_text

                        try:
                            response, response_text = await _retry_with_backoff(
                                _do_post,
                                max_retries=3, base_delay=2.0, description="POST generar reporte PDF",
                            )
                        except Exception as e:
                            error_logs.append(f"Error en POST generar reporte PDF tras reintentos: {e}")
                            return None, ""
                        return response, response_text

                response, response_text = await generar_reporte_pdf()
                if response is None:
                    error_logs.append("No se pudo generar el reporte por captcha no resuelto.")
                    response_status = None
                else:
                    response_status = response.status

                if (
                    response is not None
                    and response_status == 200
                    and _response_looks_like_captcha(response_text)
                    and capmonster_api_key
                ):
                    error_logs.append("Respuesta del POST sugiere captcha. Reintentando tras resolver.")
                    if dc:
                        dc.log("Respuesta del POST contiene captcha. Reintentando.")
                    if await _solve_captcha_if_needed(page, "generacion_reporte_reintento", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
                        response, response_text = await generar_reporte_pdf()
                        response_status = response.status if response is not None else None

                report_id_session = None

                if response is not None and response_status == 200:
                    try:
                        response_json = json.loads(response_text)
                        if str(response_json.get("resultado", "")).upper() == "OK":
                            report_id_session = response_json.get("id_session") or response_json.get("id_sesion")
                            if not report_id_session:
                                error_logs.append(f"POST respondio OK pero id_session es null. Response: {response_text[:200]}")
                            if dc:
                                dc.log(f"Reporte generado OK, id_session={report_id_session}")
                        else:
                            error_logs.append(f"Respuesta no OK: {response_json}")
                    except json.JSONDecodeError:
                        match = re.search(r"id_ses+sion\"?\s*[:=]\s*\"?([a-zA-Z0-9]+)\"?", response_text)
                        if match:
                            report_id_session = match.group(1)
                            if dc:
                                dc.log(f"Reporte id_session por regex: {report_id_session}")
                        else:
                            if _response_looks_like_captcha(response_text):
                                error_logs.append("Respuesta no es JSON valido: posible captcha en generacion de reporte")
                            else:
                                error_logs.append("Respuesta no es JSON valido")
                            if dc:
                                dc.log(f"Respuesta no parseable: {response_text[:200]}")
                elif response is not None:
                    error_logs.append(f"Error HTTP: {response_status}")

                if report_id_session:
                    await asyncio.sleep(5)
                    report_url = (
                        f"https://extranet.atmisiones.gob.ar/Framework/Funciones/reporte.php"
                        f"?id_sesion={report_id_session}&c_impresion=PDF&format=PDF"
                    )

                    download_headers = {
                        "User-Agent": user_agent,
                        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                        "Referer": "https://extranet.atmisiones.gob.ar/Extranet/Aplicaciones/consultas_ret_perc.php",
                        "Origin": "https://extranet.atmisiones.gob.ar",
                    }
                    if cookie_header:
                        download_headers["Cookie"] = cookie_header
                    if id_session_header:
                        download_headers["IdSession"] = id_session_header

                    async def intentar_descarga(url: str) -> bool:
                        try:
                            if not await _solve_captcha_if_needed(page, "descarga_pdf", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
                                return False

                            async def _do_get():
                                resp = await context.request.get(url, headers=download_headers)
                                return resp

                            pdf_response = await _retry_with_backoff(
                                _do_get,
                                max_retries=3, base_delay=2.0, description="GET descarga PDF",
                            )
                            if pdf_response.status == 200:
                                pdf_data = await pdf_response.body()
                                if try_save_pdf(pdf_data, "estrategia1"):
                                    return True

                                if capmonster_api_key and _response_looks_like_captcha(pdf_data.decode("utf-8", errors="ignore")):
                                    error_logs.append("Descarga PDF bloqueada por captcha. Reintentando una vez.")
                                    if dc:
                                        dc.log("Descarga PDF bloqueada por captcha. Reintentando.")
                                    if await _solve_captcha_if_needed(page, "descarga_pdf_reintento", capmonster_api_key, capmonster_timeout_seconds, capmonster_poll_seconds, error_logs, dc):
                                        pdf_response_retry = await context.request.get(url, headers=download_headers)
                                        if pdf_response_retry.status == 200:
                                            pdf_data_retry = await pdf_response_retry.body()
                                            if try_save_pdf(pdf_data_retry, "estrategia1_reintento"):
                                                return True
                            else:
                                error_logs.append(f"Error HTTP descarga PDF: {pdf_response.status}")
                        except Exception as e:
                            error_logs.append(f"Error en descarga PDF: {e}")
                        return False

                    pdf_ok = await intentar_descarga(report_url)
                    if not pdf_ok:
                        error_logs.append("No se pudo descargar un PDF valido")
                else:
                    error_logs.append("No se obtuvo id_session del POST")

            except Exception as e:
                error_logs.append(f"Error descargando PDF: {str(e)}")

        # ------------------------------------------------------------------
        # Close
        # ------------------------------------------------------------------
        if dc:
            await dc.capture(page, "cierre", "OK")
            await context.tracing.stop(path=str(dc.har_dir / "trace.zip"))

        await context.close()
        await browser.close()

    except Exception as e:
        error_logs.append(f"Error general en descarga: {str(e)}")
        if dc:
            dc.log(f"Error general: {e}")
        success = False
        try:
            await context.close()
            await browser.close()
        except Exception:
            pass

    return error_logs, success


# ---------------------------------------------------------------------------
# Worker (batch)
# ---------------------------------------------------------------------------

async def worker(
    semaphore: asyncio.Semaphore,
    row: pd.Series,
    timeout: int,
    proxy_config: dict = None,
    capmonster_api_key: str = "",
    capmonster_timeout_seconds: int = 180,
    capmonster_poll_seconds: int = 5,
):
    dgr = row.get("DGR", "Sin nombre")
    cuit = row.get("CUIT", "Sin CUIT")
    cuit_digits = _normalize_cuit(str(cuit))

    dc = DebugCapture(base_dir="temp", prefix=BOT_NAME, worker_label=cuit_digits)
    ubicacion = str(dc.descargas_dir) + "/"

    await asyncio.sleep(random.uniform(0.5, 3.0))

    try:
        async with semaphore:
            proxy_info = "sin proxy"
            if proxy_config:
                proxy_info = "rotativo (IP única)"

            if dc:
                dc.log(f"Worker INICIO: DGR={dgr} CUIT={cuit} Proxy={proxy_info}")
            print(f"[INICIANDO] {dgr} ({cuit}) - Proxy: {proxy_info}")

            async with async_playwright() as playwright:
                coro = descarga_retper_iibb(
                    playwright=playwright,
                    usuario=str(cuit),
                    contraseña=str(row["Clave"]),
                    periodo_desde=str(row["Desde"]),
                    periodo_hasta=str(row["Hasta"]),
                    pdf=True,
                    excel=True,
                    path_descarga=ubicacion,
                    nombre_archivo=row["Nombre del archivo"],
                    dgr=dgr,
                    proxy_config=proxy_config,
                    capmonster_api_key=capmonster_api_key,
                    capmonster_timeout_seconds=capmonster_timeout_seconds,
                    capmonster_poll_seconds=capmonster_poll_seconds,
                    dc=dc,
                )

                error_logs, success = await asyncio.wait_for(coro, timeout=timeout)

                if dc:
                    dc.log(f"Worker FIN: success={success} errors={len(error_logs)}")

                if success and not error_logs:
                    print(f"[OK] Worker para {dgr} ({cuit}) finalizó exitosamente.")
                elif success and error_logs:
                    print(f"[OK CON WARNINGS] Worker para {dgr} ({cuit}) terminó, pero con logs:")
                    for log in error_logs:
                        print(f"   - {log}")
                else:
                    print(f"[ERROR] Worker para {dgr} ({cuit}) finalizó con error lógico.")
                    for log in error_logs:
                        print(f"   - {log}")

                if len(error_logs) > 0:
                    os.makedirs("./Logs_Error_DGR", exist_ok=True)
                    log_path = f'./Logs_Error_DGR/log_{cuit_digits}_{dgr.replace(" ", "_")}.json'
                    with open(log_path, "w") as log_file:
                        json.dump({
                            "cuit": str(cuit), "dgr": dgr,
                            "periodo_desde": str(row["Desde"]),
                            "periodo_hasta": str(row["Hasta"]),
                            "fecha_ejecucion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "error_logs": error_logs,
                            "success": success,
                        }, log_file, ensure_ascii=False, indent=4)

    except asyncio.TimeoutError:
        print(f"[TIMEOUT] Worker para {dgr} ({cuit}) excedió el tiempo límite de {timeout} segundos.")
        if dc:
            dc.log(f"Worker TIMEOUT: {timeout}s excedido")
        os.makedirs("./Logs_Error_DGR", exist_ok=True)
        with open(f'./Logs_Error_DGR/log_{cuit_digits}_{dgr.replace(" ", "_")}_timeout.json', "w") as log_file:
            json.dump({
                "cuit": str(cuit), "dgr": dgr,
                "error": "Timeout", "timeout_segundos": timeout,
                "fecha_ejecucion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, log_file, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[EXCEPCIÓN] Worker para {dgr} ({cuit}) falló con error inesperado: {e}")
        if dc:
            dc.log(f"Worker EXCEPCION: {e}")
        os.makedirs("./Logs_Error_DGR", exist_ok=True)
        with open(f'./Logs_Error_DGR/log_{cuit_digits}_{dgr.replace(" ", "_")}_exception.json', "w") as log_file:
            json.dump({
                "cuit": str(cuit), "dgr": dgr,
                "error": str(e),
                "fecha_ejecucion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, log_file, ensure_ascii=False, indent=4)


# ---------------------------------------------------------------------------
# Main batch
# ---------------------------------------------------------------------------

async def main_batch(
    max_workers: int = 3,
    timeout: int = 180,
    use_proxy: bool = True,
    capmonster_api_key: str = "",
    capmonster_timeout_seconds: int = 180,
    capmonster_poll_seconds: int = 5,
):
    dc_root = DebugCapture(base_dir="temp", prefix=BOT_NAME)
    dc_root.log(f"Iniciando batch: max_workers={max_workers} timeout={timeout} use_proxy={use_proxy}")

    datos_archivo = "Listado Mis Comprobantes 2.0.xlsx"
    try:
        df_contribuyentes = pd.read_excel(datos_archivo, sheet_name="dgr")
        dc_root.log(f"Excel cargado: {len(df_contribuyentes)} filas")
    except Exception as e:
        print(f"Error leyendo el archivo Excel: {e}")
        dc_root.log(f"Error Excel: {e}")
        return

    proxy_config = None
    if use_proxy:
        proxy_username = os.getenv("PROXY_USERNAME")
        proxy_password = os.getenv("PROXY_PASSWORD")
        if proxy_username and proxy_password:
            proxy_config = {
                "server": "http://pr.oxylabs.io:7777",
                "username": f"customer-{proxy_username}-cc-ar-city-posadas",
                "password": proxy_password,
            }
            dc_root.log("Proxy configurado: Oxylabs")
            print(f"[PROXY] Usando proxy: customer-{proxy_username}-cc-ar-city-posadas@pr.oxylabs.io:7777")
            print(f"[PROXY] Cada contribuyente obtendrá una IP diferente automáticamente")
        else:
            print("[ADVERTENCIA] PROXY_USERNAME o PROXY_PASSWORD no encontrados en .env, continuando sin proxy")
            dc_root.log("Proxy no configurado (faltan credenciales)")

    semaphore = asyncio.Semaphore(max_workers)
    tasks = []

    for _, row in df_contribuyentes.iterrows():
        descargar = str(row.get("Descargar", "")).strip().lower()
        descargado = str(row.get("Descargado", "")).strip().lower()
        if (descargar == "si" or descargar == "s" or descargar == "1") and descargado != "si":
            tasks.append(
                asyncio.create_task(
                    worker(
                        semaphore,
                        row,
                        timeout,
                        proxy_config,
                        capmonster_api_key,
                        capmonster_timeout_seconds,
                        capmonster_poll_seconds,
                    )
                )
            )

    if not tasks:
        print("No hay contribuyentes marcados para descargar.")
        dc_root.log("No hay tareas para descargar")
        return

    print(f"Iniciando descarga de {len(tasks)} contribuyentes con {max_workers} workers concurrentes...")
    dc_root.log(f"Lanzando {len(tasks)} workers")
    await asyncio.gather(*tasks)
    dc_root.log("Batch completado")
    print("\n¡Proceso completado!")


if __name__ == "__main__":
    MAX_WORKERS = 3
    TIMEOUT = 180
    USE_PROXY = False
    CAPMONSTER_API_KEY = os.getenv("RETPER_CAPMONSTER_API_KEY", "").strip()
    CAPMONSTER_TIMEOUT_SECONDS = _env_int("RETPER_CAPMONSTER_TIMEOUT_SECONDS", 180)
    CAPMONSTER_POLL_SECONDS = _env_int("RETPER_CAPMONSTER_POLL_SECONDS", 5)

    if USE_PROXY:
        print("⚠️  ADVERTENCIA: El proxy de Oxylabs presenta problemas de autenticación")
        print("   Si experimentas timeouts, cambia USE_PROXY = False")
        print("   Ver PROXY_STATUS.md para más detalles\n")
    else:
        print("ℹ️  Ejecutando SIN proxy (usando IP real del servidor)")
        print("   Para habilitar proxy, cambia USE_PROXY = True")
        print("   Ver PROXY_STATUS.md para estado del proxy\n")

    if CAPMONSTER_API_KEY:
        print("ℹ️  Resolucion automatica de captcha habilitada (RETPER_CAPMONSTER_API_KEY detectada)\n")
    else:
        print("ℹ️  Resolucion automatica de captcha deshabilitada (falta RETPER_CAPMONSTER_API_KEY)\n")

    print("Logger activo. Los workers crean subdirectorios en temp/")

    asyncio.run(
        main_batch(
            max_workers=MAX_WORKERS,
            timeout=TIMEOUT,
            use_proxy=USE_PROXY,
            capmonster_api_key=CAPMONSTER_API_KEY,
            capmonster_timeout_seconds=CAPMONSTER_TIMEOUT_SECONDS,
            capmonster_poll_seconds=CAPMONSTER_POLL_SECONDS,
        )
    )
