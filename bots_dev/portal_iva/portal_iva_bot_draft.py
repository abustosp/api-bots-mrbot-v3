import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect



async def run(
    playwright: Playwright,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    periodo: str,
    operaciones_ng_o_e: bool,
    prorrateo_global: bool,
    prorrateo_asignacion_directa: bool,
    prorrateo_ambos: bool,
    importacion_definitiva_bienes: bool,
    importacion_servicios: bool,
    regimen_turiva: bool,
    bienes_usados: bool,
    ninguna_anteriores: bool,
    liv_cbte: str,
    liv_alicuota: str,
    lic_cbte: str,
    lic_alicuota: str,
    descarga_CSV_ventas: bool,
    descarga_CSV_compras: bool,
    importar_txt_ventas: bool,
    importar_txt_compras: bool
              ) -> None:
    
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    # await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representante)
    await page.get_by_role("button", name="Siguiente").click()
    # await page.get_by_label("TU CLAVE").click()
    await page.get_by_label("TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="PORTAL IVA Presentación del").click()
    page1 = await page1_info.value
    await page1.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init?_k=5va1om")
    
    # abrir sección de representados
    await page1.get_by_role("link", name="").click()
    
    # seleccionar representado
    await page1.get_by_title("Representar a...").click()
    
    # comenzar sección dentro de Portal IVA del representado
    await page1.get_by_label("Sin texto (iva.home.btn.nueva").click()
    await page1.locator("#periodo").select_option(periodo)
    await page1.get_by_label("Sin texto (iva.btn.home.validar.periodo.alt)").click()
    await page1.get_by_label("Sin texto (iva.btn.home.liva.").click()
    if operaciones_ng_o_e:
        await page1.locator("label").filter(has_text="Operaciones No Gravadas o").locator("span").click()
    if operaciones_ng_o_e and prorrateo_asignacion_directa:
        await page1.get_by_role("button", name="Con prorrateo por asignación").click()
        await page1.get_by_role("listbox").get_by_role("option", name="Con prorrateo por asignación directa", exact=True).click()
    if operaciones_ng_o_e and prorrateo_global:
        await page1.get_by_role("button", name="Con prorrateo por asignación").click()
        await page1.get_by_role("listbox").get_by_role("option", name="Con prorrateo global").click()
    if operaciones_ng_o_e and prorrateo_ambos:
        await page1.get_by_role("button", name="Con prorrateo global").click()
        await page1.get_by_role("listbox").get_by_role("option", name="Con prorrateo por asignación directa y global").click()
    if importacion_definitiva_bienes:
        await page1.locator("label").filter(has_text="Importación definitiva de").locator("span").click()
    if importacion_servicios:    
        await page1.locator("label").filter(has_text="Importación de servicios").locator("span").click()
    if regimen_turiva:    
        await page1.locator("label").filter(has_text="Incluido en el régimen TurIVA").locator("span").click()
    if bienes_usados:    
        await page1.locator("label").filter(has_text="Compras de bienes usados o").locator("span").click()
    if ninguna_anteriores:    
        await page1.locator("label").filter(has_text="No realizo ninguna actividad").locator("span").click()
    
    await page1.get_by_role("button", name="Continuar").click()
    
    # sección de libro ventas

    await page1.get_by_role("link", name=" Libro Ventas Registrá y").click()
    await page1.get_by_role("button", name="Importar").click()
    
    # Descargar CSV desde ARCA
    if descarga_CSV_ventas:
        await page1.get_by_role("link", name="   Importar desde ARCA...").click()
        await page1.locator("#btnImportarAFIPImportar").click()
        await page1.get_by_role("button", name="Close").click()
        async with page1.expect_download() as download_info:
            async with page1.expect_popup() as page2_info:
                await page1.get_by_role("button", name="CSV").click()
            page2 = await page2_info.value
        download = await download_info.value
        await page2.close()
    
    # Eliminar datos previos    
    await page1.get_by_role("button", name="Eliminar Todos...").click()
    await page1.get_by_text("Eliminar", exact=True).click()
    
    # Importar archivos de ventas
    if importar_txt_ventas:
        await page1.get_by_role("button", name="Importar").click()
        await page1.get_by_role("link", name="   Importar Archivos...").click()
        await page1.get_by_label("Archivo Ventas *").click()
        await page1.get_by_label("Archivo Ventas *").set_input_files(liv_cbte)
        await page1.locator("#btnImportarArchivosImportar").click()
        await page1.get_by_label("Archivo IVA Ventas *").click()
        await page1.get_by_label("Archivo IVA Ventas *").set_input_files(liv_alicuota)
        await page1.locator("#btnImportarArchivosImportar").click()
        await page1.locator("#btnImportarArchivosImportar").click()
        await page1.get_by_text("Procesada").click()
        await page1.get_by_role("button", name="Close").click()
        await page1.get_by_role("link", name="Libro IVA - Borrador").click()
        
    # sección de libro compras    
    await page1.get_by_role("link", name=" Libro Compras Registrá y").click()
    
    # Descargar CSV desde ARCA
    if descarga_CSV_compras:
        await page1.get_by_role("button", name="Importar").click()
        await page1.get_by_role("link", name="   Importar desde ARCA...").click()
        await page1.locator("#btnImportarAFIPImportar").click()
        await page1.get_by_text("Procesada").click()
        await page1.get_by_role("button", name="Close").click()
        async with page1.expect_download() as download_info:
            async with page1.expect_popup() as page2_info:
                await page1.get_by_role("button", name="CSV").click()
            page2 = await page2_info.value
        download = await download_info.value
        
        await page2.close()
        
    # Eliminar datos previos    
    await page1.get_by_role("button", name="Eliminar Todos...").click()
    await page1.get_by_text("Eliminar", exact=True).click()
    
    # Importar archivos de compras
    if importar_txt_compras:
        await page1.get_by_role("button", name="Importar").click()
        await page1.get_by_role("link", name="   Importar Archivos...").click()
        await page1.get_by_label("Archivo Compras *").click()
        await page1.get_by_label("Archivo Compras *").set_input_files(lic_cbte)
        await page1.get_by_label("Archivo IVA Compras *").click()
        await page1.get_by_label("Archivo IVA Compras *").set_input_files(lic_alicuota)
        await page1.locator("#btnImportarArchivosImportar").click()
        await page1.get_by_text("Procesando").click()
        
    await page1.get_by_role("button", name="Close").click()
    await page1.get_by_role("button", name="Eliminar Todos...").click()
    await page1.get_by_text("Eliminar", exact=True).click()
    await page1.get_by_role("link", name="Libro IVA - Borrador").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main(
    cuit_representado: str,
    clave_representado: str,
    periodo: str,
    operaciones_ng_o_e: bool,
    prorrateo_global: bool,
    prorrateo_asignacion_directa: bool,
    prorrateo_ambos: bool,
    importacion_definitiva_bienes: bool,
    importacion_servicios: bool,
    regimen_turiva: bool,
    bienes_usados: bool,
    ninguna_anteriores: bool,
    liv_cbte: str,
    liv_alicuota: str,
    lic_cbte: str,
    lic_alicuota: str,
    descarga_CSV_ventas: bool,
    descarga_CSV_compras: bool,
    importar_txt_ventas: bool,
    importar_txt_compras: bool
) -> None:
    
    
    async with async_playwright() as playwright:
        await run(
            playwright,
            cuit_representado,
            clave_representado,
            periodo,
            operaciones_ng_o_e,
            prorrateo_global,
            prorrateo_asignacion_directa,
            prorrateo_ambos,
            importacion_definitiva_bienes,
            importacion_servicios,
            regimen_turiva,
            bienes_usados,
            ninguna_anteriores,
            liv_cbte,
            liv_alicuota,
            lic_cbte,
            lic_alicuota,
            descarga_CSV_ventas,
            descarga_CSV_compras,
            importar_txt_ventas,
            importar_txt_compras
        )


if __name__ == "__main__":
    
    from dotenv import load_dotenv
    import os

    load_dotenv()

    cuit_representado = os.getenv("CUIT_REPRESENTADO")
    clave_representado = os.getenv("CLAVE")
    cuit_representante = os.getenv("CUIT_REPRESENTANTE")
    periodo = os.getenv("PERIODO")
    operaciones_ng_o_e = os.getenv("OPERACIONES_NO_GRAVADAS_O_EXENTAS") == "False"
    prorrateo_global = os.getenv("PRORRATEO_GLOBAL") == "False"
    prorrateo_asignacion_directa = os.getenv("PRORRATEO_ASIGNACION_DIRECTA") == "False"
    prorrateo_ambos = os.getenv("PRORRATEO_AMBOS") == "False"
    importacion_definitiva_bienes = os.getenv("IMPORTACION_DEFINITIVA_BIENES") == "False"
    importacion_servicios = os.getenv("IMPORTACION_SERVICIOS") == "False"
    regimen_turiva = os.getenv("REGIMEN_TURIVA") == "False"
    bienes_usados = os.getenv("BIENES_USADOS_O_RENOVADOS") == "False"
    ninguna_anteriores = os.getenv("NINGUNA_ANTERIORES") == "True"

    liv_cbte = os.getenv("LIV_CBTE")
    liv_alicuota = os.getenv("LIV_ALICUOTA")
    lic_cbte = os.getenv("LIC_CBTE")
    lic_alicuota = os.getenv("LIC_ALICUOTA")

    descarga_CSV_ventas = os.getenv("DESCARGA_CSV_VENTAS") == "True"    
    descarga_CSV_compras = os.getenv("DESCARGA_CSV_COMPRAS") == "True"
    importar_txt_ventas = os.getenv("IMPORTAR_TXT_VENTAS") == "True"
    importar_txt_compras = os.getenv("IMPORTAR_TXT_COMPRAS") == "True"
    
    asyncio.run(main(
        cuit_representado,
        clave_representado,
        periodo,
        operaciones_ng_o_e,
        prorrateo_global,
        prorrateo_asignacion_directa,
        prorrateo_ambos,
        importacion_definitiva_bienes,
        importacion_servicios,
        regimen_turiva,
        bienes_usados,
        ninguna_anteriores,
        liv_cbte,
        liv_alicuota,
        lic_cbte,
        lic_alicuota,
        descarga_CSV_ventas,
        descarga_CSV_compras,
        importar_txt_ventas,
        importar_txt_compras
    ))