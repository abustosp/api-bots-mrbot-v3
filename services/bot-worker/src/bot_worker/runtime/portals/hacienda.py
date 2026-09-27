"""Portal de Hacienda y Carne - Liquidación, iniciado desde Mis Comprobantes.

Porta el flujo de ``hacienda_bot.py``: selección de denominación en
Comprobantes en Línea, doble apertura del servicio de Hacienda y consultas
de liquidaciones por emisor o receptor.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, TABLAS_JS

_SERVICIO_HACIENDA = re.compile(r"Hacienda\s+y\s+Carne\s*-\s*Liquidaci", re.I)
_CONSULTA_EMISOR = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Emisor", re.I
)
_CONSULTA_RECEPTOR = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Receptor", re.I
)
_SIGUIENTE = re.compile(r"^\s*(Siguiente|Sig\.?|Next|>|>>|›|»)\s*$", re.I)
_CONSULTAS = {
    "por_emisor": _CONSULTA_EMISOR,
    "por_receptor": _CONSULTA_RECEPTOR,
}


def _normalizar_nombre(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in plano if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", sin_acentos).upper().split())


def _similitud_nombre(esperado: str, encontrado: str) -> float:
    pedido = _normalizar_nombre(esperado)
    candidato = _normalizar_nombre(encontrado)
    if not pedido or not candidato:
        return 0.0
    if pedido == candidato or pedido in candidato:
        return 1.0

    def cobertura(origen: list[str], destino: list[str]) -> float:
        if not origen or not destino:
            return 0.0
        return sum(
            max(SequenceMatcher(None, token, otro).ratio() for otro in destino)
            for token in origen
        ) / len(origen)

    tokens_pedido = pedido.split()
    tokens_candidato = candidato.split()
    dirigida = cobertura(tokens_pedido, tokens_candidato)
    inversa = cobertura(tokens_candidato, tokens_pedido)
    return (
        0.55 * dirigida
        + 0.25 * min(dirigida, inversa)
        + 0.20 * SequenceMatcher(None, pedido, candidato).ratio()
    )


class HaciendaPortal(PortalArca):
    """Acciones de consulta de liquidaciones de hacienda."""

    nombre = "hacienda"

    def _contextos(self) -> list[Any]:
        contextos = [self.page]
        try:
            marco_principal = getattr(self.page, "main_frame", None)
            for marco in self.page.frames:
                if marco is not marco_principal:
                    contextos.append(marco)
        except Exception:
            pass
        return contextos

    async def _texto_candidato(self, candidato: Any) -> str:
        for extractor in (candidato.inner_text, candidato.text_content):
            try:
                texto = " ".join(str(await extractor() or "").split())
            except Exception:
                texto = ""
            if texto:
                return texto
        for atributo in ("aria-label", "title", "value", "name"):
            try:
                texto = str(await candidato.get_attribute(atributo) or "").strip()
            except Exception:
                texto = ""
            if texto:
                return texto
        return ""

    async def _click_candidato(self, candidato: Any) -> bool:
        try:
            await candidato.scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        for opciones in ({"timeout": 8_000}, {"timeout": 8_000, "force": True}):
            try:
                await candidato.click(**opciones)
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return True
            except Exception:
                continue
        try:
            handle = await candidato.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                return True
        except Exception:
            pass
        return False

    async def seleccionar_denominacion(self, denominacion: str) -> None:
        """Selecciona en Mis Comprobantes la denominación solicitada."""
        nombre = " ".join(str(denominacion or "").split())
        if not nombre:
            raise TargetUnavailableError(
                "falta la denominación del representado",
                diagnostic_code="represented_name_missing",
            )
        await self.paso(
            "seleccionar_denominacion", self._seleccionar_denominacion(nombre)
        )

    async def _seleccionar_denominacion(self, nombre: str) -> None:
        patron_exacto = re.compile(rf"^{re.escape(nombre)}$", re.I)
        for contexto in self._contextos():
            for rol, patron, exacto in (
                ("button", nombre, True),
                ("link", nombre, True),
                ("button", patron_exacto, False),
                ("link", patron_exacto, False),
            ):
                try:
                    grupo = contexto.get_by_role(rol, name=patron, exact=exacto)
                    for indice in range(await grupo.count()):
                        if await self._click_candidato(grupo.nth(indice)):
                            return
                except Exception:
                    continue

        candidatos: list[tuple[float, bool, Any]] = []
        for contexto in self._contextos():
            grupos = (
                contexto.get_by_role("button"),
                contexto.get_by_role("link"),
                contexto.locator("[role='button'], button, a, [onclick]"),
            )
            for grupo in grupos:
                try:
                    cantidad = await grupo.count()
                except Exception:
                    continue
                for indice in range(cantidad):
                    elemento = grupo.nth(indice)
                    texto = await self._texto_candidato(elemento)
                    puntaje = _similitud_nombre(nombre, texto)
                    if puntaje < 0.90:
                        continue
                    try:
                        visible = await elemento.is_visible()
                    except Exception:
                        visible = False
                    candidatos.append((puntaje, visible, elemento))

        candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, candidato in candidatos:
            if await self._click_candidato(candidato):
                return
        raise TargetUnavailableError(
            "no se encontró una denominación coincidente en Mis Comprobantes",
            diagnostic_code="represented_name_not_selectable",
        )

    async def _boton_servicio(self) -> Any | None:
        for contexto in self._contextos():
            for rol in ("button", "link"):
                try:
                    grupo = contexto.get_by_role(rol, name=_SERVICIO_HACIENDA)
                    for indice in range(await grupo.count()):
                        candidato = grupo.nth(indice)
                        try:
                            if await candidato.is_visible():
                                return candidato
                        except Exception:
                            continue
                except Exception:
                    continue
        return None

    async def _abrir_servicio(self, boton: Any) -> Any:
        pagina_anterior = self.page
        try:
            async with pagina_anterior.expect_popup(timeout=12_000) as evento:
                await boton.click(timeout=12_000)
            destino = await evento.value
        except Exception as exc:
            if type(exc).__name__ not in {"TimeoutError", "PlaywrightTimeoutError"}:
                raise
            destino = pagina_anterior
        try:
            await destino.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass
        try:
            await destino.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        return destino

    async def abrir_hacienda(
        self, *, servicio: str = "Hacienda y Carne - Liquidacion", denominacion: str
    ) -> "HaciendaPortal":
        """Reproduce la doble apertura V2 para activar la vista de Hacienda."""
        del servicio  # ARCA muestra un único servicio con este nombre.
        return await self.paso(
            "abrir_hacienda", self._abrir_hacienda(denominacion)
        )

    async def _abrir_hacienda(self, denominacion: str) -> "HaciendaPortal":
        primer_boton = await self._boton_servicio()
        if primer_boton is None:
            raise TargetUnavailableError(
                "servicio Hacienda y Carne no visible en Mis Comprobantes",
                diagnostic_code="arca_service_not_visible",
            )
        primera_pagina = await self._abrir_servicio(primer_boton)
        if primera_pagina is not self.page:
            try:
                await primera_pagina.close()
            except Exception:
                pass

        segundo_boton = await self._boton_servicio()
        if segundo_boton is None:
            raise TargetUnavailableError(
                "servicio Hacienda y Carne no visible para la segunda apertura",
                diagnostic_code="arca_service_not_visible",
            )
        segunda_pagina = await self._abrir_servicio(segundo_boton)
        portal = type(self)(
            segunda_pagina,
            captcha=self._captcha,
            context=self._context,
            service_name=self._service_name,
        )
        await portal.seleccionar_denominacion(denominacion)
        await portal._abrir_menu_si_necesario()
        return portal

    async def _abrir_menu_si_necesario(self, *, forzar: bool = False) -> None:
        url = str(getattr(self.page, "url", "") or "")
        if not forzar and not re.search(r"inicioPerfil\.htm|/lsp-web/inicio$", url, re.I):
            return
        try:
            wrapper = self.page.locator("#wrapper")
            clases = str(await wrapper.first.get_attribute("class") or "").lower()
            if "toggled" in clases:
                return
        except Exception:
            pass
        for selector in (
            "#buttonSidebar button.hamburger[data-toggle='offcanvas']",
            "button.hamburger[data-toggle='offcanvas']",
            "button[data-toggle='offcanvas']",
            "button.navbar-toggle",
        ):
            try:
                boton = self.page.locator(selector)
                if await boton.count() and await self._clickear((boton,), total_ms=2_000):
                    await self._esperar(350)
                    return
            except Exception:
                continue

    async def consultar(
        self, *, consulta_key: str, desde: str, hasta: str
    ) -> list[dict[str, Any]]:
        """Consulta liquidaciones por emisor/receptor y devuelve filas tabulares."""
        patron = _CONSULTAS.get(str(consulta_key or "").strip().lower())
        if patron is None:
            raise TargetUnavailableError(
                "tipo de consulta Hacienda no reconocido",
                diagnostic_code="hacienda_query_invalid",
            )
        await self.paso(
            "abrir_seccion_consulta",
            self._abrir_seccion_consulta(consulta_key, patron),
        )
        await self.paso(
            "completar_fechas",
            self._completar_fechas_y_buscar(consulta_key, desde, hasta),
        )
        return await self.paso("leer_resultados", self._leer_resultados(consulta_key))

    async def _buscar_consulta(self, patron: re.Pattern[str]) -> Any | None:
        for contexto in self._contextos():
            try:
                grupo = contexto.get_by_role("link", name=patron)
                if await grupo.count():
                    return grupo.first
            except Exception:
                pass
            try:
                grupo = contexto.locator("a").filter(has_text=patron)
                if await grupo.count():
                    return grupo.first
            except Exception:
                pass
        return None

    async def _abrir_seccion_consulta(
        self, consulta_key: str, patron: re.Pattern[str]
    ) -> None:
        await self._abrir_menu_si_necesario()
        link = await self._buscar_consulta(patron)
        if link is None:
            await self._abrir_menu_si_necesario(forzar=True)
            link = await self._buscar_consulta(patron)
        if link is None:
            raise TargetUnavailableError(
                f"no se encontró la opción de consulta {consulta_key}",
                diagnostic_code="hacienda_query_not_visible",
            )
        try:
            await link.click(timeout=10_000)
        except Exception:
            href = str(await link.get_attribute("href") or "")
            if href and not href.lower().startswith("javascript:"):
                await self.page.goto(urljoin(str(self.page.url or ""), href), timeout=30_000)
            else:
                try:
                    await link.click(timeout=10_000, force=True)
                except Exception:
                    handle = await link.element_handle()
                    if handle is None:
                        raise
                    await handle.evaluate("el => el.click()")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

    async def _primer_localizador(self, constructores: tuple[Any, ...]) -> Any | None:
        for crear in constructores:
            try:
                grupo = crear()
                for indice in range(await grupo.count()):
                    candidato = grupo.nth(indice)
                    try:
                        if await candidato.is_visible():
                            return candidato
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    async def _completar_campo(self, candidatos: tuple[Any, ...], valor: str, campo: str) -> None:
        for contexto in self._contextos():
            localizador = await self._primer_localizador(
                tuple(lambda selector=crear: selector(contexto) for crear in candidatos)
            )
            if localizador is None:
                continue
            try:
                await localizador.click(timeout=2_000)
                await localizador.fill(valor)
                return
            except Exception:
                continue
        raise TargetUnavailableError(
            f"no se encontró el campo {campo} en Hacienda",
            diagnostic_code="hacienda_search_form_missing",
        )

    async def _completar_fechas_y_buscar(
        self, consulta_key: str, desde: str, hasta: str
    ) -> None:
        desde_selectores = (
            lambda c: c.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+desde", re.I)),
            lambda c: c.locator("input[name='fechaStr']"),
            lambda c: c.locator("input[id*='fecha'][id*='desde' i]"),
            lambda c: c.locator("input[placeholder*='desde' i]"),
        )
        hasta_selectores = (
            lambda c: c.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+hasta", re.I)),
            lambda c: c.locator("input[name='fechaHastaStr']"),
            lambda c: c.locator("input[id*='fecha'][id*='hasta' i]"),
            lambda c: c.locator("input[placeholder*='hasta' i]"),
        )
        await self._completar_campo(desde_selectores, desde, "fecha_desde")
        await self._completar_campo(hasta_selectores, hasta, "fecha_hasta")

        buscar_patron = re.compile(r"Buscar|Consultar", re.I)
        encontrado = False
        for contexto in self._contextos():
            candidatos = (
                lambda c=contexto: c.get_by_role("button", name=buscar_patron),
                lambda c=contexto: c.locator("button:has-text('Buscar')"),
                lambda c=contexto: c.locator("a:has-text('Buscar')"),
            )
            boton = await self._primer_localizador(candidatos)
            if boton is None:
                continue
            try:
                await boton.click(timeout=5_000)
                encontrado = True
                break
            except Exception:
                continue
        if not encontrado:
            raise TargetUnavailableError(
                f"no se encontró el botón de búsqueda para {consulta_key}",
                diagnostic_code="hacienda_search_form_missing",
            )
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        await self._seleccionar_maximo_por_pagina()

    async def _seleccionar_maximo_por_pagina(self) -> None:
        for selector in ("#cantPages", "select[id*='cantPages' i]", "select[name='displayLength']"):
            try:
                combo = self.page.locator(selector)
                if await combo.count() == 0:
                    continue
                combo = combo.first
                opciones = combo.locator("option")
                valores: list[tuple[int, str]] = []
                for indice in range(await opciones.count()):
                    opcion = opciones.nth(indice)
                    valor = str(await opcion.get_attribute("value") or "")
                    texto = str(await opcion.inner_text() or "")
                    digitos = re.sub(r"\D", "", valor or texto)
                    if digitos:
                        valores.append((int(digitos), valor or texto))
                if not valores:
                    return
                maximo, valor_maximo = max(valores)
                actual = re.sub(r"\D", "", str(await combo.input_value() or ""))
                if actual != str(maximo):
                    await combo.select_option(valor_maximo)
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=12_000)
                    except Exception:
                        pass
                return
            except Exception:
                continue

    async def _tablas_actuales(self) -> list[dict[str, Any]]:
        tablas: list[dict[str, Any]] = []
        try:
            tablas.extend(await self.tablas())
        except Exception:
            pass
        for contexto in self._contextos()[1:]:
            try:
                datos = await contexto.locator("body").evaluate(TABLAS_JS)
                if datos:
                    tablas.extend(tabla for tabla in datos if isinstance(tabla, dict))
            except Exception:
                continue
        return tablas

    @staticmethod
    def _filas_de_tablas(
        tablas: list[dict[str, Any]], consulta_key: str, pagina: int
    ) -> list[dict[str, Any]]:
        resultado: list[dict[str, Any]] = []
        for tabla in tablas:
            headers = [str(x or "").strip() for x in tabla.get("headers", [])]
            for numero, celdas in enumerate(tabla.get("rows", []), start=1):
                valores = [str(x or "").strip() for x in celdas]
                if not any(valores):
                    continue
                fila: dict[str, Any] = {"consulta": consulta_key, "pagina": pagina}
                usados: dict[str, int] = {}
                for indice, valor in enumerate(valores):
                    base = headers[indice] if indice < len(headers) and headers[indice] else f"col_{indice + 1}"
                    usados[base] = usados.get(base, 0) + 1
                    clave = base if usados[base] == 1 else f"{base}_{usados[base]}"
                    fila[clave] = valor
                resultado.append(fila)
        return resultado

    async def _siguiente_pagina(self) -> bool:
        for contexto in self._contextos():
            grupos = (
                contexto.get_by_role("link", name=_SIGUIENTE),
                contexto.get_by_role("button", name=_SIGUIENTE),
                contexto.locator("li.next:not(.disabled) a"),
                contexto.locator("a[aria-label*='Siguiente'], button[aria-label*='Siguiente']"),
                contexto.locator("a:has-text('Siguiente'), button:has-text('Siguiente')"),
            )
            for grupo in grupos:
                try:
                    for indice in range(await grupo.count()):
                        candidato = grupo.nth(indice)
                        try:
                            if not await candidato.is_visible():
                                continue
                            clase = str(await candidato.get_attribute("class") or "").lower()
                            deshabilitado = str(await candidato.get_attribute("disabled") or "")
                            aria = str(await candidato.get_attribute("aria-disabled") or "").lower()
                            parent = candidato.locator("xpath=..")
                            parent_class = str(await parent.get_attribute("class") or "").lower()
                            if deshabilitado or aria == "true" or "disabled" in clase or "disabled" in parent_class:
                                continue
                            await candidato.click(timeout=5_000)
                            try:
                                await self.page.wait_for_load_state("networkidle", timeout=10_000)
                            except Exception:
                                pass
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
        return False

    async def _leer_resultados(self, consulta_key: str) -> list[dict[str, Any]]:
        filas: list[dict[str, Any]] = []
        firmas_vistas: set[str] = set()
        for pagina in range(1, 101):
            tablas = await self._tablas_actuales()
            actuales = self._filas_de_tablas(tablas, consulta_key, pagina)
            firma = repr(
                [
                    {clave: valor for clave, valor in fila.items() if clave != "pagina"}
                    for fila in actuales
                ]
            )
            if firma and firma in firmas_vistas:
                break
            if firma:
                firmas_vistas.add(firma)
            filas.extend(actuales)
            if not await self._siguiente_pagina():
                break
        return filas
