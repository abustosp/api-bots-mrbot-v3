"""Portal de consulta de Declaraciones Juradas en Línea (solo lectura).

Port del flujo ``declaracion_en_linea_consulta`` de V1/V2. Navega a
DDJJ ya generadas, consulta el formulario y el VEP del período y materializa
sus PDF. No presenta, modifica ni genera declaraciones.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

_URL_LISTADO = "/djproforma/app/consultar/dj_generadas.aspx"
_HOST_FALLBACK = "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
_RE_F931 = re.compile(r"AbrirPopupF931\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", re.I)
_RE_VEP = re.compile(r"AbrirPopupVep\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", re.I)
_RE_ESTADO = re.compile(r"\b(pagado|pendiente|vencido|rechazado|parcial)\b", re.I)


class DeclaracionEnLineaPortal(PortalArca):
    """Consulta DDJJ generadas y sus VEP sin acciones de presentación."""

    nombre = "declaracion_en_linea"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._representado_seleccionado = False
        self._listado_preparado = False

    @staticmethod
    def _normalizar_periodo(periodo: str) -> str:
        texto = str(periodo or "").strip()
        if not re.fullmatch(r"\d{6}", texto) or not 1 <= int(texto[4:]) <= 12:
            raise TargetUnavailableError(
                "período DDJJ inválido, se esperaba AAAAMM",
                diagnostic_code="declaracion_en_linea_period_invalid",
            )
        return texto

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona el CUIT en el formulario ASP.NET legado.

        El botón ``Submit`` de esta pantalla solo confirma el representado,
        como en V2. Nunca se pulsa un botón de envío de una DDJJ.
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )

        selector = self.page.locator("#ctl00_ContentPlaceHolder1_ddlCUIT")
        if not await selector.count():
            # V2 avanzaba primero la pantalla de selección y, en algunos
            # contribuyentes, el combo aparecía luego del primer Submit.
            if await self._confirmar_representado():
                selector = self.page.locator("#ctl00_ContentPlaceHolder1_ddlCUIT")

        if await selector.count():
            opciones = await selector.first.evaluate(
                "el => Array.from(el.options).map(o => ({value: (o.value || '').trim(), "
                "label: (o.textContent || '').trim()}))"
            )
            valor = self._resolver_opcion_representado(opciones or [], digits)
            if not valor:
                raise TargetUnavailableError(
                    "el CUIT representado no está disponible en Declaración en Línea",
                    diagnostic_code="represented_cuit_not_selectable",
                )
            await selector.first.select_option(value=valor)
            if not await self._confirmar_representado():
                raise TargetUnavailableError(
                    "no se pudo confirmar el representado en Declaración en Línea",
                    diagnostic_code="declaracion_en_linea_representado_submit_missing",
                )
        else:
            await super().seleccionar_representado(digits)

        self._representado_seleccionado = True

    @staticmethod
    def _resolver_opcion_representado(opciones: list[dict[str, str]], digits: str) -> str | None:
        for opcion in opciones:
            valor = str(opcion.get("value") or "").strip()
            if solo_digitos(valor) == digits:
                return valor
        for opcion in opciones:
            etiqueta = str(opcion.get("label") or "").strip()
            if solo_digitos(etiqueta).startswith(digits):
                return str(opcion.get("value") or digits).strip()
        return None

    async def _confirmar_representado(self) -> bool:
        candidatos = (
            self.page.get_by_role("button", name=re.compile(r"^Submit$", re.I)),
            self.page.locator("input[type='submit']"),
            self.page.locator("button[type='submit']"),
        )
        for boton in candidatos:
            try:
                if not await boton.count():
                    continue
                await boton.first.click(timeout=5_000)
                await self._esperar_navegacion()
                return True
            except Exception:
                continue
        return False

    async def preparar(self) -> None:
        """Abre el listado de DDJJ generadas del representado ya seleccionado."""
        if self._listado_preparado:
            return
        if not self._representado_seleccionado:
            raise TargetUnavailableError(
                "primero debe seleccionarse el representado",
                diagnostic_code="declaracion_en_linea_representado_missing",
            )

        # Port fiel de la navegación por menú de V2. Si el menú cambió, la URL
        # directa del mismo host mantiene la sesión fiscal existente.
        try:
            ingrese = self.page.locator("a").filter(has_text=re.compile(r"INGRESE", re.I)).first
            if not await ingrese.count():
                ingrese = self.page.locator("td").filter(
                    has_text=re.compile(r"^INGRESE\.\.\.$", re.I)
                ).first
            await ingrese.hover(timeout=5_000)
            consultas = self.page.locator("a").filter(
                has_text=re.compile(r"para consultar", re.I)
            ).first
            await consultas.hover(timeout=10_000)
            enlace = self.page.locator("a").filter(
                has_text=re.compile(r"Declaraciones Juradas (presentadas|generadas)", re.I)
            )
            if not await enlace.count():
                enlace = self.page.locator("a").filter(
                    has_text=re.compile(r"Declaraciones Juradas", re.I)
                )
            await enlace.first.click(timeout=10_000)
            await self._esperar_navegacion()
        except Exception:
            url = urljoin(str(getattr(self.page, "url", "") or _HOST_FALLBACK), _URL_LISTADO)
            if not url.startswith("https://"):
                url = urljoin(_HOST_FALLBACK, _URL_LISTADO)
            try:
                await self.page.goto(url, timeout=60_000)
                await self._esperar_navegacion()
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo abrir el listado de DDJJ generadas",
                    diagnostic_code="declaracion_en_linea_listado_unavailable",
                ) from exc

        self._listado_preparado = True

    async def _esperar_navegacion(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        await self._esperar(500)

    async def _filas_ddjj(self) -> list[dict[str, Any]]:
        tabla = self.page.locator("table.TablaCuiles").filter(
            has_text=re.compile(r"declaraciones juradas", re.I)
        )
        if not await tabla.count():
            tabla = self.page.locator("table.TablaCuiles")
        if not await tabla.count():
            raise TargetUnavailableError(
                "el portal no mostró el listado de DDJJ",
                diagnostic_code="declaracion_en_linea_rows_missing",
            )
        filas = tabla.first.locator("tr").filter(
            has=self.page.locator("[id*='RptDJGeneradas']")
        )
        resultado: list[dict[str, Any]] = []
        for indice in range(await filas.count()):
            fila = filas.nth(indice)
            periodo = await self._periodo_fila(fila)
            resultado.append({"index": indice, "row": fila, "periodo": periodo})
        return resultado

    @staticmethod
    async def _periodo_fila(fila: Any) -> str | None:
        f931 = fila.locator("div[id*='dvVerF931']")
        if await f931.count():
            onclick = await f931.first.get_attribute("onclick") or ""
            match = _RE_F931.search(onclick)
            if match:
                return match.group("periodo")
        celdas = fila.locator("td")
        if await celdas.count() > 1:
            texto = " ".join((await celdas.nth(1).inner_text()).split())
            match = re.search(r"\b(\d{2})[/-](\d{4})\b", texto)
            if match:
                return f"{match.group(2)}{match.group(1)}"
            match = re.search(r"\b(\d{6})\b", texto)
            if match:
                return match.group(1)
        return None

    async def _urls_periodo(self, fila_info: dict[str, Any], periodo: str) -> tuple[str, str]:
        fila = fila_info["row"]
        f931 = fila.locator("div[id*='dvVerF931']")
        f931_onclick = await f931.first.get_attribute("onclick") if await f931.count() else ""
        f931_match = _RE_F931.search(f931_onclick or "")
        if f931_match:
            periodo_ddjj, sec_ddjj = f931_match.group("periodo", "sec")
        else:
            periodo_ddjj, sec_ddjj = periodo, "000"

        vep = fila.locator("div[id*='dvVerVep']")
        vep_onclick = await vep.first.get_attribute("onclick") if await vep.count() else ""
        vep_match = _RE_VEP.search(vep_onclick or "")
        if vep_match:
            periodo_vep, sec_vep = vep_match.group("periodo", "sec")
        else:
            periodo_vep, sec_vep = periodo, "000"

        base = str(getattr(self.page, "url", "") or _HOST_FALLBACK)
        return (
            urljoin(base, f"ver_formulario.aspx?Periodo={periodo_ddjj}&SecDJVig={sec_ddjj}"),
            urljoin(base, f"ver_Vep.aspx?Periodo={periodo_vep}&SecDJVig={sec_vep}"),
        )

    async def _pagina_nueva(self) -> Any:
        context = self._context or getattr(self.page, "context", None)
        if context is None:
            raise TargetUnavailableError(
                "no está disponible el contexto autenticado del portal",
                diagnostic_code="declaracion_en_linea_context_missing",
            )
        return await context.new_page()

    async def _abrir_url(self, pagina: Any, url: str) -> None:
        await pagina.goto(url, timeout=60_000)
        try:
            await pagina.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        try:
            await pagina.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

    async def _guardar_pdf(self, pagina: Any, destino: Path) -> Path:
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            await pagina.emulate_media(media="screen")
        except Exception:
            pass
        await pagina.pdf(path=str(destino), format="A4", print_background=True)
        if not destino.is_file() or destino.stat().st_size < 8:
            raise TargetUnavailableError(
                "el portal no produjo el PDF solicitado",
                diagnostic_code="declaracion_en_linea_pdf_empty",
            )
        if destino.read_bytes()[:5] != b"%PDF-":
            destino.unlink(missing_ok=True)
            raise TargetUnavailableError(
                "la salida del portal no es un PDF válido",
                diagnostic_code="declaracion_en_linea_pdf_invalid",
            )
        return destino

    async def _guardar_ddjj(self, url: str, destino: Path) -> Path:
        pagina = await self._pagina_nueva()
        try:
            await self._abrir_url(pagina, url)
            # El control es el mismo toggle de visualización en modo borrador
            # que usa V2. No se dispara ningún submit ni acción de presentación.
            borrador = pagina.locator("#ucF931_dvBorrador")
            if await borrador.count():
                try:
                    await borrador.first.click(timeout=5_000)
                    await self._esperar(300)
                except Exception:
                    pass
            return await self._guardar_pdf(pagina, destino)
        except TargetUnavailableError:
            raise
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo consultar la DDJJ del período",
                diagnostic_code="declaracion_en_linea_ddjj_unavailable",
            ) from exc
        finally:
            try:
                await pagina.close()
            except Exception:
                pass

    async def _fila_vep(self, pagina: Any, periodo: str) -> Any | None:
        filas = pagina.locator("table tbody tr")
        mes, anio = periodo[4:], periodo[:4]
        descriptor = f"SIJPDJ{mes}/{anio[2:]}"
        variantes = (descriptor, periodo, f"{mes}/{anio}", f"{anio}/{mes}", f"{mes}-{anio}")
        for variante in variantes:
            grupo = filas.filter(has_text=re.compile(re.escape(variante), re.I))
            cantidad = await grupo.count()
            if not cantidad:
                continue
            candidatas = [grupo.nth(i) for i in range(cantidad)]
            for fila in candidatas:
                texto = await self._texto(fila)
                if re.search(r"\bpagado\b", texto, re.I):
                    return fila
            return candidatas[0]
        if await filas.count() == 1:
            return filas.first
        return None

    async def _descargar_vep(self, pagina: Any, periodo: str, destino: Path) -> Path | None:
        fila = await self._fila_vep(pagina, periodo)
        if fila is None:
            return None
        toggle = fila.locator("button[data-bs-toggle='dropdown']")
        if not await toggle.count():
            toggle = fila.locator("span[title='ver opciones']")
        enlace = fila.locator("a[title*='PDF']")
        if not await toggle.count() or not await enlace.count():
            return None
        try:
            await toggle.first.click(timeout=5_000)
            await enlace.first.wait_for(state="visible", timeout=15_000)
            async with pagina.expect_download(timeout=30_000) as descarga:
                await enlace.first.click(timeout=15_000)
            archivo = await descarga.value
            await archivo.save_as(str(destino))
            if destino.is_file() and destino.stat().st_size >= 8 and destino.read_bytes()[:5] == b"%PDF-":
                return destino
            destino.unlink(missing_ok=True)
        except Exception:
            destino.unlink(missing_ok=True)
        return None

    async def _estado_vep(self, pagina: Any) -> dict[str, Any]:
        try:
            texto = await pagina.locator("body").inner_text(timeout=5_000)
        except Exception:
            texto = ""
        estados = [match.group(1).lower() for match in _RE_ESTADO.finditer(texto)]
        estado = next((e for e in estados if e == "pagado"), estados[0] if estados else "")
        return {"estado_vep": estado, "pagado": estado == "pagado"}

    async def _guardar_vep(self, url: str, periodo: str, destino: Path) -> tuple[Path | None, dict[str, Any]]:
        pagina = await self._pagina_nueva()
        try:
            await self._abrir_url(pagina, url)
            estado = await self._estado_vep(pagina)
            archivo = await self._descargar_vep(pagina, periodo, Path(destino))
            if archivo is not None:
                return archivo, estado
            # V2 también imprime la pantalla de detalle como respaldo cuando
            # SETI no ofrece descarga directa. No se crea PDF de páginas vacías.
            try:
                texto = await pagina.locator("body").inner_text(timeout=5_000)
            except Exception:
                texto = ""
            if len(texto.strip()) > 40 and re.search(r"\b(VEP|SIJPDJ|pagado|pendiente)\b", texto, re.I):
                return await self._guardar_pdf(pagina, Path(destino)), estado
            return None, estado
        except TargetUnavailableError:
            raise
        except Exception:
            return None, {}
        finally:
            try:
                await pagina.close()
            except Exception:
                pass

    async def consultar_periodo(
        self,
        periodo: str,
        destino_ddjj: Path,
        destino_vep: Path,
    ) -> dict[str, Any]:
        """Consulta un período ya generado y descarga sus PDF sin presentar DDJJ."""
        periodo = self._normalizar_periodo(periodo)
        await self.preparar()
        filas = await self._filas_ddjj()
        fila_info = next((f for f in filas if f.get("periodo") == periodo), None)
        if fila_info is None:
            raise TargetUnavailableError(
                "no se encontró una DDJJ generada para el período solicitado",
                diagnostic_code="declaracion_en_linea_period_not_found",
            )
        url_ddjj, url_vep = await self._urls_periodo(fila_info, periodo)
        await self.paso("descargar_ddjj", self._guardar_ddjj(url_ddjj, Path(destino_ddjj)))
        _, estado = await self._guardar_vep(url_vep, periodo, Path(destino_vep))
        return estado
