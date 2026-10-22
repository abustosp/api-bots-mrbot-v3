import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

from certificacion_primaria_granos.certificacion_primaria_granos_bot import (
    LOGIN_URL,
    SERVICE_NAME,
    SERVICE_NAME_PATTERN,
    safe_filename,
    seleccionar_denominacion,
    preparar_menu_para_certificados,
    click_menu_option_flexible,
    parse_table_records,
    records_to_dataframe,
    parse_certificados_descarga_records,
    download_certificado_direct,
    download_from_link,
    obtener_tipos_consulta_certificados,
)

BS4_SELECTORES_CERTIFICADOS = ("a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]",)


def _safe_step_name(step: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_") or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "certificacion_primaria_granos") -> None:
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
        handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        self.logger.propagate = False
        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page, step: str) -> None:
        if page is None:
            return
        self.step += 1
        prefix = f"{self.step:03d}_{_safe_step_name(step)}"
        self.logger.info("step=%s name=%s url=%s", self.step, step, getattr(page, "url", ""))
        try:
            await page.screenshot(path=str(self.screenshots_dir / f"{prefix}.png"), full_page=True)
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)
        try:
            (self.html_dir / f"{prefix}.html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            self.logger.exception("html_failed step=%s", step)


async def capture_table_and_save_logger(
    page: Page, tipo: str, consulta: str, download_dir: Path, debug: DebugCapture
) -> Path:
    await debug.capture(page, f"tabla_{tipo}_{consulta}")
    headers, records = parse_table_records(
        await page.content(), bs4_download_selectors=BS4_SELECTORES_CERTIFICADOS
    )
    debug.log(f"Tabla {tipo} {consulta}: {len(records)} registros encontrados")
    base = safe_filename(f"tabla_{tipo}_{consulta}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    download_dir.mkdir(parents=True, exist_ok=True)
    if records:
        salida = download_dir / f"{base}.xlsx"
        records_to_dataframe(headers, records).to_excel(salida, index=False)
    else:
        salida = download_dir / f"{base}.txt"
        salida.write_text("Sin registros de comprobantes", encoding="utf-8")
    return salida


TIPO_CONSULTA_PATTERN = re.compile(r"como\s+depositante", re.I)


async def seleccionar_tipo_y_consultar(page: Page, desde: str, hasta: str, debug: DebugCapture) -> Tuple[str, str]:
    tipos = await obtener_tipos_consulta_certificados(page)
    debug.log(f"Opciones de Tipo De Consulta: {tipos}")
    elegido = next((t for t in tipos if TIPO_CONSULTA_PATTERN.search(t[1])), None)
    if elegido is None:
        raise RuntimeError(f"No existe el tipo de consulta 'Certificados como depositante' entre {tipos}")
    valor, texto = elegido
    await page.locator("#tipoConsulta").first.select_option(valor)
    await page.locator("#fechaDesde").first.fill(desde)
    await page.locator("#fechaHasta").first.fill(hasta)
    await debug.capture(page, "formulario_completo")
    await page.get_by_role("button", name=re.compile(r"Consultar", re.I)).first.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    await debug.capture(page, "resultado_consulta")
    return valor, texto


async def descargar_certificados(
    page: Page, cuit_representante: str, tipo_valor: str, download_dir: Path, debug: DebugCapture
) -> List[dict]:
    await capture_table_and_save_logger(page, "CD", f"deposito_{tipo_valor}", download_dir, debug)
    certificados = parse_certificados_descarga_records(await page.content())
    debug.log(f"Certificados encontrados: {len(certificados)}")
    descargados: List[dict] = []
    for idx, cert in enumerate(certificados, start=1):
        target = download_dir / safe_filename(
            f"CPG - {cuit_representante} - {cert.tipo_certificado} - {cert.coe}.pdf"
        )
        label = f"CPG_{idx}_{cert.tipo_certificado}_{cert.cee_codigo}_{cert.coe}"
        saved = await download_certificado_direct(page, cert, target, label=label)
        if saved is None:
            link = page.locator(
                "a.imprimir"
                f"[data-tipocertificado='{cert.tipo_certificado}']"
                f"[data-liqcodigo='{cert.cee_codigo}']"
                f"[data-coe='{cert.coe}']"
            )
            if await link.count() > 0:
                saved = await download_from_link(page, link.first, target, label=f"{label}_click")
        debug.log(f"{'OK' if saved else 'FALLO'} certificado {cert.coe}")
        if saved:
            descargados.append({
                "tipo_certificado": cert.tipo_certificado,
                "cee_codigo": cert.cee_codigo,
                "coe": cert.coe,
                "archivo": saved.name,
                "local_path": str(saved),
            })
    return descargados


async def bot_certificacion_primaria_granos(
    cuit_representante: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    proxy_request: bool = False,
    headless: bool = True,
    **kwargs,
) -> Tuple[List[dict], List[str], bool]:
    error_logs: List[str] = []
    results: List[dict] = []
    debug = DebugCapture(prefix="certificacion_primaria_granos")
    debug.log("Iniciando bot Certificacion Primaria de Granos (logger)")
    proxy = get_proxy_config() if proxy_request else None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, proxy=proxy)
        context = await browser.new_context(
            accept_downloads=True,
            record_har_path=debug.har_dir / "certificacion_primaria_granos.har",
            record_har_content="embed",
        )
        page = await context.new_page()
        try:
            await debug.capture(page, "inicio")
            login = await login_arca(page, cuit_representante, clave, error_logs=error_logs, login_url=LOGIN_URL)
            await debug.capture(page, "login_completo")
            if not login.success:
                debug.log(f"Login fallo: {error_logs}")
                return results, error_logs, False

            service_page = await open_arca_service(
                page, service_name=SERVICE_NAME, service_matchers=(SERVICE_NAME_PATTERN,)
            )
            if not service_page:
                error_logs.append(f"No se encuentra habilitado el servicio de {SERVICE_NAME}")
                return results, error_logs, False
            await debug.capture(service_page, "servicio_abierto")

            await seleccionar_denominacion(service_page, denominacion)
            await debug.capture(service_page, "denominacion_seleccionada")

            await preparar_menu_para_certificados(service_page)
            await click_menu_option_flexible(
                service_page, re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I), "certificacion_electronica_menu"
            )
            await debug.capture(service_page, "menu_certificacion")
            await click_menu_option_flexible(
                service_page, re.compile(r"Consulta\s+de\s+Certificados\s+de", re.I), "consulta_certificados_menu"
            )
            await debug.capture(service_page, "form_consulta")

            valor, texto = await seleccionar_tipo_y_consultar(service_page, desde, hasta, debug)
            debug.log(f"Tipo consulta usado: {valor} / {texto}")
            results = await descargar_certificados(
                service_page, cuit_representante, valor, debug.descargas_dir, debug
            )
            (debug.descargas_dir / "resultado.json").write_text(
                json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return results, error_logs, True
        except Exception as exc:
            debug.logger.exception("error")
            error_logs.append(str(exc))
            await debug.capture(page, "error")
            return results, error_logs, False
        finally:
            await context.close()
            await browser.close()


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = "CERTIFICACION_PRIMARIA_GRANOS_"
    cuit = os.getenv(p + "CUIT_REPRESENTANTE", "")
    clave = os.getenv(p + "CLAVE", "")
    if not (cuit and clave):
        raise SystemExit("Faltan CERTIFICACION_PRIMARIA_GRANOS_CUIT_REPRESENTANTE / _CLAVE")
    res = await bot_certificacion_primaria_granos(
        cuit, clave,
        os.getenv(p + "DENOMINACION", ""),
        os.getenv(p + "DESDE", "01/01/2025"),
        os.getenv(p + "HASTA", "31/12/2025"),
        headless=os.getenv(p + "HEADLESS", "true").lower() == "true",
    )
    print(res)


if __name__ == "__main__":
    asyncio.run(main())
