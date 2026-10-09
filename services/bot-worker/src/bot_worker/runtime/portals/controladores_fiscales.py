"""Portal SETI de Presentación DDJJ y Pagos: presentación de archivos fiscales.

Port de ``api-bots-mrbot/app/bot/controladores_fiscales_bot.py`` (líneas
37-294). El plugin V3 abre el servicio con ``portal="controladores_fiscales"``
y llama ``presentar_archivo(archivo, destino)`` por cada archivo del work_dir.

Presentar tiene efecto fiscal, por eso el reintento solo existe ANTES de
pulsar «Presentar» (subida, «Continuar», declaración jurada). Una vez pulsado
el botón no se reintenta nada: se espera el resultado y se descarga la
constancia. Un estado de error o de timeout posterior al efecto se informa
como error sin volver a presentar.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

URL_PRESENTACION = "https://seti.afip.gob.ar/setiweb/#/presentacion/presentacion"

TEXTO_DECLARACION = "Declaro que los datos a transmitir son correctos y completos"
TITULO_CONSTANCIA_EXISTENTE = "Exportar detalle en archivo PDF"
BOTON_CONSTANCIA_NUEVA = "Descargar Constancia"
MAX_INTENTOS_PREVIOS = 3

ESTADO_LISTO = "ready_to_present"
ESTADO_YA_PRESENTADO = "ya_presentado"
ESTADO_PRESENTADO = "presentado_correctamente"
ESTADO_TIMEOUT = "timeout"


def estado_desde_error(texto: str) -> str | None:
    """Detecta la pantalla «Se ha producido un error» y arma ``error|detalle``."""
    lineas = [ln.strip() for ln in str(texto or "").splitlines() if ln.strip()]
    if not any("Se ha producido un error" in ln for ln in lineas):
        return None
    detalle = next((ln for ln in lineas if ln.startswith("* Error")), "Se ha producido un error")
    codigo = ""
    for indice, linea in enumerate(lineas):
        if linea.lower() == "código error" and indice + 1 < len(lineas):
            codigo = lineas[indice + 1]
            break
    if codigo:
        detalle = f"{detalle}; código {codigo}"
    return f"error|{detalle}"


def estado_desde_texto(texto: str) -> str | None:
    """Clasifica el texto visible de la página (puro, sin I/O)."""
    error = estado_desde_error(texto)
    if error is not None:
        return error
    bajo = str(texto or "").lower()
    if "ya ha sido presentada" in bajo or "ya fue presentada" in bajo:
        return ESTADO_YA_PRESENTADO
    if "presentada correctamente" in bajo or "presentación realizada" in bajo:
        return ESTADO_PRESENTADO
    if "transacción" in bajo and "ya ha sido" not in bajo:
        return ESTADO_PRESENTADO
    return None


def estado_desde_modal(texto: str) -> str | None:
    """Clasifica el texto de un modal: ``None`` si no es concluyente."""
    bajo = str(texto or "").lower()
    if "ya ha sido" in bajo or "ya fue presentada" in bajo:
        return ESTADO_YA_PRESENTADO
    if "correctamente" in bajo or "realizada" in bajo:
        return ESTADO_PRESENTADO
    return None


class ControladoresFiscalesPortal(PortalArca):
    """Presentación de archivos F8010/F8011 en SETI y descarga de constancias."""

    nombre = "controladores_fiscales"
    #: espera de la pantalla de carga antes de forzar la navegación
    espera_pantalla_carga_s = 30.0
    #: espera del estado tras «Continuar»
    espera_tras_continuar_s = 180.0
    #: espera del resultado tras «Presentar»
    espera_resultado_s = 120.0
    intervalo_sondeo_s = 0.5
    max_intentos_previos = MAX_INTENTOS_PREVIOS

    # ------------------------------------------------------------------
    # API pública

    async def presentar_archivo(self, archivo: str | Path, destino: str | Path) -> str:
        """Presenta ``archivo`` y guarda la constancia PDF en ``destino``.

        Devuelve ``presentado_correctamente`` o ``ya_presentado``. Cualquier
        otro desenlace se informa como ``TargetUnavailableError`` con un
        ``diagnostic_code`` fijo.
        """
        archivo = Path(archivo)
        destino = Path(destino)
        if not archivo.is_file():
            raise TargetUnavailableError(
                "el archivo a presentar no existe en el work_dir",
                diagnostic_code="controladores_archivo_missing",
            )
        estado = await self._preparar_con_reintentos(archivo)
        if estado == ESTADO_LISTO:
            estado = await self._presentar_y_esperar()
        if estado.startswith("error"):
            raise TargetUnavailableError(
                "el portal rechazó la presentación",
                diagnostic_code="controladores_presentacion_error",
            )
        if estado == ESTADO_TIMEOUT:
            raise TargetUnavailableError(
                "el portal no confirmó la presentación a tiempo",
                diagnostic_code="controladores_presentacion_timeout",
            )
        await self._descargar_constancia(estado, destino)
        return estado

    # ------------------------------------------------------------------
    # Fase previa al efecto (reintentable)

    async def _preparar_con_reintentos(self, archivo: Path) -> str:
        """Sube, continúa y deja lista la declaración. Reintenta solo aquí."""
        ultimo: str | None = None
        ultima_causa: BaseException | None = None
        intentos = max(1, int(self.max_intentos_previos))
        for intento in range(1, intentos + 1):
            try:
                await self._asegurar_pantalla_de_carga()
                await self._subir_archivo(archivo)
                estado = await self._continuar()
                if estado == ESTADO_LISTO:
                    await self._aceptar_declaracion()
                    return ESTADO_LISTO
                if estado == ESTADO_TIMEOUT or estado.startswith("error"):
                    ultimo = estado
                else:
                    # ya presentado o confirmado: no hay botón Presentar.
                    return estado
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - el diagnóstico se normaliza abajo
                ultima_causa = exc
                ultimo = None
            if intento < intentos:
                with contextlib.suppress(Exception):
                    await self._asegurar_pantalla_de_carga()
        if ultimo is not None:
            return ultimo
        raise TargetUnavailableError(
            f"no se pudo preparar la presentación tras {intentos} intentos",
            diagnostic_code="controladores_preparacion_failed",
        ) from ultima_causa

    async def _subir_archivo(self, archivo: Path) -> None:
        page = self.page
        await page.get_by_role("button", name="Buscá en tus archivos").wait_for(
            state="visible", timeout=10_000
        )
        entrada = page.locator("input[type='file']")
        await entrada.wait_for(state="attached", timeout=10_000)
        await entrada.set_input_files(str(archivo))
        await page.get_by_text(archivo.name, exact=True).wait_for(state="visible", timeout=10_000)

    async def _continuar(self) -> str:
        page = self.page
        continuar = page.get_by_role("button", name="Continuar")
        await continuar.wait_for(state="visible", timeout=15_000)
        await continuar.click()
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15_000)
        return await self._esperar_tras_continuar()

    async def _esperar_tras_continuar(self) -> str:
        etiqueta = self.page.locator("label", has_text=TEXTO_DECLARACION).first
        reloj = asyncio.get_running_loop()
        limite = reloj.time() + self.espera_tras_continuar_s
        while True:
            estado = await self._estado_de_pagina()
            if estado is not None:
                return estado
            try:
                if await etiqueta.count() > 0 and await etiqueta.is_visible(timeout=300):
                    return ESTADO_LISTO
            except Exception:
                pass
            if reloj.time() >= limite:
                return ESTADO_TIMEOUT
            await asyncio.sleep(self.intervalo_sondeo_s)

    async def _aceptar_declaracion(self) -> None:
        """Tilda la declaración jurada y espera el botón Presentar (sin pulsarlo)."""
        page = self.page
        etiqueta = page.locator("label", has_text=TEXTO_DECLARACION).first
        await etiqueta.wait_for(state="visible", timeout=15_000)
        casilla = etiqueta.locator("input[type='checkbox']").first
        await casilla.wait_for(state="attached", timeout=15_000)
        if not await casilla.is_checked():
            await casilla.check(force=True)
        await page.get_by_role("button", name="Presentar").wait_for(state="visible", timeout=15_000)

    async def _asegurar_pantalla_de_carga(self) -> None:
        page = self.page
        boton = page.get_by_role("button", name="Buscá en tus archivos")
        reloj = asyncio.get_running_loop()
        limite = reloj.time() + self.espera_pantalla_carga_s
        while reloj.time() < limite:
            try:
                await boton.wait_for(state="visible", timeout=1_000)
                return
            except Exception:
                pass
            if await self._click_si_visible("Volver", timeout_ms=1_000):
                continue
            await self._ir_a_presentacion()
        await self._ir_a_presentacion()
        await boton.wait_for(state="visible", timeout=15_000)

    async def _ir_a_presentacion(self) -> None:
        await self.page.goto(URL_PRESENTACION)
        await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)

    # ------------------------------------------------------------------
    # Efecto y fase posterior (sin reintentos)

    async def _presentar_y_esperar(self) -> str:
        """Pulsa Presentar UNA vez y espera el resultado."""
        page = self.page
        try:
            await page.get_by_role("button", name="Presentar").click()
        except Exception as exc:
            # Estado ambiguo: no se vuelve a presentar.
            raise TargetUnavailableError(
                "no se pudo confirmar el click en Presentar",
                diagnostic_code="controladores_presentar_click_ambiguo",
            ) from exc
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15_000)
        reloj = asyncio.get_running_loop()
        limite = reloj.time() + self.espera_resultado_s
        while True:
            estado = await self._estado_de_pagina()
            if estado is not None:
                return estado
            if reloj.time() >= limite:
                return ESTADO_TIMEOUT
            await asyncio.sleep(self.intervalo_sondeo_s)

    async def _descargar_constancia(self, estado: str, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            destino.unlink()
        page = self.page
        try:
            if estado == ESTADO_YA_PRESENTADO:
                enlace = page.locator(f'a[title="{TITULO_CONSTANCIA_EXISTENTE}"]').first
                await enlace.wait_for(state="visible", timeout=15_000)
                disparador = enlace
            else:
                disparador = page.get_by_role("button", name=BOTON_CONSTANCIA_NUEVA).first
                await disparador.wait_for(state="visible", timeout=15_000)
            await self.capturar_descarga(destino, disparador.click, espera_ms=30_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo descargar la constancia PDF",
                diagnostic_code="controladores_constancia_failed",
            ) from exc

    # ------------------------------------------------------------------
    # Lectura de estado

    async def _texto_visible(self) -> str:
        try:
            return await self.page.locator("body").inner_text(timeout=2_000)
        except Exception:
            return ""

    async def _estado_de_pagina(self) -> str | None:
        estado = estado_desde_texto(await self._texto_visible())
        if estado is not None:
            return estado
        modal = self.page.locator(".modal.show, .modal:visible").last
        try:
            if await modal.count() > 0 and await modal.is_visible(timeout=300):
                texto = (await modal.inner_text(timeout=1_000)).strip()
                estado = estado_desde_modal(texto)
                await self._cerrar_modal()
                return estado
        except Exception:
            pass
        return None

    async def _cerrar_modal(self) -> bool:
        for nombre in ("Entendido", "Aceptar", "Cancelar", "No", "Si", "Cerrar"):
            boton = self.page.get_by_role("button", name=nombre)
            try:
                if await boton.count() > 0 and await boton.first.is_visible(timeout=500):
                    await boton.first.click()
                    await self._esperar(500)
                    return True
            except Exception:
                continue
        return False

    async def _click_si_visible(self, nombre: str | re.Pattern[str], timeout_ms: int = 1_000) -> bool:
        boton = self.page.get_by_role("button", name=nombre)
        try:
            if await boton.count() == 0:
                return False
            await boton.first.wait_for(state="visible", timeout=timeout_ms)
            await boton.first.click()
            await self._esperar(500)
            return True
        except Exception:
            return False


__all__ = ["ControladoresFiscalesPortal", "estado_desde_texto", "estado_desde_error"]
