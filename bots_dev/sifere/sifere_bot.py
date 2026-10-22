import asyncio
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import Playwright, async_playwright

from shared.arca_login import start_arca_session
from shared.public_errors import safe_public_message
from shared.proxy import get_proxy_config

def _mask_value(value: str, keep_start: int = 2, keep_end: int = 2) -> str:
    if not value:
        return ""
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}{'*' * (len(value) - keep_start - keep_end)}{value[-keep_end:]}"

def extraer_tabla_html(html_content: str, table_id: str) -> pd.DataFrame:
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'id': table_id})
        
        if not table:
            return pd.DataFrame()
        
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
        
        rows_data = []
        tbody = table.find('tbody')
        if tbody:
            for row in tbody.find_all('tr'):
                cols = row.find_all('td')
                row_data = []
                for col in cols:
                    text = col.get_text(strip=True)
                    row_data.append(text)
                if row_data:
                    rows_data.append(row_data)
        
        if rows_data and headers:
            if len(headers) < len(rows_data[0]):
                headers = ['Acciones'] + headers
            elif len(headers) > len(rows_data[0]):
                headers = headers[:len(rows_data[0])]
            
            df = pd.DataFrame(rows_data, columns=headers)
            return df
        else:
            return pd.DataFrame()
            
    except Exception:
        return pd.DataFrame()

def _validar_periodo(periodo: str) -> Optional[str]:
    if not periodo:
        return "Periodo requerido."
    if len(periodo) != 6 or not periodo.isdigit():
        return "Periodo invalido. Use formato AAAAMM."
    mes = int(periodo[4:6])
    if mes < 1 or mes > 12:
        return "Periodo invalido. El mes debe estar entre 01 y 12."
    return None

async def _find_cuit_selector(page1) -> tuple[Optional[object], Optional[object]]:
    select_cuit = page1.locator("#selectedCuit")
    try:
        await select_cuit.wait_for(state="visible", timeout=5000)
        return page1, select_cuit
    except Exception:
        pass

    for frame in page1.frames:
        try:
            frame_sel = frame.locator("#selectedCuit")
            if await frame_sel.count() > 0:
                try:
                    await frame_sel.wait_for(state="visible", timeout=5000)
                except Exception:
                    pass
                return frame, frame_sel
        except Exception:
            continue

    return None, None

async def _representado_vigente(page1) -> Optional[str]:
    """CUIT que el encabezado de COMARB muestra después de "representando a"."""
    ambitos = [page1, *list(getattr(page1, "frames", []) or [])]
    for ambito in ambitos:
        try:
            texto = await ambito.locator("body").inner_text(timeout=3000)
        except Exception:
            continue
        match = re.search(r"representando\s+a\s+[^()]*\((\d{2}-?\d{8}-?\d)\)", texto or "", re.I)
        if match:
            return re.sub(r"\D", "", match.group(1))
    return None


async def _verificar_representado(page1, cuit_representado: str) -> None:
    """Falla si el encabezado de COMARB no muestra al representado pedido (con o sin selector)."""
    vigente = await _representado_vigente(page1)
    if vigente != re.sub(r"\D", "", cuit_representado):
        raise RuntimeError(f"No se seleccionó el representado {cuit_representado} en SIFERE (vigente: {vigente})")


async def _wait_ready(page, timeout_ms: int = 10000, idle_timeout_ms: int = 5000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, idle_timeout_ms))
    except Exception:
        pass


async def _first_visible(locator, max_items: int = 50):
    try:
        count = await locator.count()
    except Exception:
        return None
    for idx in range(min(count, max_items)):
        candidate = locator.nth(idx)
        try:
            if await candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


async def _wait_first_visible(locator, timeout_ms: int = 20000, max_items: int = 30):
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        candidate = await _first_visible(locator, max_items=max_items)
        if candidate is not None:
            return candidate
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.5)


# Matchea el título recortado del portal ("... SIFERE WEB - Cons...") y excluye "SIFERE WEB - DDJJ".
SIFERE_SERVICE_PATTERN = re.compile(r"CONVENIO\s+MULTILATERAL.*SIFERE\s+WEB(?!\s*(?:[-\u2013\u2014]\s*)?DD)", re.I)


async def _abrir_servicio_sifere(page, context, error_logs):
    """Abre SIFERE WEB clickeando la tarjeta del portal.

    El portal dispara un form POST con target=_blank (sin opener), por lo que
    no siempre es capturable como popup. Tras el click, la sesión COMARB queda
    establecida por el propio POST y se continúa sobre mainMenu.do.
    """
    ver_todos = page.get_by_role("link", name=re.compile(r"Ver\s+todos", re.I))
    ver_todos_el = await _wait_first_visible(ver_todos, timeout_ms=10000)
    if ver_todos_el is not None:
        try:
            await ver_todos_el.click(timeout=8000)
            await _wait_ready(page)
        except Exception:
            pass

    card = page.locator("a[role='button']").filter(has_text=SIFERE_SERVICE_PATTERN)
    card_el = await _wait_first_visible(card, timeout_ms=15000, max_items=20)
    if card_el is None:
        card = page.locator("a").filter(has_text=SIFERE_SERVICE_PATTERN)
        card_el = await _wait_first_visible(card, timeout_ms=15000, max_items=20)
    if card_el is None:
        error_logs.append("No se encuentra habilitado el servicio de SIFERE WEB.")
        return None

    try:
        await card_el.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    # Ruta COMARB http->https: se instala tras ubicar la tarjeta y antes del click, para no alterar la carga del catálogo del portal.
    async def _upgrade_comarb_route(route):
        url = route.request.url
        if url.startswith("http://app1.comarb.gob.ar"):
            # COMARB redirige a http:// y en algunos entornos el puerto 80 no
            # es alcanzable; se fuerza https:// para que las páginas carguen.
            await route.continue_(url="https://" + url[len("http://"):])
        else:
            await route.continue_()

    try:
        await context.route("**://app1.comarb.gob.ar/**", _upgrade_comarb_route)
    except Exception:
        pass

    pages_before = set(context.pages)
    clicked = False
    try:
        await card_el.click(timeout=8000, no_wait_after=True)
        clicked = True
    except Exception:
        try:
            handle = await card_el.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                clicked = True
        except Exception:
            pass

    if not clicked:
        error_logs.append("No se pudo abrir el servicio SIFERE WEB.")
        return None

    await asyncio.sleep(3)

    for candidate in [p for p in context.pages if p not in pages_before]:
        try:
            if candidate.is_closed():
                continue
            await _wait_ready(candidate, timeout_ms=8000, idle_timeout_ms=3000)
            if await candidate.locator("#selectedCuit").count() > 0:
                return candidate
            if await _representado_vigente(candidate):
                return candidate
        except Exception:
            continue

    # El popup del portal no fue capturable: la sesión COMARB ya quedó
    # establecida por el POST disparado desde el click; seguir en esta página.
    main_menu_url = "https://app1.comarb.gob.ar/siferewebconsultas/mainMenu.do"
    for _ in range(3):
        try:
            await page.goto(main_menu_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await asyncio.sleep(2)
            continue
        await asyncio.sleep(1)
        if await page.locator("#selectedCuit").count() > 0:
            return page
        if await _representado_vigente(page):
            return page
        await asyncio.sleep(2)

    error_logs.append("No se pudo abrir el servicio SIFERE WEB.")
    return None


async def sifere_consultas(
    playwright: Playwright,
    cuit_representado: str,
    cuit_representante: str,
    representado: str,
    clave: str,
    periodo: str,
    output_dir: str,
    headless: bool = False,
    proxy: Optional[dict] = None,
    jurisdicciones: Optional[list[int]] = None,
    ) -> tuple[list[str], list[str], bool]:
    saved_files: list[str] = []
    error_logs: list[str] = []
    success = True
    
    periodo_ano = periodo[:4]
    periodo_mes = periodo[-2:]
    fin_cuit = cuit_representado[-1:]

    if jurisdicciones is None:
        jurisdicciones = list(range(901, 925))
        
    jurisdicciones = [j for j in jurisdicciones if 901 <= j <= 924]

    os.makedirs(output_dir, exist_ok=True)

    browser = None
    context = None

    try:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]

        # Try to use Desktop Chrome device emulation to bypass WAF
        device = None
        try:
            device = playwright.devices["Desktop Chrome"]
        except Exception:
            device = None

        context_kwargs = {
            "locale": "es-AR",
            "timezone_id": "America/Argentina/Buenos_Aires",
            "accept_downloads": True,
            "extra_http_headers": {"Accept-Language": "es-AR,es;q=0.9"},
        }
        if device:
            context_kwargs.update(device)
        
        arca_session = await start_arca_session(
            playwright,
            cuit_representante=cuit_representante,
            clave_representante=clave,
            error_logs=error_logs,
            headless=headless,
            proxy=proxy,
            launch_args=launch_args,
            context_kwargs=context_kwargs,
        )
        if arca_session is None:
            return saved_files, error_logs, False

        browser = arca_session.browser
        context = arca_session.context
        page = arca_session.page

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page1 = await _abrir_servicio_sifere(page, context, error_logs)
        if page1 is None:
            return saved_files, error_logs, False
        await _wait_ready(page1)

        try:
            if "entryPoint.login" in page1.url:
                try:
                    form_count = await page1.locator("form").count()
                    if form_count > 0:
                        await page1.evaluate("document.forms[0].submit()")
                        await _wait_ready(page1)
                except Exception:
                    pass
                for label in ("Ingresar", "Continuar", "Aceptar"):
                    try:
                        btn = page1.get_by_role("button", name=label)
                        if await btn.count() > 0:
                            await btn.click(timeout=5000)
                            await _wait_ready(page1)
                            break
                    except Exception:
                        pass

            await _wait_ready(page1)
            target_frame, select_cuit = await _find_cuit_selector(page1)
            if select_cuit is not None:
                await select_cuit.select_option(cuit_representado)
                action_target = target_frame if target_frame is not None else page1
                await action_target.get_by_role("button", name="Seleccionar").click(timeout=8000, no_wait_after=True)
                await asyncio.sleep(3)
                await _wait_ready(page1, timeout_ms=8000)
            # Sin selector (un único CUIT) COMARB ya entra "representando a" ese CUIT; en ambos casos se verifica.
            await _verificar_representado(page1, cuit_representado)
        except Exception as exc:
            error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))
            return saved_files, error_logs, False  # sin representado correcto no se consulta

        # ==================== SIRCREB ====================
        # SIRCREB: detalleMensual muestra el "Mes/Año" del mes siguiente al parámetro.
        mes_sircreb = (int(periodo_mes) - 2) % 12 + 1
        anio_sircreb = int(periodo_ano) - (1 if periodo_mes == "01" else 0)
        await page1.goto(
            "https://app1.comarb.gob.ar/siferewebconsultas/srbPadron.do"
            f"?method=detalleMensual&anio={anio_sircreb}&mes={mes_sircreb:02d}"
        )
        await _wait_ready(page1)
        try:
            await page1.wait_for_selector("text=Retenciones y Coeficientes SIRCREB", timeout=15000)
        except Exception:
            pass

        html_content = await page1.content()
        df_retenciones = extraer_tabla_html(html_content, "retencion")
        df_coeficientes = extraer_tabla_html(html_content, "coef")

        if not df_retenciones.empty or not df_coeficientes.empty:
            excel_filename = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRCREB - Tablas - {periodo} - {representado}.xlsx"
            excel_path = os.path.join(output_dir, excel_filename)

            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                if not df_retenciones.empty:
                    df_retenciones.to_excel(writer, sheet_name="Retenciones", index=False)
                if not df_coeficientes.empty:
                    df_coeficientes.to_excel(writer, sheet_name="Coeficientes", index=False)
            saved_files.append(excel_path)

        base_sifere = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRCREB - {periodo} - {representado}"

        try:
            async with page1.expect_download(timeout=30000) as download_info:
                await page1.get_by_role("link", name="Retenciones y coeficientes").click()
            download = await download_info.value
            ext = os.path.splitext(download.suggested_filename or "")[1] or ""
            file_path = os.path.join(output_dir, f"{base_sifere}{ext}")
            await download.save_as(file_path)
            saved_files.append(file_path)
        except Exception as exc:
            error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))

        try:
            async with page1.expect_download(timeout=30000) as download1_info:
                await page1.get_by_role("link", name="Exportar archivo con").nth(1).click()
            download1 = await download1_info.value
            ext1 = os.path.splitext(download1.suggested_filename or "")[1] or ""
            file_path1 = os.path.join(output_dir, f"{base_sifere}{ext1}")
            await download1.save_as(file_path1)
            saved_files.append(file_path1)
        except Exception as exc:
            error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))

        # ==================== SIRTAC ====================
        await page1.goto(f"https://app1.comarb.gob.ar/siferewebconsultas/srtPadron.do?method=retenciones&cuit={cuit_representado}&periodo={periodo}")
        await _wait_ready(page1)

        html_content_sirtac = await page1.content()
        df_jurisdicciones = extraer_tabla_html(html_content_sirtac, "inclusion")
        df_operaciones = extraer_tabla_html(html_content_sirtac, "retencion")

        if not df_jurisdicciones.empty or not df_operaciones.empty:
            excel_filename_sirtac = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRTAC - Tablas - {periodo} - {representado}.xlsx"
            excel_path_sirtac = os.path.join(output_dir, excel_filename_sirtac)

            with pd.ExcelWriter(excel_path_sirtac, engine="openpyxl") as writer:
                if not df_jurisdicciones.empty:
                    df_jurisdicciones.to_excel(writer, sheet_name="Distribución Jurisdicciones", index=False)
                if not df_operaciones.empty:
                    df_operaciones.to_excel(writer, sheet_name="Operaciones Informadas", index=False)
            saved_files.append(excel_path_sirtac)

        base_sirtac_jurisdicciones = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRTAC - jurisdicciones - {periodo} - {representado}"
        base_sirtac_operaciones = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRTAC - operaciones - {periodo} - {representado}"

        # El sitio nuevo sirve los exports de SIRTAC inline (sin descarga directa),
        # por lo que los archivos separados se generan desde las tablas HTML.
        try:
            if not df_jurisdicciones.empty:
                file_path2 = os.path.join(output_dir, f"{base_sirtac_jurisdicciones}.xlsx")
                df_jurisdicciones.to_excel(file_path2, sheet_name="Distribución Jurisdicciones", index=False)
                saved_files.append(file_path2)
        except Exception as exc:
            error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))

        try:
            if not df_operaciones.empty:
                file_path3 = os.path.join(output_dir, f"{base_sirtac_operaciones}.xlsx")
                df_operaciones.to_excel(file_path3, sheet_name="Operaciones Informadas", index=False)
                saved_files.append(file_path3)
        except Exception as exc:
            error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))

        # ==================== SIRCAR Retenciones y Percepciones ====================
        # SIRCAR
        for method in ["retencion", "percepcion"]:
            entrada_url = f"https://app1.comarb.gob.ar/siferewebconsultas/srcRetencion.do?method={method}In"
            abierto = False
            try:
                entrada_link = page1.locator(f"a[href='srcRetencion.do?method={method}In']").first
                if await entrada_link.count() > 0 and await entrada_link.is_visible():
                    await entrada_link.click(timeout=5000, no_wait_after=True)
                    abierto = True
            except Exception:
                pass
            if not abierto:
                await page1.goto(entrada_url, wait_until="domcontentloaded", timeout=30000)
            await _wait_ready(page1)

            label_method = "Retenciones" if method == "retencion" else "Percepciones"

            for jurisdiccion in jurisdicciones:
                await page1.goto(
                    f"https://app1.comarb.gob.ar/siferewebconsultas/srcRetencion.do?method={method}&juris={jurisdiccion}&cuitAgente=&mesDesde={periodo_mes}&anioDesde={periodo_ano}&mesHasta={periodo_mes}&anioHasta={periodo_ano}"
                )
                await _wait_ready(page1)

                base_sircar = f"{fin_cuit} - {cuit_representado} - SIFERE - SIRCAR - {label_method} - {jurisdiccion} - {periodo} - {representado}"

                try:
                    async with page1.expect_download(timeout=10000) as download4_info:
                        await page1.get_by_role("link", name="Excel").click()
                    download4 = await download4_info.value
                    ext4 = os.path.splitext(download4.suggested_filename or "")[1] or ""
                    file_path4 = os.path.join(output_dir, f"{base_sircar}{ext4}")
                    await download4.save_as(file_path4)
                    saved_files.append(file_path4)
                except Exception:
                    pass
                
                try:
                    async with page1.expect_download(timeout=10000) as download5_info:
                        await page1.get_by_role("link", name="PDF").click()
                    download5 = await download5_info.value
                    ext5 = os.path.splitext(download5.suggested_filename or "")[1] or ""
                    file_path5 = os.path.join(output_dir, f"{base_sircar}{ext5}")
                    await download5.save_as(file_path5)
                    saved_files.append(file_path5)
                except Exception:
                    pass

    except Exception as exc:
        error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))
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

    return saved_files, error_logs, success


async def bot_sifere(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    periodo: str,
    representado: Optional[str] = None,
    proxy_request: bool = False,
    jurisdicciones: Optional[list[int]] = None,
) -> tuple[dict[str, object], list[str], bool]:
    results: dict[str, object] = {"archivos": []}
    error_logs: list[str] = []
    success = False

    if not all([cuit_representante, clave_representante, cuit_representado, periodo]):
        error_logs.append("Faltan parametros requeridos para la consulta SIFERE.")
        return results, error_logs, False

    periodo_error = _validar_periodo(periodo)
    if periodo_error:
        error_logs.append(periodo_error)
        return results, error_logs, False

    representado_value = (representado or cuit_representado or "").strip()
    if not representado_value:
        representado_value = cuit_representado

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    proxy = get_proxy_config() if proxy_request else None

    # Common output dir
    base_dir_root = Path("./descargas/sifere")
    safe_rep = re.sub(r"[^0-9]", "", cuit_representado)
    output_dir = base_dir_root / safe_rep / f"{periodo}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as playwright:
            archivo_paths, errores_bot, bot_success = await sifere_consultas(
                playwright=playwright,
                cuit_representado=cuit_representado,
                cuit_representante=cuit_representante,
                representado=representado_value,
                clave=clave_representante,
                periodo=periodo,
                output_dir=str(output_dir),
                headless=True,
                proxy=proxy,
                jurisdicciones=jurisdicciones,
            )
            success = bot_success
            error_logs.extend(errores_bot)
            
            archivo_nombres = [
                os.path.basename(path)
                for path in archivo_paths
                if os.path.basename(path)
            ]
            results["archivos"] = archivo_nombres
            results["output_dir"] = str(output_dir)

    except Exception as exc:
        error_logs.append(safe_public_message(exc, context="query", stage="la consulta"))
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("SIFERE_CUIT_REPRESENTANTE", "")
    clave = os.getenv("SIFERE_CLAVE_REPRESENTANTE", "")
    cuit_rep = os.getenv("SIFERE_CUIT_REPRESENTADO", "")
    periodo = os.getenv("SIFERE_PERIODO", "202401")
    
    if cuit and clave and cuit_rep:
        res = await bot_sifere(cuit, clave, cuit_rep, periodo)
        print("Success:", res[2])
        print("Archivos:", res[0]["archivos"])

if __name__ == "__main__":
    asyncio.run(main())
