import asyncio
import json
import os
import tempfile
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, quote_plus

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


RETPER_LOGIN_URL = "https://extranet.atmisiones.gob.ar/Extranet/index.php"
RETPER_CONSULTA_URL = "https://extranet.atmisiones.gob.ar/Extranet/Aplicaciones/consultas_ret_perc.php"
RETPER_REPORT_POST_URL = "https://extranet.atmisiones.gob.ar/Framework/Funciones/llamar_report.php"
RETPER_REPORT_DOWNLOAD_URL = "https://extranet.atmisiones.gob.ar/Framework/Funciones/reporte.php"
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


def _parse_positive_int(raw_value: str, default_value: int) -> int:
    value = (raw_value or "").strip()
    if not value:
        return default_value
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default_value
    except Exception:
        return default_value


def _period_to_site_format(periodo: str) -> str:
    periodo = (periodo or "").strip()
    if len(periodo) == 6 and periodo.isdigit():
        return f"{periodo[:4]}/{periodo[4:6]}"
    return periodo


def _sanitize_filename_component(value: str) -> str:
    sanitized = INVALID_FILENAME_RE.sub("_", value or "")
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def _build_filename_base(cuit: str, desde: str, hasta: str, denominacion: str) -> str:
    cuit_digits = re.sub(r"\D", "", cuit or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else ""
    parts = [
        fin_cuit,
        cuit_digits,
        desde,
        hasta,
        "RETPER IIBB MISIONES",
        _sanitize_filename_component(denominacion),
    ]
    return " - ".join([part for part in parts if part])


def _is_valid_period(periodo: str) -> bool:
    value = (periodo or "").strip()
    if len(value) != 6 or not value.isdigit():
        return False
    month = int(value[4:6])
    return 1 <= month <= 12


def _response_looks_like_captcha(payload: str) -> bool:
    text = (payload or "").lower()
    return any(marker in text for marker in ("recaptcha", "captcha", "g-recaptcha", "no soy un robot"))


def _looks_like_pdf(data: bytes) -> bool:
    if not data:
        return False
    if len(data) < 100:
        return False
    return data[:4] == b"%PDF"


async def _has_captcha(page: Page) -> bool:
    captcha_locators = [
        page.locator("iframe[src*='recaptcha']"),
        page.locator("iframe[title*='reCAPTCHA']"),
        page.locator("div.g-recaptcha"),
        page.locator("[id*='captcha' i], [class*='captcha' i]"),
    ]
    for locator in captcha_locators:
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


async def _solve_captcha_if_needed(
    page: Page,
    stage: str,
    api_key: str,
    timeout_s: int,
    poll_s: int,
    preferred_task_type: str,
    error_logs: List[str],
) -> bool:
    if not await _has_captcha(page):
        return True

    if await _is_captcha_valid(page):
        return True

    if not api_key:
        error_logs.append(
            f"Captcha detectado en {stage}, pero falta API_KEY para resolverlo"
        )
        return False

    sitekey = await detect_recaptcha_sitekey(page)
    if not sitekey:
        error_logs.append(f"Captcha detectado en {stage}, pero no se pudo detectar sitekey")
        return False

    task_candidates: list[str] = []
    for candidate in (
        (preferred_task_type or "").strip(),
        "RecaptchaV2Task",
        "NoCaptchaTaskProxyless",
    ):
        if candidate and candidate not in task_candidates:
            task_candidates.append(candidate)

    for task_type in task_candidates:
        token = await solve_recaptcha_v2(
            api_key=api_key,
            website_url=page.url,
            sitekey=sitekey,
            timeout_s=timeout_s,
            poll_s=poll_s,
            task_type=task_type,
        )
        if not token:
            continue

        await inject_recaptcha_token(page, token)
        await asyncio.sleep(1.2)
        if await _is_captcha_valid(page):
            return True

    error_logs.append(f"No se pudo resolver captcha en {stage}")
    return False


async def _click_first(candidates: Sequence[Any], timeout_ms: int = 5000) -> bool:
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _fill_first(candidates: Sequence[Any], value: str, timeout_ms: int = 5000) -> bool:
    for locator in candidates:
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=timeout_ms)
                await locator.first.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _extract_id_session(page: Page) -> str:
    try:
        value = await page.evaluate(
            """
            () => {
                const direct = [
                    localStorage.getItem('IdSession'),
                    sessionStorage.getItem('IdSession'),
                    localStorage.getItem('id_session'),
                    sessionStorage.getItem('id_session'),
                    window.IdSession,
                    window.id_session,
                    window.ID_SESSION,
                ];

                for (const candidate of direct) {
                    if (candidate) return String(candidate);
                }

                for (const store of [localStorage, sessionStorage]) {
                    for (let i = 0; i < store.length; i += 1) {
                        const key = store.key(i);
                        if (key && key.toLowerCase().includes('idsession')) {
                            const item = store.getItem(key);
                            if (item) return String(item);
                        }
                    }
                }

                const meta = document.querySelector('meta[name="IdSession"], meta[name="id_session"]');
                if (meta && meta.content) return String(meta.content);

                const input = document.querySelector('input[name="IdSession"], input[name="id_session"], input[id="IdSession"], input[id="id_session"]');
                if (input && input.value) return String(input.value);

                return '';
            }
            """
        )
        return (value or "").strip()
    except Exception:
        return ""


async def _download_excel(page: Page, output_path: Path, error_logs: List[str]) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    excel_candidates = [
        page.get_by_role("button", name=re.compile(r"Generar\s+Excel", re.I)),
        page.locator("button:has-text('Generar Excel')"),
        page.locator("input[value*='Excel' i]"),
    ]

    for locator in excel_candidates:
        try:
            if await locator.count() == 0:
                continue
            async with page.expect_download(timeout=45000) as download_info:
                await locator.first.click(timeout=8000)
            download = await download_info.value
            await download.save_as(str(output_path))
            return True
        except Exception:
            continue

    error_logs.append("No se pudo descargar el archivo Excel de Ret/Per IIBB Misiones")
    return False


async def _generate_and_download_pdf(
    page: Page,
    context: BrowserContext,
    cuit: str,
    denominacion: str,
    desde: str,
    hasta: str,
    output_path: Path,
    api_key: str,
    timeout_s: int,
    poll_s: int,
    task_type: str,
    error_logs: List[str],
) -> bool:
    cookies = await context.cookies("https://extranet.atmisiones.gob.ar")
    cookie_header = "; ".join([f"{item['name']}={item['value']}" for item in cookies])

    try:
        user_agent = await page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    id_session_header = await _extract_id_session(page)
    denominacion_encoded = quote(denominacion, safe="")
    periodo_desde_site = _period_to_site_format(desde)
    periodo_hasta_site = _period_to_site_format(hasta)

    parametros_raw = (
        f"P_CUIT_CONTRIBUYENTE={cuit}&"
        "P_CUIT_AGENTE=null&"
        "P_DATOS_CONTRIB=N&"
        "P_C_REGIMEN=null&"
        f"P_PERIODO_DESDE={periodo_desde_site}&"
        f"P_PERIODO_HASTA={periodo_hasta_site}&"
        "P_SUBTRIBUTO=null&"
        "P_ID_CONTRIBUYENTE=null&"
        "P_TITULO_REPORTE=MIS RETENCIONES/PERCEPCIONES&"
        "P_C_TIPO_INFORME=TODO&"
        f"P_D_DENOMINACION={denominacion_encoded}&"
        f"P_USER={cuit}&"
        "P_TIPO_INGRESO=EXTRANET"
    )
    parametros_encoded = quote_plus(parametros_raw, safe="")
    post_data = (
        "c_tipo_report=consultas_ret_perc&"
        f"parametros={parametros_encoded}&"
        "server_name=extranet.atmisiones.gob.ar&"
        "c_impresion=PDF"
    )

    post_headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "es-ES,en-US;q=0.7,en;q=0.3",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": RETPER_CONSULTA_URL,
        "Origin": "https://extranet.atmisiones.gob.ar",
        "X-Requested-With": "XMLHttpRequest",
        "X-Id-Menu": "99999",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }
    if cookie_header:
        post_headers["Cookie"] = cookie_header
    if id_session_header:
        post_headers["IdSession"] = id_session_header

    async def _post_report(stage: str) -> Tuple[Optional[int], str]:
        solved = await _solve_captcha_if_needed(
            page,
            stage,
            api_key,
            timeout_s,
            poll_s,
            task_type,
            error_logs,
        )
        if not solved:
            return None, ""

        response = await context.request.post(
            RETPER_REPORT_POST_URL,
            data=post_data,
            headers=post_headers,
        )
        return response.status, await response.text()

    status_code, response_text = await _post_report("generacion_reporte_pdf")
    if status_code == 200 and _response_looks_like_captcha(response_text) and api_key:
        status_code, response_text = await _post_report("generacion_reporte_pdf_retry")

    report_id_session = ""
    if status_code == 200:
        try:
            response_json = json.loads(response_text)
            if str(response_json.get("resultado", "")).upper() == "OK":
                report_id_session = str(
                    response_json.get("id_session")
                    or response_json.get("id_sesion")
                    or ""
                )
        except Exception:
            pass

        if not report_id_session:
            match = re.search(r"id_ses+sion\"?\s*[:=]\s*\"?([a-zA-Z0-9]+)\"?", response_text)
            if match:
                report_id_session = match.group(1)
    else:
        error_logs.append(f"Error HTTP al generar PDF de Ret/Per IIBB Misiones: {status_code}")

    if not report_id_session:
        error_logs.append("No se obtuvo id_session para descargar el PDF de Ret/Per IIBB Misiones")
        return False

    await asyncio.sleep(5)

    report_url = (
        f"{RETPER_REPORT_DOWNLOAD_URL}?id_sesion={report_id_session}&c_impresion=PDF&format=PDF"
    )
    get_headers = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Referer": RETPER_CONSULTA_URL,
        "Origin": "https://extranet.atmisiones.gob.ar",
    }
    if cookie_header:
        get_headers["Cookie"] = cookie_header
    if id_session_header:
        get_headers["IdSession"] = id_session_header

    async def _get_report(stage: str) -> Tuple[Optional[int], bytes]:
        solved = await _solve_captcha_if_needed(
            page,
            stage,
            api_key,
            timeout_s,
            poll_s,
            task_type,
            error_logs,
        )
        if not solved:
            return None, b""

        response = await context.request.get(report_url, headers=get_headers)
        return response.status, await response.body()

    download_status, body = await _get_report("descarga_pdf")
    if (
        download_status == 200
        and body
        and _response_looks_like_captcha(body.decode("utf-8", errors="ignore"))
        and api_key
    ):
        download_status, body = await _get_report("descarga_pdf_retry")

    if download_status != 200:
        error_logs.append(f"Error HTTP al descargar PDF de Ret/Per IIBB Misiones: {download_status}")
        return False

    if not _looks_like_pdf(body):
        error_logs.append("El contenido descargado para PDF de Ret/Per IIBB Misiones no es valido")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(body)
    return True


async def bot_retper_iibb_misiones(
    cuit_representante: str,
    clave_representante: str,
    desde: str,
    hasta: str,
    denominacion: str,

    proxy_request: bool = False,
    headless: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True

    cuit_digits = re.sub(r"\D", "", cuit_representante or "")
    if len(cuit_digits) != 11:
        return results, ["CUIT invalido para Ret/Per IIBB Misiones"], False

    if not (clave_representante or "").strip():
        return results, ["La clave del representante es obligatoria"], False

    if not (denominacion or "").strip():
        return results, ["La denominacion es obligatoria"], False

    if not _is_valid_period(desde) or not _is_valid_period(hasta):
        return results, ["Los periodos deben tener formato AAAAMM"], False

    if desde > hasta:
        return results, ["El periodo 'desde' no puede ser mayor al periodo 'hasta'"], False

    periodo_desde_site = _period_to_site_format(desde)
    periodo_hasta_site = _period_to_site_format(hasta)
    filename_base = _build_filename_base(cuit_digits, desde, hasta, denominacion)

    capmonster_api_key = (os.getenv("RETPER_CAPMONSTER_API_KEY") or "").strip()
    capmonster_timeout_s = _parse_positive_int(
        os.getenv("RETPER_CAPMONSTER_TIMEOUT_SECONDS", "180"),
        180,
    )
    capmonster_poll_s = _parse_positive_int(
        os.getenv("RETPER_CAPMONSTER_POLL_SECONDS", "5"),
        5,
    )
    capmonster_task_type = os.getenv("RETPER_CAPMONSTER_TASK_TYPE", "RecaptchaV2Task")

    proxy = get_proxy_config() if proxy_request else None

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    with tempfile.TemporaryDirectory(prefix="retper_iibb_misiones_") as temp_dir:
        temp_path = Path(temp_dir)
        excel_path = temp_path / f"{filename_base}.xlsx"
        pdf_path = temp_path / f"{filename_base}.pdf"

        try:
            async with limit_browser(), async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
                context = await browser.new_context(
                    accept_downloads=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="es-AR",
                    timezone_id="America/Argentina/Buenos_Aires",
                )
                page = await context.new_page()

                await page.goto(RETPER_LOGIN_URL, timeout=60000)
                await page.wait_for_load_state("networkidle")

                ingreso_button_ok = await _click_first(
                    [
                        page.get_by_role("button", name=re.compile(r"INGRESE\s+CON\s+CLAVE\s+FISCAL", re.I)),
                        page.locator("button:has-text('INGRESE CON CLAVE FISCAL')"),
                    ],
                    timeout_ms=10000,
                )
                if not ingreso_button_ok:
                    error_logs.append("No se encontro el boton 'INGRESE CON CLAVE FISCAL'")
                    return results, error_logs, False

                usuario_ok = await _fill_first(
                    [
                        page.get_by_role("textbox", name=re.compile(r"Usuario", re.I)),
                        page.locator("input[name='usuario']"),
                        page.locator("input[placeholder*='Usuario' i]"),
                    ],
                    cuit_digits,
                    timeout_ms=10000,
                )
                if not usuario_ok:
                    error_logs.append("No se pudo completar el usuario en login de Misiones")
                    return results, error_logs, False

                clave_ok = await _fill_first(
                    [
                        page.locator("input[type='password']"),
                        page.locator("input[name='contrasena']"),
                        page.locator("input[placeholder*='Contrase' i]"),
                    ],
                    clave_representante,
                    timeout_ms=10000,
                )
                if not clave_ok:
                    error_logs.append("No se pudo completar la clave en login de Misiones")
                    return results, error_logs, False

                ingresar_ok = await _click_first(
                    [
                        page.get_by_role("button", name=re.compile(r"^Ingresar$", re.I)),
                        page.locator("button:has-text('Ingresar')"),
                        page.locator("input[value='Ingresar']"),
                    ],
                    timeout_ms=10000,
                )
                if not ingresar_ok:
                    error_logs.append("No se pudo enviar el login en Misiones")
                    return results, error_logs, False

                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1.2)

                try:
                    if not await _solve_captcha_if_needed(
                        page,
                        "login",
                        capmonster_api_key,
                        capmonster_timeout_s,
                        capmonster_poll_s,
                        capmonster_task_type,
                        error_logs,
                    ):
                        return results, error_logs, False

                    if await _has_captcha(page):
                        await _click_first(
                            [
                                page.get_by_role("button", name=re.compile(r"^Ingresar$", re.I)),
                                page.locator("button:has-text('Ingresar')"),
                                page.locator("input[value='Ingresar']"),
                            ],
                            timeout_ms=10000,
                        )
                        await page.wait_for_load_state("networkidle")

                        if not await _solve_captcha_if_needed(
                            page,
                            "login_retry",
                            capmonster_api_key,
                            capmonster_timeout_s,
                            capmonster_poll_s,
                            capmonster_task_type,
                            error_logs,
                        ):
                            return results, error_logs, False
                except Exception:
                    pass

                login_error = page.locator(".alert-danger, .error, [class*='error']")
                try:
                    if await login_error.count() > 0:
                        login_error_text = (await login_error.first.text_content() or "").strip()
                        if login_error_text:
                            error_logs.append(f"Error en login de Misiones: {login_error_text}")
                            return results, error_logs, False
                except Exception:
                    pass

                if not await _click_first(
                    [
                        page.get_by_role("link", name=re.compile(r"Ingresos\s+Brutos", re.I)),
                        page.locator("a:has-text('Ingresos Brutos')"),
                    ],
                    timeout_ms=10000,
                ):
                    error_logs.append("No se pudo abrir la seccion Ingresos Brutos")
                    return results, error_logs, False
                await page.wait_for_load_state("networkidle")

                if not await _click_first(
                    [
                        page.get_by_role("link", name=re.compile(r"Consulta\s+de\s+Ret\./Perc\.", re.I)),
                        page.locator("a:has-text('Consulta de Ret./Perc.')"),
                    ],
                    timeout_ms=10000,
                ):
                    error_logs.append("No se pudo abrir Consulta de Ret./Perc. en Misiones")
                    return results, error_logs, False
                await page.wait_for_load_state("networkidle")

                desde_ok = await _fill_first([page.locator("#periodo_desde")], periodo_desde_site, 6000)
                hasta_ok = await _fill_first([page.locator("#periodo_hasta")], periodo_hasta_site, 6000)
                if not desde_ok or not hasta_ok:
                    error_logs.append("No se pudieron cargar los periodos en Consulta de Ret./Perc.")
                    return results, error_logs, False

                excel_ok = await _download_excel(page, excel_path, error_logs)
                pdf_ok = await _generate_and_download_pdf(
                    page=page,
                    context=context,
                    cuit=cuit_digits,
                    denominacion=denominacion,
                    desde=desde,
                    hasta=hasta,
                    output_path=pdf_path,
                    api_key=capmonster_api_key,
                    timeout_s=capmonster_timeout_s,
                    poll_s=capmonster_poll_s,
                    task_type=capmonster_task_type,
                    error_logs=error_logs,
                )

                if not excel_ok or not pdf_ok:
                    success = False

        except Exception as exc:
            error_logs.append(f"Error en bot Ret/Per IIBB Misiones: {exc}")
            success = False
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

        downloaded_files: list[tuple[str, Path]] = []
        if excel_path.exists():
            downloaded_files.append(("excel", excel_path))
        if pdf_path.exists():
            downloaded_files.append(("pdf", pdf_path))

        if not downloaded_files:
            if not error_logs:
                error_logs.append("No se descargaron archivos de Ret/Per IIBB Misiones")
            return results, error_logs, False

        for tipo, file_path in downloaded_files:

            results["archivos"].append(
                {
                    "tipo": tipo,
                    "archivo": file_path.name,

                }
            )

            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

        expected_types = {"excel", "pdf"}
        got_types = {item["tipo"] for item in results["archivos"] if item.get("tipo")}
        if expected_types != got_types:
            missing = sorted(expected_types.difference(got_types))
            if missing:
                error_logs.append(f"No se generaron todos los formatos requeridos: {', '.join(missing)}")
            success = False

    return results, error_logs, success
