import asyncio
import os
import re
import json
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright, Page, expect

from shared.arca_login import wait_page_ready
from shared.proxy import get_proxy_config

from moa.moa_bot import (
    extraer_datos_tablas,
    navegar_arbol_empresa,
    buscar_y_scrapear_despacho,
)

BOT_NAME = "moa"


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
        for p in (self.logs_dir, self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
            p.mkdir(parents=True, exist_ok=True)

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
        url = page.url if hasattr(page, "url") else ""
        self.logger.info("step=%s name=%s url=%s", self.step, step, url)

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


async def bot_moa_logger(
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    despachos: list[str],
    metodo: str = "url",
    tipo_agente: str = "IMEX-IMEX-IMPORTADOR/EXPORT.",
    rol: str = "IMEX-Rol Importador Exportador",
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {
        "metodo": metodo,
        "despachos_procesados": 0,
        "despachos_exitosos": 0,
        "despachos_con_error": 0,
        "datos": [],
    }
    error_logs: list[str] = []
    success = True

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot MOA Logger")

    proxy = get_proxy_config() if proxy_request else None
    har_path = debug.har_dir / f"{BOT_NAME}.har"
    output_dir = debug.descargas_dir

    browser = None
    context = None
    page1 = None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(
                record_har_path=har_path,
                record_har_content="embed",
            )
            page = await context.new_page()

            await debug.capture(page, "inicio")

            # --- LOGIN ---
            debug.log("step=login_inicio")
            await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
            await wait_page_ready(page)
            await debug.capture(page, "login_pagina_cargada")

            await page.get_by_role("spinbutton").click()
            await page.get_by_role("spinbutton").fill(cuit_representante)
            await page.get_by_role("button", name="Siguiente").click()
            await wait_page_ready(page)

            try:
                captcha_img = page.get_by_role("img", name=re.compile(r"Captcha", re.I))
                if await captcha_img.count() > 0 and await captcha_img.first.is_visible():
                    debug.log("Captcha detectado")
                    await debug.capture(page, "captcha_detectado")
            except Exception:
                pass

            await debug.capture(page, "login_cuit_ingresado")

            await page.get_by_role("textbox", name="TU CLAVE").click()
            await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
            await page.get_by_role("button", name="Ingresar").click()
            await wait_page_ready(page)
            await debug.capture(page, "login_clave_ingresada")

            # Check for password change required
            try:
                cambio_clave = page.get_by_role("heading", name=re.compile(r"Cambiar\s+Clave\s+Fiscal", re.I))
                if await cambio_clave.count() > 0 and await cambio_clave.first.is_visible():
                    msg = "Se requiere cambiar la clave fiscal"
                    error_logs.append(msg)
                    debug.log(msg)
                    await debug.capture(page, "cambio_clave_fiscal")
                    await context.close()
                    await browser.close()
                    return results, error_logs, False
            except Exception:
                pass

            # --- ACCEDER A MOA ---
            await page.get_by_role("link", name="Ver todos").click()
            await wait_page_ready(page)
            await debug.capture(page, "ver_todos")

            # Check if MOA service button exists before clicking
            moa_btn = page.get_by_role("button", name="MOA – REINGENIERIA Mis")
            try:
                await moa_btn.first.wait_for(state="visible", timeout=10000)
                moa_exists = True
            except Exception:
                moa_exists = False
            if not moa_exists:
                msg = "No se encuentra habilitado el servicio de MOA"
                error_logs.append(msg)
                debug.log(msg)
                await debug.capture(page, "servicio_no_habilitado")
                await context.close()
                await browser.close()
                return results, error_logs, False

            async with page.expect_popup() as page1_info:
                await moa_btn.first.click()

            page1 = await page1_info.value
            await wait_page_ready(page1)
            await debug.capture(page1, "moa_popup_abierto")

            # --- Navegar árbol de empresa ---
            debug.log("step=navegar_arbol")
            if not await navegar_arbol_empresa(page1, cuit_representado, tipo_agente, rol):
                msg = f"No se pudo seleccionar la empresa con CUIT {cuit_representado}"
                error_logs.append(msg)
                debug.log(msg)
                await debug.capture(page1, "arbol_empresa_error")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await debug.capture(page1, "arbol_empresa_ok")

            # --- Verificar menú principal ---
            try:
                await page1.get_by_text("Transacciones", exact=True).wait_for(timeout=5000)
                debug.log("step=menu_principal_ok")
            except Exception:
                msg = "No se pudo acceder al menú principal MOA"
                error_logs.append(msg)
                debug.log(msg)
                await debug.capture(page1, "menu_principal_error")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await debug.capture(page1, "menu_principal")

            # --- Navegar a Declaración Detallada ---
            await page1.get_by_text("Transacciones", exact=True).click()
            await page1.locator("a").filter(has_text="GESTION DE LA DECLARACION").click()
            await page1.get_by_role("link", name="Declaracion Detallada").click()
            await wait_page_ready(page1)
            await debug.capture(page1, "declaracion_detallada")

            from collections import deque

            MAX_RETRIES = 3

            # --- Procesar despachos con cola de reintentos ---
            cola = deque((d, MAX_RETRIES) for d in despachos)
            total = len(despachos)
            procesados = 0
            paso_idx = 0

            while cola:
                despacho, restantes = cola.popleft()
                procesados += 1
                reintento = MAX_RETRIES - restantes
                paso_idx += 1

                label = f"despacho_{procesados:03d}" if restantes == MAX_RETRIES else f"reintento_{procesados:03d}_{reintento}"
                debug.log(f"step={label} despacho={despacho} restantes={restantes}")
                await debug.capture(page1, f"{label}_inicio")

                resultado = await buscar_y_scrapear_despacho(page1, despacho, metodo)

                if resultado["error"] and restantes > 1:
                    cola.append((despacho, restantes - 1))
                    msg = f"{despacho} pasa a cola de reintentos ({restantes - 1} intentos restantes)"
                    debug.log(f"step={label}_encolar error={resultado['error']} msg={msg}")
                    print(f"  ↻ {msg}")
                    await debug.capture(page1, f"{label}_encolar")
                    continue

                if resultado["error"]:
                    error_logs.append(f"Despacho {despacho}: {resultado['error']}")
                    debug.log(f"step={label}_error error={resultado['error']}")
                    await debug.capture(page1, f"{label}_error")
                else:
                    await debug.capture(page1, f"{label}_ok")

                results["datos"].append(resultado)
                results["despachos_procesados"] += 1
                if resultado["error"]:
                    results["despachos_con_error"] += 1
                    success = False
                else:
                    results["despachos_exitosos"] += 1

            # --- Exportar resultados ---
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            json_path = output_dir / f"moa_resultados_{metodo}_{timestamp}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            debug.log(f"json_exportado={json_path}")

            csv_path = output_dir / f"moa_resultados_{metodo}_{timestamp}.csv"
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                if results["datos"]:
                    campos = set(["despacho", "metodo", "timestamp", "error"])
                    for dato in results["datos"]:
                        campos.update([f"caratula_{k}" for k in dato["datos_caratula"].keys()])
                        campos.update([f"estado_{k}" for k in dato["estado_presentacion"].keys()])

                    writer = csv.DictWriter(f, fieldnames=sorted(campos))
                    writer.writeheader()

                    for dato in results["datos"]:
                        fila = {
                            "despacho": dato["despacho"],
                            "metodo": dato["metodo"],
                            "timestamp": dato["timestamp"],
                            "error": dato["error"] or "",
                        }
                        for k, v in dato["datos_caratula"].items():
                            fila[f"caratula_{k}"] = v
                        for k, v in dato["estado_presentacion"].items():
                            fila[f"estado_{k}"] = v
                        writer.writerow(fila)

            debug.log(f"csv_exportado={csv_path}")
            debug.log("step=fin status=OK")

            await context.close()
            await browser.close()

    except Exception as exc:
        error_msg = f"Error en bot MOA logger: {exc}"
        error_logs.append(error_msg)
        debug.log(error_msg)
        if debug.step == 0:
            pass
        success = False
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass

    if har_path.exists():
        debug.log(f"har_saved path={har_path} size={har_path.stat().st_size}")

    return results, error_logs, success


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    cuit_representante = os.getenv("CUIT_REPRESENTANTE_MOA") or os.getenv("REPRESENTANTE")
    clave = os.getenv("CLAVE_MOA")
    cuit_representado = os.getenv("CUIT_REPRESENTADO_MOA") or os.getenv("REPRESENTADO")

    if not cuit_representante:
        print("ERROR: CUIT_REPRESENTANTE no configurado en .env")
        return
    if not clave:
        print("ERROR: CLAVE_MOA no configurada en .env")
        return
    if not cuit_representado:
        print("ERROR: CUIT_REPRESENTADO_MOA no configurado en .env")
        return

    with open("despachos.txt", "r") as f:
        despachos = [line.strip() for line in f if line.strip()]

    print("=" * 60)
    print("BOT MOA LOGGER - SCRAPING DE DESPACHOS")
    print("=" * 60)
    print(f"Representante: {cuit_representante}")
    print(f"Representado: {cuit_representado}")
    print(f"Despachos a procesar: {len(despachos)}")

    results, error_logs, ok = await bot_moa_logger(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        despachos=despachos,
        metodo="url",
    )

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"Procesados: {results['despachos_procesados']}")
    print(f"Exitosos: {results['despachos_exitosos']}")
    print(f"Con errores: {results['despachos_con_error']}")
    if error_logs:
        print("\nErrores:")
        for e in error_logs:
            print(f"  - {e}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
