"""
Bot Logger: Cartilla Médica Unión Personal.

Scrapea los prestadores odontológicos de la cartilla de Unión Personal y los
exporta a un CSV. La configuración actual (según el draft) es:

    Zona: Interior del país
    Plan: Classic
    Tipo: ODONTOLOGICA
    Subzona: MISIONES
    Categoría: ODONTOLOGIA
    Especialidad: ODONTOLOGIA GENERAL

Versión logger: crea una carpeta por ejecución en ./temp/{bot}_{timestamp}/
con evidencias por paso (screenshots, HTML, HAR) y el CSV en descargas/.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright, Page, TimeoutError

# ---------------------------------------------------------------------------
# Configuración del bot
# ---------------------------------------------------------------------------

URL_CARTILLA = "https://www.unionpersonal.com.ar/cartilla"

BOT_NAME = "cartilla-medica-union-personal"

# Filtros a aplicar en la cartilla (mismo flujo funcional que el draft)
ZONA = "Interior del país"
PLAN = "Plan Classic"
TIPO = "ODONTOLOGICA"
SUBZONA = "MISIONES"
CATEGORIA = "ODONTOLOGIA"
ESPECIALIDAD = "ODONTOLOGIA GENERAL"

# Columnas del CSV de salida
CSV_HEADERS = [
    "apellido_nombre",
    "direccion",
    "localidad",
    "telefono",
    "especialidad",
    "zona",
    "plan",
    "tipo",
    "subzona",
    "categoria",
]


def _safe_step_name(step: str) -> str:
    """Convierte un nombre de paso en un nombre seguro para archivo."""
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    """Captura de evidencias por paso: logs, screenshots, HTML y HAR."""

    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
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

    async def capture(self, page: Page, step: str) -> None:
        """Guarda screenshot y HTML del estado actual de la página."""
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
# Helpers de scraping
# ---------------------------------------------------------------------------

async def _extract_cards_from_page(page: Page) -> List[dict]:
    """Extrae las tarjetas de prestadores de la página actual usando locators.

    Cada tarjeta tiene la estructura:
        <h3>  NOMBRE </h3>
        <p class="text-amber-600"> Dirección </p>
        <p class="text-gray-700 uppercase"> Localidad </p>
        <p class="text-blue-600"> Tel: ... </p>
        <span ...> Especialidad </span>
    """
    cards: List[dict] = []
    headings = page.locator("main h3")
    count = await headings.count()
    for i in range(count):
        heading = headings.nth(i)
        name = (await heading.inner_text() or "").strip()
        # El contenedor de la tarjeta es el div que envuelve al h3
        card = heading.locator("xpath=..")
        paras = card.locator("p")
        para_texts = [p.strip() for p in await paras.all_inner_texts()]

        # La especialidad está en un span con clase .btn-text-fix (o cualquier span)
        span = card.locator("span .btn-text-fix, span").first
        try:
            especialidad = (await span.inner_text() or "").strip()
        except Exception:
            especialidad = ""

        cards.append({
            "nombre": name,
            "direccion": para_texts[0] if len(para_texts) > 0 else "",
            "localidad": para_texts[1] if len(para_texts) > 1 else "",
            "telefono": para_texts[2] if len(para_texts) > 2 else "",
            "especialidad": especialidad,
        })
    return cards


async def _get_pagination(page: Page) -> dict:
    """Devuelve información de paginación y total de resultados."""
    pag_text = ""
    total_text = ""
    # Texto "Página X de Y"
    pagination = page.locator("text=/Página \\d+ de \\d+/").first
    try:
        pag_text = await pagination.inner_text()
    except Exception:
        pass
    # Texto "N resultados"
    total = page.locator("text=/\\d+ resultados/").first
    try:
        total_text = await total.inner_text()
    except Exception:
        pass
    return {
        "pagina": pag_text.strip(),
        "total": total_text.strip(),
    }


async def _is_next_enabled(page: Page) -> bool:
    """Devuelve True si el botón 'Siguiente →' está habilitado."""
    next_btn = page.get_by_role("button", name="Siguiente →")
    if await next_btn.count() == 0:
        return False
    return await next_btn.is_enabled()


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

async def _select_from_combobox(page: Page, option_text: str, step_label: str, debug: DebugCapture) -> bool:
    """Abre el primer combobox visible y elige la opción por texto.

    Devuelve True si el combobox existía y se seleccionó la opción; False si no
    había ningún combobox (la opción ya estaba preseleccionada / se auto-carga).
    """
    comboboxes = page.locator("[role=combobox]")
    count = await comboboxes.count()
    if count == 0:
        debug.log(f"sin_combobox step={step_label} opcion={option_text} (preseleccionada)")
        return False

    # Abrimos el combobox
    await comboboxes.first.click()
    # Elegimos la opción por texto exacto dentro de la lista desplegada
    option = page.get_by_role("option", name=option_text)
    if await option.count() > 0:
        await option.first.click()
    else:
        # Fallback por texto exacto
        await page.get_by_text(option_text, exact=True).first.click()
    await page.wait_for_timeout(800)
    debug.log(f"combobox_seleccionado step={step_label} opcion={option_text}")
    return True


async def _navigate_to_providers(
    page: Page,
    debug: DebugCapture,
    tipo: str,
    subzona: str,
    categoria: Optional[str],
    especialidad: Optional[str],
) -> None:
    """Recorre el recorrido paso a paso hasta llegar a los prestadores.

    La página usa comboboxes y botones de selección. El flujo depende del tipo:

    - ODONTOLOGICA: el tipo resuelve la categoría automáticamente y muestra un
      combobox de ESPECIALIDAD (p. ej. ODONTOLOGIA GENERAL).
    - MEDICA: tras el tipo aparece un combobox de CATEGORÍA (p. ej. KINESIOLOGIA)
      que a su vez auto-selecciona la especialidad (dos botones iguales).

    Solo se pulsan los comboboxes que realmente existen; así el bot es genérico.
    """
    wait_ms = 800

    # Paso 1: Zona
    await _select_from_combobox(page, ZONA, "zona", debug)
    await debug.capture(page, "001_zona_seleccionada")

    # Paso 2: Plan (tarjeta clickeable con heading)
    await page.get_by_role("heading", name=PLAN).click()
    await page.wait_for_timeout(wait_ms)
    await debug.capture(page, "002_plan_seleccionado")

    # Paso 3: Tipo
    await _select_from_combobox(page, tipo, "tipo", debug)
    await debug.capture(page, "003_tipo_seleccionado")

    # Paso 4: Subzona
    await _select_from_combobox(page, subzona, "subzona", debug)
    await debug.capture(page, "004_subzona_seleccionada")

    # Paso 5: Categoría (solo si existe el combobox, p. ej. MEDICA)
    if categoria:
        await _select_from_combobox(page, categoria, "categoria", debug)
        await debug.capture(page, "005_categoria_seleccionada")

    # Paso 6: Especialidad (solo si existe un combobox, p. ej. ODONTOLOGICA)
    if especialidad:
        # Si ya se cargaron los prestadores, no hay combobox y no pulsamos nada.
        await _select_from_combobox(page, especialidad, "especialidad", debug)
        await debug.capture(page, "006_especialidad_seleccionada")

    # Esperar a que carguen los prestadores
    await page.wait_for_timeout(1500)


async def _scrape_all_pages(page: Page, debug: DebugCapture) -> List[dict]:
    """Recorre todas las páginas de resultados y acumula los prestadores."""
    all_cards: List[dict] = []
    page_num = 1

    while True:
        await page.wait_for_timeout(700)
        await debug.capture(page, f"resultados_pagina_{page_num:02d}")
        cards = await _extract_cards_from_page(page)
        all_cards.extend(cards)

        pag = await _get_pagination(page)
        debug.log(f"pagina={pag.get('pagina')} total={pag.get('total')} cards_en_pagina={len(cards)} acumuladas={len(all_cards)}")

        if not await _is_next_enabled(page):
            break

        # Avanzar a la siguiente página
        next_btn = page.get_by_role("button", name="Siguiente →")
        await next_btn.click()
        await page.wait_for_timeout(1000)
        page_num += 1

    return all_cards


def _write_csv(
    path: Path,
    rows: List[dict],
    tipo: str = TIPO,
    subzona: str = SUBZONA,
    categoria: Optional[str] = CATEGORIA,
) -> None:
    """Escribe las filas de prestadores a un CSV.

    Cada fila interna usa 'nombre' como clave; el CSV de salida tiene la columna
    'apellido_nombre'. Se rellenan además las columnas fijas del contexto.
    """
    import pandas as pd

    series = []
    for row in rows:
        series.append({
            "apellido_nombre": row.get("nombre", ""),
            "direccion": row.get("direccion", ""),
            "localidad": row.get("localidad", ""),
            "telefono": row.get("telefono", ""),
            "especialidad": row.get("especialidad", ""),
            "zona": ZONA,
            "plan": PLAN,
            "tipo": tipo,
            "subzona": subzona,
            "categoria": categoria or "",
        })
    df = pd.DataFrame(series, columns=CSV_HEADERS)
    df.to_csv(path, index=False, sep=";", encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Configuración de filtros (módulo). Se pueden sobreescribir por parámetro.
# ---------------------------------------------------------------------------

def _default_config() -> dict:
    """Configuración por defecto (flujo ODONTOLOGICA del draft)."""
    return {
        "tipo": "ODONTOLOGICA",
        "subzona": "MISIONES",
        "categoria": "ODONTOLOGIA",
        "especialidad": "ODONTOLOGIA GENERAL",
    }


async def bot_cartilla_medica_union_personal_logger(
    tipo: Optional[str] = None,
    subzona: Optional[str] = None,
    categoria: Optional[str] = None,
    especialidad: Optional[str] = None,
    zona: Optional[str] = None,
    plan: Optional[str] = None,
) -> tuple[list[dict], Optional[Path], list[str], bool]:
    """Ejecuta el bot y devuelve (resultados, ruta_del_csv, errores, success).

    Los filtros se configuran por parámetro, con valores por defecto para el
    flujo ODONTOLOGICA del draft. Para KINESIOLOGIA, por ejemplo:
        tipo="MEDICA", subzona="MISIONES", categoria="KINESIOLOGIA",
        especialidad=None  (la categoría auto-selecciona la especialidad).
    """
    cfg = _default_config()
    tipo = tipo or cfg["tipo"]
    subzona = subzona or cfg["subzona"]
    categoria = categoria if categoria is not None else cfg["categoria"]
    especialidad = especialidad if especialidad is not None else cfg["especialidad"]
    zona_ef = zona or ZONA
    plan_ef = plan or PLAN

    results: List[dict] = []
    error_logs: List[str] = []
    success = True
    csv_path: Optional[Path] = None

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Cartilla Médica Unión Personal logger")
    debug.log(f"filtros: zona={zona_ef} plan={plan_ef} tipo={tipo} subzona={subzona} categoria={categoria} especialidad={especialidad}")

    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(record_har_path=har_path, record_har_content="embed")
            page = await context.new_page()

            await debug.capture(page, "000_inicio")
            debug.log(f"url_inicio={page.url}")

            await page.goto(URL_CARTILLA, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            await debug.capture(page, "001_cartilla_cargada")

            await _navigate_to_providers(
                page, debug,
                tipo=tipo, subzona=subzona, categoria=categoria, especialidad=especialidad,
            )
            debug.log("navegacion_completada")

            # Esperar a que aparezca el contenedor de resultados
            try:
                await page.wait_for_selector("main h3", timeout=15000)
            except TimeoutError:
                error_logs.append("No se encontraron prestadores (main h3).")
                debug.log("No se encontraron prestadores")
                success = False

            if success:
                cards = await _scrape_all_pages(page, debug)
                results = cards
                debug.log(f"total_prestadores={len(cards)}")

                # Guardar el CSV en descargas/ con nombre descriptivo
                nombre_csv = f"prestadores_{tipo.lower()}_{subzona.lower()}.csv"
                csv_path = debug.descargas_dir / nombre_csv
                _write_csv(csv_path, cards, tipo=tipo, subzona=subzona, categoria=categoria)
                debug.log(f"csv_guardado={csv_path}")

            await debug.capture(page, "999_fin")

            await context.close()
            await browser.close()

    except Exception as exc:
        error_logs.append(str(exc))
        success = False
        debug.log(f"ERROR: {exc}")
        # Evidencia adicional si falla
        try:
            await debug.capture(page, "999_error")
        except Exception:
            pass

    return results, csv_path, error_logs, success


async def main() -> None:
    # args: --tipo --subzona --categoria --especialidad --zona --plan
    import argparse

    parser = argparse.ArgumentParser(description="Bot cartilla médica Unión Personal (logger)")
    parser.add_argument("--tipo", default=None, help="Tipo de cartilla, ej. ODONTOLOGICA | MEDICA")
    parser.add_argument("--subzona", default=None, help="Subzona/provincia, ej. MISIONES")
    parser.add_argument("--categoria", default=None, help="Categoría, ej. KINESIOLOGIA (vacío = ningún combobox)")
    parser.add_argument("--especialidad", default=None, help="Especialidad, ej. ODONTOLOGIA GENERAL")
    parser.add_argument("--zona", default=None, help="Zona, ej. Interior del país")
    parser.add_argument("--plan", default=None, help="Plan, ej. Plan Classic")
    args = parser.parse_args()

    results, csv_path, error_logs, success = await bot_cartilla_medica_union_personal_logger(
        tipo=args.tipo,
        subzona=args.subzona,
        categoria=args.categoria,
        especialidad=args.especialidad,
        zona=args.zona,
        plan=args.plan,
    )
    if success:
        print(f"ÉXITO: {len(results)} prestadores scrapeados")
        print(f"CSV: {csv_path}")
    else:
        print("ERROR durante la ejecución:")
        for err in error_logs:
            print(f"  - {err}")


if __name__ == "__main__":
    asyncio.run(main())
