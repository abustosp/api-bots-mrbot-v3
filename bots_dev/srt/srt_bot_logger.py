import asyncio
import csv
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
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, async_playwright

from shared.arca_login import ARCA_LOGIN_URL, login_arca, open_arca_service

BOT_NAME = "srt"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")
SRT_SERVICE_NAME = "E-SERVICIOS SRT"
ARCA_SYSTEM_LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml?action=SYSTEM"
SRT_ALICUOTAS_URL = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"
CAPMONSTER_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
CAPMONSTER_GET_TASK_RESULT_URL = "https://api.capmonster.cloud/getTaskResult"
# Sanitizado: la clave real se provee via SRT_CAPMONSTER_API_KEY (no commitear).
CAPMONSTER_API_KEY_DEFAULT = ""


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _safe_file_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return cleaned or "tabla"


class DebugCapture:
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



async def _detect_captcha(page: Page) -> bool:
    candidates = [
        page.locator("iframe[src*='recaptcha']"),
        page.locator("iframe[title*='reCAPTCHA']"),
        page.locator("div.g-recaptcha"),
        page.locator("[id*='captcha' i], [class*='captcha' i]"),
        page.get_by_text(re.compile(r"No soy un robot|reCAPTCHA", re.I)),
    ]
    for locator in candidates:
        try:
            if await locator.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _is_captcha_valid(page: Page) -> bool:
        try:
                return bool(
                        await page.evaluate(
                                """
                                () => {
                                    try {
                                        if (typeof captchaisvalid === 'function') {
                                            return !!captchaisvalid();
                                        }
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


def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
        )
        try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                        body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                if not isinstance(parsed, dict):
                        raise RuntimeError(f"Respuesta invalida de CapMonster en {url}")
                return parsed
        except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
                raise RuntimeError(f"CapMonster HTTP {exc.code} en {url}. {detail}") from exc
        except urllib.error.URLError as exc:
                raise RuntimeError(f"CapMonster URL error en {url}: {exc}") from exc


async def _extract_recaptcha_sitekey(page: Page) -> str:
        site_key = await page.evaluate(
                r"""
                () => {
                    const fromDiv = document.querySelector('[data-sitekey]');
                    if (fromDiv && fromDiv.getAttribute('data-sitekey')) {
                        return fromDiv.getAttribute('data-sitekey');
                    }

                    const recaptchaDiv = document.querySelector('.g-recaptcha[data-sitekey]');
                    if (recaptchaDiv && recaptchaDiv.getAttribute('data-sitekey')) {
                        return recaptchaDiv.getAttribute('data-sitekey');
                    }

                    const frames = Array.from(document.querySelectorAll('iframe[src*="recaptcha"]'));
                    for (const frame of frames) {
                        const src = frame.getAttribute('src') || '';
                        try {
                            const url = new URL(src, window.location.origin);
                            const key = url.searchParams.get('k');
                            if (key) return key;
                        } catch (e) {
                            continue;
                        }
                    }

                    try {
                        const cfg = window.___grecaptcha_cfg;
                        if (cfg && cfg.clients) {
                            const seen = new Set();
                            const stack = [cfg.clients];
                            while (stack.length) {
                                const current = stack.pop();
                                if (!current || typeof current !== 'object' || seen.has(current)) continue;
                                seen.add(current);
                                for (const value of Object.values(current)) {
                                    if (!value) continue;
                                    if (typeof value === 'string' && /^[A-Za-z0-9_-]{30,}$/.test(value)) {
                                        return value;
                                    }
                                    if (typeof value === 'object') {
                                        if (value.sitekey && typeof value.sitekey === 'string') return value.sitekey;
                                        if (value.siteKey && typeof value.siteKey === 'string') return value.siteKey;
                                        stack.push(value);
                                    }
                                }
                            }
                        }
                    } catch (e) {}

                    return '';
                }
                """
        )

        if isinstance(site_key, str) and site_key.strip():
                return site_key.strip()

        frame_locator = page.locator("iframe[src*='recaptcha']").first
        if await frame_locator.count() > 0:
                src = (await frame_locator.get_attribute("src")) or ""
                if src:
                        parsed = urlparse(src)
                        key = parse_qs(parsed.query).get("k", [""])[0]
                        if key:
                                return key

        raise RuntimeError("No se pudo detectar el sitekey del reCAPTCHA v2")


async def _inject_recaptcha_token(page: Page, token: str) -> None:
        await page.evaluate(
                r"""
                (token) => {
                    let textareas = Array.from(document.querySelectorAll(
                        "textarea[name='g-recaptcha-response'], textarea#g-recaptcha-response, textarea[name*='g-recaptcha-response' i], textarea[id*='g-recaptcha-response' i]"
                    ));

                    if (!textareas.length) {
                        const ta = document.createElement('textarea');
                        ta.name = 'g-recaptcha-response';
                        ta.id = 'g-recaptcha-response';
                        ta.style.display = 'block';
                        document.body.appendChild(ta);
                        textareas = [ta];
                    }

                    for (const ta of textareas) {
                        ta.value = token;
                        ta.innerHTML = token;
                        ta.textContent = token;
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                        ta.dispatchEvent(new Event('change', { bubbles: true }));
                    }

                    const callbackNodes = Array.from(document.querySelectorAll('[data-callback]'));
                    for (const node of callbackNodes) {
                        const cbName = node.getAttribute('data-callback');
                        if (!cbName) continue;
                        const callback = cbName.split('.').reduce((acc, key) => (acc ? acc[key] : undefined), window);
                        if (typeof callback === 'function') {
                            try { callback(token); } catch (e) {}
                        }
                    }

                    try {
                        const cfg = window.___grecaptcha_cfg;
                        const seen = new Set();
                        const walk = (obj) => {
                            if (!obj || typeof obj !== 'object' || seen.has(obj)) return;
                            seen.add(obj);
                            for (const [k, v] of Object.entries(obj)) {
                                if (typeof v === 'function' && /callback|success/i.test(k)) {
                                    try { v(token); } catch (e) {}
                                    continue;
                                }
                                if (v && typeof v === 'object') walk(v);
                            }
                        };
                        walk(cfg && cfg.clients ? cfg.clients : null);
                    } catch (e) {}

                    try {
                        if (typeof window.grecaptcha === 'undefined' || !window.grecaptcha) {
                            window.grecaptcha = {
                                getResponse: () => token,
                                reset: () => {},
                            };
                        } else if (typeof window.grecaptcha.getResponse === 'function') {
                            const originalGetResponse = window.grecaptcha.getResponse.bind(window.grecaptcha);
                            window.__copilot_recaptcha_token = token;
                            window.grecaptcha.getResponse = (...args) => {
                                const originalValue = originalGetResponse(...args);
                                return originalValue || window.__copilot_recaptcha_token || '';
                            };
                        } else {
                            window.__copilot_recaptcha_token = token;
                            window.grecaptcha.getResponse = () => window.__copilot_recaptcha_token || '';
                        }
                    } catch (e) {}
                }
                """,
                token,
        )


async def _solve_recaptcha_v2_capmonster_proxyless(
        *,
        api_key: str,
        website_url: str,
        site_key: str,
        timeout_seconds: int,
        poll_seconds: int,
) -> str:
        create_payload = {
                "clientKey": api_key,
                "task": {
                        "type": "NoCaptchaTaskProxyless",
                        "websiteURL": website_url,
                        "websiteKey": site_key,
                },
        }
        create_result = await asyncio.to_thread(_http_post_json, CAPMONSTER_CREATE_TASK_URL, create_payload, 60)
        if int(create_result.get("errorId", 1)) != 0:
                raise RuntimeError(
                        f"CapMonster createTask error: {create_result.get('errorCode', 'UNKNOWN')} - "
                        f"{create_result.get('errorDescription', '')}"
                )

        task_id = create_result.get("taskId")
        if not task_id:
                raise RuntimeError("CapMonster no devolvio taskId")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
                await asyncio.sleep(max(1, poll_seconds))
                result_payload = {"clientKey": api_key, "taskId": task_id}
                result = await asyncio.to_thread(_http_post_json, CAPMONSTER_GET_TASK_RESULT_URL, result_payload, 60)

                if int(result.get("errorId", 1)) != 0:
                        raise RuntimeError(
                                f"CapMonster getTaskResult error: {result.get('errorCode', 'UNKNOWN')} - "
                                f"{result.get('errorDescription', '')}"
                        )

                status = str(result.get("status", "")).lower()
                if status == "processing":
                        continue
                if status == "ready":
                        solution = result.get("solution", {}) or {}
                        token = solution.get("gRecaptchaResponse") or solution.get("token")
                        if token:
                                return str(token)
                        raise RuntimeError("CapMonster devolvio status=ready sin token")

                raise RuntimeError(f"Estado inesperado de CapMonster: {status}")

        raise TimeoutError("Tiempo de espera agotado al resolver captcha con CapMonster")


async def _solve_and_apply_recaptcha(
        page: Page,
        debug: DebugCapture,
        *,
        capmonster_api_key: str,
        timeout_seconds: int,
        poll_seconds: int,
) -> None:
        if not capmonster_api_key:
                raise RuntimeError("No se definio API key de CapMonster")

        site_key = await _extract_recaptcha_sitekey(page)
        website_url = page.url or SRT_ALICUOTAS_URL
        debug.log(f"Resolviendo reCAPTCHA v2 con CapMonster. sitekey={site_key} url={website_url}")

        token = await _solve_recaptcha_v2_capmonster_proxyless(
                api_key=capmonster_api_key,
                website_url=website_url,
                site_key=site_key,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
        )

        await _inject_recaptcha_token(page, token)
        await asyncio.sleep(1.5)


async def _extract_login_error(page: Page) -> str:
    candidates = [
        page.locator("#F1\\:msg"),
        page.get_by_text(re.compile(r"Clave o usuario incorrecto", re.I)),
        page.get_by_text(re.compile(r"No fue posible validar", re.I)),
        page.get_by_text(re.compile(r"No fue posible autenticar", re.I)),
    ]
    for locator in candidates:
        try:
            if await locator.count() == 0:
                continue
            text = (await locator.first.text_content() or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _is_arca_home(page: Page) -> bool:
    if "portalcf.cloud.afip.gob.ar/portal/app" in (page.url or ""):
        return True

    home_markers = [
        page.get_by_role("button", name=re.compile(r"Ver\s+todos", re.I)),
        page.get_by_role("link", name=re.compile(r"Ver\s+todos", re.I)),
        page.get_by_text(re.compile(r"Estado de cuenta", re.I)),
        page.get_by_text(re.compile(r"Administrador de relaciones", re.I)),
    ]
    for marker in home_markers:
        try:
            if await marker.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _extract_tables_from_scope(page: Page, in_iframe: bool) -> list[dict[str, Any]]:
    target: Any = page
    if in_iframe:
        iframe_locator = page.locator("#ifrmResultadoPorCuit, iframe[id*='Resultado' i], iframe[name*='Resultado' i]").first
        await iframe_locator.wait_for(state="attached", timeout=12_000)
        handle = await iframe_locator.element_handle()
        if handle is None:
            raise RuntimeError("No se pudo obtener el iframe de resultados")
        frame = await handle.content_frame()
        if frame is None:
            raise RuntimeError("No se pudo abrir el contenido del iframe de resultados")
        await frame.wait_for_load_state("domcontentloaded", timeout=20_000)
        target = frame

    tables: list[dict[str, Any]] = await target.evaluate(
        r"""
        () => {
          const norm = (v) => (v || '').replace(/\s+/g, ' ').trim();
          const out = [];
          const tableNodes = Array.from(document.querySelectorAll('table'));

          const pickTitle = (tableEl, index) => {
            const caption = norm(tableEl.querySelector('caption')?.innerText || '');
            if (caption) return caption;
            let prev = tableEl.previousElementSibling;
            let guard = 0;
            while (prev && guard < 6) {
              const text = norm(prev.innerText || prev.textContent || '');
              if (text) return text;
              prev = prev.previousElementSibling;
              guard += 1;
            }
            const parentTitle = norm(tableEl.closest('fieldset, section, div')?.querySelector('h1,h2,h3,h4,h5,h6,strong,b,label')?.innerText || '');
            if (parentTitle) return parentTitle;
            return `tabla_${index + 1}`;
          };

          tableNodes.forEach((tableEl, idx) => {
            const rows = Array.from(tableEl.querySelectorAll('tr'));
            if (!rows.length) return;

            let headers = [];
            const ths = Array.from(tableEl.querySelectorAll('thead th'));
            if (ths.length) {
              headers = ths.map((th) => norm(th.innerText || th.textContent || ''));
            } else {
              const firstRowCells = Array.from(rows[0].querySelectorAll('th,td'));
              headers = firstRowCells.map((c) => norm(c.innerText || c.textContent || ''));
            }

            const dataRows = [];
            rows.forEach((row, rowIdx) => {
              const cells = Array.from(row.querySelectorAll('th,td'));
              if (!cells.length) return;
              const values = cells.map((c) => norm(c.innerText || c.textContent || ''));
              if (!values.some((v) => v)) return;
              if (rowIdx === 0 && headers.length === values.length && values.every((v, i) => v === headers[i])) {
                return;
              }
              dataRows.push(values);
            });

            if (!dataRows.length) return;
            out.push({
              index: idx + 1,
              title: pickTitle(tableEl, idx),
              headers,
              rows: dataRows,
            });
          });

          if (!out.length) {
                        const fallback = norm(document.querySelector('#DatosResultados')?.innerText || '');
            if (fallback) {
              out.push({
                index: 1,
                title: 'DatosResultados',
                headers: ['texto'],
                rows: fallback.split(/\n+/).map((line) => [norm(line)]).filter((r) => r[0]),
              });
            }
          }

          return out;
        }
        """
    )

    return tables


async def _extract_all_tables_from_frame(page: Page) -> list[dict[str, Any]]:
    iframe_error: Optional[Exception] = None
    try:
        tables = await _extract_tables_from_scope(page, in_iframe=True)
        if tables:
            return tables
    except Exception as exc:
        iframe_error = exc

    if re.search(r"AlicuotasPorCuit_Resultado\.aspx", page.url or "", re.I):
        tables = await _extract_tables_from_scope(page, in_iframe=False)
        if tables:
            return tables

    if iframe_error is not None:
        raise RuntimeError(f"No se pudieron extraer tablas del iframe de resultados: {iframe_error}") from iframe_error

    raise RuntimeError("No se encontraron datos de tablas en resultados SRT")


def _save_tables(
    descargas_dir: Path,
    tables: list[dict[str, Any]],
    cuit_representado: str,
) -> dict[str, Any]:
    json_path = descargas_dir / "srt_tablas.json"
    json_path.write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_files: list[str] = []
    for table in tables:
        idx = int(table.get("index", 0) or 0)
        title = str(table.get("title", f"tabla_{idx or 1}"))
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))
        if not rows:
            continue

        safe_title = _safe_file_name(title)
        csv_path = descargas_dir / f"{idx:02d}_{safe_title}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            csv_headers = ["CUIT_REPRESENTADO", *headers] if headers and any(headers) else ["CUIT_REPRESENTADO"]
            writer.writerow(csv_headers)
            for row in rows:
                writer.writerow([cuit_representado, *row])
        csv_files.append(str(csv_path))

    return {
        "json": str(json_path),
        "csv": csv_files,
        "tables_count": len(tables),
    }


async def _maybe_click_ingresar_temporal(page: Page, debug: "DebugCapture") -> bool:
    rep_candidates = [
        page.locator("a[onclick*='LoguearRespresentado']"),
        page.locator("a[onclick*='LoguearRepresentado']"),
        page.locator("a.btn-success[onclick*='Loguear']"),
    ]
    for candidate in rep_candidates:
        try:
            if await candidate.count() == 0:
                continue
            target = candidate.first
            try:
                await target.wait_for(state="visible", timeout=2_000)
            except Exception:
                continue
            await target.click(timeout=5_000)
            try:
                await page.wait_for_url(re.compile(r"Servicios\.aspx", re.I), timeout=10_000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            debug.log("Click temporal en 'Ingresar' (seleccion de representado) ejecutado")
            return True
        except Exception:
            continue
    return False


async def bot_srt_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    *,
    headless: bool = False,
    capmonster_api_key: str = CAPMONSTER_API_KEY_DEFAULT,
    capmonster_timeout_seconds: int = 180,
    capmonster_poll_seconds: int = 5,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot SRT Logger")

    har_path = debug.har_dir / f"{BOT_NAME}.har"

    browser = None
    context = None
    page = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(
                accept_downloads=True,
                ignore_https_errors=True,
                record_har_path=str(har_path),
                record_har_content="embed",
            )
            page = await context.new_page()
            await debug.capture(page, "inicio")

            debug.log("Paso login_arca inicio")
            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
                login_url=ARCA_SYSTEM_LOGIN_URL,
                fail_on_captcha=True,
            )
            await debug.capture(page, "post_login", "OK" if login_result.success else "ERROR")
            debug.log("Paso login_arca fin")
            if not login_result.success:
                return results, error_logs, False

            if not await _is_arca_home(page):
                login_error = await _extract_login_error(page)
                if login_error:
                    error_logs.append(f"No se pudo iniciar sesión en ARCA. {login_error}")
                else:
                    error_logs.append(f"No se pudo iniciar sesión en ARCA. URL actual: {page.url}")
                await debug.capture(page, "login_validacion_fallida", "ERROR")
                return results, error_logs, False

            debug.log("Paso abrir_servicio_srt inicio")
            page_srt = await open_arca_service(
                page,
                service_name=SRT_SERVICE_NAME,
                service_matchers=(
                    re.compile(r"E-?SERVICIOS\s+SRT", re.I),
                    re.compile(r"Sistema\s+de\s+Riesgos\s+del\s+Trabajo", re.I),
                ),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de E-SERVICIOS SRT",
            )
            if page_srt is None:
                await debug.capture(page, "servicio_srt_no_disponible", "ERROR")
                return results, error_logs, False
            if page_srt is not page and not page.is_closed():
                await page.close()
            page = page_srt
            await debug.capture(page, "servicio_srt_abierto")
            debug.log("Paso abrir_servicio_srt fin")

            debug.log("Paso click_ingresar_temporal inicio")
            clicked_ingresar = await _maybe_click_ingresar_temporal(page, debug)
            await debug.capture(
                page,
                "click_ingresar_temporal_ejecutado" if clicked_ingresar else "click_ingresar_temporal_no_aplica",
            )
            debug.log(f"Paso click_ingresar_temporal fin - click={'SI' if clicked_ingresar else 'NO_APLICA'}")

            debug.log("Paso ingresar_cuit_consulta inicio")
            await page.locator('[id="11"]').first.click(timeout=12_000)
            try:
                await page.wait_for_url(re.compile(r"/Consultas/Alicuotas/Default\.aspx", re.I), timeout=20_000)
            except Exception:
                await page.goto(SRT_ALICUOTAS_URL, timeout=45_000, wait_until="domcontentloaded")

            cuit_candidates = [
                page.get_by_role("spinbutton", name="Ingrese CUIT/CUIL"),
                page.get_by_role("spinbutton"),
                page.locator("input[aria-label*='CUIT']"),
                page.locator("input[placeholder*='CUIT']"),
                page.locator("input[type='number']"),
            ]
            cuit_input = None
            for candidate in cuit_candidates:
                try:
                    if await candidate.count() == 0:
                        continue
                    cuit_input = candidate.first
                    await cuit_input.click(timeout=10_000)
                    await cuit_input.fill(cuit_representado)
                    break
                except Exception:
                    continue
            if cuit_input is None:
                raise RuntimeError("No se encontro el campo para ingresar CUIT/CUIL en SRT")
            await debug.capture(page, "cuit_representado_completado")

            if await _detect_captcha(page):
                debug.log("Captcha detectado")
                await debug.capture(page, "captcha_detectado")
                await _solve_and_apply_recaptcha(
                    page,
                    debug,
                    capmonster_api_key=capmonster_api_key,
                    timeout_seconds=capmonster_timeout_seconds,
                    poll_seconds=capmonster_poll_seconds,
                )
                debug.log("Captcha resuelto con CapMonster (Proxyless)")
                await debug.capture(page, "captcha_resuelto")
                try:
                    captcha_ok = await _is_captcha_valid(page)
                    debug.log(f"Estado captchaisvalid luego de resolver: {captcha_ok}")
                except Exception:
                    pass

            consultar_btn = None
            consultar_candidates = [
                page.locator("#btnConsultar"),
                page.get_by_role("button", name=re.compile(r"Consultar", re.I)),
                page.locator("input[type='submit'][value*='Consultar' i]"),
                page.locator("button:has-text('Consultar')"),
                page.get_by_text(re.compile(r"Consultar", re.I)),
            ]
            for candidate in consultar_candidates:
                try:
                    if await candidate.count() == 0:
                        continue
                    consultar_btn = candidate.first
                    break
                except Exception:
                    continue
            if consultar_btn is None:
                raise RuntimeError("No se encontro el boton Consultar en SRT")

            await consultar_btn.click(timeout=15_000)
            try:
                await page.locator("#ifrmResultadoPorCuit").first.wait_for(state="attached", timeout=10_000)
            except Exception:
                pass
            try:
                await page.wait_for_function(
                    "() => !!(document.body && document.body.classList && document.body.classList.contains('modal-open'))",
                    timeout=8_000,
                )
            except Exception:
                pass
            await debug.capture(page, "consulta_ejecutada")
            debug.log("Paso ingresar_cuit_consulta fin")

            debug.log("Paso extraer_tablas inicio")
            try:
                tables = await _extract_all_tables_from_frame(page)
            except Exception as first_error:
                if not await _is_captcha_valid(page):
                    debug.log("Captcha detectado post-consulta. Reintentando con CapMonster.")
                    await debug.capture(page, "captcha_detectado_post_consulta")
                    await _solve_and_apply_recaptcha(
                        page,
                        debug,
                        capmonster_api_key=capmonster_api_key,
                        timeout_seconds=capmonster_timeout_seconds,
                        poll_seconds=capmonster_poll_seconds,
                    )
                    await debug.capture(page, "captcha_resuelto_post_consulta")
                    await consultar_btn.click(timeout=15_000)
                    await debug.capture(page, "consulta_reintentada")
                    tables = await _extract_all_tables_from_frame(page)
                else:
                                        forced_open = await page.evaluate(
                                                r"""
                                                (cuit) => {
                                                    if (typeof CambiarDestino === 'function') {
                                                        CambiarDestino(cuit);
                                                        return true;
                                                    }
                                                    const iframe = document.querySelector('#ifrmResultadoPorCuit');
                                                    if (!iframe) {
                                                        return false;
                                                    }
                                                    iframe.setAttribute('src', '/Consultas/Alicuotas/AlicuotasPorCuit_Resultado.aspx?cuit=' + cuit);
                                                    return true;
                                                }
                                                """,
                                                cuit_representado,
                                        )
                                        if not forced_open:
                                                raise first_error

                                        await page.wait_for_timeout(1500)
                                        await debug.capture(page, "consulta_forzada_cambiardestino")
                                        tables = await _extract_all_tables_from_frame(page)

            if not tables:
                raise RuntimeError("No se encontraron datos de tablas en resultados SRT")

            for table in tables:
                table["cuit_representado"] = cuit_representado

            saved = _save_tables(debug.descargas_dir, tables, cuit_representado)
            results["cuit_representado"] = cuit_representado
            results["tablas"] = tables
            results["archivos"].append(saved["json"])
            results["archivos"].extend(saved["csv"])
            results["tables_count"] = saved["tables_count"]
            await debug.capture(page, "tablas_extraidas")
            debug.log(f"Paso extraer_tablas fin - tablas={saved['tables_count']}")

    except Exception as exc:
        success = False
        error_logs.append(str(exc))
        if page is not None:
            try:
                await debug.capture(page, "error_general", "ERROR")
            except Exception:
                pass
        debug.log(f"Error general: {exc}")
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

    return results, error_logs, success and not error_logs


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        return default


async def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    cuit_representante = os.getenv("SRT_CUIT_REPRESENTANTE", "")
    clave_representante = os.getenv("SRT_CLAVE_REPRESENTANTE", "")
    cuit_representado = os.getenv("SRT_CUIT_REPRESENTADO", "")
    headless = _env_flag("SRT_HEADLESS", "false")
    capmonster_api_key = os.getenv("SRT_CAPMONSTER_API_KEY", CAPMONSTER_API_KEY_DEFAULT).strip()
    capmonster_timeout_seconds = _env_int("SRT_CAPMONSTER_TIMEOUT_SECONDS", 180)
    capmonster_poll_seconds = _env_int("SRT_CAPMONSTER_POLL_SECONDS", 5)

    if not cuit_representante or not clave_representante or not cuit_representado:
        raise RuntimeError(
            "Faltan variables de entorno SRT_CUIT_REPRESENTANTE, "
            "SRT_CLAVE_REPRESENTANTE o SRT_CUIT_REPRESENTADO"
        )

    results, error_logs, success = await bot_srt_logger(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        headless=headless,
        capmonster_api_key=capmonster_api_key,
        capmonster_timeout_seconds=capmonster_timeout_seconds,
        capmonster_poll_seconds=capmonster_poll_seconds,
    )

    print(json.dumps({"success": success, "errors": error_logs, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
