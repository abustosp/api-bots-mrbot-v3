#!/usr/bin/env python3
"""
carga_931_full.py – Versión limpia (sin instrumentación por paso) todo en uno.

Mismo flujo que carga_931_full_logger.py pero:
 - Sin screenshots / HTML por paso / HAR
 - Logs mínimos (inicio, fin, errores)
 - Descargas en ./descargas/carga-931-xubio/reportes y ./descargas/
Mantiene mismos controles, reporte consolidado y timing.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "descargas" / "carga-931-xubio"
CLAVES_PATH = INPUT_DIR / "claves.xlsx"
DESCARGAS_DIR = BASE_DIR / "descargas"
REPORTES_DIR = INPUT_DIR / "reportes"
CONTROL_SRC = Path("/home/abp/Desktop/descarga-xubio/control_descargas.py")
LOGGER_SRC = BASE_DIR / "carga-931-logger.py"

BOT_NAME = "carga_931_full"
PERIODO = "08/2026"
PERIODO_OBJETIVO = "2026/08"
HEADLESS = os.getenv("CARGA_931_HEADLESS", "false").lower() in {"1", "true", "yes"}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo cargar {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


logger_mod = _load_module("carga931_logger_limpio_src", LOGGER_SRC)
control_mod = _load_module("control_descargas_limpio_src", CONTROL_SRC)
identificador_texto = control_mod.identificador_texto

# Logger mínimo
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(BOT_NAME)

# Reusar funciones de control directamente

def _estilo_header(ws, row: int = 1):
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(bold=True, color="FFFFFF")
    for c in ws[row]:
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def generar_reporte_consolidado_limpio(resultados, control_por_clave, total_seg, dest_path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws.append(["Reporte Consolidado Carga 931 – Limpio"])
    ws.append(["Periodo", PERIODO])
    ws.append(["Fecha", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws.append(["Contribuyentes", len(resultados)])
    ws.append(["Tiempo total (s)", round(total_seg,2)])
    ws.append(["Promedio (s)", round(total_seg/len(resultados),2) if resultados else 0])
    ws.append(["Errores control", sum(len(v) for v in control_por_clave.values())])
    ws.append(["Estado", "ERROR" if sum(len(v) for v in control_por_clave.values()) or any(r.get("estado")!="OK" for r in resultados) else "OK"])
    ws.append([])
    header = ["#","Razón social","CUIT","Jurisdicción","Estado","Tiempo (s)","Errores"]
    ws.append(header)
    _estilo_header(ws, ws.max_row)
    for idx, r in enumerate(resultados,1):
        razon = r.get("razon_social")
        errs = next((v for k,v in control_por_clave.items() if identificador_texto(k)==identificador_texto(razon)), [])
        ws.append([idx, razon, r.get("cuit_representado"), r.get("jurisdiccion"), r.get("estado"), r.get("tiempo_seg"), len(errs)])
    for col in range(1,8):
        ws.column_dimensions[get_column_letter(col)].width = 20
    ws.column_dimensions["B"].width = 30
    # Tiempos
    ws2 = wb.create_sheet("Tiempos")
    ws2.append(["#","Razón social","CUIT","Tiempo (s)"])
    _estilo_header(ws2,1)
    for idx, r in enumerate(resultados,1):
        ws2.append([idx, r.get("razon_social"), r.get("cuit_representado"), r.get("tiempo_seg")])
    ws2.append(["Total","","", round(total_seg,2)])
    for col in range(1,5):
        ws2.column_dimensions[get_column_letter(col)].width = 22
    # Validación
    ws3 = wb.create_sheet("Validacion_Control")
    ws3.append(["Contribuyente","Tipo","Detalle"])
    _estilo_header(ws3,1)
    for clave, errs in control_por_clave.items():
        if not errs:
            ws3.append([clave,"OK","Sin inconsistencias"])
        else:
            for e in errs:
                ws3.append([clave,"ERROR",e])
    for col, w in [(1,30),(2,10),(3,110)]:
        ws3.column_dimensions[get_column_letter(col)].width = w
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest_path)
    return dest_path

async def _procesar_sin_capture(playwright, contribuyente):
    # Versión limpia: medir tiempo pero usar logger_mod pero sin HAR/capture fine-grained
    # logger_mod.procesar_contribuyente graba HAR y capturas; para limpia, llamamos igual pero no es crítico
    # Para diferenciar, usamos un dummy debug mínimo que no graba
    class DummyDebug:
        def __init__(self):
            self.har_dir = DESCARGAS_DIR  # dummy
            self.screenshots_dir = DESCARGAS_DIR
            self.html_dir = DESCARGAS_DIR
            self.descargas_dir = DESCARGAS_DIR
            # Para compatibilidad con logger_mod que espera estos paths como strings
            self.run_dir = REPORTES_DIR
            self.logger = log
        def log(self, msg, *a): log.info(msg, *a)
        def error(self, msg, *a): log.error(msg, *a)
        async def capture(self, page, name): return
    dummy = DummyDebug()
    t0 = time.perf_counter()
    res = await logger_mod.procesar_contribuyente(playwright, contribuyente, dummy)  # type: ignore
    t1 = time.perf_counter()
    res["tiempo_seg"] = round(t1 - t0, 2)
    res["inicio_dt"] = datetime.now().isoformat()
    res["fin_dt"] = datetime.now().isoformat()
    return res

async def main() -> None:
    start_dt = datetime.now()
    t0 = time.perf_counter()
    contributors = logger_mod.leer_contribuyentes(CLAVES_PATH)
    log.info("inicio bot=%s periodo=%s contribuyentes=%s headless=%s", BOT_NAME, PERIODO, len(contributors), HEADLESS)
    results = []
    async with async_playwright() as playwright:
        for c in contributors:
            log.info("inicio_contribuyente=%s", c.razon_social)
            r = await _procesar_sin_capture(playwright, c)
            results.append(r)
            log.info("fin_contribuyente=%s estado=%s tiempo=%s", c.razon_social, r.get("estado"), r.get("tiempo_seg"))
    total_seg = round(time.perf_counter() - t0, 2)
    log.info("fase carga fin total=%.1fs promedio=%.1fs", total_seg, total_seg/len(results) if results else 0)

    # Control
    control = control_mod.Control()
    grupos = control_mod.agrupar_archivos(INPUT_DIR, control)
    scrapes = {}
    for r in results:
        parsed = control_mod.parsear_borrador_scrapeado(r.get("scrape_borrador") or "")
        if parsed:
            scrapes[control_mod.identificador_texto(r["razon_social"])] = parsed
            scrapes[r["razon_social"]] = parsed
    control_por_clave = {}
    for clave in sorted(grupos):
        start = len(control.errores)
        control_mod.controlar_grupo(clave, grupos[clave], control, scrapes=scrapes)
        control_por_clave[clave] = control.errores[start:]
        try:
            control_mod.generar_reporte(clave, grupos[clave], control_por_clave[clave], REPORTES_DIR)
        except Exception as e:
            log.error("reporte individual %s error %s", clave, e)

    # Consolidado limpio en ./descargas/
    DESCARGAS_DIR.mkdir(parents=True, exist_ok=True)
    consolidado = DESCARGAS_DIR / f"Reporte_Consolidado_931_{PERIODO.replace('/','')}_limpio.xlsx"
    generar_reporte_consolidado_limpio(results, control_por_clave, total_seg, consolidado)
    if REPORTES_DIR.is_dir():
        try:
            import shutil
            shutil.copy(consolidado, REPORTES_DIR / consolidado.name)
        except Exception:
            pass

    reporte = {
        "bot": BOT_NAME,
        "periodo": PERIODO,
        "inicio_dt": start_dt.isoformat(),
        "fin_dt": datetime.now().isoformat(),
        "tiempo_total_seg": total_seg,
        "resultados": results,
        "control": {"errores": control.errores, "por_clave": control_por_clave},
    }
    json_path = DESCARGAS_DIR / f"resultado_{BOT_NAME}_{PERIODO.replace('/','')}.json"
    json_path.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        (REPORTES_DIR / json_path.name).write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    print("\n" + "="*70)
    print(f" CARGA 931 FULL LIMPIO – {PERIODO} – {total_seg}s ({total_seg/60:.2f} min)")
    print("="*70)
    for r in results:
        print(f"  - {r['razon_social']:<28} {r.get('estado'):<5} {r.get('tiempo_seg'):>6}s")
    print(f"Errores control: {len(control.errores)}")
    print(f"Consolidado: {consolidado}")
    print(f"JSON: {json_path}")
    print("="*70)
    if any(r.get("estado")!="OK" for r in results) or control.errores:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
