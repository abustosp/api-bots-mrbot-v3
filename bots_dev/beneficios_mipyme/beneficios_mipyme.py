import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.async_api import Page, async_playwright

from shared.arca_login import start_arca_session, open_arca_service

BOT_NAME = "beneficios_mipyme"


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


async def _try_click_button(page: Page, name: str, timeout: int = 3000) -> bool:
    try:
        btn = page.get_by_role("button", name=name)
        await btn.wait_for(state="visible", timeout=timeout)
        await btn.click()
        return True
    except Exception:
        return False


def _extract_all_data_from_html(html_content: str) -> dict:
    def _clean(text: str) -> str:
        t = re.sub(r"<[^>]*>", "", text).strip()
        t = t.replace("&nbsp;", " ").replace("\xa0", " ")
        return " ".join(t.split())

    result: dict = {}
    labels = re.findall(r'class="v-label[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
    clean_labels = [_clean(l) for l in labels if _clean(l)]

    # Header
    for i, label in enumerate(clean_labels):
        if re.match(r"^\d{11} - ", label):
            result.setdefault("header", {})
            parts = label.split(" - ", 1)
            result["header"]["cuit_usuario"] = parts[0]
            result["header"]["razon_social_usuario"] = parts[1] if len(parts) > 1 else ""
        pf_match = re.search(r"Per[ií]odo\s+Fiscal\s*[=:]\s*(\S+)", label)
        if pf_match:
            result.setdefault("header", {})
            result["header"]["periodo_fiscal"] = pf_match.group(1)
        if i + 1 < len(clean_labels):
            nxt = clean_labels[i + 1]
            if re.search(r"Per[ií]odo\s+Fiscal\s*=\s*$", label) and re.match(r"^\d{4}$", nxt):
                result.setdefault("header", {})
                result["header"]["periodo_fiscal"] = nxt
            if re.search(r"Secuencia\s*=\s*$", label) and re.match(r"^\d+$", nxt):
                result.setdefault("header", {})
                result["header"]["secuencia"] = nxt

    title_match = re.search(r"F\.\d+ - (.+?)(?:</div>|$)", html_content)
    if title_match:
        result.setdefault("header", {})
        result["header"]["formulario"] = _clean(title_match.group(0))

    # Domicilio
    domicilio = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if label == "Domicilio Fiscal": domicilio["direccion"] = val
        elif label == "Localidad": domicilio["localidad"] = val
        elif label == "Código Postal": domicilio["codigo_postal"] = val
        elif label == "Provincia": domicilio["provincia"] = val
    if domicilio: result["domicilio_fiscal"] = domicilio

    # Contacto
    contacto = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if label == "Correo electrónico": contacto["email"] = val
        elif label == "Teléfono": contacto["telefono"] = val
        elif label == "Tipo de línea": contacto["tipo_linea"] = val
        elif label == "Tipo de teléfono": contacto["tipo_telefono"] = val
        elif label == "Operador": contacto["operador"] = val
    if contacto: result["contacto"] = contacto

    # Datos empresa
    empresa = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if not val or any(kw in val.lower() for kw in ("período", "impuesto", "iva", "empleador", "registro", "página")): continue
        if re.match(r"Actividad Principal", label):
            m = re.match(r"^(\d+)\s*[-–]\s*(.+)", val)
            empresa["actividad_principal"] = {"codigo": m.group(1), "descripcion": m.group(2)} if m else val
        elif re.match(r"Fecha de inscripción ante ARCA", label): empresa["fecha_inscripcion_arca"] = val
        elif re.match(r"Forma Jurídica", label):
            m = re.match(r"^(\d+)\s*[-–]\s*(.+)", val)
            empresa["forma_juridica"] = {"codigo": m.group(1), "descripcion": m.group(2)} if m else val
        elif re.match(r"Mes de Cierre", label): empresa["mes_cierre"] = val
        elif re.match(r"Tipo de sociedad", label): empresa["tipo_sociedad"] = val
        elif re.match(r"Integrantes de la forma jurídica", label): empresa["integrantes_forma_juridica"] = val

    ganancias = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if re.match(r"Período de inscripción en Impuesto a las Ganancias", label): ganancias["periodo_inscripcion"] = val
        elif re.match(r"Exento en el impuesto a las Ganancias", label): ganancias["exento"] = val.lower() == "si" if val else False
        elif re.match(r"Período Fiscal de Última DJ presentada", label): ganancias["ultima_dj_periodo"] = val
        elif re.match(r"Fecha de presentación", label) and "F657" not in label: ganancias["ultima_dj_fecha"] = val
        elif re.match(r"Total Activo", label): ganancias["total_activo"] = val
    if ganancias: empresa["impuesto_ganancias"] = ganancias

    iva = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if re.match(r"Período de inscripción en IVA Responsable Inscripto", label): iva["periodo_inscripcion_ri"] = val
        elif re.match(r"Período de baja en IVA responsable inscripto", label): iva["periodo_baja_ri"] = val if val and val != "-" else None
        elif re.match(r"Exento en IVA$", label): iva["exento"] = val.lower() == "si" if val else False
        elif re.match(r"Inscripto en Monotributo$", label): iva["monotributo"] = val.lower() == "si" if val else False
        elif re.match(r"^Categoria$", label): iva["categoria_monotributo"] = val
        elif re.match(r"Período de Alta", label): iva["monotributo_periodo_alta"] = val if val and val != "-" else None
        elif re.match(r"Período de Baja", label): iva["monotributo_periodo_baja"] = val if val and val != "-" else None
    if iva: empresa["iva"] = iva
    if empresa: result["datos_empresa"] = empresa

    # Empleador
    empleador = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if re.match(r"^Empleador$", label): empleador["es_empleador"] = bool(val and val.strip())
        elif "Empleo promedio anual del período fiscal 2025" in label: empleador["empleo_2025"] = val
        elif "Empleo promedio anual del período fiscal 2024" in label: empleador["empleo_2024"] = val
        elif "Empleo promedio anual del período fiscal 2023" in label: empleador["empleo_2023"] = val
    if empleador: result["empleador"] = empleador

    # Vinculación
    vinculacion = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        lbl = _clean(label)
        if "Vinculación con Empresas Internacionales" in lbl and "societarias" not in val.lower(): vinculacion["vinculacion_internacional"] = val
        elif lbl == "Es controlada" and len(val) <= 3: vinculacion["es_controlada"] = val
        elif lbl == "Es controlante" and len(val) <= 3: vinculacion["es_controlante"] = val
        elif lbl == "Es vinculada" and len(val) <= 3: vinculacion["es_vinculada"] = val
    if vinculacion: result["vinculacion"] = vinculacion

    # Participaciones
    ps = {}
    for i, label in enumerate(clean_labels):
        if i + 1 >= len(clean_labels): continue
        val = clean_labels[i + 1]
        if label == "Acciones / Cuotas" and re.match(r"[\d\.,]+", val): ps["acciones_totales"] = val
        elif label == "% Participación" and re.match(r"[\d\.,]+", val): ps["porcentaje_participacion"] = val
        elif label == "Fecha de presentación F657" and re.match(r"\d{2}/\d{2}/\d{4}", val): ps["fecha_presentacion_f657"] = val
        elif label == "Período de presentación F657" and re.match(r"\d{4,6}", val): ps["periodo_presentacion_f657"] = val
    if ps: result.setdefault("participaciones_societarias", {})["resumen"] = ps

    # v-table data
    tables = re.findall(r'<table class="v-table-table">(.*?)</table>', html_content, re.DOTALL)
    all_th = re.findall(r'v-table-caption-container[^>]*>(.*?)</div>', html_content, re.DOTALL)
    all_th = [re.sub(r"<[^>]*>", "", h).strip() for h in all_th if re.sub(r"<[^>]*>", "", h).strip()]
    ext_tbl, h_off = [], 0
    for tbl_html in tables:
        tbody = re.search(r"<tbody>(.*?)</tbody>", tbl_html, re.DOTALL)
        if not tbody: continue
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1), re.DOTALL):
            cells = re.findall(r"v-table-cell-wrapper[^>]*>(.*?)</div>", tr, re.DOTALL)
            cc = [re.sub(r"<[^>]*>", "", c).strip() for c in cells if c.strip()]
            if cc: rows.append(cc)
        if rows:
            n = len(rows[0])
            ext_tbl.append({"headers": all_th[h_off:h_off+n], "rows": rows, "row_count": len(rows)})
            h_off += n

    for td in ext_tbl:
        hdrs, rws = td.get("headers", []), td.get("rows", [])
        if not rws: continue
        ncol = len(rws[0])
        if ncol >= 10 and any("Denominación" in h or "Apellido" in h for h in hdrs):
            result["socios"] = [{hdrs[j]: rws[i][j] for j in range(min(len(hdrs), len(rws[i])))} for i in range(len(rws))]
        elif ncol == 3 and any("Societaria" in h or "Relación" in h or "Cargo" in h for h in hdrs):
            result["relaciones_societarias"] = [{hdrs[j]: rws[i][j] for j in range(min(len(hdrs), len(rws[i])))} for i in range(len(rws))]
        elif ncol >= 2 and any("Actividad" in h or "Descripción" in h for h in hdrs):
            result.setdefault("actividades", []).extend([{hdrs[j]: rws[i][j] for j in range(min(len(hdrs), len(rws[i])))} for i in range(len(rws))])

    # Ventas
    vd = {}
    for i in range(len(clean_labels)-1):
        cur, nxt = clean_labels[i], clean_labels[i+1]
        for p in ("Total de ventas anuales", "Total de ventas en mercado local", "Monto de ventas de exportación", "Monto de ventas de mercado local", "Suma de las ventas anuales"):
            if p in cur and re.match(r"^[\d\.,\-]+$", nxt): vd[cur] = nxt; break
    if vd: result["ventas_datos"] = vd

    # Declaración
    decl = {}
    for label in clean_labels:
        lc = label.lower()
        if "reinscripción automática" in lc: decl["tipo"] = "Reinscripción automática"
        elif len(label) > 100 and "declaro" in lc: decl["texto_declaracion"] = label
        elif len(label) > 50 and "declaración jurada" in lc: decl["texto_beneficios"] = label
        elif len(label) > 50 and "presto" in lc and "conformidad" in lc: decl["texto_consentimiento"] = label
    if decl: result["declaracion"] = decl

    # Enlaces
    dls = re.findall(r'class="v-link[^"]*"[^>]*href="([^"]*)"[^>]*>.*?<span[^>]*>(.*?)</span>', html_content, re.DOTALL)
    if dls: result["enlaces"] = [{"texto": _clean(d[1]), "url": d[0]} for d in dls]

    # Periodos
    per = re.findall(r"\b(\d{6})\b", html_content)
    if per: result.setdefault("periodos_fiscales", sorted(set(per), reverse=True)[:10])

    return result


async def _scrape_page(page: Page) -> dict:
    html = await page.content()
    data = _extract_all_data_from_html(html)
    data["_tab"] = (await page.evaluate("""() => {
        const el = document.querySelector('.v-tabsheet-tabitemcell-selected .v-captiontext');
        return el ? el.textContent.trim() : '';
    }""")).strip()
    return data


async def _paginate_and_scrape(page: Page) -> tuple[int, list[dict]]:
    visited, all_data = 0, []
    while True:
        visited += 1
        await page.wait_for_timeout(2000)
        all_data.append(await _scrape_page(page))
        next_btn = page.get_by_role("button", name=re.compile(r"Siguiente", re.I))
        try:
            cnt = await next_btn.count()
            if cnt == 0: break
            disabled = False
            for i in range(cnt):
                try:
                    d = await next_btn.nth(i).get_attribute("disabled")
                    c = await next_btn.nth(i).get_attribute("class") or ""
                    if d is not None or "v-disabled" in c or "disabled" in c: disabled = True; break
                except: continue
            if disabled: break
            await next_btn.first.click()
            await page.wait_for_timeout(2000)
        except: break
    return visited, all_data


def _export(page_data_list: list[dict], cuit: str, output_dir: Path, periodo_override: str = "") -> dict:
    merged, socios, relaciones, actividades = {"_tabs": []}, [], [], []
    domicilio, contacto, empresa, empleador = {}, {}, {}, {}
    vinculacion, declaracion, ventas, enlaces, periodos, header, participaciones = {}, {}, {}, [], [], {}, {}

    for page in page_data_list:
        tab = page.pop("_tab", "")
        if tab: merged["_tabs"].append(tab)
        for key, value in page.items():
            if key == "socios" and isinstance(value, list):
                for s in value: s["_tab"] = tab
                socios.extend(value)
            elif key == "relaciones_societarias" and isinstance(value, list):
                for r in value: r["_tab"] = tab
                relaciones.extend(value)
            elif key == "actividades" and isinstance(value, list):
                for a in value: a["_tab"] = tab
                actividades.extend(value)
            elif key == "domicilio_fiscal": domicilio = {**domicilio, **value}
            elif key == "contacto": contacto = {**contacto, **value}
            elif key == "datos_empresa": empresa = {**empresa, **value}
            elif key == "empleador": empleador = {**empleador, **value}
            elif key == "vinculacion": vinculacion = {**vinculacion, **value}
            elif key == "declaracion": declaracion = {**declaracion, **value}
            elif key == "ventas_datos": ventas = {**ventas, **value}
            elif key == "enlaces": enlaces.extend(value)
            elif key == "periodos_fiscales": periodos.extend(value)
            elif key == "header": header = {**header, **value}
            elif key == "participaciones_societarias": participaciones = {**participaciones, **value}
            elif isinstance(value, list): merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict): merged.setdefault(key, {}).update(value)
            else: merged[key] = value

    if header: merged["header"] = header
    if declaracion: merged["declaracion"] = declaracion
    if domicilio: merged["domicilio_fiscal"] = domicilio
    if contacto: merged["contacto"] = contacto
    if empresa: merged["datos_empresa"] = empresa
    if empleador: merged["empleador"] = empleador
    if vinculacion: merged["vinculacion"] = vinculacion
    if participaciones: merged["participaciones_societarias"] = participaciones
    if ventas: merged["ventas"] = ventas
    if enlaces: merged["enlaces"] = enlaces
    if periodos: merged["periodos_fiscales"] = sorted(set(periodos), reverse=True)
    if socios: merged["socios"] = socios
    if relaciones: merged["relaciones_societarias"] = relaciones
    if actividades: merged["actividades"] = actividades

    merged["metadata"] = {"cuit_representado": cuit, "fecha_extraccion": datetime.now().isoformat(), "total_paginas": len(page_data_list)}

    periodo = periodo_override or merged.get("header", {}).get("periodo_fiscal", "")
    if not periodo:
        for tab in merged.get("_tabs", []):
            if tab.isdigit(): periodo = tab; break
    if not periodo: periodo = "sin_periodo"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{cuit} - {periodo} - {ts}"
    jp = output_dir / f"{base}.json"
    xp = output_dir / f"{base}.xlsx"
    with open(jp, "w", encoding="utf-8") as f: json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
    with pd.ExcelWriter(xp, engine="openpyxl") as w:
        if socios: pd.DataFrame(socios).fillna("").to_excel(w, sheet_name="Personas_Socios", index=False)
        if relaciones: pd.DataFrame(relaciones).fillna("").to_excel(w, sheet_name="Relaciones_Societarias", index=False)
        if actividades: pd.DataFrame(actividades).fillna("").to_excel(w, sheet_name="Actividades", index=False)
        sr = []
        for k, v in merged.items():
            if k in ("_tabs", "metadata", "socios", "relaciones_societarias", "actividades"): continue
            sr.append({"seccion": k, "valor": json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)})
        if sr: pd.DataFrame(sr).to_excel(w, sheet_name="Resumen", index=False)

    return {"excel": str(xp), "json": str(jp), "socios_count": len(socios), "relaciones_count": len(relaciones), "actividades_count": len(actividades)}


async def _get_declarations_table(page: Page) -> list[dict]:
    return await page.evaluate("""() => {
        const result = [];
        const trs = document.querySelectorAll('table.v-table-table tbody tr');
        trs.forEach(tr => {
            const cells = [];
            tr.querySelectorAll('.v-table-cell-wrapper').forEach(td => cells.push(td.textContent.trim()));
            if (cells.length >= 5) result.push({periodo_fiscal: cells[4] || '', estado: cells[5] || ''});
        });
        return result;
    }""")


async def _click_row_ver(page: Page, row_index: int) -> bool:
    try:
        btns = page.locator("table.v-table-table tbody tr .v-filterselect-button")
        cnt = await btns.count()
        if row_index >= cnt: return False
        await btns.nth(row_index).scroll_into_view_if_needed()
        await btns.nth(row_index).click()
        await page.wait_for_timeout(1200)
        ver = page.locator(".v-filterselect-suggestmenu td").filter(has_text=re.compile(r"^Ver$")).first
        await ver.wait_for(state="visible", timeout=5000)
        await ver.click()
        return True
    except: return False


async def _scrape_periodo(page: Page, periodo: str, raw_text: str, cuit: str) -> dict | None:
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)
    total_pages, all_data = await _paginate_and_scrape(page)
    print(f"  Per {periodo}: {total_pages} pags")
    return _export(all_data, cuit, Path("descargas"), periodo_override=periodo)


async def _scrape_all_periods(page: Page, opt: dict, arca_page: Page, errors: list[str]) -> list[dict]:
    option_value, raw_text = opt.get("value", ""), opt.get("text", "")
    cuit_match = re.match(r"^(\d{11})", raw_text)
    cuit = cuit_match.group(1) if cuit_match else option_value

    # Select representado + Aceptar
    for s_idx in range(await page.locator("select").count()):
        try: await page.locator("select").nth(s_idx).select_option(value=option_value); break
        except: continue
    await page.wait_for_timeout(500)
    await _try_click_button(page, "Aceptar")
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    declarations = await _get_declarations_table(page)
    print(f"  === PERIODOS DISPONIBLES ({len(declarations)}) ===")
    for d in declarations:
        print(f"    periodo={d.get('periodo_fiscal','?')} estado={d.get('estado','?')}")
    if not declarations:
        r = await _scrape_periodo(page, "desconocido", raw_text, cuit)
        return [r] if r else []

    results, current_page = [], page
    for i, decl in enumerate(declarations):
        periodo = decl.get("periodo_fiscal", f"p{i}")
        estado = decl.get("estado", "")
        print(f"  [{i+1}/{len(declarations)}] {periodo} ({estado})")

        if i > 0:
            try: await current_page.close()
            except: pass
            await asyncio.sleep(2)
            svc = await open_arca_service(arca_page, service_name="PYMES SOLICITUD DE", service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),), error_logs=errors)
            if not svc: print("  No se pudo reabrir"); break
            await svc.wait_for_load_state("networkidle")
            await asyncio.sleep(3)
            for s_idx in range(await svc.locator("select").count()):
                try: await svc.locator("select").nth(s_idx).select_option(value=option_value); break
                except: continue
            await _try_click_button(svc, "Aceptar")
            await svc.wait_for_load_state("networkidle")
            await asyncio.sleep(3)
            current_page = svc

        if not await _click_row_ver(current_page, i): continue
        r = await _scrape_periodo(current_page, periodo, raw_text, cuit)
        if r: r["_periodo"] = periodo; results.append(r)

    return results


async def bot_beneficios_mipyme(
    cuit_representante: str, clave: str, cuit_representado: str,
    headless: bool = True, scrape_all_options: bool = True, max_options: int = 0,
) -> tuple[dict, list[str], bool]:
    results, error_logs = {"archivos": [], "representados": []}, []
    browser, context = None, None

    if not all([cuit_representante, clave, cuit_representado]):
        error_logs.append("Faltan parametros requeridos"); return results, error_logs, False

    cuits_a_procesar = [c.strip() for c in cuit_representado.split(",") if c.strip()]
    cuits_a_procesar = list(dict.fromkeys(cuits_a_procesar))
    print(f"CUITs a procesar: {cuits_a_procesar}")
    print(f"CUITs a procesar: {cuits_a_procesar}")

    Path("descargas").mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as pw:
            session = await start_arca_session(pw, cuit_representante, clave, error_logs=error_logs, headless=headless)
            if not session: return results, error_logs, False
            browser, context, arca_page = session.browser, session.context, session.page

            page1 = await open_arca_service(arca_page, service_name="PYMES SOLICITUD DE", service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),), error_logs=error_logs)
            if not page1: return results, error_logs, False
            await page1.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            all_opts = []
            for s_idx in range(await page1.locator("select").count()):
                opts = await _get_select_options(page1, page1.locator("select").nth(s_idx))
                if opts: all_opts = opts; break

            # Save and log ALL selectable options
            results["opciones_disponibles"] = [o["text"] for o in all_opts]
            print(f"\n=== OPCIONES DISPONIBLES ({len(all_opts)}) ===")
            for o in all_opts:
                print(f"  value={o['value']} | {o['text']}")

            if not scrape_all_options or not all_opts:
                if all_opts:
                    # Filter to only the configured CUITs
                    filtered = [o for o in all_opts if o["text"].upper() != "TODOS"
                                and any(cuit in o["text"] for cuit in cuits_a_procesar)]
                    if filtered:
                        found = []
                        for o in filtered:
                            m = re.match(r"^(\d{11})", o["text"])
                            found.append(m.group(1) if m else o["value"])
                        print(f"\nALL_OPTIONS=False: filtrando a CUITs {found} ({len(filtered)} representados)")
                        opts_to_do = filtered
                    else:
                        non_todos = [o for o in all_opts if o["text"].upper() != "TODOS"]
                        opts_to_do = [non_todos[0]] if non_todos else [all_opts[0]]
                        print(f"\nCUITs no encontrados, usando: {opts_to_do[0]['text']}")
                    for opt in opts_to_do:
                        per_results = await _scrape_all_periods(page1, opt, arca_page, error_logs)
                        for pr in per_results:
                            cuit_match = re.match(r"^(\d{11})", opt["text"])
                            results["archivos"].extend([Path(pr["json"]).name, Path(pr["excel"]).name])
                            results["representados"].append({
                                "cuit": cuit_match.group(1) if cuit_match else opt["value"],
                                "text": opt["text"], "json": pr["json"],
                                "periodo": pr.get("_periodo", ""),
                            })
                    results["output_dir"] = "descargas"
                    results["total_representados"] = len(set(r["cuit"] for r in results["representados"]))
                    return results, error_logs, len(results["representados"]) > 0
                else:
                    print("No se encontraron opciones en el select. Usando primer CUIT configurado.")
                    opt_to_use = {"value": cuits_a_procesar[0] if cuits_a_procesar else "0", "text": cuits_a_procesar[0] if cuits_a_procesar else "?"}
                    r = await _scrape_all_periods(page1, opt_to_use, arca_page, error_logs)
                    for x in r:
                        if x: results["archivos"].extend([Path(x["json"]).name, Path(x["excel"]).name])
                    return results, error_logs, bool(r)

            opts_to_do = [o for o in all_opts if o["text"].upper() != "TODOS"]
            if max_options > 0: opts_to_do = opts_to_do[:max_options]
            print(f"Escaneando {len(opts_to_do)} representados...")

            for oi, opt in enumerate(opts_to_do):
                cuit_txt = re.match(r"^(\d{11})", opt["text"])
                print(f"[{oi+1}/{len(opts_to_do)}] {opt['text']}")
                svc = page1 if oi == 0 else None
                if oi > 0:
                    try: await page1.close()
                    except: pass
                    await asyncio.sleep(2)
                    svc = await open_arca_service(arca_page, service_name="PYMES SOLICITUD DE", service_matchers=(re.compile(r"PYMES SOLICITUD", re.I),), error_logs=error_logs)
                    if not svc: continue
                    await svc.wait_for_load_state("networkidle"); await asyncio.sleep(3)
                    page1 = svc

                per_results = await _scrape_all_periods(svc, opt, arca_page, error_logs)
                for pr in per_results:
                    results["archivos"].extend([Path(pr["json"]).name, Path(pr["excel"]).name])
                    results["representados"].append({"cuit": cuit_txt.group(1) if cuit_txt else opt["value"], "text": opt["text"], "json": pr["json"], "periodo": pr.get("_periodo", "")})

            results["output_dir"] = "descargas"
            results["total_representados"] = len(set(r["cuit"] for r in results["representados"]))

    except Exception as exc:
        error_logs.append(f"Error: {exc}")
    finally:
        if context:
            try: await context.close()
            except: pass
        if browser:
            try: await browser.close()
            except: pass

    return results, error_logs, len(results["representados"]) > 0


async def main():
    try:
        from dotenv import load_dotenv; load_dotenv()
    except ImportError: pass

    cuit_rep = os.getenv("BENEFICIOS_MIPYME_CUIT_REPRESENTANTE", "")
    clave = os.getenv("BENEFICIOS_MIPYME_CLAVE", "")
    cuit_red = os.getenv("BENEFICIOS_MIPYME_CUIT_REPRESENTADO", "")
    headless = os.getenv("BENEFICIOS_MIPYME_HEADLESS", "False").lower() == "true"
    all_opts = os.getenv("BENEFICIOS_MIPYME_ALL_OPTIONS", "True").lower() == "true"
    max_opts = int(os.getenv("BENEFICIOS_MIPYME_MAX_OPTIONS", "0"))

    if not all([cuit_rep, clave, cuit_red]):
        print("ERROR: Faltan variables de entorno"); return

    results, errors, ok = await bot_beneficios_mipyme(cuit_rep, clave, cuit_red, headless, all_opts, max_opts)
    print(f"\n{'='*40}")
    print(f"Resultado: {'OK' if ok else 'ERROR'}")
    print(f"Representados: {results.get('total_representados', 0)}")
    print(f"Archivos: {len(results.get('archivos', []))}")
    if results.get('opciones_disponibles'):
        print(f"\n--- OPCIONES DISPONIBLES ---")
        for o in results['opciones_disponibles']:
            print(f"  {o}")
    for r in results.get("representados", []):
        print(f"  {r.get('cuit','?')} per={r.get('periodo','?')}: {r.get('json','').split('/')[-1]}")
    if errors:
        print(f"Errores: {len(errors)}")
        for e in errors[:10]: print(f"  - {e}")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())
