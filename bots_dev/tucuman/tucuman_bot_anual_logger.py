"""
tucuman_bot_anual_logger.py - Logger version to PRESENT Tucumán ANUAL DJ (F.711).

Presenta la Declaración Jurada Anual para cierre del ejercicio para un año dado:
selecciona el año, carga la DJ Anual, Vista Previa, Presentar, confirma y descarga
acuse + DJ anual como PDF con capturas de pantalla y HTML por paso.

Basado en: tucuman_bot_anual_draft.py
Inspiración: tucuman_bot_presentar_logger.py
Convenciones: bots-base/AGENTS.md
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from playwright.async_api import Page, Playwright, async_playwright

BOT_NAME = "tucuman_anual"

# Root directory for temp output (project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"

TUCUMAN_SERVICE_URL = (
    "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
    "?action=SYSTEM&system=dgrtuc_ddjj"
)

# URL del listado de períodos/años dentro del servicio (tras login)
SIAPRE_LISTADO_URL = "https://extranet.rentastucuman.gov.ar/nomina/rentastuc2/intranet/SiapreWeb/"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


# ---------------------------------------------------------------------------
# DebugCapture  (AGENTS.md § Estructura obligatoria)
# ---------------------------------------------------------------------------

class DebugCapture:
    def __init__(self, base_dir: str = "", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(base_dir) if base_dir else TEMP_DIR
        self.run_dir = base / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.descargas_dir = self.run_dir / "descargas"
        self.html_dir = self.run_dir / "html"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.har_dir = self.run_dir / "har"

        for path in (
            self.logs_dir, self.descargas_dir, self.html_dir,
            self.screenshots_dir, self.har_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.log(f"run_dir={self.run_dir}")

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page: Optional[Page], step_name: str, result: str = "OK") -> None:
        if page is None:
            self.log(f"capture_skipped step={step_name} page=None result={result}")
            return

        self.step += 1
        file_prefix = f"{self.step:03d}_{_safe_step_name(step_name)}"
        screenshot_path = self.screenshots_dir / f"{file_prefix}.png"
        html_path = self.html_dir / f"{file_prefix}.html"

        self.log(f"step={self.step} name={step_name} result={result} url={getattr(page, 'url', '')}")

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True, timeout=30_000)
            self.log(f"screenshot={screenshot_path}")
        except Exception as exc:
            self.log(f"screenshot_failed step={step_name} error={exc}")

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            self.log(f"html={html_path}")
        except Exception as exc:
            self.log(f"html_failed step={step_name} error={exc}")


async def _run_step(
    debug: DebugCapture,
    page: Optional[Page],
    step_name: str,
    action,
) -> None:
    debug.log(f"step_start={step_name}")
    try:
        await action()
        debug.log(f"step_end={step_name} status=OK")
        await debug.capture(page, step_name, "OK")
    except Exception as exc:
        debug.log(f"step_end={step_name} status=ERROR error={exc}")
        await debug.capture(page, f"{step_name}_error", f"ERROR: {exc}")
        raise


# ---------------------------------------------------------------------------
# ARCA / AFIP Login  (AGENTS.md § Particularidades ARCA)
# ---------------------------------------------------------------------------

async def _login_at_service_url(page: Page, debug: DebugCapture, cuit: str, clave: str) -> None:
    """Login to ARCA via the Tucumán service URL so redirect lands on service page."""

    await _run_step(
        debug, page, "001_ir_a_servicio_tucuman",
        lambda: page.goto(TUCUMAN_SERVICE_URL, timeout=60_000),
    )
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2_000)
    await debug.capture(page, "001b_pagina_login", "OK")

    # ── CUIT ──
    spinbutton = page.get_by_role("spinbutton")
    if await spinbutton.count() > 0:
        await _run_step(
            debug, page, "002_ingresar_cuit",
            lambda: spinbutton.fill(cuit),
        )
        await _run_step(
            debug, page, "003_press_enter_cuit",
            lambda: spinbutton.press("Enter"),
        )
        await page.wait_for_timeout(3_000)

    # Detectar captcha  (AGENTS.md § 1)
    captcha_selectors = [
        page.get_by_role("img", name=re.compile(r"Captcha", re.I)),
        page.locator("img[alt*='captcha' i], img[src*='captcha' i]"),
        page.locator("[id*='captcha' i], [class*='captcha' i]"),
    ]
    for sel in captcha_selectors:
        try:
            if await sel.count() > 0 and await sel.first.is_visible():
                debug.log("Captcha detectado")
                await debug.capture(page, "captcha_detectado", "CAPTCHA")
                raise RuntimeError("Captcha detectado")
        except RuntimeError:
            raise
        except Exception:
            continue

    # ── Clave ──
    clave_textbox = page.get_by_role("textbox", name="TU CLAVE")
    if await clave_textbox.count() == 0:
        sig_btn = page.get_by_role("button", name="Siguiente")
        if await sig_btn.count() > 0:
            await _run_step(debug, page, "003b_click_siguiente", lambda: sig_btn.click())
            await page.wait_for_timeout(3_000)

    if await clave_textbox.count() > 0:
        await _run_step(
            debug, page, "004_ingresar_clave",
            lambda: clave_textbox.fill(clave),
        )
        await _run_step(
            debug, page, "005_click_ingresar",
            lambda: page.get_by_role("button", name="Ingresar").click(),
        )
    else:
        fallback = page.locator("input[type='password']")
        if await fallback.count() > 0:
            await _run_step(debug, page, "004b_ingresar_clave_fallback", lambda: fallback.fill(clave))
            submit = page.get_by_role("button", name=re.compile(r"Ingresar", re.I))
            if await submit.count() > 0:
                await _run_step(debug, page, "005b_click_ingresar", lambda: submit.click())

    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(4_000)

    # Detectar cambio de clave  (AGENTS.md § 2)
    cambio = page.get_by_role("heading", name=re.compile(r"Cambiar\s+Clave\s+Fiscal", re.I))
    if await cambio.count() > 0 and await cambio.first.is_visible():
        debug.log("Se requiere cambiar la clave fiscal")
        await debug.capture(page, "cambio_clave_fiscal", "CLAVE_REQUIRED")
        raise RuntimeError("Se requiere cambiar la clave fiscal")

    await debug.capture(page, "006_post_login", "OK")

    # ── Close / Confirmar ──
    try:
        close_btn = page.get_by_role("button", name="Close")
        if await close_btn.count() > 0 and await close_btn.first.is_visible():
            await _run_step(debug, page, "007_cerrar_popup", lambda: close_btn.click())
            await page.wait_for_timeout(1_500)
    except Exception:
        pass

    try:
        confirm_btn = page.get_by_role("button", name=re.compile(r"Confirmar", re.I))
        if await confirm_btn.count() > 0 and await confirm_btn.first.is_visible():
            await _run_step(debug, page, "008_confirmar_servicio", lambda: confirm_btn.click())
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2_000)
    except Exception:
        pass

    await debug.capture(page, "009_login_completado", "OK")


# ---------------------------------------------------------------------------
# Navegación al servicio F.611 / F.711 Web
# ---------------------------------------------------------------------------

async def _navigate_to_f611(page: Page, debug: DebugCapture) -> None:
    """Click through navigation links to reach F.611 / F.711 Web."""

    links_to_try = [
        ("Presentación de DJs Web", "010_click_presentacion_djs"),
        ("SiAPre WEB", "011_click_siapre_web"),
        ("F.611 / F.711 Web", "012_click_f611_f711"),
    ]

    for link_name, step_name in links_to_try:
        try:
            link = page.get_by_role("link", name=link_name)
            if await link.count() == 0:
                debug.log(f"link_no_encontrado link={link_name}")
                continue
            await _run_step(debug, page, step_name, lambda l=link: l.click())
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2_000)
        except Exception as exc:
            debug.log(f"link_error link={link_name} error={exc}")
            try:
                alt = page.locator(f"a:has-text('{link_name}'), button:has-text('{link_name}')")
                if await alt.count() > 0:
                    await _run_step(debug, page, f"{step_name}_alt", lambda: alt.first.click())
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(2_000)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

async def _scrape_page_data(page: Page, debug: DebugCapture, tag: str) -> dict:
    data: dict[str, Any] = {}
    try:
        scraped = await page.evaluate("""
            () => {
                const r = { form_fields: {}, tables: {}, labels: {}, url: location.href };

                document.querySelectorAll('input, select, textarea').forEach(el => {
                    const id = el.id || el.name || el.className;
                    if (!id) return;
                    const tag = el.tagName.toLowerCase();
                    const field = {};
                    if (tag === 'select') {
                        field.value = el.value;
                        const opts = {};
                        el.querySelectorAll('option').forEach(o => {
                            if (o.value) opts[o.value] = o.textContent;
                        });
                        field.options = opts;
                    } else if (tag === 'textarea') {
                        field.value = el.value;
                    } else if (el.type === 'radio' || el.type === 'checkbox') {
                        field.value = el.checked ? el.value : '';
                        field.checked = el.checked;
                    } else {
                        field.value = el.value;
                    }
                    r.form_fields[id] = field;
                });

                document.querySelectorAll('table').forEach((tbl, idx) => {
                    const tid = tbl.id || `table_${idx}`;
                    const rows = [];
                    tbl.querySelectorAll('tr').forEach(row => {
                        const cells = [];
                        row.querySelectorAll('td, th').forEach(cell => {
                            cells.push({
                                text: cell.textContent.trim(),
                                colspan: cell.getAttribute('colspan') ? parseInt(cell.getAttribute('colspan'), 10) : 1,
                                rowspan: cell.getAttribute('rowspan') ? parseInt(cell.getAttribute('rowspan'), 10) : 1,
                            });
                        });
                        if (cells.length) rows.push(cells);
                    });
                    if (rows.length) r.tables[tid] = rows;
                });

                document.querySelectorAll('label').forEach(lbl => {
                    const forAttr = lbl.getAttribute('for');
                    const text = lbl.textContent.trim();
                    if (forAttr && text) r.labels[forAttr] = text;
                });

                return r;
            }
        """)
        data.update(scraped)
        debug.log(f"scrape_ok tag={tag} fields={len(data.get('form_fields', {}))}")
    except Exception as exc:
        debug.log(f"scrape_error tag={tag} error={exc}")
    return data


async def _scrape_dj_tables(page: Page, debug: DebugCapture, anio: str) -> dict:
    """Scrape the DJ Anual Vista Previa tables into structured fields."""
    data: dict[str, Any] = {"anio": anio, "tipo": "anual"}
    try:
        scraped = await page.evaluate("""
            () => {
                const r = {};
                const tables = document.querySelectorAll('table');

                function tableToGrid(table) {
                    const grid = [];
                    table.querySelectorAll('tr').forEach(row => {
                        const cells = [];
                        row.querySelectorAll('td, th').forEach(cell => {
                            let text = cell.textContent.trim();
                            text = text.replace(/[ \\t]+/g, ' ');
                            cells.push(text);
                        });
                        if (cells.some(c => c.length > 0)) grid.push(cells);
                    });
                    return grid;
                }

                const grids = [];
                tables.forEach(tbl => grids.push(tableToGrid(tbl)));
                r._tables_grid = grids;

                // Buscar encabezados comunes de la DJ Anual
                grids.forEach(grid => {
                    grid.forEach(row => {
                        const joined = row.join(' ').trim();
                        // CUIT / denominación
                        if (/C\\.U\\.I\\.T/i.test(joined) && !r.cuit) {
                            const m = joined.match(/C\\.U\\.I\\.T[^0-9]{0,10}(\\d{11})/i);
                            if (m) r.cuit = m[1];
                        }
                        // Total Impuesto Anual / Determinado
                        if (/Impuesto\\s+Determinado/i.test(joined) && !r.impuesto_determinado) {
                            const m = joined.match(/(\\d[\\d.,]*)\\s*$/);
                            if (m) r.impuesto_determinado = m[1];
                        }
                        if (/Total\\s+Ingresos/i.test(joined) && !r.total_ingresos) {
                            const m = joined.match(/(\\d[\\d.,]*)\\s*$/);
                            if (m) r.total_ingresos = m[1];
                        }
                    });
                });

                return r;
            }
        """)
        data.update(scraped)
        debug.log(f"scrape_dj_anual_ok anio={anio} cuit={data.get('cuit','')}")
    except Exception as exc:
        debug.log(f"scrape_dj_anual_error anio={anio} error={exc}")
    return data


async def _save_json(data: dict, path: Path, debug: DebugCapture) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        debug.log(f"json_guardado path={path} size={path.stat().st_size}")
    except Exception as exc:
        debug.log(f"json_error path={path} error={exc}")


# ---------------------------------------------------------------------------
# Procesamiento de la DJ Anual para un año
# ---------------------------------------------------------------------------

async def _process_anual(page: Page, debug: DebugCapture, anio: str) -> dict:
    """Carga y presenta la DJ Anual para el año dado."""
    result: dict[str, Any] = {"anio": anio, "tipo": "anual", "status": "error"}

    # ── Seleccionar año en #anio ──
    anio_sel = page.locator("#anio")
    if await anio_sel.count() == 0:
        # Quizás estamos en otra vista; intentar Nueva Consulta
        try:
            nueva_consulta = page.get_by_role("button", name=re.compile(r"Nueva Consulta", re.I))
            if await nueva_consulta.count() > 0 and await nueva_consulta.first.is_visible():
                await _run_step(debug, page, f"{anio}_nueva_consulta", lambda: nueva_consulta.click(no_wait_after=True))
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2_000)
        except Exception:
            pass
        anio_sel = page.locator("#anio")

    try:
        await _run_step(
            debug, page, f"{anio}_seleccionar_anio",
            lambda: page.locator("#anio").select_option(anio),
        )
    except Exception as exc:
        debug.log(f"{anio}_error_anio error={exc}")
        result["error"] = f"No se pudo seleccionar año {anio}: {exc}"
        return result

    # ── Buscar ──
    buscar_btn = page.locator("#btn_BuscarDjsxAnio")
    if await buscar_btn.count() == 0:
        buscar_btn = page.get_by_role("button", name=re.compile(r"Buscar", re.I))
    await _run_step(
        debug, page, f"{anio}_buscar",
        lambda: buscar_btn.first.click(),
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    await page.wait_for_timeout(3_000)

    # ── Click botón que abre el modal #cargaAnual (Cargar o Rectificar) ──
    try:
        cargar_trigger = page.locator('button[data-target="#cargaAnual"]')
        if await cargar_trigger.count() == 0:
            cargar_trigger = page.locator(f"tr:has-text('Anual {anio}') button:has-text('Cargar'), tr:has-text('Anual {anio}') button:has-text('Rectificar')")
        if await cargar_trigger.count() == 0:
            msg = f"Año {anio}: botón Cargar/Rectificar de DJ Anual no encontrado en la grilla"
            debug.log(msg)
            result["error"] = msg
            return result
        await _run_step(
            debug, page, f"{anio}_abrir_modal_anual",
            lambda: cargar_trigger.first.click(),
        )
        await page.wait_for_timeout(2_000)

        # ── Click "DJ ANUAL PARA CIERRE DEL PERIODO FISCAL" (radio) ──
        try:
            dj_anual_text = page.get_by_text("DJ ANUAL PARA CIERRE DEL", exact=False)
            if await dj_anual_text.count() > 0:
                await _run_step(
                    debug, page, f"{anio}_click_dj_anual_cierre",
                    lambda: dj_anual_text.first.click(),
                )
                await page.wait_for_timeout(1_000)
            else:
                debug.log(f"{anio}_dj_anual_cierre_text_no_encontrado")
        except Exception as exc:
            debug.log(f"{anio}_dj_anual_cierre_error error={exc}")

        # ── Click "Cargar" dentro del modal (value=view_djAnual) ──
        f711_modal = page.locator("#cargaAnual")
        cargar_modal_btn = f711_modal.locator('button[name="accion"][value="view_djAnual"]')
        if await cargar_modal_btn.count() == 0:
            cargar_modal_btn = f711_modal.get_by_role("button", name=re.compile(r"Cargar", re.I))
        if await cargar_modal_btn.count() == 0:
            cargar_modal_btn = page.locator('button[name="accion"][value="view_djAnual"]')
        if await cargar_modal_btn.count() == 0:
            msg = f"Año {anio}: botón Cargar no encontrado dentro del modal #cargaAnual"
            debug.log(msg)
            result["error"] = msg
            return result
        await _run_step(
            debug, page, f"{anio}_cargar_dj_anual",
            lambda: cargar_modal_btn.first.click(),
        )
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3_000)
    except Exception as exc:
        debug.log(f"{anio}_cargar_error error={exc}")
        result["error"] = f"No se pudo clickear Cargar: {exc}"
        return result

    # ── Scrape del formulario de DJ Anual ──
    form_data = await _scrape_page_data(page, debug, f"{anio}_form_anual")
    result["formulario"] = form_data
    json_path_form = debug.descargas_dir / f"{anio}_formulario.json"
    await _save_json(form_data, json_path_form, debug)

    # ── Vista Previa ──
    try:
        vista_previa_btn = page.get_by_role("button", name=re.compile(r"Vista Previa", re.I))
        if await vista_previa_btn.count() > 0:
            await _run_step(
                debug, page, f"{anio}_vista_previa",
                lambda: vista_previa_btn.first.click(no_wait_after=True),
            )
            await page.wait_for_timeout(3_000)
        else:
            debug.log(f"{anio}_vista_previa_boton_no_encontrado")
    except Exception as exc:
        debug.log(f"{anio}_vista_previa_error error={exc}")

    # ── Scrape DJ data ANTES de presentar ──
    await debug.capture(page, f"{anio}_dj_vista_previa", "OK")
    dj_data = await _scrape_dj_tables(page, debug, anio)
    result["dj_json"] = dj_data
    dj_json_path = debug.descargas_dir / f"{anio}_dj_completo.json"
    await _save_json(dj_data, dj_json_path, debug)

    # Guardar HTML del formulario DJ
    try:
        html_content = await page.content()
        html_path = debug.descargas_dir / f"{anio}_dj_formulario.html"
        html_path.write_text(html_content, encoding="utf-8")
        debug.log(f"html_formulario_guardado path={html_path} size={html_path.stat().st_size}")
    except Exception as exc:
        debug.log(f"html_formulario_error error={exc}")

    # ── Presentar Declaración ──
    presento_ok = False
    try:
        presentar_btn = page.get_by_role("button", name=re.compile(r"Presentar", re.I))
        if await presentar_btn.count() > 0 and await presentar_btn.first.is_visible():
            await _run_step(
                debug, page, f"{anio}_presentar",
                lambda: presentar_btn.first.click(no_wait_after=True),
            )
            await page.wait_for_timeout(2_000)

            # Checkbox de aceptación de términos (#my_checkbox según draft)
            try:
                checkbox = page.locator("#my_checkbox")
                if await checkbox.count() > 0:
                    await _run_step(
                        debug, page, f"{anio}_aceptar_terminos",
                        lambda: checkbox.check(),
                    )
                    await page.wait_for_timeout(1_000)
                else:
                    # fallback: cualquier checkbox/radio de aceptación
                    acepto = page.get_by_role("checkbox", name=re.compile(r"Acepto", re.I))
                    if await acepto.count() == 0:
                        acepto = page.locator("input[type='checkbox'], input[type='radio']").first
                    if await acepto.count() > 0:
                        await _run_step(debug, page, f"{anio}_aceptar_terminos_fallback", lambda: acepto.check())
                        await page.wait_for_timeout(1_000)
            except Exception as exc:
                debug.log(f"{anio}_aceptar_terminos_error error={exc}")

            # Confirmar presentación
            try:
                confirm_btn = page.get_by_role("button", name="Confirmar")
                if await confirm_btn.count() > 0:
                    await _run_step(
                        debug, page, f"{anio}_confirmar_presentacion",
                        lambda: confirm_btn.first.click(no_wait_after=True),
                    )
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(3_000)
                    presento_ok = True
                else:
                    debug.log(f"{anio}_confirmar_no_encontrado")
            except Exception as exc:
                debug.log(f"{anio}_confirmar_presentacion_error error={exc}")
        else:
            debug.log(f"{anio}_presentar_skip motivo=boton_no_visible")
    except Exception as exc:
        debug.log(f"{anio}_presentar_error error={exc}")
        result["error_presentar"] = str(exc)

    # ── Descargar Acuse + DJ Anual (el servidor devuelve PDFs como descarga) ──
    if presento_ok:
        await debug.capture(page, f"{anio}_presentacion_exitosa", "OK")
        result["status"] = "presentado"

        # 1. Imprimir Acuse de Recibo (descarga PDF)
        try:
            acuse_link = page.get_by_role("link", name=re.compile(r"Imprimir Acuse de Recibo", re.I))
            if await acuse_link.count() == 0:
                acuse_link = page.locator("a:has-text('Acuse')")
            if await acuse_link.count() > 0:
                debug.log(f"{anio}_descargando_acuse")
                try:
                    async with page.expect_download(timeout=30_000) as download_info:
                        await acuse_link.first.click()
                    download = await download_info.value
                    acuse_path = debug.descargas_dir / f"{anio}_acuse_recibo.pdf"
                    await download.save_as(str(acuse_path))
                    debug.log(f"acuse_pdf_guardado path={acuse_path} size={acuse_path.stat().st_size}")
                    result["acuse_pdf"] = str(acuse_path.name)
                except Exception as exc:
                    debug.log(f"{anio}_acuse_download_error error={exc}")
            else:
                debug.log(f"{anio}_acuse_link_no_encontrado")
        except Exception as exc:
            debug.log(f"{anio}_acuse_error error={exc}")

        # 2. Declaración Jurada Anual (descarga PDF)
        try:
            dj_link = page.get_by_role("link", name=re.compile(r"Declaración Jurada Anual", re.I))
            if await dj_link.count() == 0:
                dj_link = page.locator("a:has-text('Declaración Jurada')")
            if await dj_link.count() > 0:
                debug.log(f"{anio}_descargando_dj_anual")
                try:
                    async with page.expect_download(timeout=30_000) as download_info:
                        await dj_link.first.click()
                    download = await download_info.value
                    dj_path = debug.descargas_dir / f"{anio}_dj_anual.pdf"
                    await download.save_as(str(dj_path))
                    debug.log(f"dj_anual_pdf_guardado path={dj_path} size={dj_path.stat().st_size}")
                    result["dj_anual_pdf"] = str(dj_path.name)
                except Exception as exc:
                    debug.log(f"{anio}_dj_anual_download_error error={exc}")
            else:
                debug.log(f"{anio}_dj_anual_link_no_encontrado")
        except Exception as exc:
            debug.log(f"{anio}_dj_anual_error error={exc}")

    # ── Captura final ──
    await debug.capture(page, f"{anio}_dj_anual_presentada", "OK")
    try:
        html_content = await page.content()
        html_path = debug.descargas_dir / f"{anio}_dj_presentada.html"
        html_path.write_text(html_content, encoding="utf-8")
        debug.log(f"html_presentada_guardado path={html_path} size={html_path.stat().st_size}")
    except Exception as exc:
        debug.log(f"html_presentada_error error={exc}")

    if result["status"] != "presentado":
        result["status"] = "error"
    debug.log(f"anio_completado anio={anio} status={result['status']}")
    return result


# ---------------------------------------------------------------------------
# Bot principal
# ---------------------------------------------------------------------------

async def bot_tucuman_anual(
    playwright: Playwright,
    cuit: str,
    clave: str,
    anio: str,
    headless: bool = False,
) -> dict:
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(f"Iniciando bot Tucumán Anual Logger anio={anio} headless={headless}")

    page: Optional[Page] = None
    context = None
    browser = None
    result: dict[str, Any] = {"anio": anio, "status": "error"}

    try:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
        )
        page = await context.new_page()
        page.set_default_timeout(30_000)
        await debug.capture(page, "000_inicio", "OK")

        # ── Login (directamente en URL del servicio) ──
        await _login_at_service_url(page, debug, cuit, clave)

        # ── Navegar a F.611 ──
        await _navigate_to_f611(page, debug)

        # ── Procesar DJ Anual ──
        result = await _process_anual(page, debug, anio)

        # Guardar resumen
        summary = {
            "bot": BOT_NAME,
            "anio": anio,
            "resultado": result,
        }
        summary_path = debug.descargas_dir / "resultados.json"
        await _save_json(summary, summary_path, debug)
        debug.log("Flujo completado OK")

    except Exception as exc:
        debug.log(f"error_general={exc}")
        result["status"] = "error"
        result["error_general"] = str(exc)
        if page is not None:
            await debug.capture(page, "999_error_general", f"ERROR: {exc}")
        raise
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

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    load_dotenv()

    cuit = os.getenv("REPRESENTANTE") or os.getenv("CUIT_REPRESENTANTE") or ""
    clave = os.getenv("CLAVE") or os.getenv("ARCA_CLAVE") or ""
    anio = os.getenv("TUCUMAN_ANIO") or os.getenv("ANIO") or ""

    if not anio:
        anio = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or input("Año a presentar (ej: 2017): ").strip()
    if not cuit:
        cuit = input("CUIT: ").strip()
    if not clave:
        clave = input("Clave fiscal: ").strip()

    print(f"[{BOT_NAME}] Ejecutando logger | CUIT: {cuit} | Año: {anio}")

    headless_str = os.getenv("TUCUMAN_HEADLESS", "True")
    headless = headless_str.lower() == "true"

    async with async_playwright() as playwright:
        result = await bot_tucuman_anual(
            playwright=playwright,
            cuit=cuit,
            clave=clave,
            anio=anio,
            headless=headless,
        )

    print(f"[{BOT_NAME}] Logger finalizado. status={result.get('status')}")


if __name__ == "__main__":
    asyncio.run(main())
