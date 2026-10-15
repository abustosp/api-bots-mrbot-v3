import asyncio
import os
import re
import time
from pathlib import Path


from playwright.async_api import Page, async_playwright

from shared.arca_login import login_arca, open_arca_service

BOT_NAME = "controladores_fiscales"
_DECLARATION_TEXT = "Declaro que los datos a transmitir son correctos y completos"
_EXISTING_CONSTANCIA_TITLE = "Exportar detalle en archivo PDF"
_NEW_CONSTANCIA_BUTTON = "Descargar Constancia"
_MAX_FILE_ATTEMPTS = 3


async def _all_pem_files(archivos_dir: str, prefix: str) -> list[str]:
    archivos = sorted(Path(archivos_dir).glob(f"{prefix}.*.pem"))
    return [str(a) for a in archivos]


async def _upload_file(page: Page, file_path: str) -> None:
    await page.get_by_role("button", name="Buscá en tus archivos").wait_for(state="visible", timeout=10_000)
    file_input = page.locator("input[type='file']")
    await file_input.wait_for(state="attached", timeout=10_000)
    await file_input.set_input_files(file_path)
    await page.get_by_text(Path(file_path).name, exact=True).wait_for(state="visible", timeout=10_000)


async def _dismiss_modal(page: Page) -> bool:
    for btn_name in ("Entendido", "Aceptar", "Cancelar", "No", "Si", "Cerrar"):
        btn = page.get_by_role("button", name=btn_name)
        if await btn.count() > 0:
            try:
                if await btn.first.is_visible(timeout=500):
                    await btn.first.click()
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
    return False


async def _visible_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""


def _error_status_from_text(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not any("Se ha producido un error" in line for line in lines):
        return None

    detail = next((line for line in lines if line.startswith("* Error")), "Se ha producido un error")
    code = ""
    for index, line in enumerate(lines):
        if line.lower() == "código error" and index + 1 < len(lines):
            code = lines[index + 1]
            break
    if code:
        detail = f"{detail}; código {code}"
    return f"error|{detail}"


async def _click_button_if_visible(page: Page, name: str | re.Pattern[str], timeout_ms: int = 1_000) -> bool:
    button = page.get_by_role("button", name=name)
    if await button.count() == 0:
        return False
    try:
        await button.first.wait_for(state="visible", timeout=timeout_ms)
        await button.first.click()
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False


async def _ensure_upload_screen(page: Page) -> None:
    upload_button = page.get_by_role("button", name="Buscá en tus archivos")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            await upload_button.wait_for(state="visible", timeout=1_000)
            return
        except Exception:
            pass

        if await _click_button_if_visible(page, "Volver", timeout_ms=1_000):
            continue

        await page.goto("https://seti.afip.gob.ar/setiweb/#/presentacion/presentacion")
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)

    await page.goto("https://seti.afip.gob.ar/setiweb/#/presentacion/presentacion")
    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    await upload_button.wait_for(state="visible", timeout=15_000)


async def _submission_status_from_page(page: Page) -> str | None:
    text = await _visible_text(page)
    error_status = _error_status_from_text(text)
    if error_status is not None:
        return error_status

    text_lower = text.lower()
    if "ya ha sido presentada" in text_lower or "ya fue presentada" in text_lower:
        return "ya_presentado"
    if "presentada correctamente" in text_lower or "presentación realizada" in text_lower:
        return "presentado_correctamente"
    if "transacción" in text_lower and "ya ha sido" not in text_lower:
        return "presentado_correctamente"

    modal = page.locator(".modal.show, .modal:visible").last
    try:
        if await modal.count() > 0 and await modal.is_visible(timeout=300):
            modal_text = (await modal.inner_text(timeout=1_000)).strip()
            modal_text_lower = modal_text.lower()
            if "ya ha sido" in modal_text_lower or "ya fue presentada" in modal_text_lower:
                return "ya_presentado"
            if "correctamente" in modal_text_lower or "realizada" in modal_text_lower:
                await _dismiss_modal(page)
                return "presentado_correctamente"
            await _dismiss_modal(page)
    except Exception:
        pass

    return None


async def _wait_submission_result(page: Page) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = await _submission_status_from_page(page)
        if status is not None:
            return status
        await asyncio.sleep(0.5)

    return "timeout"


async def _wait_after_continue(page: Page) -> str:
    declaration_label = page.locator("label", has_text=_DECLARATION_TEXT).first
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = await _submission_status_from_page(page)
        if status is not None:
            return status

        try:
            if await declaration_label.count() > 0 and await declaration_label.is_visible(timeout=300):
                return "ready_to_present"
        except Exception:
            pass

        await asyncio.sleep(0.5)

    return "timeout"


def _constancia_path(descargas_dir: Path, file_path: str) -> Path:
    return descargas_dir / f"{Path(file_path).stem}_constancia.pdf"


async def _save_pdf_download(page: Page, trigger, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    async with page.expect_download(timeout=30_000) as download_info:
        await trigger.click()
    download = await download_info.value
    await download.save_as(str(destination))


async def _download_existing_constancia(page: Page, destination: Path) -> None:
    pdf_link = page.locator(f'a[title="{_EXISTING_CONSTANCIA_TITLE}"]').first
    await pdf_link.wait_for(state="visible", timeout=15_000)
    await _save_pdf_download(page, pdf_link, destination)


async def _download_new_constancia(page: Page, destination: Path) -> None:
    constancia_button = page.get_by_role("button", name=_NEW_CONSTANCIA_BUTTON).first
    await constancia_button.wait_for(state="visible", timeout=15_000)
    await _save_pdf_download(page, constancia_button, destination)


async def _download_constancia_for_status(page: Page, status: str, file_path: str, descargas_dir: Path) -> str:
    destination = _constancia_path(descargas_dir, file_path)
    try:
        if status == "ya_presentado":
            await _download_existing_constancia(page, destination)
        elif status == "presentado_correctamente":
            await _download_new_constancia(page, destination)
    except Exception as exc:
        return f"error_descarga|No se pudo descargar constancia PDF: {exc}"

    return status


async def _accept_declaration_and_present(page: Page) -> None:
    declaration_label = page.locator("label", has_text=_DECLARATION_TEXT).first
    await declaration_label.wait_for(state="visible", timeout=15_000)

    checkbox = declaration_label.locator("input[type='checkbox']").first
    await checkbox.wait_for(state="attached", timeout=15_000)
    if not await checkbox.is_checked():
        await checkbox.check(force=True)

    presentar = page.get_by_role("button", name="Presentar")
    await presentar.wait_for(state="visible", timeout=15_000)
    await presentar.click()


async def _submit_current_file(page: Page, file_path: str, descargas_dir: Path) -> str:
    continuar = page.get_by_role("button", name="Continuar")
    await continuar.wait_for(state="visible", timeout=15_000)
    await continuar.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass

    status = await _wait_after_continue(page)
    if status != "ready_to_present":
        return await _download_constancia_for_status(page, status, file_path, descargas_dir)

    await _accept_declaration_and_present(page)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass

    status = await _wait_submission_result(page)
    return await _download_constancia_for_status(page, status, file_path, descargas_dir)


def _is_retryable_status(status: str) -> bool:
    return status == "timeout" or status.startswith("error")


async def _present_file_with_retries(page: Page, file_path: str, descargas_dir: Path) -> str:
    last_status: str | None = None
    last_error: Exception | None = None

    for attempt in range(1, _MAX_FILE_ATTEMPTS + 1):
        try:
            await _ensure_upload_screen(page)
            await _upload_file(page, file_path)
            status = await _submit_current_file(page, file_path, descargas_dir)
            if not _is_retryable_status(status):
                return status
            last_status = status
        except Exception as exc:
            last_error = exc

        if attempt < _MAX_FILE_ATTEMPTS:
            try:
                await _ensure_upload_screen(page)
            except Exception:
                pass

    if last_status is not None:
        return last_status
    return f"error|No se pudo completar presentacion tras {_MAX_FILE_ATTEMPTS} intentos: {last_error}"


def _format_result(file_path: str, status: str) -> str:
    file_name = Path(file_path).name
    if status == "ya_presentado":
        return f"{file_name}: ya habia sido presentado anteriormente"
    if status == "presentado_correctamente":
        return f"{file_name}: presentado correctamente"
    if status.startswith("error_descarga|"):
        return f"{file_name}: error en descarga de constancia - {status.split('|', 1)[1]}"
    if status.startswith("error|"):
        return f"{file_name}: error en presentacion - {status.split('|', 1)[1]}"
    return f"{file_name}: sin respuesta del sistema (timeout)"


async def bot_controladores_fiscales(
    cuit_representante: str,
    clave_representante: str,
    archivos_dir: str,
    headless: bool = True,
) -> tuple[list[str], list[str], bool]:
    resultados: list[str] = []
    error_logs: list[str] = []
    success = True

    browser = None
    context = None
    descargas_dir = Path("descargas")
    descargas_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            login_errors: list[str] = []
            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=login_errors,
                fail_on_captcha=True,
                click_ver_todos=False,
            )
            for line in login_errors:
                error_logs.append(line)
            if login_result.captcha_detected:
                raise RuntimeError("Captcha detectado")
            if login_result.password_change_required:
                raise RuntimeError("Se requiere cambiar la clave fiscal")
            if not login_result.success:
                raise RuntimeError(error_logs[-1] if error_logs else "No se pudo iniciar sesión en ARCA")

            service_page = await open_arca_service(
                page,
                service_name="PRESENTACIÓN DDJJ Y PAGOS -",
                service_matchers=(
                    re.compile(r"PRESENTACI[OÓ]N DDJJ Y PAGOS", re.I),
                    "PRESENTACIÓN DDJJ Y PAGOS -",
                ),
                error_logs=error_logs,
                click_ver_todos=True,
                service_unavailable_message="No se encuentra habilitado el servicio de Presentación DDJJ y Pagos",
                popup_timeout_ms=20_000,
            )
            if service_page is None:
                raise RuntimeError(error_logs[-1] if error_logs else "No se pudo abrir el servicio")

            await service_page.wait_for_load_state("domcontentloaded", timeout=15_000)
            await service_page.wait_for_timeout(3_000)
            if "auth.afip" in (service_page.url or "") or "Unauthorized" in await service_page.content():
                error_logs.append("Service page 401, retrying")
                service_page = await open_arca_service(
                    page,
                    service_name="PRESENTACIÓN DDJJ Y PAGOS -",
                    service_matchers=(
                        re.compile(r"PRESENTACI[OÓ]N DDJJ Y PAGOS", re.I),
                        "PRESENTACIÓN DDJJ Y PAGOS -",
                    ),
                    error_logs=error_logs,
                    click_ver_todos=True,
                    service_unavailable_message="No se encuentra habilitado el servicio de Presentación DDJJ y Pagos",
                    popup_timeout_ms=20_000,
                )
                if service_page is None:
                    raise RuntimeError("No se pudo abrir el servicio en el reintento")
                await service_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                await service_page.wait_for_timeout(3_000)

            if service_page is not page and not page.is_closed():
                await page.close()
            if "seti.afip.gob.ar" not in (service_page.url or ""):
                raise RuntimeError(f"URL del servicio inesperada: {service_page.url}")

            f8010_files = await _all_pem_files(archivos_dir, "F8010")
            f8011_files = await _all_pem_files(archivos_dir, "F8011")
            all_files = f8010_files + f8011_files
            if not all_files:
                raise RuntimeError(f"No se encontraron archivos en {archivos_dir}")

            for i, fpath in enumerate(all_files, start=1):
                status = await _present_file_with_retries(service_page, fpath, descargas_dir)
                if status == "timeout" or status.startswith("error"):
                    success = False
                resultados.append(_format_result(fpath, status))

                if i < len(all_files):
                    await _ensure_upload_screen(service_page)

    except Exception as exc:
        error_logs.append(f"Error global: {exc}")
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

    return resultados, error_logs, success


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    cuit_representante = os.getenv("CONTROLADORES_FISCALES_CUIT_REPRESENTANTE", "")
    clave_representante = os.getenv("CONTROLADORES_FISCALES_CLAVE", "")
    archivos_dir = os.getenv(
        "CONTROLADORES_FISCALES_ARCHIVOS_DIR",
        "/home/abp/Desktop/Proyectos Python/Scripts/bots-python-abp/bots-base/archivos-controladores-fiscales",
    )
    headless = os.getenv("CONTROLADORES_FISCALES_HEADLESS", "False").lower() in ("true", "1", "yes")

    if not cuit_representante or not clave_representante:
        print("Faltan variables de entorno CONTROLADORES_FISCALES_CUIT_REPRESENTANTE y CONTROLADORES_FISCALES_CLAVE")
        return

    print("Iniciando Controladores Fiscales...")
    resultados, errores, ok = await bot_controladores_fiscales(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        archivos_dir=archivos_dir,
        headless=headless,
    )

    for r in resultados:
        print(f"  - {r}")
    if errores:
        for e in errores:
            print(f"  Error: {e}")
    print(f"Estado: {'OK' if ok else 'ERROR'}")


if __name__ == "__main__":
    asyncio.run(main())
