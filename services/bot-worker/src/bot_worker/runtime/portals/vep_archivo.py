"""Portal SETI de Presentación de DDJJ y Pagos: generar VEP desde archivo.

Port de ``api-bots-mrbot/app/bot/vep_archivo_bot.py``: ``crear_zip`` (:204) y
``generar_vep_desde_archivo`` (:214). El plugin V3 abre el servicio con
``portal="vep_archivo"`` y llama ``generar_vep_desde_archivo(archivo,
medio_pago)`` y ``descargar_detalle(destino)``.

Generar el VEP tiene efecto fiscal: el reintento de V1 (``VEP_RETRY_GENERAR``)
queda acotado a los pasos previos al click final de «Generar VEP». Después de
ese click no se reintenta nada, ni siquiera ante una pantalla de error.

El QR (medio de pago ``qr``) se porta pero queda inactivo: el esquema V3 no
acepta ese medio, así que el plugin nunca llama ``descargar_qr``.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

#: Medios de pago del portal -> id de la imagen a clickear (V1).
MEDIO_PAGO_IMG_IDS: dict[str, str] = {
    "qr": "0",
    "link": "1001",
    "pago_mis_cuentas": "1002",
    "internet_banking": "1003",
    "xn_group": "1005",
}
MAX_REINTENTOS_PREVIOS = 2
MAX_REGISTROS_POR_LOTE = 600


@dataclass(frozen=True)
class ArchivoVEP:
    nombre: str
    contenido: str
    cuit: str
    id_lote: str
    nro_lote: int
    total_lotes: int


def crear_zip(archivos: list[ArchivoVEP]) -> BytesIO:
    """Empaqueta los .txt de VEP en un zip en memoria (solo stdlib)."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for archivo in archivos:
            arcname = f"{archivo.cuit}/lote_{archivo.id_lote}/{archivo.nombre}"
            zipf.writestr(arcname, archivo.contenido)
    buffer.seek(0)
    return buffer


def decodificar_qr(src: str | None) -> bytes | None:
    """Decodifica el ``src`` base64/data-URL de la imagen QR (inactivo en V3)."""
    if not src:
        return None
    src = src.strip()
    if src.startswith("data:"):
        try:
            cabecera, carga = src.split(",", 1)
        except ValueError:
            return None
        if "base64" not in cabecera:
            return None
        src = carga.strip()
    try:
        return base64.b64decode(src)
    except Exception:
        return None


def contar_registros(texto: str) -> int:
    """Cantidad de registros ``02`` del archivo de VEP."""
    return sum(1 for linea in texto.splitlines() if linea.startswith("02"))


def lote_desde_nombre(nombre: str) -> str:
    """``nro.001de003`` del nombre estándar del archivo, o cadena vacía."""
    encontrado = re.search(r"nro\.(\d+de\d+)", nombre)
    return encontrado.group(1) if encontrado else ""


def validar_medio_pago(medio_pago: str) -> str:
    medio = str(medio_pago or "").strip().lower()
    if medio not in MEDIO_PAGO_IMG_IDS:
        raise TargetUnavailableError(
            "medio de pago no habilitado",
            diagnostic_code="vep_medio_pago_invalid",
        )
    return medio


class VepArchivoPortal(PortalArca):
    """Generación de VEP desde archivo .txt y descarga del detalle."""

    nombre = "vep_archivo"
    max_reintentos_previos = MAX_REINTENTOS_PREVIOS
    espera_pantalla_error_s = 1.0

    # ------------------------------------------------------------------
    # Generación

    async def generar_vep_desde_archivo(
        self, archivo: str | Path, medio_pago: str
    ) -> dict[str, Any]:
        """Sube el .txt, genera el VEP y elige el medio de pago.

        Devuelve ``{"lote": str, "registros": int}``.
        """
        medio = validar_medio_pago(medio_pago)
        ruta = Path(archivo)
        if not ruta.is_file():
            raise TargetUnavailableError(
                "el archivo de VEP no existe en el work_dir",
                diagnostic_code="vep_archivo_missing",
            )
        registros = contar_registros(ruta.read_text(encoding="utf-8", errors="replace"))
        await self._aceptar_aviso_inicial()
        await self._generar(ruta)
        await self._cerrar_modal_texto()
        await self.paso("seleccionar_medio_pago", self._elegir_medio_pago(medio))
        return {"lote": lote_desde_nombre(ruta.name), "registros": registros}

    async def _aceptar_aviso_inicial(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=5_000)
        with contextlib.suppress(Exception):
            await self.page.get_by_role("button", name="Aceptar").click(timeout=5_000)

    async def _generar(self, ruta: Path) -> None:
        """Pasos previos (reintentables) y click final sin reintento."""
        page = self.page
        maximo = max(0, int(self.max_reintentos_previos))
        for intento in range(1, maximo + 2):
            try:
                await self._preparar_lote(ruta)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if intento <= maximo:
                    await self._volver_a_inicio()
                    continue
                raise TargetUnavailableError(
                    "no se pudo preparar la generación del VEP",
                    diagnostic_code="vep_preparacion_failed",
                ) from exc
        # Efecto fiscal: un único click, sin reintentos posteriores.
        try:
            await page.get_by_role("button", name="Generar VEP").click(timeout=15_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo confirmar el click final de Generar VEP",
                diagnostic_code="vep_generar_click_ambiguo",
            ) from exc
        await asyncio.sleep(self.espera_pantalla_error_s)
        if await self._es_pantalla_de_error():
            raise TargetUnavailableError(
                "el portal mostró una pantalla de error al generar el VEP",
                diagnostic_code="vep_pantalla_error",
            )

    async def _preparar_lote(self, ruta: Path) -> None:
        page = self.page
        await page.get_by_role("button", name="Generar VEP").click(timeout=5_000)
        await page.get_by_text("description Desde archivo").click(timeout=5_000)
        with contextlib.suppress(Exception):
            await page.get_by_role("button", name="Close").click(timeout=5_000)
        await self._cargar_archivo(ruta)
        # El botón final debe estar listo antes de pulsarlo (fase reintentable).
        await page.get_by_role("button", name="Generar VEP").wait_for(
            state="visible", timeout=15_000
        )

    async def _cargar_archivo(self, ruta: Path) -> None:
        page = self.page
        with contextlib.suppress(Exception):
            await page.wait_for_selector(
                '#VepSeleccionarArchivo input[type="file"]', timeout=5_000, state="attached"
            )
        for selector in (
            '#VepSeleccionarArchivo input[type="file"]',
            'input[type="file"]',
            'input[type="file"][name]',
            'input[type="file"][id]',
            'input[id*="file"]',
            'input[name*="file"]',
        ):
            entrada = page.locator(selector)
            if await entrada.count() > 0:
                await entrada.first.set_input_files(str(ruta))
                return
        with contextlib.suppress(Exception):
            etiqueta = page.locator('label:has-text("Buscá en tus archivos")').first
            if await etiqueta.count() > 0:
                destino_id = await etiqueta.get_attribute("for")
                if destino_id:
                    entrada = page.locator(f"#{destino_id}")
                    if await entrada.count() > 0:
                        await entrada.set_input_files(str(ruta))
                        return
        raise TargetUnavailableError(
            "no se encontró el input de archivo",
            diagnostic_code="vep_input_archivo_missing",
        )

    async def _volver_a_inicio(self) -> None:
        page = self.page
        for accion in (
            lambda: page.get_by_text("close", exact=True).click(timeout=2_000),
            lambda: page.get_by_role("button", name="Close").click(timeout=2_000),
            lambda: page.get_by_role("button", name="Cancelar").click(timeout=2_000),
            lambda: page.get_by_text("Pagos", exact=True).click(timeout=5_000),
        ):
            with contextlib.suppress(Exception):
                await accion()

    async def _es_pantalla_de_error(self) -> bool:
        page = self.page
        with contextlib.suppress(Exception):
            if "#/error" in str(page.url or ""):
                return True
        with contextlib.suppress(Exception):
            if await page.locator("#HttpError").count() > 0:
                return True
        return False

    async def _cerrar_modal_texto(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.get_by_text("close", exact=True).click(timeout=5_000)

    async def _elegir_medio_pago(self, medio: str) -> None:
        page = self.page
        await page.locator(f'img[id="{MEDIO_PAGO_IMG_IDS[medio]}"]').click(timeout=5_000)
        await self._esperar(500)
        with contextlib.suppress(Exception):
            await page.get_by_role("button", name="Aceptar").click(timeout=5_000)

    # ------------------------------------------------------------------
    # Descargas

    async def descargar_detalle(self, destino: str | Path) -> Path:
        """Descarga el PDF del VEP generado en ``destino``."""
        return await self.paso("descargar_detalle", descargar_detalle_vep(self, Path(destino)))

    async def descargar_qr(self, destino: str | Path) -> bool:
        """Guarda el QR del modal «Pagar con QR». Inactivo en V3."""
        return await guardar_qr(self.page, Path(destino))


async def descargar_detalle_vep(portal: PortalArca, destino: Path) -> Path:
    """Descarga el PDF: botón «Descargar VEP» y, si falla, popup «Ver detalle».

    Compartido con ``vep_ccma`` (misma pantalla final del portal).
    """
    page = portal.page
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        return await portal.capturar_descarga(
            destino,
            lambda: page.get_by_role("button", name="Descargar VEP").click(),
            espera_ms=30_000,
        )
    except TargetUnavailableError:
        pass
    try:
        async with page.expect_popup() as info_popup:
            await page.get_by_title("Ver detalle").click()
        popup = await info_popup.value
        try:
            async with popup.expect_download() as info_descarga:
                await popup.get_by_role("button", name="picture_as_pdf").click()
            descarga = await info_descarga.value
            await descarga.save_as(str(destino))
        finally:
            with contextlib.suppress(Exception):
                await popup.close()
    except Exception as exc:
        raise TargetUnavailableError(
            "no se pudo descargar el PDF del VEP",
            diagnostic_code="vep_detalle_download_failed",
        ) from exc
    if not destino.exists() or destino.stat().st_size == 0:
        raise TargetUnavailableError(
            "la descarga del VEP llegó vacía",
            diagnostic_code="portal_download_empty",
        )
    return destino


async def guardar_qr(page: Any, destino: Path) -> bool:
    """Extrae el QR del modal y lo guarda; ``False`` si no está disponible."""
    try:
        modal = page.locator('div.modal:has-text("Pagar con QR")')
        if await modal.count() == 0:
            return False
        imagen = modal.locator("img").first
        if await imagen.count() == 0:
            return False
        datos = decodificar_qr(await imagen.get_attribute("src"))
    except Exception:
        return False
    if not datos:
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    return True
