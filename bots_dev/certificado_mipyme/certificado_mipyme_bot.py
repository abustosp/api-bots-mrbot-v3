import asyncio
import os
import re
from pathlib import Path

from playwright.async_api import Page, async_playwright

from shared.arca_login import start_arca_session, open_arca_service


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


async def _select_representado(page: Page, cuit_representado: str) -> bool:
    opciones_encontradas: list[str] = []
    try:
        combobox = page.get_by_role("combobox", name="Ingrese al menos 3 caracteres")
        await combobox.wait_for(state="visible", timeout=10000)
        await combobox.click()

        await _try_click_expand_combobox(page)
        await page.wait_for_timeout(1500)

        options = page.locator('[role="option"], li[role="option"], .MuiAutocomplete-option')
        option_found: bool = False
        for _ in range(5):
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

        await combobox.fill(cuit_representado)
        await page.wait_for_timeout(1500)

        for _ in range(5):
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

    except Exception:
        return False
    finally:
        print("Opciones encontradas:", opciones_encontradas)


async def bot_certificado_mipyme(
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    headless: bool = True,
) -> tuple[dict, list[str], bool]:
    results: dict = {"archivos": []}
    error_logs: list[str] = []
    success = False
    browser = None
    context = None

    if not all([cuit_representante, clave, cuit_representado]):
        error_logs.append("Faltan parametros requeridos: CUIT representante, clave y CUIT representado.")
        return results, error_logs, False

    output_dir = Path("descargas")
    output_dir.mkdir(parents=True, exist_ok=True)

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
            }

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
                return results, error_logs, False

            browser = arca_session.browser
            context = arca_session.context
            page = arca_session.page

            page1 = await open_arca_service(
                page,
                service_name="LUFE",
                service_matchers=(re.compile(r"LUFE", re.I),),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de LUFE",
            )
            if page1 is None:
                return results, error_logs, False
            if page1 is not page and not page.is_closed():
                await page.close()

            representado_ok = await _select_representado(page1, cuit_representado)
            if not representado_ok:
                error_logs.append("No se pudo seleccionar el representado en el combo")
                return results, error_logs, False

            try:
                seleccionar_btn = page1.get_by_role("button", name="Seleccionar")
                await seleccionar_btn.wait_for(state="visible", timeout=5000)
                await seleccionar_btn.click()
            except Exception:
                pass

            await page1.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            page_content = await page1.content()
            if "No se encuentra habilitado" in page_content:
                error_logs.append("No se encuentra habilitado el servicio de Certificado MiPyME")
                return results, error_logs, False

            download_link = page1.get_by_role("link", name=re.compile(r"Descargar Certificado MiPyME", re.I))
            await download_link.wait_for(state="visible", timeout=15000)

            async with page1.expect_download(timeout=30000) as download_info:
                await download_link.click()
            download = await download_info.value

            filename = download.suggested_filename or f"certificado_mipyme_{cuit_representado}.pdf"
            file_path = output_dir / filename
            await download.save_as(file_path)

            results["archivos"] = [filename]
            results["output_dir"] = str(output_dir)
            success = True

    except Exception as exc:
        error_logs.append(f"Error en bot Certificado MiPyME: {exc}")
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

    cuit_representante = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTANTE", "")
    clave = os.getenv("CERTIFICADO_MIPYME_CLAVE", "")
    cuit_representado = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTADO", "")
    headless_str = os.getenv("CERTIFICADO_MIPYME_HEADLESS", "False")
    headless = headless_str.lower() == "true"

    if not all([cuit_representante, clave, cuit_representado]):
        print("ERROR: Faltan variables de entorno. Verificar .env")
        return

    results, errors, ok = await bot_certificado_mipyme(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        headless=headless,
    )

    print(f"\n{'='*40}")
    print(f"Resultado: {'OK' if ok else 'ERROR'}")
    print(f"Archivos: {results.get('archivos', [])}")
    if errors:
        print(f"Errores:")
        for e in errors:
            print(f"  - {e}")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())
