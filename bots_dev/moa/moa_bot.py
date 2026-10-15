import asyncio
import re
import json
import csv
from pathlib import Path
from datetime import datetime
from playwright.async_api import Playwright, async_playwright, expect
from shared.arca_login import wait_page_ready


async def extraer_datos_tablas(page, selector_contenedor: str) -> dict:
    """
    Extrae datos de tablas con estructura thead/tbody dentro de un contenedor específico.
    
    Args:
        page: Página de Playwright
        selector_contenedor: Selector CSS del contenedor padre
    
    Returns:
        dict: Diccionario con pares nombre_campo: valor
    """
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
    """
    Busca un despacho y extrae datos de carátula y estado.
    
    Args:
        page: Página de Playwright
        despacho: Número de despacho a buscar
        metodo: 'url' (navegar directo) o 'volver' (usar botones)
    
    Returns:
        dict: Datos extraídos con estructura {despacho, metodo, timestamp, datos_caratula, estado_presentacion, error}
    """
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
            await wait_page_ready(page)
        
        await page.locator("input[name=\"declaracion\"]").click()
        await page.locator("input[name=\"declaracion\"]").fill("")
        await page.locator("input[name=\"declaracion\"]").fill(despacho)
        
        buscar_button = page.get_by_role("button", name="Buscar")
        await expect(buscar_button).to_be_enabled(timeout=5000)
        await buscar_button.click()
        
        await page.get_by_role("link", name=despacho).click()
        await wait_page_ready(page)
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
    """
    Navega por el árbol de selección Select2 para empresa, tipo de agente y rol.
    
    Args:
        page: Página de Playwright
        cuit_representado: CUIT del representado
        tipo_agente: Tipo de agente a seleccionar
        rol: Rol específico a seleccionar
    
    Returns:
        bool: True si la navegación fue exitosa, False en caso contrario
    """
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
        await wait_page_ready(page)
        print(f"  ✓ Ingresado al sistema")
        return True
        
    except Exception as e:
        print(f"  ✗ Error navegando árbol: {e}")
        return False


async def moa_bot(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    despachos: list[str],
    output_dir: str = "resultados_moa",
    metodo: str = "url",
    tipo_agente: str = "IMEX-IMEX-IMPORTADOR/EXPORT.",
    rol: str = "IMEX-Rol Importador Exportador"
    ) -> dict:
    """
    Bot automatizado para extraer datos de despachos del sistema MOA de AFIP.
    
    Args:
        playwright: Instancia de Playwright
        cuit_representante: CUIT del representante
        clave: Clave fiscal
        cuit_representado: CUIT del representado
        despachos: Lista de números de despacho a procesar
        output_dir: Directorio de salida para JSON/CSV
        metodo: 'url' (recomendado) o 'volver'
        tipo_agente: Tipo de agente MOA
        rol: Rol MOA
    
    Returns:
        dict: Resumen de ejecución con datos extraídos
    """
    Path(output_dir).mkdir(exist_ok=True)
    
    resultados = {
        "ejecucion": datetime.now().isoformat(),
        "metodo": metodo,
        "despachos_procesados": 0,
        "despachos_exitosos": 0,
        "despachos_con_error": 0,
        "datos": []
    }
    
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()
    
    try:
        # Login
        await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
        await page.get_by_role("spinbutton").click()
        await page.get_by_role("spinbutton").fill(cuit_representante)
        await page.get_by_role("button", name="Siguiente").click()
        await page.get_by_role("textbox", name="TU CLAVE").click()
        await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
        await page.get_by_role("button", name="Ingresar").click()
        await wait_page_ready(page)
        
        # Acceder a MOA
        await page.get_by_role("link", name="Ver todos").click()
        async with page.expect_popup() as page1_info:
            await page.get_by_role("button", name="MOA – REINGENIERIA Mis").click()
        page1 = await page1_info.value
        await wait_page_ready(page1)
        
        # Navegar por el árbol de empresa
        print("\n🌳 Navegando árbol de empresa...")
        if not await navegar_arbol_empresa(page1, cuit_representado, tipo_agente, rol):
            print("❌ ERROR: No se pudo navegar el árbol de empresa automáticamente")
            print(f"   Verifica que el CUIT {cuit_representado} existe y tiene acceso")
            return {
                "ejecucion": datetime.now().isoformat(),
                "metodo": metodo,
                "despachos_procesados": 0,
                "despachos_exitosos": 0,
                "despachos_con_error": 0,
                "datos": [],
                "error": f"No se pudo seleccionar la empresa con CUIT {cuit_representado}"
            }
        
        # Verificar que estamos en la página correcta antes de continuar
        try:
            await page1.get_by_text("Transacciones", exact=True).wait_for(timeout=5000)
        except:
            print("❌ ERROR: No se pudo acceder al menú principal")
            print("   La navegación automática no llegó al menú de Transacciones")
            return {
                "ejecucion": datetime.now().isoformat(),
                "metodo": metodo,
                "despachos_procesados": 0,
                "despachos_exitosos": 0,
                "despachos_con_error": 0,
                "datos": [],
                "error": "No se pudo acceder al menú principal MOA"
            }
        
        # Navegar a Declaración Detallada
        await page1.get_by_text("Transacciones", exact=True).click()
        await page1.locator("a").filter(has_text="GESTION DE LA DECLARACION").click()
        await page1.get_by_role("link", name="Declaracion Detallada").click()
        await wait_page_ready(page1)
        
        from collections import deque
        
        MAX_RETRIES = 3
        
        # Procesar despachos con cola de reintentos
        cola = deque((d, MAX_RETRIES) for d in despachos)
        total = len(despachos)
        procesados = 0
        
        while cola:
            despacho, restantes = cola.popleft()
            procesados += 1
            reintento = MAX_RETRIES - restantes
            label = f"despacho {procesados}/{total}" if restantes == MAX_RETRIES else f"reintento {reintento}/{MAX_RETRIES - 1}"
            print(f"\n[{label}] {despacho}")
            
            resultado = await buscar_y_scrapear_despacho(page1, despacho, metodo)
            
            if resultado["error"] and restantes > 1:
                cola.append((despacho, restantes - 1))
                print(f"  ↻ Pasa a cola de reintentos")
                continue
            
            resultados["datos"].append(resultado)
            resultados["despachos_procesados"] += 1
            
            if resultado["error"]:
                resultados["despachos_con_error"] += 1
            else:
                resultados["despachos_exitosos"] += 1
            
            print(f"  - Datos caratula: {len(resultado['datos_caratula'])} campos")
            print(f"  - Estado presentacion: {len(resultado['estado_presentacion'])} campos")
        
        # Exportar resultados
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        json_path = Path(output_dir) / f"moa_resultados_{metodo}_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(resultados, f, indent=2, ensure_ascii=False)
        print(f"\n✓ JSON: {json_path}")
        
        csv_path = Path(output_dir) / f"moa_resultados_{metodo}_{timestamp}.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            if resultados["datos"]:
                campos = set(["despacho", "metodo", "timestamp", "error"])
                for dato in resultados["datos"]:
                    campos.update([f"caratula_{k}" for k in dato["datos_caratula"].keys()])
                    campos.update([f"estado_{k}" for k in dato["estado_presentacion"].keys()])
                
                writer = csv.DictWriter(f, fieldnames=sorted(campos))
                writer.writeheader()
                
                for dato in resultados["datos"]:
                    fila = {
                        "despacho": dato["despacho"],
                        "metodo": dato["metodo"],
                        "timestamp": dato["timestamp"],
                        "error": dato["error"] or ""
                    }
                    for k, v in dato["datos_caratula"].items():
                        fila[f"caratula_{k}"] = v
                    for k, v in dato["estado_presentacion"].items():
                        fila[f"estado_{k}"] = v
                    
                    writer.writerow(fila)
        
        print(f"✓ CSV: {csv_path}")
        
    finally:
        await context.close()
        await browser.close()
    
    return resultados




async def main() -> None:
    """Función principal para ejecutar el bot MOA con configuración desde .env"""
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    cuit_representante = os.getenv("CUIT_REPRESENTANTE_MOA") or os.getenv("REPRESENTANTE")
    clave = os.getenv("CLAVE_MOA")
    cuit_representado = os.getenv("CUIT_REPRESENTADO_MOA") or os.getenv("REPRESENTADO")
    
    if not cuit_representante:
        print("❌ ERROR: CUIT_REPRESENTANTE no configurado en .env")
        return
    if not clave:
        print("❌ ERROR: CLAVE_MOA no configurada en .env")
        return
    if not cuit_representado:
        print("❌ ERROR: CUIT_REPRESENTADO_MOA no configurado en .env")
        return
    
    # despachos_ejemplo = [
    #     # "25046EC03000042E",
    #     # "25086EC01000605L",
    #     # "25086EC01000606M",
    #     # "25086EC01000607N",
    #     # "25086EC01000608Y",
    #     "25086EC01000609P",
    #     # "25086EC01000610H",
    #     # "25086EC01000618P",
    #     # "25086EC01000619Z",
    #     # "25086EC01000637Z",
    #     # "25086EC01000640K",
    #     # "25086EC01000658T",
    #     # "25086EC01000659U",
    #     # "25086EC01000661N",
    #     # "25086EC01000676T",
    #     # "25086EC03000495U",
    #     # "25086EC03000501X",
    #     # "25086EC03000509Z",
    #     # "25086EC03000525Y",
    #     # "25086EC03000531L",
    #     # "25086EC03000532M",
    # ]
    
    with open("despachos.txt", "r") as f:
        despachos_ejemplo = [line.strip() for line in f if line.strip()]
    
    print("="*60)
    print("BOT MOA - SCRAPING DE DESPACHOS (AUTOMÁTICO)")
    print("="*60)
    print(f"Representante: {cuit_representante}")
    print(f"Representado: {cuit_representado}")
    print(f"Despachos a procesar: {len(despachos_ejemplo)}")
    
    async with async_playwright() as playwright:
        print("\n🔄 Iniciando proceso...")
        print("-"*60)
        resultados = await moa_bot(
            playwright, 
            cuit_representante, 
            clave,
            cuit_representado,
            despachos_ejemplo,
            output_dir="resultados_moa",
            metodo="url"
        )
        
        print("\n" + "="*60)
        print("RESUMEN")
        print("="*60)
        print(f"Procesados: {resultados['despachos_procesados']}")
        print(f"Exitosos: {resultados['despachos_exitosos']}")
        print(f"Con errores: {resultados['despachos_con_error']}")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
