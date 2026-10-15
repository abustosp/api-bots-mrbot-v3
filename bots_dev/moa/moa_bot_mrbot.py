import os
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from playwright.async_api import Playwright, async_playwright, expect

from shared.arca_login import start_arca_session
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


MOA_POPUP_TIMEOUT = 30_000


async def extraer_datos_tablas(page, selector_contenedor: str) -> dict:
    datos = {}

    try:
        await page.wait_for_timeout(1000)
        contenedor = page.locator(f"{selector_contenedor} .tabla-detallada").first

        if not await contenedor.count():
            return datos

        tablas = await contenedor.locator("table").all()

        for idx, tabla in enumerate(tablas):
            try:
                headers = await tabla.locator("thead tr th label").all()
                nombres_columnas = [await h.inner_text() for h in headers]
                nombres_columnas = [n.strip() for n in nombres_columnas]

                primera_fila = tabla.locator("tbody tr").first
                celdas_valores = await primera_fila.locator("td").all()
                valores = [await c.inner_text() for c in celdas_valores]
                valores = [v.strip() for v in valores]

                if len(nombres_columnas) != len(valores):
                    print(f"      ⚠ Desbalance en tabla {idx+1}: {len(nombres_columnas)} headers vs {len(valores)} valores")

                for i, nombre in enumerate(nombres_columnas):
                    if i < len(valores) and nombre:
                        nombre_limpio = nombre.replace("\n", " ").strip()
                        datos[nombre_limpio] = valores[i]

            except Exception as e:
                print(f"    ⚠ Error en tabla {idx+1}: {e}")
                continue

    except Exception as e:
        print(f"    ⚠ Error extrayendo datos de tablas: {e}")

    return datos


async def buscar_y_scrapear_despacho(page, despacho: str, metodo: str = "url") -> dict:
    resultado = {
        "despacho": despacho,
        "metodo": metodo,
        "timestamp": datetime.now().isoformat(),
        "datos_caratula": {},
        "estado_presentacion": {},
        "error": None
    }

    try:
        if metodo == "url":
            await page.goto("https://serviciosadu.afip.gob.ar/DIAV2/MOA.Web/Moa.Detallada.Web/MoaDetallada/FiltroDeclas")
            await page.wait_for_load_state("networkidle")

        await page.locator("input[name=\"declaracion\"]").click()
        await page.locator("input[name=\"declaracion\"]").fill("")
        await page.locator("input[name=\"declaracion\"]").fill(despacho)

        buscar_button = page.get_by_role("button", name="Buscar")
        await expect(buscar_button).to_be_enabled(timeout=5000)
        await buscar_button.click()

        await page.get_by_role("link", name=despacho).click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)

        await page.get_by_text("DATOS DE LA CARATULA CANAL").click()
        resultado["datos_caratula"] = await extraer_datos_tablas(page, ".cabecera.dga-moa-decla-detallada-caratula")

        await page.get_by_text("ESTADO PRESENTACION").click()
        resultado["estado_presentacion"] = await extraer_datos_tablas(page, ".cabecera-estado.dga-moa-decla-detallada-caratula-estado")

        if metodo == "volver":
            await page.get_by_role("button", name="Volver").click()
            await page.wait_for_timeout(300)
            await page.get_by_role("button", name="Volver").click()
            await page.wait_for_timeout(300)

    except Exception as e:
        resultado["error"] = str(e)
        print(f"    ✗ Error: {e}")

    return resultado


async def navegar_arbol_empresa(
    page,
    cuit_representado: str,
    tipo_agente: str = "IMEX-IMEX-IMPORTADOR/EXPORT.",
    rol: str = "IMEX-Rol Importador Exportador"
) -> bool:
    try:
        await page.wait_for_timeout(2000)

        # Seleccionar empresa por CUIT
        try:
            selector_empresa = page.locator(".select2-container").first
            await selector_empresa.click()
            await page.wait_for_timeout(1000)
            await page.wait_for_selector("li[role='treeitem']", timeout=5000)

            empresa_elem = page.locator(f"li[role='treeitem']:has-text('{cuit_representado}')")
            if await empresa_elem.count() > 0:
                texto_completo = await empresa_elem.first.inner_text()
                print(f"  ✓ Empresa: {texto_completo}")
                await empresa_elem.first.click()
                await page.wait_for_timeout(1000)
            else:
                print(f"  ⚠ No se encontró empresa con CUIT: {cuit_representado}")
                return False
        except Exception as e:
            print(f"  ⚠ Error seleccionando empresa: {e}")
            return False

        # Seleccionar tipo de agente
        try:
            selector_tipo = page.locator(".select2-container").nth(1)
            await selector_tipo.click()
            await page.wait_for_timeout(1000)
            await page.wait_for_selector("li[role='treeitem']", timeout=5000)

            tipo_elem = page.locator(f"li[role='treeitem']:has-text('{tipo_agente}')")
            if await tipo_elem.count() > 0:
                print(f"  ✓ Tipo de agente: {tipo_agente}")
                await tipo_elem.first.click()
                await page.wait_for_timeout(1000)
            else:
                print(f"  ⚠ No se encontró tipo de agente: {tipo_agente}")
                return False
        except Exception as e:
            print(f"  ⚠ Error seleccionando tipo de agente: {e}")
            return False

        # Seleccionar rol
        try:
            selector_rol = page.locator(".select2-container").nth(2)
            await selector_rol.click()
            await page.wait_for_timeout(1000)
            await page.wait_for_selector("li[role='treeitem']", timeout=5000)

            rol_elem = page.locator(f"li[role='treeitem']:has-text('{rol}')")
            if await rol_elem.count() > 0:
                print(f"  ✓ Rol: {rol}")
                await rol_elem.first.click()
                await page.wait_for_timeout(1000)
            else:
                print(f"  ⚠ No se encontró rol: {rol}")
                return False
        except Exception as e:
            print(f"  ⚠ Error seleccionando rol: {e}")
            return False

        await page.get_by_role("button", name="Ingresar").click()
        await page.wait_for_load_state("networkidle")
        print(f"  ✓ Ingresado al sistema")
        return True

    except Exception as e:
        print(f"  ✗ Error navegando árbol: {e}")
        return False


async def _safe_close(browser, context):
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


async def moa_bot(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    despachos: List[str],
    tipo_agente: str = "IMEX-IMEX-IMPORTADOR/EXPORT.",
    rol: str = "IMEX-Rol Importador Exportador",
    metodo: str = "url",
    proxy_request: bool = False,

) -> Tuple[Dict, List[str], bool]:
    results: Dict = {}
    errors: List[str] = []
    success = True

    resultados = {
        "ejecucion": datetime.now().isoformat(),
        "metodo": metodo,
        "despachos_procesados": 0,
        "despachos_exitosos": 0,
        "despachos_con_error": 0,
        "datos": []
    }

    proxy = get_proxy_config() if proxy_request else None

    try:
        async with limit_browser(), async_playwright() as pw:
            arca_session = await start_arca_session(
                pw,
                cuit_representante,
                clave_representante,
                headless=True,
                proxy=proxy,
                login_url="https://auth.afip.gob.ar/contribuyente_/login.xhtml",
            )

            if arca_session is None:
                errors.append("No se pudo iniciar sesion en ARCA. Verifique los datos ingresados")
                return results, errors, False

            main_page = arca_session.page

            try:
                # Acceder a "Ver todos"
                await main_page.get_by_role("link", name="Ver todos").click()
                await main_page.wait_for_load_state("networkidle")

                # Abrir MOA en popup
                async with main_page.expect_popup(timeout=MOA_POPUP_TIMEOUT) as page1_info:
                    await main_page.get_by_role("button", name="MOA – REINGENIERIA Mis").click()
                page1 = await page1_info.value
                await page1.wait_for_load_state("networkidle")
            except Exception as e:
                errors.append(f"No se pudo acceder al servicio MOA: {e}")
                await _safe_close(arca_session.browser, arca_session.context)
                return results, errors, False

            # Navegar por el árbol de empresa
            print("\n   Navegando arbol de empresa...")
            if not await navegar_arbol_empresa(page1, cuit_representado, tipo_agente, rol):
                errors.append(f"No se pudo seleccionar la empresa con CUIT {cuit_representado}")
                await _safe_close(arca_session.browser, arca_session.context)
                return results, errors, False

            # Verificar que estamos en la página correcta
            try:
                await page1.get_by_text("Transacciones", exact=True).wait_for(timeout=5000)
            except Exception:
                errors.append("No se pudo acceder al menu principal MOA")
                await _safe_close(arca_session.browser, arca_session.context)
                return results, errors, False

            # Navegar a Declaración Detallada
            await page1.get_by_text("Transacciones", exact=True).click()
            await page1.locator("a").filter(has_text="GESTION DE LA DECLARACION").click()
            await page1.get_by_role("link", name="Declaracion Detallada").click()
            await page1.wait_for_load_state("networkidle")

            # Procesar cada despacho
            for i, despacho in enumerate(despachos):
                print(f"\nProcesando despacho {i+1}/{len(despachos)}: {despacho}")

                dato = await buscar_y_scrapear_despacho(page1, despacho, metodo)
                resultados["datos"].append(dato)
                resultados["despachos_procesados"] += 1

                if dato["error"]:
                    resultados["despachos_con_error"] += 1
                else:
                    resultados["despachos_exitosos"] += 1

                print(f"  - Datos caratula: {len(dato['datos_caratula'])} campos")
                print(f"  - Estado presentacion: {len(dato['estado_presentacion'])} campos")


            # Cerrar sesion
            await _safe_close(arca_session.browser, arca_session.context)

            results["datos"] = resultados["datos"]
            results["despachos_procesados"] = resultados["despachos_procesados"]
            results["despachos_exitosos"] = resultados["despachos_exitosos"]
            results["despachos_con_error"] = resultados["despachos_con_error"]

            return results, errors, success

    except Exception as e:
        errors.append(str(e))
        return results, errors, False
