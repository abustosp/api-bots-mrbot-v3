"""Navegación y consulta del portal MOA de ARCA.

Porta el árbol de empresa y la consulta de declaraciones detalladas desde
``api-bots-mrbot-v2/app/bot/moa_bot.py``. La selección del árbol también abre
la pantalla Declaración Detallada, paso que en V2 vivía en el orquestador.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

URL_DECLARACION_DETALLADA = (
    "https://serviciosadu.afip.gob.ar/DIAV2/MOA.Web/"
    "Moa.Detallada.Web/MoaDetallada/FiltroDeclas"
)


class MoaPortal(PortalArca):
    """Acciones de consulta de despachos aduaneros en MOA."""

    nombre = "moa"

    async def navegar_arbol_empresa(
        self,
        cuit_representado: str,
        tipo_agente: str = "IMEX-IMEX-IMPORTADOR/EXPORT.",
        rol: str = "IMEX-Rol Importador Exportador",
    ) -> None:
        """Selecciona empresa, tipo y rol, luego abre Declaración Detallada."""
        await self._esperar(2_000)
        await self.paso(
            "seleccionar_empresa",
            self._seleccionar_opcion_arbol(0, cuit_representado),
        )
        await self.paso(
            "seleccionar_tipo_agente",
            self._seleccionar_opcion_arbol(1, tipo_agente),
        )
        await self.paso("seleccionar_rol", self._seleccionar_opcion_arbol(2, rol))
        await self.paso("ingresar_arbol", self._ingresar_arbol())
        await self.paso("abrir_declaracion_detallada", self._abrir_declaracion_detallada())

    async def _seleccionar_opcion_arbol(self, indice: int, texto: str) -> None:
        selector = self.page.locator(".select2-container").nth(indice)
        await selector.click(timeout=8_000)
        await self.page.wait_for_selector("li[role='treeitem']", timeout=8_000)
        opciones = self.page.get_by_role("treeitem").filter(has_text=texto)
        if await opciones.count() == 0:
            raise TargetUnavailableError(
                "una opción requerida no está disponible en el árbol MOA",
                diagnostic_code="moa_tree_option_missing",
            )
        await opciones.first.click(timeout=8_000)
        await self._esperar(1_000)

    async def _ingresar_arbol(self) -> None:
        boton = self.page.get_by_role("button", name="Ingresar")
        if not await self._clickear((boton,), total_ms=8_000):
            raise TargetUnavailableError(
                "MOA no permitió ingresar al árbol de empresa",
                diagnostic_code="moa_tree_submit_missing",
            )
        await self._esperar_carga()

    async def _abrir_declaracion_detallada(self) -> None:
        transacciones = self.page.get_by_text("Transacciones", exact=True)
        if not await self._clickear((transacciones,), total_ms=8_000):
            raise TargetUnavailableError(
                "no se encontró el menú Transacciones de MOA",
                diagnostic_code="moa_transactions_menu_missing",
            )
        gestion = self.page.locator("a").filter(
            has_text="GESTION DE LA DECLARACION"
        )
        if not await self._clickear((gestion,), total_ms=8_000):
            raise TargetUnavailableError(
                "no se encontró Gestión de la Declaración en MOA",
                diagnostic_code="moa_declaration_management_missing",
            )
        detalle = self.page.get_by_role(
            "link", name=re.compile(r"Declaracion Detallada", re.I)
        )
        if not await self._clickear((detalle,), total_ms=8_000):
            raise TargetUnavailableError(
                "no se encontró Declaración Detallada en MOA",
                diagnostic_code="moa_detailed_declaration_missing",
            )
        await self._esperar_carga()

    async def scrapear_despacho(self, despacho: str, metodo: str = "url") -> dict[str, Any]:
        """Consulta un despacho y retorna los datos de carátula y presentación.

        ``url`` abre el formulario por su URL directa. ``form`` reutiliza el
        formulario y vuelve dos niveles al terminar, como el flujo heredado.
        """
        despacho = str(despacho).strip()
        if not despacho:
            raise TargetUnavailableError(
                "el identificador del despacho está vacío",
                diagnostic_code="moa_dispatch_empty",
            )
        if metodo not in {"url", "form"}:
            raise TargetUnavailableError(
                "método de consulta MOA no reconocido",
                diagnostic_code="moa_method_invalid",
            )

        resultado: dict[str, Any] = {
            "despacho": despacho,
            "metodo": metodo,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "datos_caratula": {},
            "estado_presentacion": {},
        }
        await self.paso("buscar_despacho", self._buscar_despacho(despacho, metodo))
        await self.paso("abrir_datos_caratula", self._abrir_seccion("DATOS DE LA CARATULA CANAL"))
        resultado["datos_caratula"] = await self.paso(
            "leer_datos_caratula",
            self._extraer_datos_tablas(
                ".cabecera.dga-moa-decla-detallada-caratula"
            ),
        )
        await self.paso("abrir_estado_presentacion", self._abrir_seccion("ESTADO PRESENTACION"))
        resultado["estado_presentacion"] = await self.paso(
            "leer_estado_presentacion",
            self._extraer_datos_tablas(
                ".cabecera-estado.dga-moa-decla-detallada-caratula-estado"
            ),
        )
        if not resultado["datos_caratula"] and not resultado["estado_presentacion"]:
            raise TargetUnavailableError(
                "la declaración MOA no devolvió datos de consulta",
                diagnostic_code="moa_dispatch_details_empty",
            )
        if metodo == "form":
            await self.paso("volver_al_formulario", self._volver_al_formulario())
        return resultado

    async def _buscar_despacho(self, despacho: str, metodo: str) -> None:
        if metodo == "url":
            respuesta = await self.page.goto(URL_DECLARACION_DETALLADA, timeout=45_000)
            await self._esperar_carga()
            estado_http = getattr(respuesta, "status", None)
            if estado_http is not None and estado_http >= 400:
                raise TargetUnavailableError(
                    "el formulario de búsqueda MOA no está disponible",
                    diagnostic_code="moa_search_page_http_error",
                )
        campo = self.page.locator('input[name="declaracion"]')
        if await campo.count() == 0:
            raise TargetUnavailableError(
                "no se encontró el formulario de búsqueda de despachos MOA",
                diagnostic_code="moa_search_form_missing",
            )
        await campo.click(timeout=8_000)
        await campo.fill("")
        await campo.fill(despacho)
        buscar = self.page.get_by_role("button", name="Buscar")
        if not await self._primero_visible((buscar,), total_ms=8_000):
            raise TargetUnavailableError(
                "no se encontró el botón de búsqueda de despachos MOA",
                diagnostic_code="moa_search_button_missing",
            )
        await buscar.click(timeout=8_000)
        enlace = self.page.get_by_role("link", name=despacho)
        if not await self._clickear((enlace,), total_ms=20_000):
            raise TargetUnavailableError(
                "el despacho no apareció en los resultados MOA",
                diagnostic_code="moa_dispatch_not_found",
            )
        await self._esperar_carga()
        await self._esperar(1_000)

    async def _abrir_seccion(self, titulo: str) -> None:
        elemento = self.page.get_by_text(titulo)
        if not await self._clickear((elemento,), total_ms=8_000):
            raise TargetUnavailableError(
                "no se pudo abrir una sección de la declaración MOA",
                diagnostic_code="moa_declaration_section_missing",
            )

    async def _extraer_datos_tablas(self, selector_contenedor: str) -> dict[str, str]:
        """Extrae la primera fila de las tablas detalladas, como hacía V2."""
        await self._esperar(1_000)
        datos: dict[str, str] = {}
        contenedor = self.page.locator(
            f"{selector_contenedor} .tabla-detallada"
        ).first
        if await contenedor.count() == 0:
            return datos
        for tabla in await contenedor.locator("table").all():
            try:
                encabezados = await tabla.locator("thead tr th label").all()
                nombres = [
                    (await celda.inner_text()).replace("\n", " ").strip()
                    for celda in encabezados
                ]
                fila = tabla.locator("tbody tr").first
                valores = [
                    (await celda.inner_text()).strip()
                    for celda in await fila.locator("td").all()
                ]
                for indice, nombre in enumerate(nombres):
                    if nombre and indice < len(valores):
                        datos[nombre] = valores[indice]
            except Exception:
                # Un panel sin filas útiles no debe abortar la lectura del
                # otro panel, igual que en el scraper heredado.
                continue
        return datos

    async def _volver_al_formulario(self) -> None:
        volver = self.page.get_by_role("button", name="Volver")
        for _ in range(2):
            if not await self._clickear((volver,), total_ms=5_000):
                raise TargetUnavailableError(
                    "MOA no permitió volver al formulario de búsqueda",
                    diagnostic_code="moa_back_button_missing",
                )
            await self._esperar(300)
        campo = self.page.locator('input[name="declaracion"]')
        if not await self._primero_visible((campo,), total_ms=15_000):
            raise TargetUnavailableError(
                "MOA no volvió al formulario de búsqueda",
                diagnostic_code="moa_back_form_missing",
            )

    async def _esperar_carga(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            # Algunas pantallas MOA mantienen conexiones asíncronas abiertas.
            pass
