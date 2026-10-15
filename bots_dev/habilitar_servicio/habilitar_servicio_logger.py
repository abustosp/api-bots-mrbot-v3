import asyncio
import os
import re
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    open_arca_service,
    wait_page_ready,
    ARCA_LOGIN_URL,
)
from shared.proxy import get_proxy_config


# ---------------------------------------------------------------------------
# Configuración de servicios habilitables
# ---------------------------------------------------------------------------

@dataclass
class ServicioHabilitable:
    clave: str
    nombre: str
    termino_busqueda: str
    matchers_dropdown: Tuple[str, ...]
    ya_habilitado_textos: Tuple[str, ...] = ()
    confirmacion_textos: Tuple[str, ...] = ()


SERVICIO_MIS_COMPROBANTES = ServicioHabilitable(
    clave="mis_comprobantes",
    nombre="Mis Comprobantes",
    termino_busqueda="mis comprobantes",
    matchers_dropdown=("Mis Comprobantes",),
    ya_habilitado_textos=(
        "Comprobantes Emitidos",
        "Comprobantes Recibidos",
        "Consulta de Comprobantes Electrónicos",
    ),
    confirmacion_textos=(
        "habilitación ha sido",
        "adhesión ha sido",
        "servicio se habilitó",
        "operación se realizó",
        "se ha habilitado",
    ),
)

SERVICIOS_DISPONIBLES: Dict[str, ServicioHabilitable] = {
    "mis_comprobantes": SERVICIO_MIS_COMPROBANTES,
}

DEFAULT_SERVICIOS: List[str] = ["mis_comprobantes"]


# ---------------------------------------------------------------------------
# Captura de evidencias por paso (logger)
# ---------------------------------------------------------------------------

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "habilitar_servicio") -> None:
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

    async def capture(self, page, step: str) -> None:
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


# ---------------------------------------------------------------------------
# Helpers de localización
# ---------------------------------------------------------------------------

async def _find_visible(locators: Sequence[Locator], timeout_ms: int = 10_000) -> Optional[Locator]:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        for loc in locators:
            try:
                count = await loc.count()
            except Exception:
                continue
            if count <= 0:
                continue
            candidate = loc.first
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.25)


async def _click_loc(locator, timeout_ms: int = 8_000) -> bool:
    try:
        await locator.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    try:
        await locator.click(timeout=timeout_ms)
        return True
    except Exception:
        try:
            handle = await locator.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Búsqueda en el buscador de ARCA + selección del servicio
# ---------------------------------------------------------------------------

async def buscar_y_abrir_servicio(
    page: Page,
    servicio: ServicioHabilitable,
    debug: DebugCapture,
    error_logs: List[str],
    popup_timeout_ms: int = 15_000,
) -> Optional[Page]:
    debug.log(f"Buscando servicio '{servicio.nombre}' con término '{servicio.termino_busqueda}'")

    input_locators = [
        page.locator("#buscadorInput"),
        page.get_by_placeholder(re.compile(r"¿Qué necesitás", re.I)),
        page.get_by_role("combobox"),
    ]
    input_el = await _find_visible(input_locators, timeout_ms=12_000)
    if input_el is None:
        error_logs.append("No se pudo encontrar el buscador de servicios en la página principal de ARCA")
        return None

    try:
        await input_el.click(timeout=5_000)
        await input_el.fill("")
        await input_el.fill(servicio.termino_busqueda)
        await page.wait_for_timeout(900)
    except Exception as exc:
        error_logs.append(f"No se pudo escribir en el buscador: {exc}")
        return None

    await debug.capture(page, f"buscador_{servicio.clave}_escrito")

    primary_matcher = servicio.matchers_dropdown[0]
    dropdown_locators = [
        page.locator("#resultadoBusqueda a.dropdown-item").filter(
            has_text=re.compile(re.escape(primary_matcher), re.I)
        ),
        page.locator("a.dropdown-item").filter(
            has_text=re.compile(re.escape(primary_matcher), re.I)
        ),
        page.get_by_text(re.compile(re.escape(primary_matcher), re.I)),
    ]
    dropdown_item = await _find_visible(dropdown_locators, timeout_ms=12_000)
    if dropdown_item is None:
        error_logs.append(
            f"No se encontró el servicio '{servicio.nombre}' en los resultados de búsqueda"
        )
        await debug.capture(page, f"buscador_{servicio.clave}_sin_resultados")
        return None

    await debug.capture(page, f"buscador_{servicio.clave}_resultado_visible")

    try:
        async with page.expect_popup(timeout=popup_timeout_ms) as popup_info:
            clicked = await _click_loc(dropdown_item, timeout_ms=8_000)
            if not clicked:
                raise RuntimeError("No se pudo hacer clic en el resultado del buscador")
        service_page = await popup_info.value
    except PlaywrightTimeoutError:
        # Si no abre popup, se trabaja en la misma pestaña.
        if not clicked:
            await _click_loc(dropdown_item, timeout_ms=3_000)
        service_page = page
    except Exception as exc:
        error_logs.append(f"Error al abrir el servicio '{servicio.nombre}': {exc}")
        return None

    await wait_page_ready(service_page)
    await debug.capture(service_page, f"servicio_{servicio.clave}_abierto")
    return service_page


# ---------------------------------------------------------------------------
# Habilitación del servicio
# ---------------------------------------------------------------------------

def _contiene_alguno(texto: str, needles: Sequence[str]) -> bool:
    for needle in needles:
        if re.search(re.escape(needle), texto or "", re.I):
            return True
    return False


async def habilitar_servicio(
    service_page: Page,
    main_page: Page,
    servicio: ServicioHabilitable,
    debug: DebugCapture,
    error_logs: List[str],
) -> Tuple[bool, str]:
    await wait_page_ready(service_page)
    contenido = await service_page.content()

    if _contiene_alguno(contenido, servicio.ya_habilitado_textos):
        debug.log(f"Servicio '{servicio.nombre}' ya se encuentra habilitado")
        await debug.capture(service_page, f"{servicio.clave}_ya_habilitado")
        return True, "ya_habilitado"

    # Buscar botón Continuar en el modal de agregar servicio.
    continuar_locators = [
        service_page.get_by_role("button", name=re.compile(r"Continuar", re.I)),
        service_page.locator("button, a, input[type='submit']").filter(
            has_text=re.compile(r"Continuar", re.I)
        ),
        service_page.get_by_role("button", name=re.compile(r"Habilitar|Adherir", re.I)),
        service_page.locator("button, a, input[type='submit']").filter(
            has_text=re.compile(r"Habilitar|Adherir", re.I)
        ),
    ]
    boton = await _find_visible(continuar_locators, timeout_ms=12_000)

    if boton is None:
        error_logs.append(f"No se encuentra habilitado el servicio de {servicio.nombre}")
        await debug.capture(service_page, f"{servicio.clave}_sin_boton_habilitar")
        return False, "no_habilitado"

    # Clickear Continuar una o dos veces (el modal suele tener dos pasos).
    for intento in range(2):
        debug.log(f"Habilitando servicio '{servicio.nombre}' (intento {intento + 1})")
        if not await _click_loc(boton, timeout_ms=8_000):
            error_logs.append(
                f"No se pudo hacer clic en Continuar del servicio {servicio.nombre}"
            )
            await debug.capture(service_page, f"{servicio.clave}_error_click_habilitar")
            return False, "no_habilitado"

        await asyncio.sleep(1.5)
        await wait_page_ready(service_page)

        # Si el modal se cerró, salir.
        nuevo_boton = await _find_visible(continuar_locators, timeout_ms=2_000)
        if nuevo_boton is None:
            break
        boton = nuevo_boton

    await debug.capture(service_page, f"{servicio.clave}_post_habilitar")

    # Cerrar popup de la búsqueda para trabajar desde la página principal.
    es_popup = service_page is not main_page
    if es_popup and not service_page.is_closed():
        try:
            await service_page.close()
        except Exception:
            pass

    # Verificar habilitación: abrir el servicio vía "Ver todos".
    await debug.capture(main_page, f"{servicio.clave}_antes_verificar")
    service_abierta = await open_arca_service(
        main_page,
        service_name=servicio.nombre.upper(),
        service_matchers=(
            re.compile(servicio.matchers_dropdown[0], re.I),
            servicio.nombre,
        ),
        error_logs=error_logs,
        ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
        service_unavailable_message=f"No se encuentra habilitado el servicio de {servicio.nombre}",
        popup_timeout_ms=15_000,
    )

    if service_abierta is not None:
        if service_abierta is not main_page and not service_abierta.is_closed():
            try:
                await service_abierta.close()
            except Exception:
                pass
        debug.log(f"Servicio '{servicio.nombre}' habilitado correctamente")
        await debug.capture(main_page, f"{servicio.clave}_habilitado_ok")
        return True, "habilitado"

    debug.log(f"No se pudo confirmar la habilitación del servicio de {servicio.nombre}")
    await debug.capture(main_page, f"{servicio.clave}_habilitacion_fallida")
    error_logs.append(
        f"No se pudo confirmar la habilitación del servicio de {servicio.nombre}"
    )
    return False, "no_habilitado"


# ---------------------------------------------------------------------------
# Bot principal (logger)
# ---------------------------------------------------------------------------

async def bot_habilitar_servicio_logger(
    cuit: str,
    clave: str,
    servicios: Optional[List[str]] = None,
    headless: bool = True,
    proxy_request: bool = False,
) -> Dict[str, Any]:
    debug = DebugCapture(prefix="habilitar_servicio")
    debug.log(f"Iniciando bot Habilitar Servicio para CUIT {cuit}")

    servicios_keys = list(servicios or DEFAULT_SERVICIOS)
    result: Dict[str, Any] = {
        "cuit": cuit,
        "success": False,
        "run_dir": str(debug.run_dir),
        "servicios": [],
        "logs": [],
        "errors": [],
    }
    error_logs: List[str] = []

    proxy = get_proxy_config() if proxy_request else None
    har_path = debug.har_dir / "habilitar_servicio.har"

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    try:
        async with async_playwright() as playwright:
            try:
                browser, context, page = await launch_arca_browser_context(
                    playwright,
                    headless=headless,
                    proxy=proxy,
                    context_kwargs={
                        "record_har_path": str(har_path),
                        "record_har_content": "embed",
                    },
                )
            except Exception as exc:
                error_logs.append(f"No se pudo lanzar el navegador: {exc}")
                result["errors"] = error_logs
                return result

            # Auto-aceptar diálogos nativos (confirmaciones de habilitación).
            def _auto_accept_dialog(dialog):
                try:
                    asyncio.ensure_future(dialog.accept())
                except Exception:
                    pass

            page.on("dialog", _auto_accept_dialog)

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                str(cuit),
                str(clave),
                error_logs=error_logs,
            )
            await debug.capture(page, "post_login")

            if not login_result.success:
                debug.log(f"Login fallido: {error_logs}")
                result["errors"] = error_logs
                return result

            debug.log("Login exitoso")
            await debug.capture(page, "home_arca_con_buscador")

            todos_ok = True
            for key in servicios_keys:
                servicio = SERVICIOS_DISPONIBLES.get(key)
                if servicio is None:
                    error_logs.append(f"Servicio desconocido: {key}")
                    todos_ok = False
                    continue

                service_page = await buscar_y_abrir_servicio(page, servicio, debug, error_logs)
                if service_page is None:
                    result["servicios"].append(
                        {"servicio": servicio.nombre, "estado": "no_habilitado", "ok": False}
                    )
                    todos_ok = False
                    continue

                ok, estado = await habilitar_servicio(service_page, page, servicio, debug, error_logs)
                result["servicios"].append(
                    {"servicio": servicio.nombre, "estado": estado, "ok": ok}
                )
                if not ok:
                    todos_ok = False

                # Asegurar que la página principal vuelva al home de ARCA.
                try:
                    if page.is_closed():
                        page = await context.new_page()
                        page.on("dialog", _auto_accept_dialog)
                    await page.goto(ARCA_LOGIN_URL, timeout=45_000)
                    await wait_page_ready(page)
                except Exception:
                    pass

            result["success"] = todos_ok
            result["errors"] = error_logs
            result["logs"] = []
            return result

    except Exception as exc:
        error_logs.append(f"Error general en bot habilitar servicio: {exc}")
        result["errors"] = error_logs
        result["success"] = False
        return result
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
# Ejecución masiva
# ---------------------------------------------------------------------------

async def ejecutar_masivo(
    contribuyentes: List[Dict[str, str]],
    servicios: Optional[List[str]] = None,
    headless: bool = True,
    proxy_request: bool = False,
    pausa_segundos: float = 3.0,
) -> List[Dict[str, Any]]:
    print(f"Iniciando ejecución masiva: {len(contribuyentes)} contribuyente(s)")
    print(f"Headless: {headless} | Proxy: {proxy_request}")

    resumen: List[Dict[str, Any]] = []
    for idx, c in enumerate(contribuyentes, start=1):
        cuit = str(c.get("cuit", "")).strip()
        clave = str(c.get("clave", "")).strip()
        print(f"\n[{idx}/{len(contribuyentes)}] CUIT {cuit} ...")
        if not cuit or not clave:
            resumen.append({
                "cuit": cuit,
                "success": False,
                "estado": "datos_incompletos",
                "servicios": [],
                "errors": ["CUIT o clave incompletos"],
                "run_dir": "",
            })
            continue

        resultado = await bot_habilitar_servicio_logger(
            cuit=cuit,
            clave=clave,
            servicios=servicios,
            headless=headless,
            proxy_request=proxy_request,
        )
        estado_global = "ok" if resultado.get("success") else "fallido"
        servicios_res = resultado.get("servicios", [])
        estado_servicios = ", ".join(
            f"{s.get('servicio')}: {s.get('estado')}" for s in servicios_res
        ) or "sin_procesar"
        print(f"  -> {estado_global} | {estado_servicios}")
        if resultado.get("errors"):
            for err in resultado["errors"][:3]:
                print(f"     ! {err}")

        resumen.append({
            "cuit": cuit,
            "success": resultado.get("success", False),
            "estado": estado_servicios,
            "servicios": servicios_res,
            "errors": resultado.get("errors", []),
            "run_dir": resultado.get("run_dir", ""),
        })

        if pausa_segundos > 0 and idx < len(contribuyentes):
            await asyncio.sleep(pausa_segundos)

    _guardar_reporte_masivo(resumen)
    _imprimir_resumen_masivo(resumen)
    return resumen


def _guardar_reporte_masivo(resumen: List[Dict[str, Any]]) -> None:
    descargas = Path("./descargas")
    descargas.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filas = []
    for r in resumen:
        fila = {
            "cuit": r.get("cuit"),
            "success": r.get("success"),
            "estado": r.get("estado"),
            "errores": " | ".join(r.get("errors", [])),
            "run_dir": r.get("run_dir"),
        }
        for s in r.get("servicios", []):
            fila[f"{s.get('servicio')}_estado"] = s.get("estado")
            fila[f"{s.get('servicio')}_ok"] = s.get("ok")
        filas.append(fila)

    df = pd.DataFrame(filas)
    xlsx_path = descargas / f"habilitar_servicio_resumen_{timestamp}.xlsx"
    try:
        df.to_excel(xlsx_path, index=False, engine="openpyxl")
        print(f"\nReporte Excel: {xlsx_path}")
    except Exception as exc:
        print(f"No se pudo generar el reporte Excel: {exc}")

    csv_path = descargas / f"habilitar_servicio_resumen_{timestamp}.csv"
    try:
        df.to_csv(csv_path, index=False, sep=";")
        print(f"Reporte CSV: {csv_path}")
    except Exception as exc:
        print(f"No se pudo generar el reporte CSV: {exc}")


def _imprimir_resumen_masivo(resumen: List[Dict[str, Any]]) -> None:
    total = len(resumen)
    ok = sum(1 for r in resumen if r.get("success"))
    fallidos = total - ok
    print("\n" + "=" * 70)
    print("RESUMEN EJECUCIÓN MASIVA")
    print("=" * 70)
    print(f"Total: {total} | OK: {ok} | Fallidos: {fallidos}")
    for r in resumen:
        marca = "OK " if r.get("success") else "ERR"
        print(f"  [{marca}] {r.get('cuit')} -> {r.get('estado')}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cuit = os.getenv("HABILITAR_CUIT", "").strip()
    clave = os.getenv("HABILITAR_CLAVE", "").strip()
    headless = _env_flag("HABILITAR_HEADLESS", "false")
    proxy_request = _env_flag("HABILITAR_PROXY_REQUEST", "false")
    servicios_env = os.getenv("HABILITAR_SERVICIOS", "")
    servicios = [s.strip() for s in servicios_env.split(",") if s.strip()] or None

    if not cuit or not clave:
        print("Modo de uso:")
        print("  HABILITAR_CUIT=... HABILITAR_CLAVE=... .venv/bin/python habilitar_servicio_logger.py")
        print("  HABILITAR_HEADLESS=true  (opcional, default false)")
        print("  HABILITAR_PROXY_REQUEST=true  (opcional)")
        print("  HABILITAR_SERVICIOS=mis_comprobantes  (opcional, default mis_comprobantes)")
        return

    resultado = await bot_habilitar_servicio_logger(
        cuit=cuit,
        clave=clave,
        servicios=servicios,
        headless=headless,
        proxy_request=proxy_request,
    )
    print("Success:", resultado.get("success"))
    for s in resultado.get("servicios", []):
        print(f"  {s.get('servicio')}: {s.get('estado')}")
    if resultado.get("errors"):
        print("Errores:", resultado["errors"])


if __name__ == "__main__":
    asyncio.run(main())
