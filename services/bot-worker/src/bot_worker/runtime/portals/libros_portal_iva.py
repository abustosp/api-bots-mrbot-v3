"""Portal IVA para descargar libros y declaraciones juradas (DDJJ).

Port de ``api-bots-mrbot/app/bot/libros_portal_iva_bot.py`` (V1/V2).
La apertura de la URL del Portal IVA y la sesión fiscal las realiza
``ArcaSession``; esta clase implementa la selección del representado y las
descargas de los periodos solicitados por el plugin V3.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_DDJJ_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores"


class LibrosPortalIvaPortal(PortalArca):
    """Acciones de consulta y descarga del Portal IVA."""

    nombre = "libros_portal_iva"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Usa primero los selectores comunes y luego el switcher del Portal IVA."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        try:
            await super().seleccionar_representado(cuit)
            return
        except TargetUnavailableError as error_original:
            # El Portal IVA usa una SPA con un selector de relación propio. En
            # ciertos perfiles no aparece como combo ni como link directo.
            objetivo = solo_digitos(cuit)
            formateado = self._formatear_cuit(objetivo)
            cambio = await self._clickear(
                (
                    self.page.get_by_title("cambio relacion"),
                    self.page.get_by_role("link", name="\uf0c0"),
                    self.page.locator("[title*='cambio relacion' i]"),
                ),
                total_ms=4_000,
            )
            if cambio:
                await self._esperar(1_500)
                try:
                    await self.page.wait_for_url("**/changeRelation**", timeout=5_000)
                except Exception:
                    pass
                await self._esperar(1_000)
            elegido = await self._clickear(
                (
                    self.page.locator("button.list-group-item").filter(has_text=formateado),
                    self.page.locator("a.list-group-item").filter(has_text=formateado),
                    self.page.get_by_role("button", name=re.compile(re.escape(formateado))),
                    self.page.get_by_text(formateado, exact=False),
                    self.page.get_by_text(objetivo, exact=False),
                ),
                total_ms=6_000,
            )
            if not elegido:
                await self._clickear(
                    (
                        self.page.get_by_title("Representar a..."),
                        self.page.get_by_text("Representar a...", exact=False),
                    ),
                    total_ms=2_000,
                )
                elegido = await self._clickear(
                    (
                        self.page.locator("button.list-group-item").filter(has_text=formateado),
                        self.page.locator("a.list-group-item").filter(has_text=formateado),
                        self.page.get_by_role("button", name=re.compile(re.escape(formateado))),
                        self.page.get_by_text(formateado, exact=False),
                        self.page.get_by_text(objetivo, exact=False),
                    ),
                    total_ms=5_000,
                )
            if not elegido:
                raise error_original
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            await self._esperar(2_000)

    async def descargar_periodo(
        self, *, operacion: str, periodo: str, destino: Path
    ) -> str:
        """Descarga un periodo. Libros se agrupan en ZIP; DDJJ es un PDF."""
        periodo = str(periodo or "").strip()
        if not re.fullmatch(r"\d{6}", periodo) or not 1 <= int(periodo[4:]) <= 12:
            raise TargetUnavailableError(
                "periodo inválido para Portal IVA",
                diagnostic_code="libros_period_invalid",
            )
        if operacion == "descargar_libros":
            return await self.descargar_libros(periodo=periodo, destino=Path(destino))
        if operacion == "descargar_ddjj":
            return await self.descargar_ddjj(periodo=periodo, destino=Path(destino))
        raise TargetUnavailableError(
            "operación desconocida para Portal IVA",
            diagnostic_code="libros_operation_unknown",
        )

    async def descargar_libros(self, *, periodo: str, destino: Path) -> str:
        """Descarga Vista Previa, Acuse y Presentación de cada libro del mes.

        El contrato V3 recibe un archivo por periodo, por eso reúne los
        documentos descargados (ZIP de la vista previa y PDFs restantes) en un
        ZIP bajo el directorio temporal del artifact store.
        """
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        await self._abrir_listado_libros()
        filas = await self._filas_periodo(periodo, columna_periodo=2)
        if not filas:
            raise TargetUnavailableError(
                "no hay libros IVA para el periodo solicitado",
                diagnostic_code="libros_period_not_found",
            )

        cantidad = 0
        with tempfile.TemporaryDirectory(
            prefix=".libros_iva_partes_", dir=str(destino.parent)
        ) as temporal:
            carpeta = Path(temporal)
            archivos: list[Path] = []
            for fila_num, fila in enumerate(filas, start=1):
                await self._abrir_listado_libros()
                filas_actuales = await self._filas_periodo(periodo, columna_periodo=2)
                fila_actual = next(
                    (
                        item for item in filas_actuales
                        if item["secuencia"] == fila["secuencia"]
                        and item["ocurrencia"] == fila["ocurrencia"]
                    ),
                    None,
                )
                if fila_actual is None:
                    continue
                await self._abrir_fila_libro(fila_actual)
                secuencia = self._nombre_seguro(fila["secuencia"] or f"libro_{fila_num}")
                descargas = (
                    ("Vista_Previa", ".zip", True, "#btnDescargarVistaPrevia"),
                    ("Acuse", ".pdf", False, "#btnDescargarAcuse"),
                    ("Presentacion", ".pdf", False, "#btnDescargarPresentacion"),
                )
                for etiqueta, extension, popup, selector in descargas:
                    salida = carpeta / f"{periodo}_{secuencia}_{etiqueta}{extension}"
                    try:
                        await self._capturar_boton_descarga(
                            selector, salida, puede_abrir_popup=popup
                        )
                        archivos.append(salida)
                    except TargetUnavailableError:
                        # El portal puede no tener alguno de los documentos
                        # secundarios. Se conserva todo lo que sí entregó.
                        continue
                await self._volver_inicio()

            if not archivos:
                raise TargetUnavailableError(
                    "el Portal IVA no entregó documentos del libro",
                    diagnostic_code="libros_download_missing",
                )
            with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
                for archivo in archivos:
                    paquete.write(archivo, arcname=archivo.name)
                    cantidad += 1

        if cantidad == 0 or not destino.exists() or destino.stat().st_size == 0:
            raise TargetUnavailableError(
                "el archivo del libro IVA llegó vacío",
                diagnostic_code="libros_download_empty",
            )
        return destino.name

    async def descargar_ddjj(self, *, periodo: str, destino: Path) -> str:
        """Descarga como PDF la DDJJ presentada en el periodo indicado."""
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        await self._abrir_listado_ddjj()
        filas = await self._filas_periodo(periodo, columna_periodo=1)
        if not filas:
            raise TargetUnavailableError(
                "no hay DDJJ para el periodo solicitado",
                diagnostic_code="ddjj_period_not_found",
            )
        fila = filas[0]
        tabla = self.page.locator("table").nth(fila["tabla"])
        filas_tabla = tabla.locator("tbody tr")
        fila_locator = filas_tabla.nth(fila["fila"])
        boton = fila_locator.get_by_title("Descargar formulario")
        if await boton.count() == 0:
            boton = fila_locator.get_by_title("Ver declaración jurada")
        if await boton.count() == 0:
            boton = fila_locator.locator(
                '[title="Descargar formulario"], [title="Ver declaración jurada"]'
            )
        if await boton.count() == 0:
            raise TargetUnavailableError(
                "la DDJJ no ofrece una acción de descarga",
                diagnostic_code="ddjj_download_button_missing",
            )

        await self._capturar_ddjj(boton.first, destino)
        if not destino.exists() or destino.stat().st_size == 0:
            raise TargetUnavailableError(
                "la descarga de DDJJ llegó vacía",
                diagnostic_code="ddjj_download_empty",
            )
        return destino.name

    # ------------------------------------------------------------------
    # Navegación y lectura de listados

    async def _ir_a_inicio(self) -> None:
        if "/init" in await self._url():
            return
        await self.abrir_url(PORTAL_IVA_INIT_URL)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await self._esperar(1_000)

    async def _abrir_listado_libros(self) -> None:
        if "/livas.anteriores" in await self._url():
            return
        await self._ir_a_inicio()
        consultar = await self._clickear(
            (
                self.page.get_by_label(
                    "Sin texto (iva.home.btn.declaraciones.anteriores.alt)"
                ),
                self.page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
            ),
            total_ms=15_000,
        )
        if not consultar:
            raise TargetUnavailableError(
                "Portal IVA no mostró Consultar declaraciones anteriores",
                diagnostic_code="libros_consultar_missing",
            )
        await self._esperar(1_000)
        abierto = await self._clickear(
            (
                self.page.get_by_label(
                    "Sin texto (iva.home.btn.declaraciones.anteriores.LibroIVA.alt)"
                ),
                self.page.locator("button").filter(has_text=re.compile(r"Libro IVA", re.I)),
            ),
            total_ms=15_000,
        )
        if not abierto:
            raise TargetUnavailableError(
                "Portal IVA no mostró Libro IVA",
                diagnostic_code="libros_menu_missing",
            )
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await self._esperar(1_500)
        if "/livas.anteriores" not in await self._url():
            raise TargetUnavailableError(
                "no se pudo abrir el listado de libros IVA",
                diagnostic_code="libros_list_navigation_failed",
            )

    async def _abrir_listado_ddjj(self) -> None:
        if "declaraciones.anteriores" in await self._url():
            return
        await self._ir_a_inicio()
        consultar = await self._clickear(
            (
                self.page.get_by_label(
                    "Sin texto (iva.home.btn.declaraciones.anteriores.alt)"
                ),
                self.page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
            ),
            total_ms=12_000,
        )
        if consultar:
            await self._esperar(1_000)
            abierto = await self._clickear(
                (
                    self.page.get_by_label(
                        "Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)"
                    ),
                    self.page.locator("button").filter(
                        has_text=re.compile(r"Portal|Declaraciones Juradas", re.I)
                    ),
                ),
                total_ms=8_000,
            )
        else:
            abierto = False
        if not abierto:
            await self.abrir_url(PORTAL_DDJJ_URL)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await self._esperar(1_500)
        if "declaraciones.anteriores" not in await self._url():
            raise TargetUnavailableError(
                "no se pudo abrir el listado de DDJJ anteriores",
                diagnostic_code="ddjj_list_navigation_failed",
            )

    async def _volver_inicio(self) -> None:
        with contextlib.suppress(Exception):
            await self.abrir_url(PORTAL_IVA_INIT_URL)
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
            await self._esperar(1_000)

    async def _filas_periodo(
        self, periodo: str, *, columna_periodo: int
    ) -> list[dict[str, Any]]:
        """Encuentra filas que corresponden al periodo (columna 0-based)."""
        periodo_portal = f"{periodo[4:]}/{periodo[:4]}"
        resultado: list[dict[str, Any]] = []
        ocurrencias: dict[str, int] = {}
        tablas = self.page.locator("table")
        for indice_tabla in range(min(await tablas.count(), 300)):
            tabla = tablas.nth(indice_tabla)
            filas = tabla.locator("tbody tr")
            for indice_fila in range(await filas.count()):
                celdas = filas.nth(indice_fila).locator("td")
                if await celdas.count() < 5:
                    continue
                texto_periodo = (await celdas.nth(columna_periodo).inner_text()).strip()
                if texto_periodo != periodo_portal:
                    continue
                secuencia = (await celdas.nth(3).inner_text()).strip()
                ocurrencia = ocurrencias.get(secuencia, 0)
                ocurrencias[secuencia] = ocurrencia + 1
                resultado.append(
                    {
                        "tabla": indice_tabla,
                        "fila": indice_fila,
                        "secuencia": secuencia,
                        "ocurrencia": ocurrencia,
                    }
                )
        return resultado

    async def _abrir_fila_libro(self, fila: dict[str, Any]) -> None:
        filas = self.page.locator("table").nth(fila["tabla"]).locator("tbody tr")
        fila_locator = filas.nth(fila["fila"])
        boton = fila_locator.get_by_title("Ver")
        if await boton.count() == 0:
            boton = fila_locator.locator('[title="Ver"]')
        if await boton.count() == 0:
            raise TargetUnavailableError(
                "el libro no ofrece una acción para abrirlo",
                diagnostic_code="libros_view_button_missing",
            )
        await boton.first.click(timeout=15_000)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await self._esperar(1_500)

    # ------------------------------------------------------------------
    # Descargas

    async def _capturar_boton_descarga(
        self,
        selector: str,
        destino: Path,
        *,
        puede_abrir_popup: bool = False,
    ) -> Path:
        boton = self.page.locator(selector)
        if await boton.count() == 0:
            # Variantes del portal cambian el ID pero mantienen el rótulo.
            etiqueta = {
                "#btnDescargarVistaPrevia": "Vista Previa",
                "#btnDescargarAcuse": "Acuse",
                "#btnDescargarPresentacion": "Presentación",
            }.get(selector, "Descargar")
            boton = self.page.get_by_role("button", name=re.compile(etiqueta, re.I))
        if await boton.count() == 0:
            raise TargetUnavailableError(
                "el Portal IVA no mostró uno de los botones de descarga",
                diagnostic_code="libros_download_button_missing",
            )
        popup_task = None
        if puede_abrir_popup:
            popup_task = asyncio.create_task(
                self.page.wait_for_event("popup", timeout=8_000)
            )
        try:
            await self.capturar_descarga(
                Path(destino),
                lambda: boton.first.click(timeout=15_000),
                espera_ms=45_000,
            )
        finally:
            if popup_task is not None:
                if popup_task.done():
                    with contextlib.suppress(Exception):
                        popup = popup_task.result()
                        await popup.close()
                else:
                    popup_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await popup_task
        return Path(destino)

    async def _capturar_ddjj(self, boton: Any, destino: Path) -> None:
        """Acepta tanto descarga directa como popup de visualización de DDJJ."""
        url_anterior = await self._url()
        popup_task = asyncio.create_task(self.page.wait_for_event("popup", timeout=8_000))
        download_task = asyncio.create_task(
            self.page.wait_for_event("download", timeout=35_000)
        )
        popup = None
        try:
            await boton.click(timeout=15_000)
            pendientes = {popup_task, download_task}
            hecho, pendientes = await asyncio.wait(
                pendientes, timeout=10, return_when=asyncio.FIRST_COMPLETED
            )
            popup = None
            descarga = None
            for tarea in hecho:
                with contextlib.suppress(Exception):
                    resultado = tarea.result()
                    if tarea is popup_task:
                        popup = resultado
                    else:
                        descarga = resultado
            if descarga is None and popup is None and pendientes:
                hecho, pendientes = await asyncio.wait(
                    pendientes, timeout=25, return_when=asyncio.FIRST_COMPLETED
                )
                for tarea in hecho:
                    with contextlib.suppress(Exception):
                        resultado = tarea.result()
                        if tarea is popup_task:
                            popup = resultado
                        else:
                            descarga = resultado
            if descarga is not None:
                await descarga.save_as(str(destino))
                if destino.exists() and destino.stat().st_size > 0:
                    if not destino.read_bytes().startswith(b"%PDF"):
                        raise TargetUnavailableError(
                            "el archivo descargado de DDJJ no es un PDF",
                            diagnostic_code="ddjj_pdf_invalid",
                        )
                    return
                raise TargetUnavailableError(
                    "la DDJJ llegó vacía", diagnostic_code="ddjj_download_empty"
                )

            pagina_pdf = popup or self.page
            try:
                await pagina_pdf.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            await self._esperar(2_000)
            visualizar = pagina_pdf.get_by_text(
                re.compile(r"visualizar formulario", re.I), exact=False
            )
            if await visualizar.count() > 0:
                await visualizar.first.click(timeout=10_000)
                try:
                    await pagina_pdf.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    pass
                await self._esperar(2_000)
            if popup is None and await self._url() == url_anterior:
                raise TargetUnavailableError(
                    "el Portal IVA no abrió la DDJJ seleccionada",
                    diagnostic_code="ddjj_document_not_opened",
                )
            pdf = await pagina_pdf.pdf(print_background=True)
            Path(destino).write_bytes(pdf)
            if not Path(destino).read_bytes().startswith(b"%PDF"):
                raise TargetUnavailableError(
                    "el documento abierto no es un PDF válido",
                    diagnostic_code="ddjj_pdf_invalid",
                )
        except TargetUnavailableError:
            raise
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo materializar el documento DDJJ",
                diagnostic_code="ddjj_pdf_failed",
            ) from exc
        finally:
            for tarea in (popup_task, download_task):
                if not tarea.done():
                    tarea.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tarea
            if popup is not None:
                with contextlib.suppress(Exception):
                    await popup.close()

    @staticmethod
    def _formatear_cuit(cuit: str) -> str:
        digitos = solo_digitos(cuit)
        if len(digitos) == 11:
            return f"{digitos[:2]}-{digitos[2:10]}-{digitos[10]}"
        return digitos

    @staticmethod
    def _nombre_seguro(valor: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", str(valor or "")).strip("._") or "libro"
