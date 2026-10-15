import asyncio
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, async_playwright

from shared.arca_login import login_arca, open_arca_service

SRT_SERVICE_NAME = "E-SERVICIOS SRT"
ARCA_SYSTEM_LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml?action=SYSTEM"
SRT_ALICUOTAS_URL = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"

CAPMONSTER_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
CAPMONSTER_GET_TASK_RESULT_URL = "https://api.capmonster.cloud/getTaskResult"
# Sanitizado: la clave real se provee via SRT_CAPMONSTER_API_KEY (no commitear).
CAPMONSTER_API_KEY_DEFAULT = ""

NO_DATA_PATTERNS = (
    re.compile(r"no\s+tiene\s+afiliaci[oó]n\s+vigente", re.I),
    re.compile(r"no\s+registra\s+afiliaci[oó]n", re.I),
)

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_cuit(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "").strip("_")
    return cleaned or "tabla"


def _parse_cuits_env(raw: str) -> list[str]:
    parsed: list[str] = []
    for item in (raw or "").split(","):
        token = item.strip()
        if not token:
            continue

        digits = _normalize_cuit(token)
        if len(digits) > 11 and len(digits) % 11 == 0:
            parsed.extend(digits[idx : idx + 11] for idx in range(0, len(digits), 11))
            continue

        parsed.append(token)
    return parsed


def _build_cuits_consulta(cuit_representado: str, cuit_consulta: list[str] | None) -> tuple[list[str], list[str]]:
    ordered: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    candidates: list[str] = []
    if cuit_consulta:
        candidates.extend(cuit_consulta)
    if cuit_representado:
        candidates.append(cuit_representado)

    for raw in candidates:
        normalized = _normalize_cuit(raw)
        if len(normalized) != 11:
            if str(raw).strip():
                invalid.append(str(raw).strip())
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    return ordered, invalid


def _http_post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
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


async def _detect_captcha(page: Page) -> bool:
    locators = [
        page.locator("iframe[src*='recaptcha']"),
        page.locator("iframe[title*='reCAPTCHA']"),
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


async def _extract_recaptcha_sitekey(page: Page) -> str:
    site_key = await page.evaluate(
        """
        () => {
          const byData = document.querySelector('[data-sitekey]');
          if (byData && byData.getAttribute('data-sitekey')) {
            return byData.getAttribute('data-sitekey');
          }
          const byClass = document.querySelector('.g-recaptcha[data-sitekey]');
          if (byClass && byClass.getAttribute('data-sitekey')) {
            return byClass.getAttribute('data-sitekey');
          }
          return '';
        }
        """
    )
    if isinstance(site_key, str) and site_key.strip():
        return site_key.strip()

    iframe = page.locator("iframe[src*='recaptcha']").first
    if await iframe.count() > 0:
        src = (await iframe.get_attribute("src")) or ""
        if src:
            parsed = urlparse(src)
            key = parse_qs(parsed.query).get("k", [""])[0]
            if key:
                return key

    raise RuntimeError("No se pudo detectar el sitekey del reCAPTCHA v2")


async def _inject_recaptcha_token(page: Page, token: str) -> None:
    await page.evaluate(
        """
        (token) => {
          let ta = document.querySelector("textarea#g-recaptcha-response, textarea[name='g-recaptcha-response']");
          if (!ta) {
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


async def _solve_recaptcha_v2_capmonster_proxyless(
    api_key: str,
    website_url: str,
    site_key: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> str:
    task_types: list[str] = []
    for candidate in (
        os.getenv("SRT_CAPMONSTER_TASK_TYPE", "RecaptchaV2Task").strip(),
        "RecaptchaV2Task",
        "NoCaptchaTaskProxyless",
    ):
        if candidate and candidate not in task_types:
            task_types.append(candidate)

    create_errors: list[str] = []

    for task_type in task_types:
        task_payload = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": site_key,
        }
        create_result = await asyncio.to_thread(
            _http_post_json,
            CAPMONSTER_CREATE_TASK_URL,
            {"clientKey": api_key, "task": task_payload},
            60,
        )
        if int(create_result.get("errorId", 1)) != 0:
            create_errors.append(
                f"{task_type}: {create_result.get('errorCode', 'UNKNOWN')} - "
                f"{create_result.get('errorDescription', '')}"
            )
            continue

        task_id = create_result.get("taskId")
        if not task_id:
            create_errors.append(f"{task_type}: CapMonster no devolvio taskId")
            continue

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(max(1, poll_seconds))
            result = await asyncio.to_thread(
                _http_post_json,
                CAPMONSTER_GET_TASK_RESULT_URL,
                {"clientKey": api_key, "taskId": task_id},
                60,
            )

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

        create_errors.append(f"{task_type}: Tiempo de espera agotado al resolver captcha")

    if create_errors:
        raise RuntimeError("CapMonster createTask fallido. " + " | ".join(create_errors))
    raise RuntimeError("CapMonster no pudo iniciar tarea de captcha")


async def _solve_and_apply_recaptcha(
    page: Page,
    capmonster_api_key: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> None:
    if not capmonster_api_key:
        raise RuntimeError("No se definio API key de CapMonster")

    site_key = await _extract_recaptcha_sitekey(page)
    website_url = page.url or SRT_ALICUOTAS_URL
    token = await _solve_recaptcha_v2_capmonster_proxyless(
        api_key=capmonster_api_key,
        website_url=website_url,
        site_key=site_key,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    await _inject_recaptcha_token(page, token)
    await asyncio.sleep(1.2)
    if not await _is_captcha_valid(page):
        raise RuntimeError("Token de captcha aplicado, pero captchaisvalid() sigue en false")


async def _is_arca_home(page: Page) -> bool:
    if "portalcf.cloud.afip.gob.ar/portal/app" in (page.url or ""):
        return True
    checks = [
        page.get_by_role("link", name=re.compile(r"Ver\s+todos", re.I)),
        page.get_by_role("button", name=re.compile(r"Ver\s+todos", re.I)),
        page.get_by_text(re.compile(r"Administrador de relaciones", re.I)),
    ]
    for check in checks:
        try:
            if await check.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _is_srt_query_page(page: Page) -> bool:
    if re.search(r"/Consultas/Alicuotas/Default\.aspx", page.url or "", re.I):
        return True
    checks = [
        page.get_by_text(re.compile(r"Ingrese\s*CUIT/CUIL", re.I)),
        page.locator("#txtCuilCuit"),
        page.locator("input[aria-label*='CUIT'], input[placeholder*='CUIT']"),
    ]
    for check in checks:
        try:
            if await check.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _maybe_click_ingresar_temporal(page: Page) -> bool:
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
            return True
        except Exception:
            continue
    return False


async def _open_srt_query_page(page: Page) -> Page:
    if await _is_srt_query_page(page):
        return page

    popup_page = None
    consulta_link = page.locator(
        'a[href*="Alicuotas"], div[id="Consulta de Alícuotas"] a,'
        ' div[id="Consulta de Alicuotas"] a, a:has-text("Consulta de Alícuotas")'
    ).first
    if await consulta_link.count() > 0:
        link_visible = False
        try:
            link_visible = await consulta_link.is_visible()
        except Exception:
            link_visible = False

        if link_visible:
            try:
                async with page.expect_popup(timeout=5_000) as popup_info:
                    await consulta_link.click(timeout=4_000)
                popup_page = await popup_info.value
            except Exception:
                try:
                    await consulta_link.click(timeout=4_000)
                except Exception:
                    pass

    if popup_page is not None:
        await popup_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        if page is not popup_page and not page.is_closed():
            await page.close()
        page = popup_page

    if not await _is_srt_query_page(page):
        await page.goto(SRT_ALICUOTAS_URL, timeout=45_000, wait_until="domcontentloaded")

    if not await _is_srt_query_page(page):
        raise RuntimeError("No se pudo abrir Consulta de Alicuotas")

    return page


async def _fill_srt_cuit(page: Page, cuit_representado: str) -> None:
    cuit = _normalize_cuit(cuit_representado)
    if len(cuit) != 11:
        raise RuntimeError(f"CUIT/CUIL invalido para consulta SRT: {cuit_representado}")

    candidates = [
        page.locator("#txtCuilCuit"),
        page.get_by_role("spinbutton", name="Ingrese CUIT/CUIL"),
        page.get_by_label(re.compile(r"CUIT/CUIL", re.I)),
        page.locator("input[aria-label*='CUIT']"),
        page.locator("input[placeholder*='CUIT']"),
        page.locator("input[id*='cuit' i], input[name*='cuit' i], input[type='number']"),
    ]
    for candidate in candidates:
        try:
            if await candidate.count() == 0:
                continue
            field = candidate.first
            await field.click(timeout=8_000)
            await field.fill("")
            await field.fill(cuit)
            value = _normalize_cuit(await field.input_value())
            if value == cuit:
                return
        except Exception:
            continue

    raise RuntimeError(f"No se encontro el campo para ingresar CUIT/CUIL en SRT. URL actual: {page.url}")


async def _find_consultar_button(page: Page):
    candidates = [
        page.locator("#btnConsultar"),
        page.get_by_role("button", name=re.compile(r"Consultar", re.I)),
        page.locator("input[type='submit'][value*='Consultar' i]"),
        page.locator("button:has-text('Consultar')"),
    ]
    for candidate in candidates:
        try:
            if await candidate.count() > 0:
                return candidate.first
        except Exception:
            continue
    return None


async def _wait_result_iframe(page: Page, cuit_representado: str, timeout_ms: int = 9_000) -> bool:
    cuit = _normalize_cuit(cuit_representado)
    try:
        await page.wait_for_function(
            """
            (cuit) => {
              const frame = document.querySelector('#ifrmResultadoPorCuit');
              if (!frame) return false;
              const src = (frame.getAttribute('src') || '').toLowerCase();
              return src.includes('alicuotasporcuit_resultado.aspx') && src.includes(cuit);
            }
            """,
            cuit,
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _force_open_results_modal(page: Page, cuit_representado: str) -> bool:
    cuit = _normalize_cuit(cuit_representado)
    try:
        forced = await page.evaluate(
            """
            (cuit) => {
              if (typeof CambiarDestino === 'function') {
                CambiarDestino(cuit);
                return true;
              }
              const iframe = document.querySelector('#ifrmResultadoPorCuit');
              if (!iframe) return false;
              iframe.setAttribute('src', '/Consultas/Alicuotas/AlicuotasPorCuit_Resultado.aspx?cuit=' + cuit);
              return true;
            }
            """,
            cuit,
        )
        return bool(forced)
    except Exception:
        return False


async def _extract_tables(page: Page, in_iframe: bool) -> list[dict]:
    target = page
    if in_iframe:
        iframe_locator = page.locator("#ifrmResultadoPorCuit, iframe[id*='Resultado' i], iframe[name*='Resultado' i]").first
        try:
            await iframe_locator.wait_for(state="attached", timeout=8_000)
            handle = await iframe_locator.element_handle()
            if handle is None:
                return []
            frame = await handle.content_frame()
            if frame is None:
                return []
            await frame.wait_for_load_state("domcontentloaded", timeout=7_000)
            target = frame
        except Exception:
            return []

    return await target.evaluate(
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
            while (prev && guard < 4) {
              const text = norm(prev.innerText || prev.textContent || '');
              if (text) return text;
              prev = prev.previousElementSibling;
              guard += 1;
            }
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


async def _extract_all_tables(page: Page) -> list[dict]:
    tables = await _extract_tables(page, in_iframe=True)
    if tables:
        return tables
    return await _extract_tables(page, in_iframe=False)


def _save_tables(descargas_dir: Path, tables: list[dict], cuit_representado: str) -> dict:
    cuit_suffix = _safe_name(cuit_representado)
    json_path = descargas_dir / f"srt_tablas_{cuit_suffix}.json"
    json_path.write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_files: list[str] = []
    for table in tables:
        idx = int(table.get("index", 0) or 0)
        title = str(table.get("title", f"tabla_{idx or 1}"))
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))
        if not rows:
            continue

        csv_path = descargas_dir / f"{cuit_suffix}_{idx:02d}_{_safe_name(title)}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            csv_headers = ["CUIT_REPRESENTADO", *headers] if headers and any(headers) else ["CUIT_REPRESENTADO"]
            writer.writerow(csv_headers)
            for row in rows:
                writer.writerow([cuit_representado, *row])
        csv_files.append(str(csv_path))

    return {"json": str(json_path), "csv": csv_files, "tables_count": len(tables)}


def _save_no_data_result(descargas_dir: Path, cuit_representado: str, message: str) -> dict:
    cuit_suffix = _safe_name(cuit_representado)
    payload = [
        {
            "index": 1,
            "title": "estado_consulta",
            "headers": ["ESTADO", "MENSAJE"],
            "rows": [["SIN_AFILIACION_VIGENTE", message]],
            "cuit_representado": cuit_representado,
        }
    ]

    json_path = descargas_dir / f"srt_tablas_{cuit_suffix}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = descargas_dir / f"{cuit_suffix}_00_estado_consulta.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["CUIT_REPRESENTADO", "ESTADO", "MENSAJE"])
        writer.writerow([cuit_representado, "SIN_AFILIACION_VIGENTE", message])

    return {
        "json": str(json_path),
        "csv": [str(csv_path)],
        "tables_count": 0,
        "status": "SIN_AFILIACION_VIGENTE",
    }


async def _extract_no_data_message(page: Page) -> str:
    modal_text = ""
    try:
        modal_text = str(
            await page.evaluate(
                """
                () => {
                  const modal = document.querySelector('#myModal .modal-body') || document.querySelector('#myModal');
                  return modal ? (modal.innerText || modal.textContent || '') : '';
                }
                """
            )
            or ""
        )
    except Exception:
        modal_text = ""

    if not modal_text.strip():
        try:
            iframe_locator = page.locator("#ifrmResultadoPorCuit").first
            if await iframe_locator.count() > 0:
                handle = await iframe_locator.element_handle()
                if handle is not None:
                    frame = await handle.content_frame()
                    if frame is not None:
                        modal_text = str(
                            await frame.evaluate("() => (document.body && (document.body.innerText || document.body.textContent)) || ''")
                            or ""
                        )
        except Exception:
            pass

    normalized_lines = [" ".join(line.split()) for line in re.split(r"[\r\n]+", modal_text) if line.strip()]
    for line in normalized_lines:
        for pattern in NO_DATA_PATTERNS:
            if pattern.search(line):
                return line

    merged_text = " ".join(normalized_lines)
    for pattern in NO_DATA_PATTERNS:
        if pattern.search(merged_text):
            return merged_text[:400]

    return ""


async def _download_no_data_pdf(page: Page, descargas_dir: Path, cuit_representado: str) -> str | None:
    cuit_suffix = _safe_name(cuit_representado)
    candidates = [
        page.locator("#myModal a:has-text('DESCARGAR PDF')"),
        page.locator("#myModal button:has-text('DESCARGAR PDF')"),
        page.get_by_role("link", name=re.compile(r"DESCARGAR\s+PDF", re.I)),
        page.get_by_role("button", name=re.compile(r"DESCARGAR\s+PDF", re.I)),
    ]

    for candidate in candidates:
        try:
            if await candidate.count() == 0:
                continue
            button = candidate.first
            async with page.expect_download(timeout=8_000) as download_info:
                await button.click(timeout=5_000)
            download = await download_info.value
            ext = os.path.splitext(download.suggested_filename or "")[1] or ".pdf"
            pdf_path = descargas_dir / f"{cuit_suffix}_00_estado_consulta{ext}"
            await download.save_as(str(pdf_path))
            return str(pdf_path)
        except Exception:
            continue

    return None


async def _close_modal_and_click_limpiar(page: Page) -> None:
    modal_open = False
    try:
        modal_open = bool(
            await page.evaluate(
                """
                () => {
                  const modal = document.querySelector('#myModal');
                  if (!modal) return false;
                  const display = window.getComputedStyle(modal).display;
                  return document.body.classList.contains('modal-open') || display !== 'none';
                }
                """
            )
        )
    except Exception:
        modal_open = False

    close_candidates = [
        page.locator("#myModal button.close"),
        page.locator("#myModal [data-dismiss='modal']"),
        page.get_by_role("button", name=re.compile(r"Cerrar|Close", re.I)),
    ]
    if modal_open:
        closed = False
        try:
            closed = bool(
                await page.evaluate(
                    """
                    () => {
                      const modal = document.querySelector('#myModal');
                      if (!modal) return false;
                      try {
                        if (window.$ && typeof window.$ === 'function' && window.$(modal).modal) {
                          window.$(modal).modal('hide');
                        }
                      } catch (e) {}
                      modal.classList.remove('in', 'show');
                      modal.style.display = 'none';
                      modal.setAttribute('aria-hidden', 'true');
                      return true;
                    }
                    """
                )
            )
        except Exception:
            closed = False

        for candidate in close_candidates:
            try:
                if await candidate.count() == 0:
                    continue
                await candidate.first.click(timeout=5_000)
                closed = True
                break
            except Exception:
                continue

        if not closed:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        try:
            await page.wait_for_function(
                "() => !document.body.classList.contains('modal-open')",
                timeout=1_500,
            )
        except Exception:
            pass

    await _force_clear_modal_overlay(page)

    try:
        invoked = bool(
            await page.evaluate(
                """
                () => {
                  try {
                    if (typeof limpiar === 'function') {
                      limpiar();
                      return true;
                    }
                  } catch (e) {}
                  return false;
                }
                """
            )
        )
        if invoked:
            await page.wait_for_timeout(120)
            return
    except Exception:
        pass

    limpiar_candidates = [
        page.locator("#btnLimpiar"),
        page.locator("input[type='button'][value*='Limpiar' i]"),
        page.get_by_role("button", name=re.compile(r"Limpiar", re.I)),
        page.get_by_text(re.compile(r"Limpiar", re.I)),
    ]
    for candidate in limpiar_candidates:
        try:
            if await candidate.count() == 0:
                continue
            await candidate.first.click(timeout=8_000)
            await page.wait_for_timeout(120)
            return
        except Exception:
            continue


async def _force_clear_modal_overlay(page: Page) -> None:
    try:
        await page.evaluate(
            """
            () => {
              const modal = document.querySelector('#myModal');
              if (modal) {
                modal.classList.remove('in', 'show');
                modal.style.display = 'none';
                modal.setAttribute('aria-hidden', 'true');
              }

              document.body.classList.remove('modal-open');
              document.body.style.overflow = '';
              document.body.style.paddingRight = '';

              const backdrops = Array.from(document.querySelectorAll('.modal-backdrop'));
              backdrops.forEach((el) => el.remove());
            }
            """
        )
    except Exception:
        pass


async def _click_consultar(page: Page, consultar_btn) -> None:
    try:
        await consultar_btn.click(timeout=15_000)
        return
    except Exception:
        pass

    try:
        await _close_modal_and_click_limpiar(page)
    except Exception:
        pass
    await _force_clear_modal_overlay(page)

    try:
        await consultar_btn.click(timeout=8_000)
    except Exception:
        await consultar_btn.click(timeout=8_000, force=True)


async def bot_srt_capmonster(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    cuit_consulta: list[str] | None = None,
    headless: bool = False,
    capmonster_api_key: str = CAPMONSTER_API_KEY_DEFAULT,
    capmonster_timeout_seconds: int = 180,
    capmonster_poll_seconds: int = 5,
) -> tuple[dict, list[str], bool]:
    results: dict = {"archivos": [], "consultas": []}
    error_logs: list[str] = []
    cuits_consulta: list[str] = []

    browser = None
    context = None
    page = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(accept_downloads=True, ignore_https_errors=True)
            page = await context.new_page()

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
                login_url=ARCA_SYSTEM_LOGIN_URL,
                fail_on_captcha=True,
            )
            if not login_result.success:
                return results, error_logs, False

            if not await _is_arca_home(page):
                error_logs.append(f"No se pudo iniciar sesión en ARCA. URL actual: {page.url}")
                return results, error_logs, False

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
                return results, error_logs, False

            if page_srt is not page and not page.is_closed():
                await page.close()
            await _maybe_click_ingresar_temporal(page_srt)
            page = await _open_srt_query_page(page_srt)

            cuits_consulta, invalid_cuits = _build_cuits_consulta(cuit_representado, cuit_consulta)
            if invalid_cuits:
                error_logs.append("Se omitieron CUITs invalidos en SRT_CUIT_CONSULTA: " + ", ".join(invalid_cuits))
            if not cuits_consulta:
                raise RuntimeError("No hay CUITs validos para consultar en SRT_CUIT_CONSULTA")

            consultas_ok = 0

            for cuit_actual in cuits_consulta:
                try:
                    try:
                        await _close_modal_and_click_limpiar(page)
                    except Exception:
                        pass
                    await _force_clear_modal_overlay(page)

                    await _fill_srt_cuit(page, cuit_actual)

                    if await _detect_captcha(page) and not await _is_captcha_valid(page):
                        await _solve_and_apply_recaptcha(
                            page,
                            capmonster_api_key,
                            capmonster_timeout_seconds,
                            capmonster_poll_seconds,
                        )

                    consultar_btn = await _find_consultar_button(page)
                    if consultar_btn is None:
                        raise RuntimeError(f"No se encontro el boton Consultar en SRT. URL actual: {page.url}")

                    await _click_consultar(page, consultar_btn)
                    iframe_ready = await _wait_result_iframe(page, cuit_actual)
                    if not iframe_ready:
                        await _force_open_results_modal(page, cuit_actual)
                        await page.wait_for_timeout(600)

                    no_data_message = await _extract_no_data_message(page)
                    if no_data_message:
                        saved = _save_no_data_result(DOWNLOAD_DIR, cuit_actual, no_data_message)
                        pdf_file = await _download_no_data_pdf(page, DOWNLOAD_DIR, cuit_actual)
                        files = [saved["json"], *saved["csv"]]
                        if pdf_file:
                            files.append(pdf_file)

                        results["archivos"].extend(files)
                        results["consultas"].append(
                            {
                                "cuit_representado": cuit_actual,
                                "ok": True,
                                "no_data": True,
                                "status": "SIN_AFILIACION_VIGENTE",
                                "message": no_data_message,
                                "tables_count": 0,
                                "archivos": files,
                            }
                        )
                        consultas_ok += 1
                        await _close_modal_and_click_limpiar(page)
                        continue

                    tables = await _extract_all_tables(page)
                    if not tables and await _detect_captcha(page):
                        await _solve_and_apply_recaptcha(
                            page,
                            capmonster_api_key,
                            capmonster_timeout_seconds,
                            capmonster_poll_seconds,
                        )
                        await _click_consultar(page, consultar_btn)
                        if not await _wait_result_iframe(page, cuit_actual):
                            await _force_open_results_modal(page, cuit_actual)

                        no_data_message = await _extract_no_data_message(page)
                        if no_data_message:
                            saved = _save_no_data_result(DOWNLOAD_DIR, cuit_actual, no_data_message)
                            pdf_file = await _download_no_data_pdf(page, DOWNLOAD_DIR, cuit_actual)
                            files = [saved["json"], *saved["csv"]]
                            if pdf_file:
                                files.append(pdf_file)

                            results["archivos"].extend(files)
                            results["consultas"].append(
                                {
                                    "cuit_representado": cuit_actual,
                                    "ok": True,
                                    "no_data": True,
                                    "status": "SIN_AFILIACION_VIGENTE",
                                    "message": no_data_message,
                                    "tables_count": 0,
                                    "archivos": files,
                                }
                            )
                            consultas_ok += 1
                            await _close_modal_and_click_limpiar(page)
                            continue

                        tables = await _extract_all_tables(page)

                    if not tables:
                        no_data_message = await _extract_no_data_message(page)
                        if no_data_message:
                            saved = _save_no_data_result(DOWNLOAD_DIR, cuit_actual, no_data_message)
                            pdf_file = await _download_no_data_pdf(page, DOWNLOAD_DIR, cuit_actual)
                            files = [saved["json"], *saved["csv"]]
                            if pdf_file:
                                files.append(pdf_file)

                            results["archivos"].extend(files)
                            results["consultas"].append(
                                {
                                    "cuit_representado": cuit_actual,
                                    "ok": True,
                                    "no_data": True,
                                    "status": "SIN_AFILIACION_VIGENTE",
                                    "message": no_data_message,
                                    "tables_count": 0,
                                    "archivos": files,
                                }
                            )
                            consultas_ok += 1
                            await _close_modal_and_click_limpiar(page)
                            continue

                        raise RuntimeError("No se encontraron datos de tablas en resultados SRT")

                    for table in tables:
                        table["cuit_representado"] = cuit_actual

                    saved = _save_tables(DOWNLOAD_DIR, tables, cuit_actual)
                    files = [saved["json"], *saved["csv"]]
                    results["archivos"].extend(files)
                    results["consultas"].append(
                        {
                            "cuit_representado": cuit_actual,
                            "ok": True,
                            "tables_count": saved["tables_count"],
                            "archivos": files,
                        }
                    )
                    consultas_ok += 1

                    await _close_modal_and_click_limpiar(page)

                except Exception as exc:
                    error_logs.append(f"CUIT {cuit_actual}: {exc}")
                    results["consultas"].append(
                        {
                            "cuit_representado": cuit_actual,
                            "ok": False,
                            "error": str(exc),
                        }
                    )
                    try:
                        if page is not None and not page.is_closed():
                            await _close_modal_and_click_limpiar(page)
                        elif context is not None:
                            page = await context.new_page()
                            await page.goto(SRT_ALICUOTAS_URL, timeout=45_000, wait_until="domcontentloaded")
                    except Exception as recover_exc:
                        error_logs.append(f"No se pudo recuperar la pagina SRT tras cierre inesperado: {recover_exc}")
                        break

            results["cuits_consulta"] = cuits_consulta
            results["consultas_ok"] = consultas_ok
            results["consultas_error"] = len(cuits_consulta) - consultas_ok

    except Exception as exc:
        error_logs.append(str(exc))
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

    overall_success = bool(results.get("consultas_ok", 0) > 0 and results.get("consultas_error", 0) == 0)
    return results, error_logs, overall_success


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
        return value if value > 0 else default
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
    cuit_consulta_raw = os.getenv("SRT_CUIT_CONSULTA", "")
    cuit_consulta = _parse_cuits_env(cuit_consulta_raw)
    if not cuit_representado and cuit_consulta:
        cuit_representado = cuit_consulta[0]

    headless = _env_flag("SRT_HEADLESS", "false")
    capmonster_api_key = os.getenv("SRT_CAPMONSTER_API_KEY", CAPMONSTER_API_KEY_DEFAULT).strip()
    capmonster_timeout_seconds = _env_int("SRT_CAPMONSTER_TIMEOUT_SECONDS", 180)
    capmonster_poll_seconds = _env_int("SRT_CAPMONSTER_POLL_SECONDS", 5)

    if not cuit_representante or not clave_representante or (not cuit_representado and not cuit_consulta):
        raise RuntimeError(
            "Faltan variables de entorno SRT_CUIT_REPRESENTANTE, "
            "SRT_CLAVE_REPRESENTANTE, SRT_CUIT_REPRESENTADO o SRT_CUIT_CONSULTA"
        )

    results, error_logs, success = await bot_srt_capmonster(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        cuit_consulta=cuit_consulta or None,
        headless=headless,
        capmonster_api_key=capmonster_api_key,
        capmonster_timeout_seconds=capmonster_timeout_seconds,
        capmonster_poll_seconds=capmonster_poll_seconds,
    )

    print(json.dumps({"success": success, "errors": error_logs, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())