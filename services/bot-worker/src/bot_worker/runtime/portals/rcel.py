"""Portal de Comprobantes en Línea (RCEL): selección y descarga de facturas.

Port de ``descargar_facturas`` de V1/V2. La selección prioriza la
denominación ``nombre_rcel`` y valida la empresa activa contra el CUIT y/o el
nombre esperados antes de consultar comprobantes.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

URL_RCEL = "https://fe.afip.gob.ar/rcel/jsp/index_bis.jsp"
TITULO_PDF = "Ver el documento original para imprimir o reimprimir"


def _normalizar_nombre(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in plano if not unicodedata.combining(c))
    tokens = re.sub(r"[^A-Za-z0-9]+", " ", sin_acentos).upper().split()
    normalizados: list[str] = []
    indice = 0
    while indice < len(tokens):
        if tokens[indice : indice + 3] == ["S", "R", "L"]:
            normalizados.append("SRL")
            indice += 3
        elif tokens[indice : indice + 3] == ["S", "A", "S"]:
            normalizados.append("SAS")
            indice += 3
        elif tokens[indice : indice + 2] == ["S", "A"]:
            normalizados.append("SA")
            indice += 2
        else:
            normalizados.append(tokens[indice])
            indice += 1
    return " ".join(normalizados)


def _similitud_nombre(esperado: str, encontrado: str) -> float:
    """Puntúa nombres incluso cuando el portal añade el CUIT al botón."""
    pedido = _normalizar_nombre(esperado)
    candidato = _normalizar_nombre(encontrado)
    if not pedido or not candidato:
        return 0.0
    if pedido == candidato or pedido in candidato:
        return 1.0
    tokens_pedido = pedido.split()
    tokens_candidato = candidato.split()

    def cobertura(origen: list[str], destino: list[str]) -> float:
        if not origen or not destino:
            return 0.0
        return sum(
            max(SequenceMatcher(None, token, otro).ratio() for otro in destino)
            for token in origen
        ) / len(origen)

    dirigida = cobertura(tokens_pedido, tokens_candidato)
    inversa = cobertura(tokens_candidato, tokens_pedido)
    return 0.55 * dirigida + 0.25 * min(dirigida, inversa) + 0.20 * SequenceMatcher(
        None, pedido, candidato
    ).ratio()


class RcelPortal(PortalArca):
    """Acciones de consulta y descarga de Comprobantes en Línea."""

    nombre = "rcel"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._cuit_representado = ""
        self._nombre_representado = ""

    def _contextos(self) -> list[Any]:
        contextos = [self.page]
        try:
            for frame in self.page.frames:
                if frame is not getattr(self.page, "main_frame", None):
                    contextos.append(frame)
        except Exception:
            pass
        return contextos

    async def seleccionar_representado(
        self, cuit: str, nombre_rcel: str = ""
    ) -> None:
        """Selecciona por denominación, con respaldo por CUIT y botones.

        ``nombre_rcel`` es el nombre declarado por la petición V1/V2. En V3
        llega como ``representado_nombre``. El encabezado del portal valida la
        selección para evitar continuar con una empresa distinta.
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido", diagnostic_code="represented_cuit_invalid"
            )
        nombre = str(nombre_rcel or "").strip()
        if not nombre:
            raise TargetUnavailableError(
                "falta la denominación del representado",
                diagnostic_code="rcel_company_name_missing",
            )
        self._cuit_representado = digits
        self._nombre_representado = nombre
        await self.paso("seleccionar_empresa", self._seleccionar_empresa(digits, nombre))

    async def _seleccionar_empresa(self, cuit: str, nombre: str) -> None:
        if await self._seleccionar_por_nombre(nombre) and await self._verificar_empresa(
            cuit, nombre
        ):
            return

        await self._volver_a_seleccion()
        if await self._seleccionar_por_cuit(cuit) and await self._verificar_empresa(
            cuit, nombre
        ):
            return

        await self._volver_a_seleccion()
        if await self._iterar_empresas(cuit, nombre):
            return
        raise TargetUnavailableError(
            "no se encontró la empresa RCEL solicitada por denominación o CUIT",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def _seleccionar_por_nombre(self, nombre: str) -> bool:
        candidatos: list[tuple[float, bool, Any]] = []
        for contexto in self._contextos():
            grupos = (
                contexto.get_by_role("button"),
                contexto.get_by_role("link"),
                contexto.locator(
                    "input.btn_empresa, button.btn_empresa, [class*='btn_empresa'], "
                    "input[type='button'][class*='ui-button']"
                ),
            )
            for grupo in grupos:
                try:
                    cantidad = await grupo.count()
                except Exception:
                    continue
                for indice in range(cantidad):
                    elemento = grupo.nth(indice)
                    texto = await self._texto_candidato(elemento)
                    if not texto:
                        continue
                    score = _similitud_nombre(nombre, texto)
                    if score >= 0.90:
                        try:
                            visible = await elemento.is_visible()
                        except Exception:
                            visible = False
                        candidatos.append((score, visible, elemento))

        candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, candidato in candidatos:
            if await self._click_candidato(candidato):
                await self._esperar_carga()
                return True
        return False

    async def _texto_candidato(self, elemento: Any) -> str:
        for extractor in (elemento.inner_text, elemento.text_content):
            try:
                texto = " ".join(str(await extractor() or "").split())
            except Exception:
                texto = ""
            if texto:
                return texto
        for atributo in ("aria-label", "title", "value", "name"):
            try:
                texto = str(await elemento.get_attribute(atributo) or "").strip()
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
        for opciones in ({"timeout": 5_000}, {"timeout": 5_000, "force": True}):
            try:
                await candidato.click(**opciones)
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

    async def _leer_empresa_encabezado(self) -> tuple[str, str]:
        for contexto in self._contextos():
            try:
                tabla = contexto.locator("table#encabezado_usuario")
                if await tabla.count() == 0:
                    tabla = contexto.locator("table[id*='encabezado']")
                filas = tabla.locator("tr")
                for indice in range(await filas.count()):
                    texto = await filas.nth(indice).inner_text()
                    if "representando" not in (texto or "").lower():
                        continue
                    for linea in texto.splitlines():
                        coincidencia = re.search(r"(\d{11})\s*-\s*(.*)", linea.strip())
                        if coincidencia:
                            return coincidencia.group(1), coincidencia.group(2).strip()
                    coincidencia = re.search(r"\d{11}", texto)
                    if coincidencia:
                        return coincidencia.group(0), ""
            except Exception:
                continue
        return "", ""

    async def _verificar_empresa(self, cuit: str, nombre: str) -> bool:
        cuit_activo, nombre_activo = await self._leer_empresa_encabezado()
        if cuit_activo:
            return solo_digitos(cuit_activo) == cuit
        return bool(nombre_activo and _similitud_nombre(nombre, nombre_activo) >= 0.90)

    async def _seleccionar_por_cuit(self, cuit: str) -> bool:
        for contexto in self._contextos():
            try:
                combos = contexto.locator("select")
                for indice in range(await combos.count()):
                    combo = combos.nth(indice)
                    opciones = combo.locator("option")
                    for posicion in range(await opciones.count()):
                        opcion = opciones.nth(posicion)
                        valor = str(await opcion.get_attribute("value") or "")
                        etiqueta = str(await opcion.text_content() or "")
                        if cuit not in {solo_digitos(valor), solo_digitos(etiqueta)}:
                            continue
                        if valor:
                            await combo.select_option(value=valor)
                        else:
                            await combo.select_option(label=etiqueta)
                        await self._esperar_carga()
                        return True
            except Exception:
                continue
        return False

    async def _iterar_empresas(self, cuit: str, nombre: str) -> bool:
        for contexto in self._contextos():
            try:
                botones = contexto.locator(
                    "input.btn_empresa, button.btn_empresa, [class*='btn_empresa'], "
                    "input[type='button'][class*='ui-button']"
                )
                cantidad = await botones.count()
            except Exception:
                continue
            for indice in range(cantidad):
                if indice:
                    await self._volver_a_seleccion()
                candidato = botones.nth(indice)
                if not await self._click_candidato(candidato):
                    continue
                await self._esperar_carga()
                if await self._verificar_empresa(cuit, nombre):
                    return True
        return False

    async def _volver_a_seleccion(self) -> None:
        patrones = (
            re.compile(r"Volver", re.I),
            re.compile(r"Seleccionar\s+otro", re.I),
            re.compile(r"Cambiar\s+contribuyente", re.I),
            re.compile(r"Seleccionar\s+contribuyente", re.I),
        )
        for contexto in self._contextos():
            for patron in patrones:
                for tipo in ("button", "link"):
                    try:
                        localizador = contexto.get_by_role(tipo, name=patron)
                        if await localizador.count() and await self._click_candidato(
                            localizador.first
                        ):
                            await self._esperar_carga()
                            return
                    except Exception:
                        continue
        try:
            await self.page.goto(URL_RCEL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass
        await self._esperar_carga()

    async def _esperar_carga(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

    async def descargar_facturas(
        self, desde: str, hasta: str, destino_dir: Path
    ) -> list[str]:
        """Consulta el rango y guarda cada comprobante PDF en ``destino_dir``."""
        destino = Path(destino_dir)
        destino.mkdir(parents=True, exist_ok=True)
        await self.paso("consultas", self._abrir_consultas())
        await self.paso("rango_fechas", self._configurar_rango(desde, hasta))
        await self.paso("buscar", self._buscar())
        return await self.paso("descargar_pdfs", self._descargar_pdfs(destino))

    async def _accion(self, nombre: str) -> Any | None:
        patron = re.compile(rf"^{re.escape(nombre)}$", re.I)
        localizadores: list[Any] = []
        for contexto in self._contextos():
            localizadores.extend(
                (
                    contexto.get_by_role("button", name=patron),
                    contexto.get_by_role("link", name=patron),
                    contexto.locator(f"button:has-text('{nombre}'), a:has-text('{nombre}')"),
                )
            )
        return await self._primero_visible(localizadores, total_ms=8_000)

    async def _abrir_consultas(self) -> None:
        consultas = await self._accion("Consultas")
        if consultas is None:
            raise TargetUnavailableError(
                "no se encontró el menú Consultas de RCEL",
                diagnostic_code="rcel_consultas_missing",
            )
        if not await self._click_candidato(consultas):
            raise TargetUnavailableError(
                "no se pudo abrir Consultas en RCEL",
                diagnostic_code="rcel_consultas_unavailable",
            )
        await self._esperar_carga()

    async def _configurar_rango(self, desde: str, hasta: str) -> None:
        campos: list[tuple[str, str]] = [("Desde", desde), ("Hasta", hasta)]
        for etiqueta, valor in campos:
            encontrado = False
            for contexto in self._contextos():
                try:
                    campo = contexto.get_by_label(etiqueta, exact=True)
                    if await campo.count():
                        await campo.first.fill(str(valor))
                        encontrado = True
                        break
                except Exception:
                    continue
            if not encontrado:
                raise TargetUnavailableError(
                    f"no se encontró el campo {etiqueta} del rango RCEL",
                    diagnostic_code="rcel_date_field_missing",
                )

    async def _buscar(self) -> None:
        buscar = await self._accion("Buscar")
        if buscar is None or not await self._click_candidato(buscar):
            raise TargetUnavailableError(
                "no se pudo ejecutar la búsqueda RCEL",
                diagnostic_code="rcel_search_unavailable",
            )
        await self._esperar_carga()

    async def _descargar_pdfs(self, destino: Path) -> list[str]:
        enlaces: list[Any] = []
        for contexto in self._contextos():
            try:
                localizador = contexto.locator(f"[title={TITULO_PDF!r}]")
                for indice in range(await localizador.count()):
                    enlaces.append(localizador.nth(indice))
            except Exception:
                continue

        nombres: list[str] = []
        for indice, enlace in enumerate(enlaces, start=1):
            nombre = f"comprobante_{indice:03d}.pdf"
            ruta = await self.capturar_descarga(
                destino / nombre,
                lambda enlace=enlace: enlace.click(timeout=15_000),
                espera_ms=60_000,
            )
            try:
                es_pdf = ruta.read_bytes()[:5] == b"%PDF-"
            except OSError:
                es_pdf = False
            if not es_pdf:
                raise TargetUnavailableError(
                    "RCEL entregó un archivo que no es un PDF válido",
                    diagnostic_code="rcel_pdf_invalid",
                )
            nombres.append(nombre)
        return nombres
