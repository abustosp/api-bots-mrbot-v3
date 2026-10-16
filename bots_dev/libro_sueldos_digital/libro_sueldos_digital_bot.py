"""Bot Libro de Sueldos Digital (ARCA) - versión limpia.

Misma lógica funcional que ``libro_sueldos_digital_bot_logger.py`` sin
instrumentación por paso (sin screenshots, HTML ni HAR).

Descarga las DDJJ / Libro de Sueldos Digital (PDF) de todos los períodos del
rango solicitado. Si un período tiene varias liquidaciones, descarga todas.

Si la cuenta representa a más de un CUIT, el servicio muestra un selector de
representado (``IndexContribuyente``): se elige el CUIT solicitado —o el
primero, la preselección del sitio— y se verifica el empleador resultante
antes de descargar.

Integración de producción:

- Descargas en directorio temporal (``tempfile.TemporaryDirectory``) cuando
  ``carga_minio=True``; en caso contrario quedan en ``./descargas/``.
- Subida a MinIO/S3 con ``subir_archivo_a_minio`` (bucket ``MINIO_BUCKET_LSD``,
  por defecto ``lsd``).
- Errores ARCA con los mensajes públicos de siempre: ``"Captcha detectado"``,
  ``"Se requiere cambiar la clave fiscal"`` y
  ``"No se encuentra habilitado el servicio de Libro de Sueldos Digital"``.
"""

import asyncio
import hashlib
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    open_arca_service,
    wait_page_ready,
)

BOT_NAME = "libro_sueldos_digital"
BOT_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)

LSD_BASE_URL = "https://serviciossegsoc.afip.gob.ar/LibrodeSueldos/app/Contribuyente"
LSD_PERIODOS_URL = f"{LSD_BASE_URL}/LibroSueldos/periodos.aspx"
LSD_SERVICE_NAME = "Libro de Sueldos Digital"

# La paginación del sitio devuelve 5 períodos por página.
PERIODOS_PAGE_SIZE = 10
MAX_PERIODOS_PAGES = 60

NAV_TIMEOUT_MS = 60_000
LS_PAGE_TIMEOUT_MS = 90_000
GENERAR_TIMEOUT_MS = 180_000
DOWNLOAD_TIMEOUT_MS = 180_000

DESCARGAR_BTN_SELECTOR = (
    "input#ctl00_ContentPlaceHolder1_btnDescargarLibro, "
    "input[value*='DESCARGAR LIBRO SUELDOS' i]"
)
GENERAR_BTN_SELECTOR = (
    "input#ctl00_ContentPlaceHolder1_btnReenviarVentanilla2, "
    "input[value*='GENERAR LIBRO SUELDOS' i]"
)
SALIR_BTN_SELECTOR = "input#ctl00_ContentPlaceHolder1_btnSalir"

# Selector de representado: cuando la cuenta representa a más de un CUIT, el
# servicio abre en IndexContribuyente.aspx con este combo y el botón Aceptar.
REPRESENTADO_SELECT_SELECTOR = "select#ddlCUIT, select[name='ddlCUIT']"
REPRESENTADO_ACEPTAR_SELECTOR = "input#btnAceptar, input[name='btnAceptar']"
EMPLEADOR_CUIT_SELECTOR = "#ctl00_lblCuit"
EMPLEADOR_DENOMINACION_SELECTOR = "#ctl00_lblEmpleador"

INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PERIODO_YYYYMM_RE = re.compile(r"^(\d{4})(\d{2})$")
PERIODO_LABEL_RE = re.compile(r"^(\d{2})/(\d{4})$")

# ---------------------------------------------------------------- MinIO/S3 --

minio_url_interna = os.getenv("MINIO_URL_INTERNA")
minio_url_externa = os.getenv("MINIO_URL_EXTERNA")
minio_ssl = os.getenv("MINIO_SSL", "False").lower() == "true"
minio_interno = os.getenv("MINIO_INTERNO", "False").lower() == "true"

minio_bucket_lsd = os.getenv("MINIO_BUCKET_LSD", "lsd")
minio_access_key_lsd = os.getenv("MINIO_MRBOT_ACCESS_KEY")
minio_secret_key_lsd = os.getenv("MINIO_MRBOT_SECRET_KEY")


async def subir_archivo_a_minio(
    *,
    minio_url_interna: Optional[str] = None,
    minio_url_externa: Optional[str] = None,
    minio_ssl: bool = False,
    minio_interno: bool = False,
    minio_access_key: Optional[str] = None,
    minio_secret_key: Optional[str] = None,
    minio_bucket: str = "lsd",
    archivo_a_subir: str,
    nombre_archivo_en_bucket: Optional[str] = None,
) -> str:
    """Sube ``archivo_a_subir`` al bucket y devuelve su URL pública o ``""``.

    Misma firma que ``app.utils.bucket.subir_archivo_a_minio`` del seed de
    producción; en desarrollo usa el SDK de MinIO contra ``MINIO_*``.
    """
    nombre = nombre_archivo_en_bucket or Path(archivo_a_subir).name
    host = (
        (minio_url_interna if minio_interno else minio_url_externa)
        or minio_url_interna
        or minio_url_externa
    )
    if not host or not minio_access_key or not minio_secret_key:
        return ""

    def _subir() -> str:
        from minio import Minio

        cliente = Minio(
            host,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            secure=minio_ssl,
        )
        try:
            if not cliente.bucket_exists(minio_bucket):
                cliente.make_bucket(minio_bucket)
        except Exception:
            # Bucket existente sin permiso de listado: se intenta subir igual.
            pass
        cliente.fput_object(minio_bucket, nombre, archivo_a_subir)
        base = minio_url_externa or host
        esquema = "https" if minio_ssl else "http"
        return f"{esquema}://{base.rstrip('/')}/{minio_bucket}/{nombre}"

    return await asyncio.to_thread(_subir)


# ------------------------------------------------------------------ Helpers --


def _safe_filename(name: str) -> str:
    safe = INVALID_FILENAME_RE.sub("_", (name or "").strip()).strip(" .")
    return safe or "libro_sueldos_digital.pdf"


def _solo_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _parse_periodo(value: str) -> Optional[tuple[int, int]]:
    """Acepta 'YYYYMM' o 'MM/YYYY' y devuelve (anio, mes)."""
    value = (value or "").strip()
    match = PERIODO_YYYYMM_RE.match(value)
    if match:
        anio, mes = int(match.group(1)), int(match.group(2))
    else:
        match = PERIODO_LABEL_RE.match(value)
        if not match:
            return None
        mes, anio = int(match.group(1)), int(match.group(2))
    if not 1 <= mes <= 12:
        return None
    return anio, mes


def _periodo_yyyymm(periodo: tuple[int, int]) -> str:
    return f"{periodo[0]:04d}{periodo[1]:02d}"


def _periodo_label(periodo: tuple[int, int]) -> str:
    return f"{periodo[1]:02d}/{periodo[0]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as archivo:
        for chunk in iter(lambda: archivo.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Cuenta (en DOM) de períodos con sus liquidaciones y controles Ver LS.
LISTAR_PERIODOS_JS = """
() => {
  const periodos = [];
  const labels = document.querySelectorAll(
    'span[id^="ctl00_ContentPlaceHolder1_rptPadre_"][id$="_Label2"]'
  );
  labels.forEach((lbl) => {
    const idx = ((lbl.id || '').match(/rptPadre_(ctl\\d+)_Label2$/) || [])[1];
    if (!idx) return;
    const liqs = [];
    const verls = document.querySelectorAll(
      'input[id^="ctl00_ContentPlaceHolder1_rptPadre_' + idx +
      '_uc_Liquidaciones_rptLiquidaciones_"][id$="_VerLS"]'
    );
    verls.forEach((inp) => {
      const m = (inp.id || '').match(/rptLiquidaciones_(ctl\\d+)_VerLS$/);
      const fila = inp.closest('tr');
      const celdaNro = fila ? fila.querySelector('td[nroliq]') : null;
      const nroTxt = celdaNro ? (celdaNro.getAttribute('nroliq') || '').trim() : '';
      liqs.push({
        nro: nroTxt || (m ? m[1].replace('ctl', '') : ''),
        ctl: m ? m[1] : '',
        id: inp.id,
        title: inp.title || inp.value || '',
        visible: inp.offsetParent !== null,
      });
    });
    periodos.push({
      label: (lbl.innerText || '').trim(),
      element_id: lbl.id,
      visible: lbl.offsetParent !== null,
      liquidaciones: liqs,
    });
  });
  return periodos;
}
"""


async def _iniciar_sesion_arca(
    page: Page,
    cuit_representante: str,
    clave_representante: str,
    error_logs: list[str],
) -> bool:
    login_result = await login_arca(
        page,
        cuit_representante,
        clave_representante,
        error_logs=error_logs,
        click_ver_todos=False,
    )
    if login_result.captcha_detected:
        if "Captcha detectado" not in error_logs:
            error_logs.append("Captcha detectado")
        return False
    if login_result.password_change_required:
        if "Se requiere cambiar la clave fiscal" not in error_logs:
            error_logs.append("Se requiere cambiar la clave fiscal")
        return False
    return bool(login_result.success)


async def _abrir_servicio_lsd(
    page: Page,
    error_logs: list[str],
) -> Optional[Page]:
    service_page = await open_arca_service(
        page,
        service_name=LSD_SERVICE_NAME,
        service_matchers=(re.compile(r"LIBRO\s+DE\s+SUELDOS", re.I),),
        error_logs=error_logs,
        ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
        service_unavailable_message=(
            f"No se encuentra habilitado el servicio de {LSD_SERVICE_NAME}"
        ),
    )
    if service_page is None:
        return None
    try:
        await service_page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(service_page)
    return service_page


async def _ir_a_liquidaciones(page: Page) -> None:
    link = page.get_by_role("link", name=re.compile(r"Liquidaciones\s+y\s+DDJJ", re.I))
    await link.first.click(timeout=30_000)
    try:
        await page.wait_for_url(re.compile(r"periodos\.aspx", re.I), timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(page)

    # El listado pagina de a 1 período por defecto: seleccionamos 5 por página
    # (postback) igual que el flujo manual, para poder recorrer todo el rango.
    try:
        select = page.locator("#ctl00_ContentPlaceHolder1_regxpagina")
        if await select.count() > 0 and (await select.first.input_value()) != "5":
            try:
                async with page.expect_navigation(timeout=30_000):
                    await select.first.select_option("5")
            except PlaywrightTimeoutError:
                pass
            await wait_page_ready(page)
    except Exception:
        pass


async def _leer_empleador(page: Page) -> dict[str, str]:
    empleador = {"cuit": "", "denominacion": ""}
    for clave, selector in (
        ("cuit", EMPLEADOR_CUIT_SELECTOR),
        ("denominacion", EMPLEADOR_DENOMINACION_SELECTOR),
    ):
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                empleador[clave] = (await locator.first.inner_text(timeout=5_000)).strip()
        except Exception:
            continue
    return empleador


async def _seleccionar_representado(
    page: Page,
    cuit_representado: str,
    error_logs: list[str],
) -> Optional[str]:
    """Elige el representado cuando el servicio muestra el selector.

    El servicio abre en ``IndexContribuyente.aspx`` (combo ``ddlCUIT`` +
    botón ``Aceptar``) cuando la cuenta representa a más de un contribuyente.
    Con un solo representado el sitio abre directo en el empleador y esta
    función no interviene.

    Devuelve el CUIT elegido (11 dígitos) o ``None`` si no hubo selector.
    """
    selector = page.locator(REPRESENTADO_SELECT_SELECTOR)
    if await selector.count() == 0:
        return None

    opciones = await selector.first.evaluate(
        "el => Array.from(el.options).map(o => ({"
        "value: (o.value || '').trim(), "
        "label: (o.textContent || '').trim()"
        "}))"
    ) or []
    esperado = _solo_digitos(cuit_representado)
    elegido = ""
    if esperado:
        for opcion in opciones:
            valor = str(opcion.get("value") or "").strip()
            etiqueta = str(opcion.get("label") or "").strip()
            if _solo_digitos(valor) == esperado or _solo_digitos(etiqueta).startswith(esperado):
                elegido = valor or esperado
                break
        if not elegido:
            disponibles = ", ".join(
                f"{_solo_digitos(opcion.get('value'))} ({str(opcion.get('label') or '').strip()})"
                for opcion in opciones
            )
            error_logs.append(
                f"No se encontró el CUIT representado {esperado} en el selector de representados. "
                f"Opciones disponibles: {disponibles or 'sin opciones'}"
            )
            return ""
    else:
        # Sin representado explícito se respeta la preselección del sitio.
        elegido = str((opciones[0] if opciones else {}).get("value") or "").strip()
        if not elegido:
            error_logs.append("El selector de representados no tiene opciones")
            return ""

    await selector.first.select_option(value=elegido)
    aceptar = page.locator(REPRESENTADO_ACEPTAR_SELECTOR)
    await aceptar.first.wait_for(state="visible", timeout=20_000)
    await aceptar.first.click(timeout=30_000)
    # Aceptar deja la sesión en el empleador (Index.aspx): el CUIT del
    # encabezado es la señal de que la selección se aplicó.
    try:
        await page.locator(EMPLEADOR_CUIT_SELECTOR).first.wait_for(
            state="visible", timeout=NAV_TIMEOUT_MS
        )
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(page)
    return _solo_digitos(elegido)


def _verificar_representado(
    empleador: dict[str, str],
    cuit_solicitado: str,
    cuit_seleccionado: Optional[str],
    error_logs: list[str],
) -> bool:
    """Confirma que la sesión quedó posicionada en el representado esperado.

    - Con selector: compara con el CUIT confirmado en ``_seleccionar_representado``.
    - Sin selector: compara con el representado solicitado por parámetro.
    - Siempre exige que la cabecera del empleador (``#ctl00_lblCuit``) informe
      un CUIT válido de 11 dígitos; sin eso no se descarga.
    Sin expectativa (sin selector y sin representado) solo se acepta el
    empleador que informa el sitio.
    """
    actual = _solo_digitos(empleador.get("cuit"))
    if len(actual) != 11:
        error_logs.append(
            "No se pudo confirmar el representado en Libro de Sueldos Digital: "
            "la cabecera del empleador no informó un CUIT válido"
        )
        return False
    esperado = _solo_digitos(cuit_seleccionado or cuit_solicitado)
    if not esperado:
        return True
    if actual != esperado:
        error_logs.append(
            f"El empleador de Libro de Sueldos Digital ({actual}) no coincide "
            f"con el representado solicitado ({esperado})"
        )
        return False
    return True


async def _recolectar_objetivos(
    page: Page,
    desde: tuple[int, int],
    hasta: tuple[int, int],
) -> list[dict[str, Any]]:
    """Recorre las páginas de períodos y devuelve las liquidaciones a descargar."""
    objetivos: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()
    firma_anterior: tuple[str, ...] = ()

    for pagina in range(1, MAX_PERIODOS_PAGES + 1):
        url = f"{LSD_PERIODOS_URL}?p={pagina}&t={PERIODOS_PAGE_SIZE}"
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await wait_page_ready(page)
        periodos = await page.evaluate(LISTAR_PERIODOS_JS) or []

        if not periodos:
            break

        etiquetas: list[tuple[int, int]] = []
        for item in periodos:
            periodo = _parse_periodo(item.get("label", ""))
            if periodo is None:
                continue
            etiquetas.append(periodo)
            if not (desde <= periodo <= hasta):
                continue

            for liq in item.get("liquidaciones") or []:
                if not liq.get("visible"):
                    continue
                # Los ids del repeater (ctl00..ctl04) se repiten en cada
                # página: deduplicamos por período + número de liquidación.
                clave = (
                    _periodo_yyyymm(periodo),
                    str(liq.get("nro") or liq.get("ctl") or ""),
                )
                if clave in vistos:
                    continue
                vistos.add(clave)
                objetivos.append(
                    {
                        "pagina": pagina,
                        "periodo": _periodo_yyyymm(periodo),
                        "periodo_label": _periodo_label(periodo),
                        "liquidacion": liq.get("nro"),
                        "verls_id": liq.get("id"),
                        "verls_title": liq.get("title"),
                    }
                )

        # Si la página repite el contenido de la anterior, llegamos al final
        # del listado (el sitio vuelve a mostrar la última página).
        firma = tuple(sorted(_periodo_yyyymm(p) for p in etiquetas))
        if firma and firma == firma_anterior:
            break
        firma_anterior = firma

        # Los períodos vienen en orden descendente: al pasar el extremo
        # inferior del rango podemos cortar la paginación.
        if etiquetas and min(etiquetas) < desde:
            break

    objetivos.sort(key=lambda o: (o["periodo"], str(o["liquidacion"])))
    return objetivos


async def _esperar_botones_ls(page: Page, timeout_ms: int = LS_PAGE_TIMEOUT_MS) -> str:
    """Espera la página del LS y devuelve 'descargar' o 'generar'."""
    descargar = page.locator(DESCARGAR_BTN_SELECTOR)
    generar = page.locator(GENERAR_BTN_SELECTOR)
    deadline = time.monotonic() + (timeout_ms / 1000)

    while time.monotonic() < deadline:
        if "auth.afip.gob.ar" in (page.url or ""):
            raise RuntimeError("La sesión de ARCA expiró")
        for nombre, locator in (("descargar", descargar), ("generar", generar)):
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return nombre
            except Exception:
                continue
        await asyncio.sleep(0.5)

    raise RuntimeError(
        "No se encontró el botón de descarga/generación del Libro de Sueldos"
    )


async def _volver_con_salir(page: Page) -> None:
    try:
        salir = page.locator(SALIR_BTN_SELECTOR)
        if await salir.count() > 0 and await salir.first.is_visible():
            await salir.first.click(timeout=15_000)
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception:
        pass


async def _descargar_liquidacion(
    page: Page,
    objetivo: dict[str, Any],
    descargas_dir: Path,
) -> Path:
    periodo = objetivo["periodo"]
    liquidacion = str(objetivo.get("liquidacion") or "")

    await page.goto(
        f"{LSD_PERIODOS_URL}?p={objetivo['pagina']}&t={PERIODOS_PAGE_SIZE}",
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS,
    )
    await wait_page_ready(page)

    verls = page.locator(f"input#{objetivo['verls_id']}")
    await verls.first.wait_for(state="visible", timeout=20_000)
    await verls.first.scroll_into_view_if_needed()
    await verls.first.click()

    try:
        estado = await _esperar_botones_ls(page)

        if estado == "generar":
            generar = page.locator(GENERAR_BTN_SELECTOR)
            await generar.first.click()
            await page.locator(DESCARGAR_BTN_SELECTOR).first.wait_for(
                state="visible", timeout=GENERAR_TIMEOUT_MS
            )

        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            await page.locator(DESCARGAR_BTN_SELECTOR).first.click()
        download = await download_info.value

        suggested = download.suggested_filename or f"libro_sueldos_{periodo}_{liquidacion}.pdf"
        destino = descargas_dir / _safe_filename(suggested)
        await download.save_as(str(destino))
        return destino
    finally:
        await _volver_con_salir(page)


async def bot_libro_sueldos_digital(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = "",
    periodo_desde: str = "202501",
    periodo_hasta: str = "202512",
    headless: bool = True,
    carga_minio: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {
        "empleador": {"cuit": "", "denominacion": ""},
        "periodos_solicitados": {"desde": periodo_desde, "hasta": periodo_hasta},
        "periodos_encontrados": [],
        "archivos": [],
    }
    error_logs: list[str] = []
    success = False

    desde = _parse_periodo(periodo_desde)
    hasta = _parse_periodo(periodo_hasta)
    if desde is None or hasta is None or desde > hasta:
        error_logs.append(
            "Rango de períodos inválido: PERIODO_DESDE/PERIODO_HASTA deben ser YYYYMM y desde <= hasta"
        )
        return results, error_logs, False

    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    if carga_minio:
        # Producción: descargas en directorio temporal, subida a MinIO.
        temp_dir = tempfile.TemporaryDirectory(prefix=f"{BOT_NAME}_")
        descargas_dir = Path(temp_dir.name)
    else:
        # Desarrollo local: descargas en ./descargas/{bot}_{timestamp}/.
        descargas_dir = DOWNLOAD_DIR / f"{BOT_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    descargas_dir.mkdir(parents=True, exist_ok=True)

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    try:
        async with async_playwright() as playwright:
            browser, context, page = await launch_arca_browser_context(
                playwright,
                headless=headless,
                context_kwargs={"accept_downloads": True},
            )
            page.set_default_timeout(NAV_TIMEOUT_MS)

            if not await _iniciar_sesion_arca(
                page, cuit_representante, clave_representante, error_logs
            ):
                await context.close()
                await browser.close()
                return results, error_logs, False

            servicio_page = await _abrir_servicio_lsd(page, error_logs)
            if servicio_page is None:
                await context.close()
                await browser.close()
                return results, error_logs, False
            servicio_page.set_default_timeout(NAV_TIMEOUT_MS)

            # Selección de representado (si el servicio la pide) antes de
            # navegar: con una sola relación el sitio abre directo en el
            # empleador y esta interacción no se ejecuta.
            cuit_seleccionado = await _seleccionar_representado(
                servicio_page, cuit_representado, error_logs
            )
            if cuit_seleccionado == "":
                await context.close()
                await browser.close()
                return results, error_logs, False

            empleador = await _leer_empleador(servicio_page)
            results["empleador"] = empleador
            results["representado"] = {
                "cuit_solicitado": _solo_digitos(cuit_representado),
                "cuit_seleccionado": cuit_seleccionado or "",
                "cuit_empleador": _solo_digitos(empleador.get("cuit")),
                "denominacion": empleador.get("denominacion") or "",
            }

            if not _verificar_representado(
                empleador, cuit_representado, cuit_seleccionado, error_logs
            ):
                await context.close()
                await browser.close()
                return results, error_logs, False

            await _ir_a_liquidaciones(servicio_page)

            objetivos = await _recolectar_objetivos(servicio_page, desde, hasta)
            results["periodos_encontrados"] = sorted(
                {objetivo["periodo"] for objetivo in objetivos}
            )
            if not objetivos:
                error_logs.append(
                    f"No se encontraron liquidaciones para el rango {periodo_desde}..{periodo_hasta}"
                )

            for objetivo in objetivos:
                try:
                    destino = await _descargar_liquidacion(
                        servicio_page, objetivo, descargas_dir
                    )
                except Exception as exc:
                    error_logs.append(
                        f"Error al descargar período {objetivo['periodo']} "
                        f"liquidación {objetivo['liquidacion']}: {exc}"
                    )
                    continue

                registro: dict[str, Any] = {
                    "periodo": objetivo["periodo"],
                    "liquidacion": objetivo["liquidacion"],
                    "archivo": destino.name,
                    "sha256": _sha256(destino),
                    "url_minio": None,
                    "object_key": None,
                }
                if carga_minio:
                    try:
                        url = await subir_archivo_a_minio(
                            minio_url_interna=minio_url_interna,
                            minio_url_externa=minio_url_externa,
                            minio_ssl=minio_ssl,
                            minio_interno=minio_interno,
                            minio_access_key=minio_access_key_lsd,
                            minio_secret_key=minio_secret_key_lsd,
                            minio_bucket=minio_bucket_lsd,
                            archivo_a_subir=str(destino),
                            nombre_archivo_en_bucket=destino.name,
                        )
                        registro["url_minio"] = url or None
                        registro["object_key"] = url or None
                    except Exception as exc:
                        error_logs.append(
                            f"Error al subir {destino.name} a MinIO: {exc}"
                        )
                results["archivos"].append(registro)

            await context.close()
            await browser.close()
            browser = None
            context = None

            success = len(results["archivos"]) > 0 and not error_logs
    except Exception as exc:
        error_logs.append(f"Error en bot libro_sueldos_digital: {exc}")
        try:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
        except Exception:
            pass
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    return results, error_logs, success


async def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=BOT_DIR / ".env")
        load_dotenv()
    except Exception:
        pass

    cuit_representante = os.getenv("LSD_CUIT_REPRESENTANTE", "")
    clave = os.getenv("LSD_CLAVE", "")
    cuit_representado = os.getenv("LSD_REPRESENTADO_CUIT", "")
    periodo_desde = os.getenv("PERIODO_DESDE", "202501")
    periodo_hasta = os.getenv("PERIODO_HASTA", "202512")
    headless = os.getenv("LSD_HEADLESS", "False").lower() == "true"
    carga_minio = os.getenv("LSD_CARGA_MINIO", "False").lower() == "true"

    if not cuit_representante or not clave:
        print("Faltan variables de entorno LSD_CUIT_REPRESENTANTE / LSD_CLAVE")
        return

    results, error_logs, success = await bot_libro_sueldos_digital(
        cuit_representante,
        clave,
        cuit_representado=cuit_representado,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        headless=headless,
        carga_minio=carga_minio,
    )
    print("OK" if success else "ERROR")
    representado = results.get("representado") or {}
    if representado:
        print(
            "representado:",
            representado.get("cuit_empleador") or "(sin informar)",
            "-",
            representado.get("denominacion") or "(sin denominación)",
            f"(solicitado={representado.get('cuit_solicitado') or '-'}"
            f", seleccionado={representado.get('cuit_seleccionado') or '-'})",
        )
    print("archivos:", len(results.get("archivos", [])))
    for archivo in results.get("archivos", []):
        print(" -", archivo["archivo"], "->", archivo.get("url_minio") or "(sin subir)")
    if error_logs:
        print("errores:")
        for error in error_logs:
            print(" -", error)


if __name__ == "__main__":
    asyncio.run(main())
