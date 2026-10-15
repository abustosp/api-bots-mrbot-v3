"""
tucuman_bot_logger.py - Logger version for Tucumán F.611/F.711 Web DJ drafts.

Procesa un rango de periodos (desde-hasta en formato YYYYMM), carga las secciones
de retenciones y percepciones (r12/r13/r14), guarda como borrador, y scrapea todos
los datos de la DJ como JSON con capturas de pantalla.

Basado en: tucuman_bot_draft.py
Convenciones: bots-base/AGENTS.md
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from playwright.async_api import Page, Playwright, async_playwright

BOT_NAME = "tucuman"

TUCUMAN_SERVICE_URL = (
    "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
    "?action=SYSTEM&system=dgrtuc_ddjj"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _generate_periods(desde: str, hasta: str) -> list[str]:
    if not re.fullmatch(r"\d{6}", desde) or not re.fullmatch(r"\d{6}", hasta):
        raise ValueError(f"Periodos deben estar en formato YYYYMM: desde={desde!r} hasta={hasta!r}")
    y1, m1 = int(desde[:4]), int(desde[4:])
    y2, m2 = int(hasta[:4]), int(hasta[4:])
    periods: list[str] = []
    y, m = y1, m1
    while (y < y2) or (y == y2 and m <= m2):
        periods.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return periods


def _group_periods_by_year(periods: list[str]) -> dict[str, list[str]]:
    by_year: dict[str, list[str]] = {}
    for p in periods:
        by_year.setdefault(p[:4], []).append(p)
    return by_year


# ---------------------------------------------------------------------------
# DebugCapture  (AGENTS.md § Estructura obligatoria)
# ---------------------------------------------------------------------------

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
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
        # Maybe the page didn't transition; try clicking Siguiente first
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
        # fallback: try filling directly
        fallback = page.locator("input[type='password']")
        if await fallback.count() > 0:
            await _run_step(debug, page, "004b_ingresar_clave_fallback", lambda: fallback.fill(clave))
            submit = page.get_by_role("button", name=re.compile(r"Ingresar", re.I))
            if await submit.count() > 0:
                await _run_step(debug, page, "005b_click_ingresar", lambda: submit.click())

    # Wait for auth to complete
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
            # try other selectors
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

async def _scrape_page_data(page: Page, debug: DebugCapture, periodo: str, tag: str) -> dict:
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
        debug.log(f"scrape_ok period={periodo} tag={tag} fields={len(data.get('form_fields', {}))}")
    except Exception as exc:
        debug.log(f"scrape_error period={periodo} tag={tag} error={exc}")
    return data


async def _scrape_dj_tables(page: Page, debug: DebugCapture, periodo: str) -> dict:
    """Scrape the DJ Vista Previa tables into structured fields."""
    data: dict[str, Any] = {"periodo": periodo}
    try:
        scraped = await page.evaluate("""
            () => {
                const r = {};
                const tables = document.querySelectorAll('table');

                // --- Helper: extract text from table cells as a flat grid ---
                function tableToGrid(table) {
                    const grid = [];
                    table.querySelectorAll('tr').forEach(row => {
                        const cells = [];
                        row.querySelectorAll('td, th').forEach(cell => {
                            let text = cell.textContent.trim();
                            // Collapse multiple spaces
                            text = text.replace(/[ \t]+/g, ' ');
                            cells.push(text);
                        });
                        if (cells.some(c => c.length > 0)) grid.push(cells);
                    });
                    return grid;
                }

                // --- Extract each table to grid ---
                const grids = [];
                tables.forEach(tbl => grids.push(tableToGrid(tbl)));
                r._tables_grid = grids;

                // --- Header identification (grid index 1) ---
                if (grids.length > 1) {
                    const hdr = grids[1];
                    if (hdr.length >= 2) {
                        for (let ci = 0; ci < hdr[0].length && ci < hdr[1].length; ci++) {
                            const label = hdr[0][ci];
                            if (label.includes('C.U.I.T')) r.cuit = hdr[1][ci];
                            if (label.includes('Apellido') || label.includes('Denominaci')) {
                                r.denominacion = hdr[1][ci];
                            }
                        }
                    }
                }

                // --- Period info (grid index 2) ---
                if (grids.length > 2) {
                    const per = grids[2];
                    if (per.length >= 2) {
                        for (let ci = 0; ci < per[0].length && ci < per[1].length; ci++) {
                            const label = per[0][ci];
                            if (/^Anticipo$/i.test(label.trim())) r.anticipo_numero = per[1][ci];
                            if (/Año/i.test(label)) r.anio = per[1][ci];
                            if (/Secuencia/i.test(label)) r.secuencia = per[1][ci];
                            if (/Fecha de Presentaci/i.test(label)) r.fecha_presentacion = per[1][ci];
                            if (/Número de Obligaci/i.test(label)) r.numero_obligacion = per[1][ci];
                        }
                    }
                }

                // --- Activity + totals (grid index 3) ---
                if (grids.length > 3) {
                    const det = grids[3];
                    r.detalle_anticipo = [];

                    for (let ri = 0; ri < det.length; ri++) {
                        const row = det[ri];
                        const joined = row.join(' ').trim();

                        // Activity data row (Row 2: ['', 'Servicios jurídicos...', '', '', ''])
                        if (ri === 2 && row.length >= 5) {
                            r.detalle_anticipo.push({
                                codigo: row[0],
                                actividad: row[1],
                                base_calculo: row[2],
                                porcentaje: row[3],
                                anticipo: row[4],
                            });
                        }

                        // Forma de Ingreso lines — label in col 0, value in col 1
                        const label = (row[0] || '').trim();
                        const value = (row[1] || '').trim();

                        if (/Importe Determinado en Concepto de Anticipo/i.test(label)) {
                            r.importe_determinado_anticipo = value;
                        } else if (/Anticipo del mes/i.test(label)) {
                            r.anticipo_del_mes = value;
                        } else if (/Anticipo o Mínimo Mensual/i.test(label)) {
                            r.anticipo_minimo_mensual = value;
                        } else if (/Menos Pago a cuenta/i.test(label)) {
                            r.menos_pago_cuenta = value;
                        } else if (/Menos Saldo favorable Anticipo anterior/i.test(label)) {
                            r.menos_saldo_favorable_anticipo = value;
                        } else if (/SUBTOTAL[ \t]*[(][ \t]*1[ \t]*[)]/i.test(label)) {
                            r.subtotal_1 = value;
                        } else if (/Menos Saldo favorable Impuesto/i.test(label)) {
                            r.menos_saldo_favorable_impuesto = value;
                        } else if (/SUBTOTAL[ \t]*[(][ \t]*2[ \t]*[)]/i.test(label)) {
                            r.subtotal_2 = value;
                        } else if (/Menos Recaudación bancaria/i.test(label)) {
                            r.menos_recaudacion_bancaria = value;
                        } else if (/Menos Retenciones según anexo/i.test(label)) {
                            r.menos_retenciones = value;
                        } else if (/Menos Percepciones según anexo/i.test(label)) {
                            r.menos_percepciones = value;
                        } else if (/Certificados de créditos fiscales.*transferibles/i.test(label)) {
                            r.certificados_transferibles = value;
                        } else if (/Certificados de créditos fiscales.*no transferibles/i.test(label)) {
                            r.certificados_no_transferibles = value;
                        } else if (/Otros Regímenes Promocionales/i.test(label)) {
                            r.otros_regimenes_promocionales = value;
                        } else if (/A favor del contribuyente/i.test(label)) {
                            r.a_favor_contribuyente = value;
                        } else if (/A favor DGR/i.test(label)) {
                            r.a_favor_dgr = value;
                        }

                        // Also check row[2] for "Anticipo" + "12" + "Año" + "2016" (nested table)
                        if (row.length >= 6 && /Anticipo/i.test(row[2]) && /Año/i.test(row[4])) {
                            r.anticipo_valor = row[3];
                            r.anticipo_anio = row[5];
                        }
                    }
                }

                return r;
            }
        """)
        data.update(scraped)
        debug.log(f"scrape_dj_ok period={periodo} cuit={data.get('cuit','')} denominacion={data.get('denominacion','')}")
    except Exception as exc:
        debug.log(f"scrape_dj_error period={periodo} error={exc}")
    return data


async def _save_json(data: dict, path: Path, debug: DebugCapture) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        debug.log(f"json_guardado path={path} size={path.stat().st_size}")
    except Exception as exc:
        debug.log(f"json_error path={path} error={exc}")


# ---------------------------------------------------------------------------
# Procesamiento de un período
# ---------------------------------------------------------------------------

RET_PER_SECTIONS = [
    {"button": "#r12Button", "name": "retenciones_r12"},
    {"button": "#r13Button", "name": "percepciones_r13"},
    {"button": "#r14Button", "name": "adicional_r14"},
]


async def _process_period(page: Page, debug: DebugCapture, periodo: str) -> dict:
    """Load ret/per sections, save borrador, then scrape + screenshot."""
    result: dict[str, Any] = {"periodo": periodo, "status": "error", "secciones": {}}
    year = periodo[:4]

    # ── Year + search (handle "Nueva Consulta" if needed) ──
    # Check if #anio is available; if not, click "Nueva Consulta" first
    anio_sel = page.locator("#anio")
    if await anio_sel.count() == 0:
        try:
            nueva_consulta = page.get_by_role("button", name=re.compile(r"Nueva Consulta", re.I))
            if await nueva_consulta.count() > 0 and await nueva_consulta.first.is_visible():
                await _run_step(debug, page, f"{periodo}_nueva_consulta", lambda: nueva_consulta.click(no_wait_after=True))
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2_000)
        except Exception:
            pass

    try:
        await _run_step(
            debug, page, f"{periodo}_seleccionar_anio",
            lambda: page.locator("#anio").select_option(year),
        )
    except Exception as exc:
        debug.log(f"{periodo}_error_anio error={exc}")
        result["error"] = f"No se pudo seleccionar año {year}: {exc}"
        return result

    await _run_step(
        debug, page, f"{periodo}_buscar",
        lambda: page.get_by_role("button", name=re.compile(r"Buscar", re.I)).click(no_wait_after=True),
    )
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2_000)

    # ── Click "Cargar" button in the period's row ──
    cargar_form = page.locator(f'form:has(input[value="{periodo}"][name="periodo"])')
    if await cargar_form.count() == 0:
        msg = f"Período {periodo} no encontrado en la grilla"
        debug.log(msg)
        result["error"] = msg
        return result

    await _run_step(
        debug, page, f"{periodo}_click_cargar",
        lambda: cargar_form.locator('button[type="submit"]').click(no_wait_after=True),
    )
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(3_000)

    # ── Process each ret/per section ──
    for section in RET_PER_SECTIONS:
        btn = page.locator(section["button"])
        try:
            if await btn.count() == 0 or not await btn.first.is_visible():
                debug.log(f"{periodo}_skip section={section['name']} motivo=boton_no_visible")
                continue
        except Exception:
            debug.log(f"{periodo}_skip section={section['name']} motivo=error_acceso")
            continue

        await _run_step(
            debug, page, f"{periodo}_{section['name']}_abrir",
            lambda: btn.click(no_wait_after=True),
        )
        await page.wait_for_timeout(2_000)

        # Click "CONFIRMAR TODO" if present (pre-filled data confirmation)
        try:
            confirmar_todo = page.get_by_role("button", name=re.compile(r"CONFIRMAR TODO", re.I))
            if await confirmar_todo.count() > 0 and await confirmar_todo.first.is_visible():
                await _run_step(
                    debug, page, f"{periodo}_{section['name']}_confirmar_todo",
                    lambda: confirmar_todo.click(no_wait_after=True),
                )
                await page.wait_for_timeout(1_500)
        except Exception:
            pass

        section_data = await _scrape_page_data(page, debug, periodo, section["name"])
        result["secciones"][section["name"]] = section_data
        json_path_sec = debug.descargas_dir / f"{periodo}_{section['name']}.json"
        await _save_json(section_data, json_path_sec, debug)

        await _run_step(
            debug, page, f"{periodo}_{section['name']}_terminar",
            lambda: page.get_by_role("button", name=re.compile(r"Terminar Carga", re.I)).click(no_wait_after=True),
        )
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1_500)

    # ── Vista Previa (first) ──
    try:
        await _run_step(
            debug, page, f"{periodo}_vista_previa_1",
            lambda: page.get_by_role("button", name=re.compile(r"Vista Previa", re.I)).click(no_wait_after=True),
        )
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        debug.log(f"{periodo}_vista_previa_1_error error={exc}")

    # ── Select actividad0 (required before Guardar Borrador appears) ──
    try:
        actividad = page.locator("#actividad0")
        if await actividad.count() > 0:
            current_val = await actividad.input_value()
            debug.log(f"{periodo}_actividad0_actual value={current_val}")
            option_values = await actividad.locator("option").all()
            for opt in option_values:
                val = await opt.get_attribute("value")
                if val and val != "t-9999-741101":  # skip placeholder
                    if val != current_val:
                        await _run_step(
                            debug, page, f"{periodo}_seleccionar_actividad",
                            lambda v=val: actividad.select_option(v),
                        )
                        # Auto-submits via onchange, wait for reload
                        await page.wait_for_load_state("networkidle")
                        await page.wait_for_timeout(3_000)
                    break
        else:
            debug.log(f"{periodo}_actividad0_no_encontrado")
    except Exception as exc:
        debug.log(f"{periodo}_actividad0_error error={exc}")

    # ── Vista Previa (second, after actividad selection) ──
    try:
        await _run_step(
            debug, page, f"{periodo}_vista_previa_2",
            lambda: page.get_by_role("button", name=re.compile(r"Vista Previa", re.I)).click(no_wait_after=True),
        )
        await page.wait_for_timeout(3_000)
    except Exception as exc:
        debug.log(f"{periodo}_vista_previa_2_error error={exc}")

    # ── Guardar Borrador ──
    try:
        borrador_btn = page.get_by_role("button", name=re.compile(r"Guardar Borrador", re.I))
        if await borrador_btn.count() > 0 and await borrador_btn.first.is_visible():
            # Click Guardar Borrador → triggers modal
            await _run_step(
                debug, page, f"{periodo}_guardar_borrador",
                lambda: borrador_btn.click(no_wait_after=True),
            )
            await page.wait_for_timeout(1_500)

            # Handle confirmation modal
            borrador_modal = page.locator("#borrador_modal")
            if await borrador_modal.count() > 0 and await borrador_modal.first.is_visible():
                debug.log(f"{periodo}_borrador_modal_detectado")
                confirmar_modal = borrador_modal.get_by_role("button", name="Confirmar")
                if await confirmar_modal.count() > 0:
                    await _run_step(
                        debug, page, f"{periodo}_confirmar_modal",
                        lambda: confirmar_modal.click(no_wait_after=True),
                    )
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(3_000)
                else:
                    await _run_step(
                        debug, page, f"{periodo}_confirmar_modal_submit",
                        lambda: borrador_modal.locator('button[value="guardar_borrador"]').click(no_wait_after=True),
                    )
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(3_000)

            if await borrador_modal.count() > 0 and await borrador_modal.first.is_visible():
                try:
                    cancel_btn = borrador_modal.get_by_role("button", name="Cancelar")
                    if await cancel_btn.count() > 0:
                        await cancel_btn.click(no_wait_after=True)
                        await page.wait_for_timeout(1_000)
                except Exception:
                    pass

        else:
            debug.log(f"{periodo}_guardar_borrador_skip motivo=boton_no_visible")
    except Exception as exc:
        debug.log(f"{periodo}_guardar_borrador_error error={exc}")
        result["error_borrador"] = str(exc)

    # ── Scrape + screenshot DESPUÉS de Guardar Borrador ──
    await debug.capture(page, f"{periodo}_dj_guardada", "OK")

    # Save HTML of the borrador to descargas
    try:
        html_content = await page.content()
        html_path = debug.descargas_dir / f"{periodo}_dj_borrador.html"
        html_path.write_text(html_content, encoding="utf-8")
        debug.log(f"html_borrador_guardado path={html_path} size={html_path.stat().st_size}")
    except Exception as exc:
        debug.log(f"html_borrador_error error={exc}")

    # Scrape structured DJ fields from the Vista Previa tables
    final_data = await _scrape_dj_tables(page, debug, periodo)
    result["dj_json"] = final_data
    final_json_path = debug.descargas_dir / f"{periodo}_dj_completo.json"
    await _save_json(final_data, final_json_path, debug)

    # ── Volver al listado (navegar directamente a la URL del listado de períodos) ──
    try:
        await _run_step(
            debug, page, f"{periodo}_ir_listado",
            lambda: page.goto("https://extranet.rentastucuman.gov.ar/nomina/rentastuc2/intranet/SiapreWeb/", timeout=30_000),
        )
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        debug.log(f"{periodo}_navegacion_listado_error error={exc}")

    result["status"] = "ok"
    debug.log(f"periodo_completado period={periodo}")
    return result


# ---------------------------------------------------------------------------
# Bot principal
# ---------------------------------------------------------------------------

async def bot_tucuman(
    playwright: Playwright,
    cuit: str,
    clave: str,
    desde: str,
    hasta: str,
    headless: bool = False,
) -> None:
    debug = DebugCapture(prefix=BOT_NAME)
    periods = _generate_periods(desde, hasta)
    debug.log(f"Iniciando bot Tucumán Logger periods_count={len(periods)} periods={periods}")

    # ── Resume: encontrar último período completado ──
    resume_from = ""
    try:
        run_dirs = sorted(Path("temp").glob(f"{BOT_NAME}_*/descargas/resultados.json"), reverse=True)
        for rp in run_dirs:
            try:
                prev = json.loads(rp.read_text(encoding="utf-8"))
                prev_results = prev.get("resultados", [])
                for r in reversed(prev_results):
                    if r.get("status") == "ok" and r.get("periodo") in periods:
                        resume_from = r["periodo"]
                        break
                if resume_from:
                    break
            except Exception:
                continue
        if resume_from:
            debug.log(f"resume_desde period={resume_from} archivo={run_dirs[0] if run_dirs else 'N/A'}")
    except Exception as exc:
        debug.log(f"resume_check_error error={exc}")

    # Filtrar periodos: saltar los ya procesados
    if resume_from:
        idx = periods.index(resume_from) + 1
        periods = periods[idx:]
        debug.log(f"periodos_restantes count={len(periods)} desde={periods[0] if periods else 'ninguno'}")

    if not periods:
        debug.log("todos_los_periodos_ya_procesados saltando_ejecucion")
        return

    periods_by_year = _group_periods_by_year(periods)

    page: Optional[Page] = None
    context = None
    browser = None
    results: list[dict] = []

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

        # ── Procesar períodos agrupados por año ──
        for year in sorted(periods_by_year):
            periods_this_year = sorted(periods_by_year[year])
            debug.log(f"procesando_año year={year} periods={periods_this_year}")

            for periodo in periods_this_year:
                debug.log(f"procesando_periodo period={periodo}")
                result = await _process_period(page, debug, periodo)
                results.append(result)

                # Re-navegar si se perdió el estado
                current_url = page.url
                if "F.611" not in current_url and "SiAPre" not in current_url:
                    debug.log(f"re-navegando_a_listado period={periodo} url={current_url}")
                    await _navigate_to_f611(page, debug)

        # ── Guardar resumen (merge con resultados previos si existe resume) ──
        all_results = list(results)
        if resume_from:
            try:
                for rp in sorted(Path("temp").glob(f"{BOT_NAME}_*/descargas/resultados.json"), reverse=True):
                    prev = json.loads(rp.read_text(encoding="utf-8"))
                    for pr in prev.get("resultados", []):
                        if pr.get("periodo") not in [x.get("periodo") for x in all_results]:
                            all_results.append(pr)
                    break
            except Exception:
                pass
        all_results.sort(key=lambda x: x.get("periodo", ""))

        summary = {
            "bot": BOT_NAME,
            "desde": desde,
            "hasta": hasta,
            "periodos_totales": len(_generate_periods(desde, hasta)),
            "periodos_procesados": len(all_results),
            "resultados": all_results,
        }
        summary_path = debug.descargas_dir / "resultados.json"
        await _save_json(summary, summary_path, debug)
        debug.log("Flujo completado OK")

    except Exception as exc:
        debug.log(f"error_general={exc}")
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    load_dotenv()

    cuit = os.getenv("REPRESENTANTE") or os.getenv("CUIT_REPRESENTANTE") or ""
    clave = os.getenv("CLAVE") or os.getenv("ARCA_CLAVE") or ""
    desde = os.getenv("TUCUMAN_DESDE") or os.getenv("DESDE_PERIODO") or ""
    hasta = os.getenv("TUCUMAN_HASTA") or os.getenv("HASTA_PERIODO") or ""

    if not desde:
        desde = input("Periodo DESDE (YYYYMM, ej: 201701): ").strip()
    if not hasta:
        hasta = input("Periodo HASTA (YYYYMM, ej: 201812): ").strip()
    if not cuit:
        cuit = input("CUIT: ").strip()
    if not clave:
        clave = input("Clave fiscal: ").strip()

    print(f"[{BOT_NAME}] Ejecutando logger | CUIT: {cuit} | Desde: {desde} | Hasta: {hasta}")

    headless_str = os.getenv("TUCUMAN_HEADLESS", "True")
    headless = headless_str.lower() == "true"

    async with async_playwright() as playwright:
        await bot_tucuman(
            playwright=playwright,
            cuit=cuit,
            clave=clave,
            desde=desde,
            hasta=hasta,
            headless=headless,
        )

    print(f"[{BOT_NAME}] Logger finalizado.")


if __name__ == "__main__":
    asyncio.run(main())
