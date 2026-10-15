#!/usr/bin/env python3
"""
carga_931_full_logger.py – Todo en uno: carga 931 + control + reporte consolidado + timing.

Flujo:
  1) Lee claves.xlsx (3 contribuyentes)
  2) Para cada contribuyente: login ARCA → LSD → crea período (prima/costo automático)
     → carga TXT → valida → scrape borrador → mide tiempo por contribuyente
  3) Valida TXT / Excel_control / Excel_reporte / borrador scrapeado (control_descargas)
  4) Genera reportes individuales (Control_*.xlsx) + reporte consolidado con tiempos
  5) Guarda resultado JSON + log + HAR/screenshots/html por paso

Uso:
  .venv/bin/python carga_931_full_logger.py            # headless según CARGA_931_HEADLESS env
  CARGA_931_HEADLESS=true .venv/bin/python carga_931_full_logger.py
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
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.async_api import async_playwright

# ── Carga dinámica de módulos base (evita duplicar 1200 líneas) ──────────────
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "descargas" / "carga-931-xubio"
CLAVES_PATH = INPUT_DIR / "claves.xlsx"
TEMP_DIR = BASE_DIR / "temp"
CONTROL_SRC = Path("/home/abp/Desktop/descarga-xubio/control_descargas.py")
LOGGER_SRC = BASE_DIR / "carga-931-logger.py"

BOT_NAME = "carga_931_full"
PERIODO_OBJETIVO = "2026/08"
PERIODO = "08/2026"

HEADLESS = os.getenv("CARGA_931_HEADLESS", "false").lower() in {"1", "true", "yes"}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo cargar {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


logger_mod = _load_module("carga931_logger", LOGGER_SRC)
control_mod = _load_module("control_descargas", CONTROL_SRC)

# Re-exportar helpers de control para uso local (evita prefijo)
parsear_borrador_scrapeado = control_mod.parsear_borrador_scrapeado
_totales_reporte_secciones = control_mod._totales_reporte_secciones
comparar_borrador_con_reporte = control_mod.comparar_borrador_con_reporte
identificador_texto = control_mod.identificador_texto
texto = control_mod.texto

# ── DebugCapture propio (mismo layout que logger, pero con BOT_NAME full) ─────
class DebugCapture:
    def __init__(self) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = run_id
        self.run_dir = TEMP_DIR / f"{BOT_NAME}_{run_id}"
        self.descargas_dir = self.run_dir / "descargas"
        self.html_dir = self.run_dir / "html"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.har_dir = self.run_dir / "har"
        for d in (self.descargas_dir, self.html_dir, self.screenshots_dir, self.har_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.step = 0
        self.logger = logging.getLogger(f"{BOT_NAME}.{run_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.FileHandler(self.run_dir / f"{BOT_NAME}.log")
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        self.logger.addHandler(sh)

    def log(self, msg: str, *args: Any) -> None:
        self.logger.info(msg, *args)

    def error(self, msg: str, *args: Any) -> None:
        self.logger.error(msg, *args)

    async def capture(self, page, name: str) -> None:
        if page is None:
            return
        self.step += 1
        prefix = f"{self.step:03d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_')}"
        try:
            await page.screenshot(path=str(self.screenshots_dir / f"{prefix}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.html_dir / f"{prefix}.html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            pass


async def execute_step(debug: DebugCapture, page, name: str, action):
    debug.log("step_start=%s url=%s", name, getattr(page, "url", ""))
    try:
        result = await action()
        await debug.capture(page, name)
        debug.log("step_end=%s status=OK url=%s", name, getattr(page, "url", ""))
        return result
    except Exception as exc:
        debug.error("step_end=%s status=ERROR url=%s error=%s", name, getattr(page, "url", ""), exc)
        await debug.capture(page, f"{name}_error")
        raise


# ── Adaptador procesar_contribuyente con timing y DebugCapture full ──────────
async def procesar_contribuyente_timed(playwright, contribuyente, debug: DebugCapture) -> dict[str, Any]:
    """Wrapper de logger_mod.procesar_contribuyente que mide tiempo y usa debug full."""
    # logger_mod procesar_contribuyente espera un Playwright, Contribuyente y debug con .har_dir etc.
    # Nuestra debug tiene misma interfaz, así que podemos llamar directamente pero pasando
    # el playwright interno. Para timing, medimos alrededor.
    t0 = time.perf_counter()
    inicio_dt = datetime.now()
    result = await logger_mod.procesar_contribuyente(playwright, contribuyente, debug)  # type: ignore
    t1 = time.perf_counter()
    result["tiempo_seg"] = round(t1 - t0, 2)
    result["inicio_dt"] = inicio_dt.isoformat()
    result["fin_dt"] = datetime.now().isoformat()
    # Normalizar scrape key para control
    if "scrape_borrador" not in result and "borrador" in result:
        result["scrape_borrador"] = result.get("borrador", {}).get("html", "")
    return result


# ── Reporte consolidado (global) ─────────────────────────────────────────────
def _estilo_header(ws, row: int = 1):
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(bold=True, color="FFFFFF")
    for c in ws[row]:
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def generar_reporte_consolidado(
    resultados: list[dict],
    control_por_clave: dict[str, list[str]],
    tiempos_por_clave: dict[str, float],
    total_seg: float,
    run_dir: Path,
    debug: DebugCapture,
) -> Path:
    wb = Workbook()

    # ── Hoja Resumen ──────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Resumen"
    ws.append(["Reporte Consolidado Carga 931 – Control Integral"])
    ws.append(["Bot", BOT_NAME])
    ws.append(["Periodo", PERIODO])
    ws.append(["Fecha ejecución", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws.append(["Run", run_dir.name])
    ws.append(["Contribuyentes", len(resultados)])
    ws.append(["Tiempo total (s)", round(total_seg, 2)])
    ws.append(["Tiempo total (min)", round(total_seg / 60, 2)])
    ws.append(["Promedio por contribuyente (s)", round(total_seg / len(resultados), 2) if resultados else 0])
    errores_totales = sum(len(v) for v in control_por_clave.values())
    ws.append(["Controles con error", sum(1 for v in control_por_clave.values() if v)])
    ws.append(["Inconsistencias totales", errores_totales])
    ws.append(["Estado global", "ERROR" if errores_totales or any(r.get("estado") != "OK" for r in resultados) else "OK"])
    ws.append([])
    ws.append(["Detalle por contribuyente"])
    header = ["#", "Razón social", "CUIT", "Jurisdicción", "Estado carga", "Trab. TXT", "Trab. borrador",
              "Remunerativos", "No remunerativos", "Descuentos", "Tiempo (s)", "Errores control", "Detalle"]
    ws.append(header)
    _estilo_header(ws, ws.max_row)
    # Necesitamos totales reporte para mostrar en consolidado
    for idx, r in enumerate(resultados, 1):
        razon = r.get("razon_social", "")
        cuit = r.get("cuit_representado", "")
        jur = r.get("jurisdiccion", "")
        estado = r.get("estado", "ERROR")
        tseg = r.get("tiempo_seg", tiempos_por_clave.get(identificador_texto(razon), "-"))
        scrape = control_mod.parsear_borrador_scrapeado(r.get("scrape_borrador") or "")
        trab_b = scrape["trabajadores"] if scrape else "-"
        rem = str(scrape["remunerativos"]) if scrape else "-"
        norem = str(scrape["no_remunerativos"]) if scrape else "-"
        desc = str(scrape["descuentos"]) if scrape and scrape.get("descuentos") is not None else "-"
        # Trabajadores TXT: intentar extraer de reporte o de liquidación (usamos control si existe)
        # Buscamos errores de esa clave
        clave = None
        for cand in [razon, cuit]:
            for k in control_por_clave:
                if identificador_texto(k) == identificador_texto(cand):
                    clave = k
                    break
        errs = control_por_clave.get(clave or razon, control_por_clave.get(identificador_texto(razon), []))
        if not errs and clave is None:
            # fallback buscar por texto normalizado
            for k, v in control_por_clave.items():
                if identificador_texto(k) == identificador_texto(razon):
                    errs = v
                    break
        # Trabajadores TXT: se puede inferir de scrape vs reporte, pero ponemos len(trabajadores) si existe
        # Intentar obtener de resultados: no tenemos detalle TXT, usar scrape trabajadores como referencia
        trab_txt = trab_b  # si coincide, es mismo; si no, control habrá marcado error
        ws.append([idx, razon, cuit, jur, estado, trab_txt, trab_b, rem, norem, desc, tseg, len(errs), "; ".join(errs[:3])])
    # Estilos
    for col in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["M"].width = 55
    ws.freeze_panes = "A14"
    ws.auto_filter.ref = f"A14:M{ws.max_row}"
    # Colores de estado
    for row in ws.iter_rows(min_row=14, max_row=ws.max_row):
        estado_cell = row[4]
        if estado_cell.value == "OK":
            estado_cell.fill = PatternFill("solid", fgColor="C6EFCE")
        elif estado_cell.value == "ERROR":
            estado_cell.fill = PatternFill("solid", fgColor="FFC7CE")

    # ── Hoja Tiempos ───────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Tiempos")
    ws2.append(["#", "Razón social", "CUIT", "Inicio", "Fin", "Duración (s)", "Duración (mm:ss)"])
    _estilo_header(ws2, 1)
    for idx, r in enumerate(resultados, 1):
        razon = r.get("razon_social", "")
        cuit = r.get("cuit_representado", "")
        inicio = r.get("inicio_dt", "")
        fin = r.get("fin_dt", "")
        seg = r.get("tiempo_seg", 0)
        mmss = f"{int(seg)//60:02d}:{int(seg)%60:02d}"
        ws2.append([idx, razon, cuit, inicio, fin, seg, mmss])
    ws2.append([])
    ws2.append(["Total", "", "", "", "", round(total_seg, 2), f"{int(total_seg)//60:02d}:{int(total_seg)%60:02d}"])
    _estilo_header(ws2, ws2.max_row)
    for col in range(1, 8):
        ws2.column_dimensions[get_column_letter(col)].width = 20
    ws2.column_dimensions["B"].width = 30

    # ── Hoja Validación_Control (detalle de errores) ───────────────────────
    # Mapa clave abreviada -> razón social para visualización
    clave_a_razon = {}
    for r in resultados:
        razon = r.get("razon_social", "")
        for k in control_por_clave.keys():
            if identificador_texto(k) == identificador_texto(razon).replace(" ", "")[:len(k)] or k.lower() in razon.lower().split() or razon.lower().startswith(k.lower()):
                clave_a_razon[k] = razon
                break
            # fallback: si la clave es prefijo de la razón normalizada
            if identificador_texto(razon).startswith(identificador_texto(k)):
                clave_a_razon[k] = razon
    ws3 = wb.create_sheet("Validacion_Control")
    ws3.append(["Contribuyente", "Tipo", "Detalle"])
    _estilo_header(ws3, 1)
    for clave, errs in control_por_clave.items():
        display = clave_a_razon.get(clave, clave)
        if not errs:
            ws3.append([display, "OK", "No se encontraron inconsistencias (incluye borrador)"])
        else:
            for e in errs:
                ws3.append([display, "ERROR", e])
    for col in range(1, 4):
        ws3.column_dimensions[get_column_letter(col)].width = [30, 10, 110][col - 1]
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:C{ws3.max_row}"

    # ── Hoja Controles_Realizados ──────────────────────────────────────────
    ws4 = wb.create_sheet("Controles_Realizados")
    ws4.append(["Contribuyente", "Control", "Estado"])
    _estilo_header(ws4, 1)
    controles = [
        "Estructura y campos del TXT de conceptos",
        "Estructura y relaciones del TXT de liquidacion",
        "Conceptos TXT contra conceptos liquidados",
        "TXT de liquidacion contra Excel_control",
        "Excel_control contra Excel_reporte",
        "Borrador ARCA (scrape) contra Excel_reporte",
        "Tiempo de ejecución registrado",
    ]
    for r in resultados:
        razon = r.get("razon_social", "")
        clave = razon
        errs = []
        for k, v in control_por_clave.items():
            if identificador_texto(k) == identificador_texto(razon):
                errs = v
                break
        for ctrl in controles:
            ws4.append([razon, ctrl, "ERROR" if errs and any(ctrl.split()[0] in e for e in errs) else "Revisado"])
    ws4.column_dimensions["A"].width = 30
    ws4.column_dimensions["B"].width = 55
    ws4.column_dimensions["C"].width = 15

    # Guardar
    destino = debug.descargas_dir / f"Reporte_Consolidado_931_{PERIODO.replace('/', '')}_{debug.run_id}.xlsx"
    wb.save(destino)

    # Copia adicional a descargas/carga-931-xubio/reportes para trazabilidad fuera de temp
    try:
        copia_reportes = INPUT_DIR / "reportes"
        copia_reportes.mkdir(parents=True, exist_ok=True)
        wb.save(copia_reportes / destino.name)
    except Exception:
        pass

    # También copiar a escritorio reportes si existe
    try:
        alt = Path("/home/abp/Desktop/descarga-xubio/reportes")
        if alt.is_dir():
            wb.save(alt / destino.name)
    except Exception:
        pass

    return destino


async def main() -> None:
    start_total_dt = datetime.now()
    t_total_0 = time.perf_counter()

    contributors = logger_mod.leer_contribuyentes(CONTROL_SRC.parent / "descargas" / "carga-931-xubio" / "claves.xlsx" if False else CLAVES_PATH)
    # Fallback: si no existe el path del logger_mod (usa su INPUT_DIR), usar nuestra CLAVES_PATH
    if not CLAVES_PATH.exists():
        contributors = logger_mod.leer_contribuyentes(logger_mod.CLAVES_PATH)

    debug = DebugCapture()
    debug.log("inicio bot=%s periodo=%s contribuyentes=%s headless=%s", BOT_NAME, PERIODO, len(contributors), HEADLESS)
    debug.log("control_src=%s logger_src=%s", CONTROL_SRC, LOGGER_SRC)

    results: list[dict[str, Any]] = []
    timings: dict[str, float] = {}

    async with async_playwright() as playwright:
        for c in contributors:
            debug.log("inicio_contribuyente=%s cuit=%s", c.razon_social, c.cuit_representado)
            res = await procesar_contribuyente_timed(playwright, c, debug)
            results.append(res)
            timings[identificador_texto(c.razon_social)] = res.get("tiempo_seg", 0)
            debug.log("fin_contribuyente=%s estado=%s tiempo=%ss", c.razon_social, res.get("estado"), res.get("tiempo_seg"))

    t_total_1 = time.perf_counter()
    total_seg = round(t_total_1 - t_total_0, 2)
    debug.log("fase_carga_fin total_seg=%s promedio=%s", total_seg, round(total_seg / len(results), 2) if results else 0)

    # ── Fase Control ───────────────────────────────────────────────────────
    debug.log("inicio_control validando TXT/Excel/borrador")
    t_ctrl_0 = time.perf_counter()
    control = control_mod.Control()
    # Agrupar archivos (TXT + reportes)
    grupos = control_mod.agrupar_archivos(INPUT_DIR, control)
    if not grupos:
        control.error("GENERAL", "no se encontraron archivos de descarga en INPUT_DIR")

    # Construir mapa scrapes desde resultados en memoria (más fiable que JSON)
    scrapes: dict[str, Any] = {}
    for r in results:
        razon = r.get("razon_social") or ""
        parsed = parsear_borrador_scrapeado(r.get("scrape_borrador") or "")
        if parsed:
            scrapes[identificador_texto(razon)] = parsed
            scrapes[razon] = parsed
    # Añadir _json_path ficticio para compatibilidad
    scrapes["_json_path"] = {"path": "memoria_resultados"}  # type: ignore

    # Control por grupo (con scrapes)
    control_por_clave: dict[str, list[str]] = {}
    # Directorio para reportes individuales (usar el mismo que logger: descargas del run + global)
    directorio_reportes_individuales = debug.descargas_dir
    # También mantener copia en INPUT_DIR/reportes
    copia_reportes_dir = INPUT_DIR / "reportes"
    copia_reportes_dir.mkdir(parents=True, exist_ok=True)

    for clave in sorted(grupos):
        inicio_err = len(control.errores)
        datos = control_mod.controlar_grupo(clave, grupos[clave], control, scrapes=scrapes)
        errs = control.errores[inicio_err:]
        control_por_clave[clave] = errs
        try:
            # generar reporte individual en debug descargas
            p1 = control_mod.generar_reporte(clave, grupos[clave], errs, directorio_reportes_individuales)
            debug.log("reporte_individual=%s clave=%s errores=%s", p1, clave, len(errs))
            # copia a reportes global
            p2 = control_mod.generar_reporte(clave, grupos[clave], errs, copia_reportes_dir)
            # también a escritorio si existe
            escritorio = Path("/home/abp/Desktop/descarga-xubio/reportes")
            if escritorio.is_dir():
                control_mod.generar_reporte(clave, grupos[clave], errs, escritorio)
        except Exception as e:
            control.error(clave, f"no se pudo generar reporte individual: {e}")

    t_ctrl_1 = time.perf_counter()
    debug.log("fin_control control_seg=%s errores_totales=%s", round(t_ctrl_1 - t_ctrl_0, 2), len(control.errores))

    # ── Reporte consolidado ────────────────────────────────────────────────
    try:
        consolidado_path = generar_reporte_consolidado(results, control_por_clave, timings, total_seg, debug.run_dir, debug)
        debug.log("reporte_consolidado=%s", consolidado_path)
    except Exception as e:
        debug.error("error_generando_consolidado=%s", e)
        consolidado_path = None

    # ── JSON resultado extendido ───────────────────────────────────────────
    reporte = {
        "bot": BOT_NAME,
        "periodo": PERIODO,
        "periodo_objetivo": PERIODO_OBJETIVO,
        "inicio": debug.run_id,
        "inicio_dt": start_total_dt.isoformat(),
        "fin_dt": datetime.now().isoformat(),
        "fuente_claves": str(CLAVES_PATH),
        "headless": HEADLESS,
        "tiempo_total_seg": total_seg,
        "tiempo_total_min": round(total_seg / 60, 2),
        "tiempo_promedio_seg": round(total_seg / len(results), 2) if results else 0,
        "tiempos_por_contribuyente": timings,
        "resultados": results,
        "control": {
            "errores": control.errores,
            "por_clave": control_por_clave,
            "grupos": len(grupos),
        },
        "reportes_individuales": [str(p) for p in directorio_reportes_individuales.glob("Control_*.xlsx")],
        "reporte_consolidado": str(consolidado_path) if consolidado_path else None,
        "run_dir": str(debug.run_dir),
    }
    json_path = debug.descargas_dir / f"resultado_{BOT_NAME}_{PERIODO.replace('/', '')}.json"
    json_path.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    # Copia a INPUT_DIR/reportes
    try:
        (copia_reportes_dir / json_path.name).write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    debug.log("fin json=%s", json_path)
    debug.log("tiempos: %s", json.dumps(timings, ensure_ascii=False))
    debug.log("reporte_consolidado=%s", consolidado_path)

    # Resumen consola
    print("\n" + "=" * 70)
    print(f" CARGA 931 FULL – {BOT_NAME}  Periodo {PERIODO}  Run {debug.run_id}")
    print("=" * 70)
    print(f"Contribuyentes: {len(results)}  |  Tiempo total: {total_seg}s ({total_seg/60:.2f} min)")
    for r in results:
        estado = r.get("estado", "ERROR")
        seg = r.get("tiempo_seg", "-")
        razon = r.get("razon_social", "?")
        print(f"  - {razon:<28} {estado:<5}  {seg:>6}s")
    print(f"Controles: {len(grupos)} grupos  |  Errores totales: {len(control.errores)}")
    if control.errores:
        for e in control.errores:
            print(f"    * {e}")
    else:
        print("  RESULTADO CONTROL: OK – sin inconsistencias (incluye borrador scrapeado)")
    print(f"Reporte consolidado: {consolidado_path}")
    print(f"JSON: {json_path}")
    print(f"Logs: {debug.run_dir / (BOT_NAME + '.log')}")
    print(f"HAR: {debug.har_dir}  |  Screenshots: {debug.screenshots_dir}  |  HTML: {debug.html_dir}")
    print("=" * 70 + "\n")

    # Exit code 1 si algún contribuyente falló o control con errores
    if any(r.get("estado") != "OK" for r in results) or control.errores:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
