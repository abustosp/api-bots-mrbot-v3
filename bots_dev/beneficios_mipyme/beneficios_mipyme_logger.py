import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from playwright.async_api import Page, async_playwright

from shared.arca_login import start_arca_session, open_arca_service

BOT_NAME = "beneficios_mipyme"


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
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


async def _try_click_expand_combobox(page: Page) -> None:
    for label in ("expand combobox", "Abrir"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _get_select_options(page: Page, select_locator) -> list[dict]:
    result: list[dict] = []
    try:
        options = select_locator.locator("option")
        count = await options.count()
        for i in range(count):
            text = (await options.nth(i).text_content() or "").strip()
            value = await options.nth(i).get_attribute("value") or ""
            result.append({"index": i, "value": value, "text": text})
    except Exception:
        pass
    return result


async def _select_representado(page: Page, cuit_representado: str, debug: Optional[DebugCapture] = None) -> bool:
    opciones_encontradas: list[str] = []

    try:
        select_elements = page.locator("select")
        select_count = await select_elements.count()
        if select_count > 0:
            if debug:
                debug.log(f"Encontrados {select_count} elementos <select>")
            for s_idx in range(select_count):
                sel = select_elements.nth(s_idx)
                options_data = await _get_select_options(page, sel)
                if not options_data:
                    continue
                if debug:
                    debug.log(f"Select #{s_idx}: {len(options_data)} opciones")
                for opt in options_data:
                    opciones_encontradas.append(f"[select{s_idx}] {opt['text']}")
                    if debug:
                        debug.log(f"  Option: value={opt['value']} text={opt['text']}")
                for opt in options_data:
                    if cuit_representado in opt["text"]:
                        await sel.select_option(value=opt["value"])
                        if debug:
                            debug.log(f"Seleccionado: {opt['text']} (value={opt['value']})")
                        return True

                if debug:
                    debug.log(f"CUIT {cuit_representado} no encontrado en select #{s_idx}")

            if debug:
                debug.log("CUIT no encontrado. Seleccionando ultima opcion.")
            for s_idx in range(select_count):
                sel = select_elements.nth(s_idx)
                options_data = await _get_select_options(page, sel)
                if options_data:
                    last_opt = options_data[-1]
                    await sel.select_option(value=last_opt["value"])
                    if debug:
                        debug.log(f"Seleccionado por defecto: {last_opt['text']}")
                    return True

            return False

        combobox = page.get_by_role("combobox", name="Ingrese al menos 3 caracteres")
        await combobox.wait_for(state="visible", timeout=5000)
        await combobox.click()
        if debug:
            debug.log("Combobox clicked")

        await _try_click_expand_combobox(page)
        await page.wait_for_timeout(1500)

        options = page.locator('[role="option"], li[role="option"], .MuiAutocomplete-option')
        option_found: bool = False
        for attempt in range(5):
            count = await options.count()
            if count > 0:
                for i in range(count):
                    text = await options.nth(i).inner_text()
                    opciones_encontradas.append(text)
                    if not option_found and cuit_representado in text:
                        await options.nth(i).click()
                        option_found = True
                if not option_found:
                    await options.first.click()
                    option_found = True
                return True
            await page.wait_for_timeout(500)

        return False

    except Exception as exc:
        if debug:
            debug.log(f"Error selecting representado: {exc}")
        return False
    finally:
        if debug:
            debug.log(f"Opciones encontradas: {opciones_encontradas}")


async def _try_click_button(page: Page, name: str, timeout: int = 3000) -> bool:
    try:
        btn = page.get_by_role("button", name=name)
        await btn.wait_for(state="visible", timeout=timeout)
        await btn.click()
        return True
    except Exception:
        return False


async def _extract_boolean_states(page: Page) -> dict:
    states = await page.evaluate("""() => {
        const result = {};
        const filterSelects = document.querySelectorAll('.v-filterselect.v-readonly');
        filterSelects.forEach(fs => {
            const input = fs.querySelector('input');
            if (!input) return;
            const value = input.value.trim();
            // Find the nearest label/caption
            let parent = fs.closest('.v-formlayout-row, .v-slot, .v-verticallayout');
            if (!parent) parent = fs.parentElement;
            // Walk up to find the label
            let current = fs;
            let label = '';
            for (let i = 0; i < 20; i++) {
                const prevDiv = current.previousElementSibling;
                if (prevDiv) {
                    const labels = prevDiv.querySelectorAll('.v-label');
                    for (const l of labels) {
                        const text = l.textContent.trim().replace(/\\s+/g, ' ');
                        if (text) label = text;
                    }
                    if (label) break;
                }
                current = current.parentElement;
                if (!current || current.tagName === 'BODY') break;
            }
            if (label) {
                result[label] = value || null;
            }
        });
        return result;
    }""")
    return states


def _extract_all_data_from_html(html_content: str) -> dict:

    def _clean(text: str) -> str:
        t = re.sub(r"<[^>]*>", "", text).strip()
        t = t.replace("&nbsp;", " ").replace("\xa0", " ")
        return " ".join(t.split())

    result: dict = {}

    labels = re.findall(r'class="v-label[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
    clean_labels = [_clean(l) for l in labels if _clean(l)]

    result["_raw_labels"] = clean_labels

    # --- Header info ---
    for label in clean_labels:
        if re.match(r"^\d{11} - ", label):
            result.setdefault("header", {})
            parts = label.split(" - ", 1)
            result["header"]["cuit_usuario"] = parts[0]
            result["header"]["razon_social_usuario"] = parts[1] if len(parts) > 1 else ""

    title_match = re.search(r"F\.\d+ - (.+?)(?:</div>|$)", html_content)
    if title_match:
        result.setdefault("header", {})
        result["header"]["formulario"] = _clean(title_match.group(0))

    for i, label in enumerate(clean_labels):
        pf_match = re.search(r"Per[ií]odo\s+Fiscal\s*[=:]\s*(\S+)", label)
        if pf_match:
            result.setdefault("header", {})
            result["header"]["periodo_fiscal"] = pf_match.group(1)
        sq_match = re.search(r"Secuencia\s*[=:]\s*(\S+)", label)
        if sq_match:
            result.setdefault("header", {})
            result["header"]["secuencia"] = sq_match.group(1)

        # Fallback: periodo fiscal value might be in the NEXT label
        if i + 1 < len(clean_labels):
            next_lbl = clean_labels[i + 1]
            if re.search(r"Per[ií]odo\s+Fiscal\s*=\s*$", label) and re.match(r"^\d{4}$", next_lbl):
                result.setdefault("header", {})
                result["header"]["periodo_fiscal"] = next_lbl
            if re.search(r"Secuencia\s*=\s*$", label) and re.match(r"^\d+$", next_lbl):
                result.setdefault("header", {})
                result["header"]["secuencia"] = next_lbl

    # --- Form layout rows ---
    form_blocks = re.findall(
        r'v-formlayout-captioncell[^>]*>(.*?)</div>\s*</div>\s*</div>.*?'
        r'v-formlayout-contentcell[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html_content, re.DOTALL,
    )
    form_data = {}
    for cap_block, con_block in form_blocks:
        key = _clean(cap_block)
        value = _clean(con_block)
        if key:
            form_data[key] = value
    if form_data:
        result.setdefault("form_data", form_data)

    # --- Domicilio fiscal ---
    domicilio = {}
    for i, label in enumerate(clean_labels):
        if label == "Domicilio Fiscal" and i + 1 < len(clean_labels):
            domicilio["direccion"] = clean_labels[i + 1]
        elif label == "Localidad" and i + 1 < len(clean_labels):
            domicilio["localidad"] = clean_labels[i + 1]
        elif label == "Código Postal" and i + 1 < len(clean_labels):
            domicilio["codigo_postal"] = clean_labels[i + 1]
        elif label == "Provincia" and i + 1 < len(clean_labels):
            domicilio["provincia"] = clean_labels[i + 1]
    if domicilio:
        result["domicilio_fiscal"] = domicilio

    # --- Contacto ---
    contacto = {}
    for i, label in enumerate(clean_labels):
        if label == "Correo electrónico" and i + 1 < len(clean_labels):
            contacto["email"] = clean_labels[i + 1]
        elif label == "Teléfono" and i + 1 < len(clean_labels):
            contacto["telefono"] = clean_labels[i + 1]
        elif label == "Tipo de línea" and i + 1 < len(clean_labels):
            contacto["tipo_linea"] = clean_labels[i + 1]
        elif label == "Tipo de teléfono" and i + 1 < len(clean_labels):
            contacto["tipo_telefono"] = clean_labels[i + 1]
        elif label == "Operador" and i + 1 < len(clean_labels):
            contacto["operador"] = clean_labels[i + 1]
    if contacto:
        result["contacto"] = contacto

    # --- Datos empresa ---
    empresa = {}
    label_patterns = {
        "actividad_principal": (r"Actividad Principal", False),
        "fecha_inscripcion_arca": (r"Fecha de inscripción ante ARCA", False),
        "forma_juridica": (r"Forma Jurídica", False),
        "mes_cierre": (r"Mes de Cierre", False),
        "tipo_sociedad": (r"Tipo de sociedad", False),
        "integrantes_forma_juridica": (r"Integrantes de la forma jurídica", False),
    }
    for i, label in enumerate(clean_labels):
        for key, (pattern, _) in label_patterns.items():
            if re.match(pattern, label) and i + 1 < len(clean_labels):
                val = clean_labels[i + 1]
                if val and not any(kw in val.lower() for kw in ("período", "impuesto", "iva", "empleador", "registro", "página")):
                    if key in ("actividad_principal", "forma_juridica"):
                        m = re.match(r"^(\d+)\s*[-–]\s*(.+)", val)
                        if m:
                            empresa[key] = {"codigo": m.group(1), "descripcion": m.group(2)}
                        else:
                            empresa[key] = val
                    else:
                        empresa[key] = val
                    break

    # Impuesto a las ganancias
    ganancias = {}
    ganancias_map = {
        "periodo_inscripcion": r"Período de inscripción en Impuesto a las Ganancias",
        "exento": r"Exento en el impuesto a las Ganancias",
        "ultima_dj_periodo": r"Período Fiscal de Última DJ presentada",
        "ultima_dj_fecha": r"Fecha de presentación",
        "total_activo": r"Total Activo",
    }
    for i, label in enumerate(clean_labels):
        for gk, gp in ganancias_map.items():
            if re.match(gp, label) and i + 1 < len(clean_labels) and gk not in ganancias:
                val = clean_labels[i + 1]
                if gk == "exento":
                    val_lower = val.lower()
                    ganancias[gk] = val_lower in ("si", "sí") or (val_lower != "" and not any(
                        kw in val for kw in ("período", "fiscal", "total", "fecha", "inscripci")
                    ))
                else:
                    if val and not any(kw in val.lower() for kw in ("período", "impuesto", "iva", "exento", "monotributo", "empleador")):
                        ganancias[gk] = val
                    break
    if ganancias:
        empresa["impuesto_ganancias"] = ganancias

    # IVA
    iva = {}
    iva_map = {
        "periodo_inscripcion_ri": r"Período de inscripción en IVA Responsable Inscripto",
        "periodo_baja_ri": r"Período de baja en IVA responsable inscripto",
        "exento": r"Exento en IVA$",
        "monotributo": r"Inscripto en Monotributo$",
        "categoria_monotributo": r"^Categoria$",
        "monotributo_periodo_alta": r"Período de Alta",
        "monotributo_periodo_baja": r"Período de Baja",
    }
    for i, label in enumerate(clean_labels):
        for ik, ip in iva_map.items():
            if re.match(ip, label) and i + 1 < len(clean_labels) and ik not in iva:
                val = clean_labels[i + 1]
                if ik in ("exento", "monotributo"):
                    val_lower = val.lower() if val else ""
                    iva[ik] = val_lower in ("si", "sí") if val else False
                elif ik == "periodo_baja_ri":
                    iva[ik] = val if val and val != "-" else None
                else:
                    if val and not any(kw in val.lower() for kw in ("período", "categoria", "impuesto", "iva", "exento", "monotributo", "empleador", "inscripci")):
                        iva[ik] = val
                    break
    if iva:
        empresa["iva"] = iva

    if empresa:
        result["datos_empresa"] = empresa

    # --- Empleador ---
    empleador = {}
    empleo_map = {
        "es_empleador": r"^Empleador$",
        "empleo_2025": r"Empleo promedio anual del período fiscal 2025",
        "empleo_2024": r"Empleo promedio anual del período fiscal 2024",
        "empleo_2023": r"Empleo promedio anual del período fiscal 2023",
    }
    for i, label in enumerate(clean_labels):
        for ek, ep in empleo_map.items():
            if re.match(ep, label) and i + 1 < len(clean_labels) and ek not in empleador:
                val = clean_labels[i + 1]
                if ek == "es_empleador":
                    empleador[ek] = bool(val and val.strip() and not any(
                        kw in val.lower() for kw in ("empleo", "promedio", "período", "fiscal")
                    ))
                else:
                    if val and val.isdigit():
                        empleador[ek] = val
                    break
    if empleador:
        result["empleador"] = empleador

    # --- Vinculación ---
    vinculacion = {}
    for i, label in enumerate(clean_labels):
        lbl = _clean(label)
        if "Vinculación con Empresas Internacionales" in lbl and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and "societarias" not in val.lower():
                vinculacion["vinculacion_internacional"] = val
        elif lbl == "Es controlada" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and len(val) <= 3:
                vinculacion["es_controlada"] = val
        elif lbl == "Es controlante" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and len(val) <= 3:
                vinculacion["es_controlante"] = val
        elif lbl == "Es vinculada" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and len(val) <= 3:
                vinculacion["es_vinculada"] = val
    if vinculacion:
        result["vinculacion"] = vinculacion

    # --- Participaciones societarias summary ---
    participaciones_summary = {}
    for i, label in enumerate(clean_labels):
        if label == "Acciones / Cuotas" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and re.match(r"[\d\.,]+", val):
                participaciones_summary["acciones_totales"] = val
        elif label == "% Participación" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and re.match(r"[\d\.,]+", val):
                participaciones_summary["porcentaje_participacion"] = val
        elif label == "Fecha de presentación F657" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if re.match(r"\d{2}/\d{2}/\d{4}", val):
                participaciones_summary["fecha_presentacion_f657"] = val
        elif label == "Período de presentación F657" and i + 1 < len(clean_labels):
            val = clean_labels[i + 1]
            if val and re.match(r"\d{4,6}", val):
                participaciones_summary["periodo_presentacion_f657"] = val
    if participaciones_summary:
        result.setdefault("participaciones_societarias", {})
        result["participaciones_societarias"]["resumen"] = participaciones_summary

    # --- Extract v-table data ---
    tables = re.findall(r'<table class="v-table-table">(.*?)</table>', html_content, re.DOTALL)
    all_table_headers = re.findall(
        r'v-table-caption-container[^>]*>(.*?)</div>',
        html_content, re.DOTALL,
    )
    all_table_headers_clean = [re.sub(r"<[^>]*>", "", h).strip() for h in all_table_headers]
    all_table_headers_clean = [h for h in all_table_headers_clean if h]

    extracted_tables = []
    header_offset = 0
    for _t_idx, table_html in enumerate(tables):
        tbody_match = re.search(r"<tbody>(.*?)</tbody>", table_html, re.DOTALL)
        if not tbody_match:
            continue
        tbody = tbody_match.group(1)
        rows = []
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.DOTALL)
        for tr in trs:
            cells = re.findall(r"v-table-cell-wrapper[^>]*>(.*?)</div>", tr, re.DOTALL)
            clean_cells = [re.sub(r"<[^>]*>", "", c).strip() for c in cells if c.strip()]
            if clean_cells:
                rows.append(clean_cells)

        if rows:
            hdr_count = len(rows[0]) if rows else 0
            extracted_tables.append({
                "headers": all_table_headers_clean[header_offset:header_offset + hdr_count],
                "rows": rows,
                "row_count": len(rows),
            })
            header_offset += hdr_count

    for tbl_data in extracted_tables:
        headers = tbl_data.get("headers", [])
        rows = tbl_data.get("rows", [])
        if not rows:
            continue
        first_row_cols = len(rows[0])

        if first_row_cols >= 10 and any("Denominación" in h or "Apellido" in h for h in headers):
            result["socios"] = []
            for row in rows:
                entry = {}
                for j, h in enumerate(headers):
                    if j < len(row):
                        entry[h] = row[j]
                result["socios"].append(entry)

        elif first_row_cols == 3 and any("Societaria" in h or "Relación" in h or "Cargo" in h for h in headers):
            result["relaciones_societarias"] = []
            for row in rows:
                entry = {}
                for j, h in enumerate(headers):
                    if j < len(row):
                        entry[h] = row[j]
                result["relaciones_societarias"].append(entry)

        elif first_row_cols >= 2 and any("Actividad" in h or "Descripción" in h for h in headers):
            result.setdefault("actividades", [])
            for row in rows:
                entry = {}
                for j, h in enumerate(headers):
                    if j < len(row):
                        entry[h] = row[j]
                result["actividades"].append(entry)

    # --- Ventas data ---
    ventas_data = {}
    ventas_patterns = [
        "Total de ventas anuales (incluido el 25% de las exportaciones neto de impuestos internos e IVA)",
        "Total de ventas anuales (incluido el 25",
        "Total de ventas en mercado local",
        "Total de ventas anuales",
        "Monto de ventas de exportación considerado para el cálculo (25%)",
        "Monto de ventas de exportación considerado para el cálculo",
        "Monto de ventas de mercado local (segregado por actividad)",
        "Monto de ventas de mercado local más el 25% de las exportaciones (sin segregar por Actividad)",
        "Monto de ventas de mercado local más el 25% de las exportaciones",
        "Suma de las ventas anuales por actividad",
    ]
    for i in range(len(clean_labels) - 1):
        current = clean_labels[i]
        next_val = clean_labels[i + 1]
        for pat in ventas_patterns:
            if pat in current and next_val:
                m = re.match(r"^[\d\.,\-]+$", next_val)
                if m:
                    ventas_data[current] = next_val
                    break
    if ventas_data:
        result["ventas_datos"] = ventas_data

    # --- Declaración texts ---
    declaracion = {}
    for label in clean_labels:
        lc = label.lower()
        if "reinscripción automática" in lc:
            declaracion["tipo"] = "Reinscripción automática"
        elif len(label) > 100 and ("declaro" in lc or "juramento" in lc) and "beneficios" not in lc:
            declaracion["texto_declaracion"] = label
        elif len(label) > 50 and ("declaración jurada" in lc or "solicitando la categorización" in lc):
            declaracion["texto_beneficios"] = label
        elif len(label) > 50 and ("presto" in lc and "conformidad" in lc):
            declaracion["texto_consentimiento"] = label

    for i, label in enumerate(clean_labels):
        if "Solicita el beneficio del Pago del I.V.A." in label and i + 1 < len(clean_labels):
            answer = clean_labels[i + 1]
            if answer.upper() in ("SI", "SÍ", "NO"):
                declaracion["solicita_iva_diferido"] = answer
        elif "Acepta compartir información" in label and i + 1 < len(clean_labels):
            answer = clean_labels[i + 1]
            if answer.upper() in ("SI", "SÍ", "NO"):
                declaracion["acepta_compartir_informacion"] = answer
    if declaracion:
        result["declaracion"] = declaracion

    # --- Download links ---
    downloads = re.findall(
        r'class="v-link[^"]*"[^>]*href="([^"]*)"[^>]*>.*?<span[^>]*>(.*?)</span>',
        html_content, re.DOTALL,
    )
    if downloads:
        result["enlaces"] = [{"texto": _clean(d[1]), "url": d[0]} for d in downloads]

    # --- Periodos fiscales ---
    periodos = re.findall(r"\b(\d{6})\b", html_content)
    unique_periodos = sorted(set(periodos), reverse=True)
    if unique_periodos:
        result.setdefault("periodos_fiscales", unique_periodos[:10])

    result.pop("_raw_labels", None)
    return result


async def _get_declarations_table(page: Page) -> list[dict]:
    """Extract all rows from the declarations table. Returns list of {periodo, estado, fecha, ...}."""
    rows = await page.evaluate("""() => {
        const result = [];
        const table = document.querySelector('table.v-table-table');
        if (!table) return result;
        const trs = table.querySelectorAll('tbody tr');
        trs.forEach((tr, idx) => {
            const cells = [];
            tr.querySelectorAll('.v-table-cell-wrapper').forEach(td => cells.push(td.textContent.trim()));
            if (cells.length >= 5) {
                result.push({
                    row_index: idx,
                    actions: cells[0] || '',
                    cuit: cells[1] || '',
                    est: cells[2] || '',
                    formulario: cells[3] || '',
                    periodo_fiscal: cells[4] || '',
                    estado: cells[5] || '',
                    fecha_modificacion: cells[6] || '',
                    usuario: cells[7] || ''
                });
            }
        });
        return result;
    }""")
    return rows


async def _click_row_ver_action(page: Page, row_index: int, debug: DebugCapture) -> bool:
    """Click 'Ver' in the Acciones menu for a specific row in the declarations table."""
    # Find the Nth row in the declarations table and click its filter button
    try:
        # Get all filter buttons inside the table rows
        filter_btns = page.locator("table.v-table-table tbody tr .v-filterselect-button")
        count = await filter_btns.count()
        debug.log(f"Filter buttons found: {count}, targeting index {row_index}")

        if row_index >= count:
            debug.log(f"Row index {row_index} out of range (max {count-1})")
            return False

        btn = filter_btns.nth(row_index)
        await btn.scroll_into_view_if_needed()
        await btn.click()
        debug.log(f"Clicked filter button at row {row_index}")
        await page.wait_for_timeout(1200)

        # Now select "Ver" from the dropdown using Playwright
        ver_option = page.locator(".v-filterselect-suggestmenu td").filter(has_text=re.compile(r"^Ver$")).first
        try:
            await ver_option.wait_for(state="visible", timeout=5000)
            await ver_option.click()
            debug.log("Selected Ver from dropdown")
            return True
        except Exception:
            debug.log("Ver option not found in dropdown")
            return False

    except Exception as exc:
        debug.log(f"Failed to click row {row_index}: {exc}")
        return False


async def _go_back_to_declarations_table(page: Page, debug: DebugCapture, max_attempts: int = 10) -> bool:
    """Navigate back from form view to the declarations table using '<<' button."""
    for attempt in range(max_attempts):
        first_btn = page.locator('.v-button-pagedtable-first').first
        prev_btn = page.get_by_role("button", name=re.compile(r"Anterior|<<", re.I)).first

        first_count = await first_btn.count()
        prev_count = await prev_btn.count()

        if first_count == 0 and prev_count == 0:
            debug.log("No navigation buttons found - already at declarations table?")
            break

        # Check if disabled
        clicked = False
        if first_count > 0:
            try:
                classes = await first_btn.get_attribute("class") or ""
                if "v-disabled" not in classes and "disabled" not in classes:
                    await first_btn.click()
                    debug.log("Click en << (back to declarations)")
                    clicked = True
            except Exception:
                pass

        if not clicked and prev_count > 0:
            try:
                classes = await prev_btn.get_attribute("class") or ""
                if "v-disabled" not in classes and "disabled" not in classes:
                    await prev_btn.click()
                    debug.log("Click en Anterior")
                    clicked = True
            except Exception:
                pass

        if not clicked:
            debug.log("Navigation buttons all disabled - at declarations table")
            break

        await page.wait_for_timeout(2000)

    # Verify we're back at the declarations table
    try:
        has_table = await page.evaluate("""() => {
            const tbody = document.querySelector('table.v-table-table tbody');
            return tbody ? tbody.querySelectorAll('tr').length : 0;
        }""")
        debug.log(f"Rows in declarations table after navigation: {has_table}")
        return has_table > 0
    except Exception:
        return False


async def _scrape_periodo_tabs(page: Page, periodo: str, raw_text: str, cuit: str, debug: DebugCapture, errors: list[str]) -> dict | None:
    """After clicking Ver on a declaration row, scrape all tabs for that period."""
    debug.log(f"Scrapeando periodo {periodo} para {raw_text}")

    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    # Check for service not enabled
    pagina_content = await page.content()
    if "No se encuentra habilitado" in pagina_content:
        errors.append(f"No se encuentra habilitado el servicio para {raw_text} periodo {periodo}")
        return None

    await debug.capture(page, f"declaracion_{cuit}_{periodo}")

    # When coming from declarations table, the form tabs are already loaded - no filter needed
    # Paginate and scrape all tabs directly
    total_pages, all_page_data = await _paginate_and_scrape_all(page, debug)
    debug.log(f"Periodo {periodo}: {total_pages} paginas")

    # Consolidate with period as the name
    export_result = _consolidate_and_export(all_page_data, cuit, debug.descargas_dir, periodo_override=periodo)
    debug.log(f"Periodo {periodo}: JSON={export_result['json_size']}B")
    return export_result


async def _scrape_page1_option(
    page: Page, opt: dict, debug: DebugCapture, errors: list[str],
    arca_page: Page = None,
) -> list[dict]:
    """Select a representado, then scrape ALL periods from the declarations table.
    Returns list of per-period export results."""
    option_value = opt.get("value", "")
    raw_text = opt.get("text", "")
    cuit_match = re.match(r"^(\d{11})", raw_text)
    cuit = cuit_match.group(1) if cuit_match else option_value

    debug.log(f"Iniciando scraping multi-periodo para: {raw_text} (cuit={cuit})")

    # Select this option
    sel_found = False
    select_count_loc = await page.locator("select").count()
    for s_idx in range(select_count_loc):
        try:
            sel = page.locator("select").nth(s_idx)
            await sel.select_option(value=option_value)
            sel_found = True
            debug.log(f"Seleccionado: {raw_text}")
            break
        except Exception:
            continue

    if not sel_found:
        errors.append(f"No se pudo seleccionar: {raw_text}")
        return []

    await page.wait_for_timeout(500)
    await debug.capture(page, f"seleccion_{cuit}")

    # Click Aceptar
    aceptar_ok = await _try_click_button(page, "Aceptar")
    if aceptar_ok:
        debug.log("Click en Aceptar")
    else:
        debug.log("No se encontro boton Aceptar, continuando...")

    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    pagina_content = await page.content()
    if "No se encuentra habilitado" in pagina_content:
        errors.append(f"No se encuentra habilitado el servicio para {raw_text}")
        await debug.capture(page, f"servicio_no_habilitado_{cuit}")
        return []

    await debug.capture(page, f"tabla_declaraciones_{cuit}")

    # Extract all declarations from the table
    declarations = await _get_declarations_table(page)
    debug.log(f"=== PERIODOS DISPONIBLES para {raw_text} ({len(declarations)}) ===")
    for d in declarations:
        debug.log(f"  periodo={d.get('periodo_fiscal','?')} estado={d.get('estado','?')} fecha={d.get('fecha_modificacion','?')} formulario={d.get('formulario','?')}")
    debug.log(f"Encontradas {len(declarations)} declaraciones: {[d['periodo_fiscal'] for d in declarations]}")

    if not declarations:
        debug.log("No se encontro tabla de declaraciones, usando scraping single-period")
        result = await _scrape_periodo_tabs(page, "desconocido", raw_text, cuit, debug, errors)
        return [result] if result else []

    all_period_results = []
    current_page = page  # track the current service page

    for i, decl in enumerate(declarations):
        periodo = decl.get("periodo_fiscal", f"p{i}")
        estado = decl.get("estado", "")

        debug.log(f"--- Periodo {i+1}/{len(declarations)}: {periodo} ({estado}) ---")

        if i > 0:
            # Re-open the service to get back to declarations table
            if arca_page is not None:
                try:
                    await current_page.close()
                except Exception:
                    pass
                await asyncio.sleep(2)

                svc_page = await open_arca_service(
                    arca_page,
                    service_name="PYMES SOLICITUD DE",
                    service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),),
                    error_logs=errors,
                )
                if svc_page is None:
                    debug.log("No se pudo reabrir el servicio")
                    break
                await svc_page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)

                current_page = svc_page

                # Re-select representado
                for s_idx in range(await current_page.locator("select").count()):
                    try:
                        sel = current_page.locator("select").nth(s_idx)
                        await sel.select_option(value=option_value)
                        break
                    except Exception:
                        continue
                await current_page.wait_for_timeout(500)

                # Re-click Aceptar
                await _try_click_button(current_page, "Aceptar")
                await current_page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)
            else:
                debug.log("No se puede reabrir (sin referencia ARCA). Intentando reload...")
                await current_page.goto(current_page.url)
                await current_page.wait_for_load_state("networkidle")
                await asyncio.sleep(5)
                # Re-select and accept
                for s_idx in range(await current_page.locator("select").count()):
                    try:
                        sel = current_page.locator("select").nth(s_idx)
                        await sel.select_option(value=option_value)
                        break
                    except Exception:
                        continue
                await _try_click_button(current_page, "Aceptar")
                await current_page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)

        # Click "Ver" on the current row
        ver_ok = await _click_row_ver_action(current_page, i, debug)
        if not ver_ok:
            debug.log(f"No se pudo clickear Ver en fila {i}")
            continue

        # Scrape all tabs for this period
        result = await _scrape_periodo_tabs(current_page, periodo, raw_text, cuit, debug, errors)
        if result:
            result["_periodo"] = periodo
            all_period_results.append(result)

    return all_period_results


async def _scrape_full_page(page: Page) -> dict:
    html = await page.content()
    data = _extract_all_data_from_html(html)

    # Add boolean states from Vaadin filterSelect widgets
    try:
        bool_states = await _extract_boolean_states(page)
        if bool_states:
            # Merge boolean states into existing categories
            for label, value in bool_states.items():
                lc = label.lower()
                if "iva" in lc or "monotributo" in lc or "exento" in lc:
                    iva = data.setdefault("datos_empresa", {}).setdefault("iva", {})
                    if "exento en iva" in lc:
                        iva["exento"] = bool(value)
                    elif "monotributo" in lc:
                        iva["monotributo"] = bool(value)
                elif "ganancias" in lc or "exento" in lc:
                    gan = data.setdefault("datos_empresa", {}).setdefault("impuesto_ganancias", {})
                    if "exento" in lc:
                        gan["exento"] = bool(value)
                elif "empleador" in lc:
                    data.setdefault("empleador", {})["es_empleador"] = bool(value)
                elif "solicita" in lc or "iva" in lc:
                    data.setdefault("declaracion", {})["solicita_iva_diferido"] = value if value else None
                elif "compartir" in lc or "acept" in lc:
                    data.setdefault("declaracion", {})["acepta_compartir_informacion"] = value if value else None
    except Exception:
        pass

    tab_name = await page.evaluate("""() => {
        const el = document.querySelector('.v-tabsheet-tabitemcell-selected .v-captiontext');
        return el ? el.textContent.trim() : '';
    }""")
    data["_tab"] = str(tab_name).strip()
    return data


async def _paginate_and_scrape_all(page: Page, debug: DebugCapture) -> tuple[int, list[dict]]:
    pages_visited = 0
    all_page_data: list[dict] = []

    while True:
        pages_visited += 1
        await page.wait_for_timeout(2000)

        await debug.capture(page, f"pagina_{pages_visited}_datos")

        page_data = await _scrape_full_page(page)
        all_page_data.append(page_data)

        tab = page_data.get("_tab", "")
        total_keys = len(page_data) - 1
        debug.log(f"Pagina {pages_visited} - Tab: '{tab}' - {total_keys} secciones extraidas")

        next_btn = page.get_by_role("button", name=re.compile(r"Siguiente", re.I))
        try:
            count = await next_btn.count()
            if count == 0:
                debug.log("No se encontro boton Siguiente. Fin de paginacion.")
                break

            is_disabled = False
            for i in range(count):
                try:
                    disabled = await next_btn.nth(i).get_attribute("disabled")
                    classes = await next_btn.nth(i).get_attribute("class") or ""
                    if disabled is not None or "v-disabled" in classes or "disabled" in classes:
                        is_disabled = True
                        break
                except Exception:
                    continue

            if is_disabled:
                debug.log("Boton Siguiente deshabilitado. Fin de paginacion.")
                break

            await next_btn.first.click()
            debug.log("Click en Siguiente >")
            await page.wait_for_timeout(2000)
        except Exception as exc:
            debug.log(f"Error en paginacion: {exc}")
            break

    return pages_visited, all_page_data


def _consolidate_and_export(page_data_list: list[dict], cuit_representado: str, output_dir: Path, periodo_override: str = "") -> dict:
    """Consolidate data from all pages, merge by section, export JSON + Excel."""
    merged: dict = {"_tabs": []}
    socios = []
    relaciones = []
    actividades = []
    domicilio = {}
    contacto = {}
    empresa = {}
    empleador = {}
    vinculacion = {}
    declaracion = {}
    ventas = {}
    enlaces = []
    periodos = []
    header = {}
    form_data = {}
    participaciones = {}

    for page in page_data_list:
        tab = page.pop("_tab", "")
        if tab:
            merged["_tabs"].append(tab)

        for key, value in page.items():
            if key == "socios" and isinstance(value, list):
                for s in value:
                    s["_tab"] = tab
                socios.extend(value)
            elif key == "relaciones_societarias" and isinstance(value, list):
                for r in value:
                    r["_tab"] = tab
                relaciones.extend(value)
            elif key == "actividades" and isinstance(value, list):
                for a in value:
                    a["_tab"] = tab
                actividades.extend(value)
            elif key == "domicilio_fiscal":
                domicilio = {**domicilio, **value}
            elif key == "contacto":
                contacto = {**contacto, **value}
            elif key == "datos_empresa":
                empresa = {**empresa, **value}
            elif key == "empleador":
                empleador = {**empleador, **value}
            elif key == "vinculacion":
                vinculacion = {**vinculacion, **value}
            elif key == "declaracion":
                declaracion = {**declaracion, **value}
            elif key == "ventas_datos":
                ventas = {**ventas, **value}
            elif key == "enlaces":
                enlaces.extend(value)
            elif key == "periodos_fiscales":
                periodos.extend(value)
            elif key == "header":
                header = {**header, **value}
            elif key == "form_data":
                form_data = {**form_data, **value}
            elif key == "participaciones_societarias":
                participaciones = {**participaciones, **value}
            elif isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            else:
                merged[key] = value

    if header:
        merged["header"] = header
    if declaracion:
        merged["declaracion"] = declaracion
    if domicilio:
        merged["domicilio_fiscal"] = domicilio
    if contacto:
        merged["contacto"] = contacto
    if empresa:
        merged["datos_empresa"] = empresa
    if empleador:
        merged["empleador"] = empleador
    if vinculacion:
        merged["vinculacion"] = vinculacion
    if participaciones:
        merged["participaciones_societarias"] = participaciones
    if form_data:
        merged["form_data"] = form_data
    if ventas:
        merged["ventas"] = ventas
    if enlaces:
        merged["enlaces"] = enlaces
    if periodos:
        merged["periodos_fiscales"] = sorted(set(periodos), reverse=True)
    if socios:
        merged["socios"] = socios
    if relaciones:
        merged["relaciones_societarias"] = relaciones
    if actividades:
        merged["actividades"] = actividades

    merged["metadata"] = {
        "cuit_representado": cuit_representado,
        "fecha_extraccion": datetime.now().isoformat(),
        "total_paginas": len(page_data_list),
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Extract periodo fiscal
    periodo = periodo_override or merged.get("header", {}).get("periodo_fiscal", "")
    if not periodo:
        for tab in merged.get("_tabs", []):
            if tab.isdigit():
                periodo = tab
                break
    if not periodo:
        periodo = "sin_periodo"

    base_name = f"{cuit_representado} - {periodo} - {timestamp}"

    json_path = output_dir / f"{base_name}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
    json_size = json_path.stat().st_size

    excel_path = output_dir / f"{base_name}.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        if socios:
            df = pd.DataFrame(socios).fillna("").replace("nan", "").replace("NaN", "")
            df = df.drop_duplicates().reset_index(drop=True)
            df.to_excel(writer, sheet_name="Personas_Socios", index=False)
        if relaciones:
            df = pd.DataFrame(relaciones).fillna("").replace("nan", "").replace("NaN", "")
            df = df.drop_duplicates().reset_index(drop=True)
            df.to_excel(writer, sheet_name="Relaciones_Societarias", index=False)
        if actividades:
            pd.DataFrame(actividades).fillna("").to_excel(writer, sheet_name="Actividades", index=False)

        summary_rows = []
        for key, value in merged.items():
            if key in ("_tabs", "metadata", "socios", "relaciones_societarias", "actividades"):
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            summary_rows.append({"seccion": key, "valor": str(value)})
        if summary_rows:
            pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Resumen", index=False)

    return {
        "excel": str(excel_path),
        "json": str(json_path),
        "json_size": json_size,
        "socios_count": len(socios),
        "relaciones_count": len(relaciones),
        "actividades_count": len(actividades),
    }


async def bot_beneficios_mipyme_logger(
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    headless: bool = True,
    scrape_all_options: bool = True,
    max_options: int = 0,
) -> tuple[dict, list[str], bool]:
    results: dict = {"archivos": [], "representados": []}
    error_logs: list[str] = []
    success = False
    browser = None
    context = None

    if not all([cuit_representante, clave, cuit_representado]):
        error_logs.append("Faltan parametros requeridos: CUIT representante, clave y CUIT representado.")
        return results, error_logs, False

    # Parse CUITs: support comma-separated list
    cuits_a_procesar = [c.strip() for c in cuit_representado.split(",") if c.strip()]
    cuits_a_procesar = list(dict.fromkeys(cuits_a_procesar))  # dedup, preserve order

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Beneficios MiPyME Logger (multi-opcion)")
    debug.log(f"CUITs a procesar: {cuits_a_procesar}")

    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as pw:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]

            context_kwargs = {
                "locale": "es-AR",
                "timezone_id": "America/Argentina/Buenos_Aires",
                "accept_downloads": True,
                "extra_http_headers": {"Accept-Language": "es-AR,es;q=0.9"},
                "record_har_path": str(har_path),
                "record_har_content": "embed",
            }

            debug.log("Paso 1: Iniciando sesion en ARCA")
            arca_session = await start_arca_session(
                pw,
                cuit_representante=cuit_representante,
                clave_representante=clave,
                error_logs=error_logs,
                headless=headless,
                launch_args=launch_args,
                context_kwargs=context_kwargs,
            )
            if arca_session is None:
                debug.log("No se pudo iniciar sesion en ARCA")
                return results, error_logs, False

            browser = arca_session.browser
            context = arca_session.context
            arca_page = arca_session.page  # keep ARCA portal page open

            await debug.capture(arca_page, "login_arca_ok")

            debug.log("Paso 2: Abriendo servicio PYMES SOLICITUD DE (primera vez)")
            page1 = await open_arca_service(
                arca_page,
                service_name="PYMES SOLICITUD DE",
                service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de PYMES SOLICITUD DE",
            )
            if page1 is None:
                debug.log("No se pudo abrir el servicio PYMES SOLICITUD DE")
                return results, error_logs, False

            await page1.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # Extract ALL available options from the select element
            all_options: list[dict] = []
            select_count = await page1.locator("select").count()
            for s_idx in range(select_count):
                sel = page1.locator("select").nth(s_idx)
                opts = await _get_select_options(page1, sel)
                if opts:
                    all_options = opts
                    debug.log(f"Select #{s_idx}: {len(opts)} opciones: {[o['text'] for o in opts]}")
                    break

            if not all_options:
                debug.log("No se encontraron opciones en select. Intentando buscar por CUIT...")
                # Try to find and select the matching option
                representado_ok = await _select_representado(page1, cuit_representado, debug)
                if not representado_ok:
                    error_logs.append("No se pudo seleccionar el representado")
                    return results, error_logs, False
                export_results = await _scrape_page1_option(
                    page1,
                    {"value": cuit_representado, "text": cuit_representado},
                    debug, error_logs,
                    arca_page=arca_page,
                )
                for er in export_results:
                    if er:
                        results["archivos"].extend([Path(er["json"]).name, Path(er["excel"]).name])
                        results["output_dir"] = str(debug.descargas_dir)
                        results["representados"].append({"cuit": cuit_representado, "json": er["json"], "periodo": er.get("_periodo", "")})
                success = True
                return results, error_logs, success

            options_to_scrape = [o for o in all_options if o["text"].upper() != "TODOS"]

            # Save and log ALL selectable representados
            results["opciones_disponibles"] = [o["text"] for o in all_options]
            debug.log(f"=== OPCIONES DISPONIBLES ({len(all_options)}) ===")
            for o in all_options:
                debug.log(f"  value={o['value']} | {o['text']}")

            # If not scraping ALL options, filter to only the configured CUIT list
            if not scrape_all_options:
                filtered = [o for o in options_to_scrape
                            if any(cuit in o["text"] for cuit in cuits_a_procesar)]
                if filtered:
                    found_cuits = []
                    for o in filtered:
                        m = re.match(r"^(\d{11})", o["text"])
                        found_cuits.append(m.group(1) if m else o["value"])
                    debug.log(f"ALL_OPTIONS=False: filtrando a CUITs {found_cuits} ({len(filtered)} representados)")
                    options_to_scrape = filtered
                else:
                    debug.log(f"ALL_OPTIONS=False: ningun CUIT de {cuits_a_procesar} encontrado. Usando primer representado.")
                    options_to_scrape = options_to_scrape[:1]

            if max_options > 0:
                options_to_scrape = options_to_scrape[:max_options]

            debug.log(f"Se escanearan {len(options_to_scrape)} representados: {[o['text'] for o in options_to_scrape]}")

            for opt_idx, opt in enumerate(options_to_scrape):
                debug.log(f"--- Representado {opt_idx+1}/{len(options_to_scrape)}: {opt['text']} ---")

                if opt_idx == 0:
                    # First: use the already open page1
                    svc_page = page1
                else:
                    # Close previous service popup and re-open
                    try:
                        await page1.close()
                    except Exception:
                        pass
                    await asyncio.sleep(2)

                    svc_page = await open_arca_service(
                        arca_page,
                        service_name="PYMES SOLICITUD DE",
                        service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),),
                        error_logs=error_logs,
                        service_unavailable_message="No se encuentra habilitado el servicio de PYMES SOLICITUD DE",
                    )
                    if svc_page is None:
                        debug.log(f"No se pudo reabrir el servicio para {opt['text']}")
                        continue
                    await svc_page.wait_for_load_state("networkidle")
                    await asyncio.sleep(3)
                    page1 = svc_page

                export_results = await _scrape_page1_option(svc_page, opt, debug, error_logs, arca_page=arca_page)

                if export_results:
                    cuit_from_text = re.match(r"^(\d{11})", opt["text"])
                    cuit_val = cuit_from_text.group(1) if cuit_from_text else opt["value"]
                    for er in export_results:
                        results["archivos"].extend([Path(er["json"]).name, Path(er["excel"]).name])
                        results["representados"].append({
                            "cuit": cuit_val,
                            "text": opt["text"],
                            "json": er["json"],
                            "excel": er["excel"],
                            "periodo": er.get("_periodo", ""),
                        })

            await debug.capture(arca_page, "exportacion_completada")

            results["output_dir"] = str(debug.descargas_dir)
            results["total_representados"] = len(results["representados"])
            success = len(results["representados"]) > 0

    except Exception as exc:
        error_msg = f"Error en bot Beneficios MiPyME: {exc}"
        if debug:
            debug.log(error_msg)
        error_logs.append(error_msg)
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

    return results, error_logs, success


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cuit_representante = os.getenv("BENEFICIOS_MIPYME_CUIT_REPRESENTANTE", "")
    clave = os.getenv("BENEFICIOS_MIPYME_CLAVE", "")
    cuit_representado = os.getenv("BENEFICIOS_MIPYME_CUIT_REPRESENTADO", "")
    headless_str = os.getenv("BENEFICIOS_MIPYME_HEADLESS", "False")
    headless = headless_str.lower() == "true"
    all_opts_str = os.getenv("BENEFICIOS_MIPYME_ALL_OPTIONS", "True")
    scrape_all_options = all_opts_str.lower() == "true"
    max_opts = int(os.getenv("BENEFICIOS_MIPYME_MAX_OPTIONS", "0"))

    if not all([cuit_representante, clave, cuit_representado]):
        print("ERROR: Faltan variables de entorno. Verificar .env")
        print("  BENEFICIOS_MIPYME_CUIT_REPRESENTANTE")
        print("  BENEFICIOS_MIPYME_CLAVE")
        print("  BENEFICIOS_MIPYME_CUIT_REPRESENTADO")
        return

    results, errors, ok = await bot_beneficios_mipyme_logger(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        headless=headless,
        scrape_all_options=scrape_all_options,
        max_options=max_opts,
    )

    print(f"\n{'='*40}")
    print(f"Resultado: {'OK' if ok else 'ERROR'}")
    print(f"Representados escaneados: {results.get('total_representados', 0)}")
    print(f"Archivos generados: {len(results.get('archivos', []))}")
    print(f"Output dir: {results.get('output_dir', 'N/A')}")

    # Print available options
    opciones = results.get("opciones_disponibles", [])
    if opciones:
        print(f"\n--- OPCIONES DISPONIBLES ({len(opciones)}) ---")
        for o in opciones:
            print(f"  {o}")

    for rep in results.get("representados", []):
        print(f"  - {rep.get('text', rep.get('cuit', ''))} | periodo={rep.get('periodo','?')}: {Path(rep.get('json', '')).name}")

    if errors:
        print(f"Errores:")
        for e in errors:
            print(f"  - {e}")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())
