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
from bot_worker.bots.declaracion_en_linea.helpers import (
    contenido_con_timeout,
    combinar_vep,
    construir_json_ddjj,
    es_vep_pagado,
    extraer_cabecera_ddjj,
    extraer_info_vep,
    extraer_secciones_ddjj,
    mapa_estados_vep,
    parsear_tablas,
    pdf_con_timeout,
    periodo_a_yyyymm,
    periodo_to_sijpdj,
    con_timeout,
    variantes_periodo,
    VEP_DOWNLOAD_TIMEOUT_S,
    VEP_PROBE_TIMEOUT_S,
    escribir_json_ddjj,
)

from .base import PortalArca, solo_digitos

_URL_LISTADO = "/djproforma/app/consultar/dj_generadas.aspx"
_HOST_FALLBACK = "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
_RE_F931 = re.compile(r"AbrirPopupF931\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", re.I)
_RE_VEP = re.compile(r"AbrirPopupVep\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", re.I)
_RE_ESTADO = re.compile(r"\b(pagado|pendiente|vencido|rechazado|parcial)\b", re.I)
_RE_NOMINA = re.compile(r"AbrirPopupNomina\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", re.I)


class DeclaracionEnLineaPortal(PortalArca):
    """Consulta DDJJ generadas y sus VEP sin acciones de presentación."""

    nombre = "declaracion_en_linea"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._representado_seleccionado = False
        self._listado_preparado = False
        self._seti_intentado = False
        self._seti_mapa: dict[str, dict[str, str]] = {}
        self._seti_pagina: Any | None = None
        self._seti_url: str | None = None

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
            # V1 toma el primer numero de 6 digitos entrecomillado del handler.
            coincidencia = re.search(r"'(\d{6})'", onclick)
            if coincidencia:
                return coincidencia.group(1)
        celdas = fila.locator("td")
        if await celdas.count() > 1:
            return periodo_a_yyyymm(await celdas.nth(1).inner_text())
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

    async def _abrir_url(self, pagina: Any, url: str, intentos: int = 3) -> None:
        """Navega con reintentos como ``goto_with_retry`` de V1."""
        ultimo_error: Exception | None = None
        for _ in range(max(1, intentos)):
            try:
                await pagina.goto(url, timeout=60_000)
                ultimo_error = None
                break
            except Exception as exc:  # noqa: PERF203 - reintento secuencial de V1
                ultimo_error = exc
        if ultimo_error is not None:
            raise TargetUnavailableError(
                "no se pudo navegar a la pagina del portal",
                diagnostic_code="declaracion_en_linea_navigation_failed",
            ) from ultimo_error
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
        await pdf_con_timeout(pagina, destino)
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

    async def _guardar_ddjj(self, url: str, destino: Path) -> Path | None:
        """Abre la DDJJ y la imprime a ``destino`` (port de V1 ``save_ddjj_pdf``).

        Si el portal no puede imprimir, V1 devuelve ``None`` y la consulta
        sigue: el periodo queda sin PDF pero con su JSON y su nómina.
        """
        pdf, _ = await self._leer_ddjj(url, destino)
        return pdf

    async def _activar_borrador(self, pagina: Any) -> None:
        """Toggle de visualización en modo borrador (V1 ``#ucF931_dvBorrador``)."""
        borrador = pagina.locator("#ucF931_dvBorrador")
        if await borrador.count():
            try:
                await borrador.first.click(timeout=5_000)
                await self._esperar(300)
            except Exception:
                pass

    async def _leer_ddjj(
        self, url: str, destino: Path | None = None
    ) -> tuple[Path | None, str]:
        """Abre la DDJJ, lee su HTML y, si se pide, la imprime a PDF.

        Una sola pestaña para el borrador, el HTML y la impresión (como V1 con
        ``page2``). Si la impresión falla se reintenta en una pestaña nueva, el
        equivalente V3 del navegador con ``storage_state`` de V1; si tampoco
        sale, el PDF queda vacío sin cortar la consulta.
        """
        pagina = await self._pagina_nueva()
        try:
            await self._abrir_url(pagina, url)
            await self._activar_borrador(pagina)
            html = await contenido_con_timeout(pagina)
            pdf: Path | None = None
            if destino is not None:
                try:
                    pdf = await self._guardar_pdf(pagina, Path(destino))
                except Exception:
                    pdf = await self._pdf_en_pestana_nueva(url, Path(destino))
            return pdf, html
        finally:
            try:
                await pagina.close()
            except Exception:
                pass

    async def _pdf_en_pestana_nueva(self, url: str, destino: Path) -> Path | None:
        """Respaldo de impresión de la DDJJ en una pestaña nueva (V1)."""
        pagina = await self._pagina_nueva()
        try:
            await self._abrir_url(pagina, url)
            await self._activar_borrador(pagina)
            return await self._guardar_pdf(pagina, Path(destino))
        except Exception:
            return None
        finally:
            try:
                await pagina.close()
            except Exception:
                pass

    async def _boton_vep(self) -> Any | None:
        """Botón que abre el listado de VEPs de SETI (V1 ``ImageButton3``)."""
        try:
            filas = await self._filas_ddjj()
        except Exception:
            filas = []
        if filas:
            candidato = filas[0]["row"].locator("[id*='ImageButton3']")
            if await candidato.count():
                return candidato.first
        respaldo = self.page.locator(
            "#ctl00_ContentPlaceHolder1_RptDJGeneradas_ctl01_ImageButton3"
        )
        if await respaldo.count():
            return respaldo.first
        return None

    async def _mapa_vep_seti(self) -> dict[str, dict[str, str]]:
        """Mapa de estados de VEP del listado SETI (port del popup de V1).

        V1 abre el popup una vez, navega a ``#/pago/consulta-veps`` y arma el
        mapa; luego completa cada periodo con él. Acá se arma una sola vez y
        las fallas del popup se ignoran igual que en V1.
        """
        if self._seti_intentado:
            return self._seti_mapa
        self._seti_intentado = True
        try:
            boton = await self._boton_vep()
            if boton is None:
                return self._seti_mapa
            async with self.page.expect_popup() as evento:
                await boton.click()
            pagina = await evento.value
            try:
                await pagina.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                pass
            base = str(getattr(pagina, "url", "") or "").split("#")[0]
            if "setiweb" in base:
                url = f"{base}#/pago/consulta-veps"
            else:
                url = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
            if "consulta-veps" not in str(getattr(pagina, "url", "")):
                await self._abrir_url(pagina, url)
            await self._abrir_url(pagina, url)
            html = await contenido_con_timeout(pagina)
            self._seti_mapa = mapa_estados_vep(parsear_tablas(html))
            self._seti_pagina = pagina
            self._seti_url = url
        except Exception:
            self._seti_mapa = {}
        return self._seti_mapa

    async def _esperar_filas_vep(self, pagina: Any, timeout_ms: int = 15_000) -> int:
        """Espera a que el listado SPA de VEPs renderice filas (port de ``wait_for_vep_rows``)."""
        filas = pagina.locator("table tbody tr")
        esperado = 0
        while esperado < timeout_ms:
            try:
                cantidad = await filas.count()
            except Exception:
                cantidad = 0
            if cantidad > 0:
                return cantidad
            try:
                await pagina.wait_for_timeout(400)
            except Exception:
                return 0
            esperado += 400
        try:
            return await filas.count()
        except Exception:
            return 0

    async def _fila_vep(self, pagina: Any, periodo: str, nro_vep: str | None = None) -> Any | None:
        """Fila del listado de VEPs que corresponde al periodo (port de ``find_vep_row``).

        Primero el VEP informado en el detalle; después el descriptor SIJPDJ y
        las variantes del periodo; si hay una sola fila, esa. Entre varias
        candidatas se prioriza la pagada.
        """
        await self._esperar_filas_vep(pagina)
        filas = pagina.locator("table tbody tr")
        if nro_vep:
            por_nro = filas.filter(has_text=re.compile(re.escape(str(nro_vep))))
            if await por_nro.count() > 0:
                return por_nro.first

        candidatas = None
        descriptor = periodo_to_sijpdj(periodo)
        if descriptor:
            por_descriptor = filas.filter(has_text=re.compile(re.escape(descriptor), re.I))
            if await por_descriptor.count() > 0:
                candidatas = por_descriptor

        if candidatas is None:
            for variante in variantes_periodo(periodo or ""):
                por_variante = filas.filter(has_text=re.compile(re.escape(variante), re.I))
                if await por_variante.count() > 0:
                    candidatas = por_variante
                    break

        if candidatas is None:
            if await filas.count() == 1:
                return filas.first
            return None

        for indice in range(await candidatas.count()):
            fila = candidatas.nth(indice)
            try:
                texto = await fila.inner_text()
            except Exception:
                continue
            if es_vep_pagado(texto):
                return fila
        return candidatas.first

    async def _descargar_vep(
        self, pagina: Any, periodo: str, destino: Path, nro_vep: str | None = None
    ) -> Path | None:
        """Descarga el PDF del VEP (port de ``download_vep_pdf_from_page``)."""
        fila = await self._fila_vep(pagina, periodo, nro_vep)
        if fila is None:
            return None
        toggle = fila.locator("button[data-bs-toggle='dropdown']")
        if await toggle.count() == 0:
            toggle = fila.locator("span[title='ver opciones']")
        if await toggle.count() == 0:
            return None
        enlace = fila.locator("a[title*='PDF']")
        if await enlace.count() == 0:
            return None
        try:
            await toggle.first.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        for opciones in ({"timeout": 5_000}, {"timeout": 5_000, "force": True}):
            try:
                await toggle.first.click(**opciones)
                break
            except Exception:
                continue
        else:
            return None
        try:
            await enlace.first.wait_for(state="visible", timeout=15_000)
        except Exception:
            return None
        try:
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

    async def _guardar_vep(
        self, url: str, periodo: str, destino: Path, nro_vep: str | None = None
    ) -> tuple[Path | None, dict[str, Any]]:
        pagina = await self._pagina_nueva()
        try:
            await self._abrir_url(pagina, url)
            html = await contenido_con_timeout(pagina)
            # V1 arma el detalle desde el HTML y lo completa con el listado SETI.
            info = extraer_info_vep(html, periodo)
            estado_texto = await self._estado_vep(pagina)
            info = combinar_vep(info, (await self._mapa_vep_seti()).get(periodo, {}))
            # V1 acota la busqueda de la fila (20 s) y la descarga (90 s).
            fila_vep = await con_timeout(
                self._fila_vep(pagina, periodo, nro_vep), VEP_PROBE_TIMEOUT_S, default=None
            )
            archivo = await con_timeout(
                self._descargar_vep(
                    pagina, periodo, Path(destino), nro_vep=info.get("nro_vep") or nro_vep
                ),
                VEP_DOWNLOAD_TIMEOUT_S,
                default=None,
            )
            if archivo is None and fila_vep is not None:
                # V1 también imprime la pantalla de detalle como respaldo
                # (``save_vep_pdf``). No se crea PDF de páginas vacías.
                try:
                    texto = await pagina.locator("body").inner_text(timeout=5_000)
                except Exception:
                    texto = ""
                if len(texto.strip()) > 40 and re.search(
                    r"\b(VEP|SIJPDJ|pagado|pendiente)\b", texto, re.I
                ):
                    archivo = await self._guardar_pdf(pagina, Path(destino))
            estado_vep = str(info.get("estado") or estado_texto.get("estado_vep") or "")
            return archivo, {
                "estado_vep": estado_vep,
                "pagado": es_vep_pagado(estado_vep),
                "vep": info,
            }
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


    async def consultar_periodo_detalle(
        self,
        periodo: str,
        destino_ddjj: Path,
        destino_vep: Path,
        destino_json: Path | None = None,
    ) -> dict[str, Any]:
        """Como ``consultar_periodo`` pero tambien lee cabecera, secciones y VEP.

        Port del flujo por periodo de V1. El HTML de la DDJJ se lee con tope de
        tiempo; si la pagina no responde, el dato faltante queda en
        ``advertencias`` en vez de colgar el bot.
        """
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
        advertencias: list[str] = []

        # Una sola pestana para el HTML y el PDF de la DDJJ, como V1 con page2.
        ddjj_pdf, html_ddjj = await self._leer_ddjj(url_ddjj, Path(destino_ddjj))
        if ddjj_pdf is None:
            advertencias.append(
                f"No se pudo generar el PDF de la declaración jurada del período {periodo}."
            )
        if not html_ddjj:
            advertencias.append(f"No se pudo leer la declaración jurada del período {periodo}.")
        cabecera = extraer_cabecera_ddjj(html_ddjj)
        secciones = extraer_secciones_ddjj(html_ddjj)

        vep_pdf, estado = await self._guardar_vep(url_vep, periodo, Path(destino_vep))
        vep_info = dict((estado or {}).get("vep") or {})
        if vep_pdf is None:
            advertencias.append(f"El período {periodo} no tiene PDF de VEP.")

        resultado: dict[str, Any] = {
            "estado_vep": (estado or {}).get("estado_vep", ""),
            "pagado": bool((estado or {}).get("pagado", False)),
            "vep": vep_info,
            "cabecera": cabecera,
            "secciones": secciones,
            "advertencias": advertencias,
        }
        if destino_json is not None:
            payload = construir_json_ddjj(
                cabecera=cabecera,
                secciones=secciones,
                periodo=periodo,
                vep=vep_info,
            )
            resultado["json"] = str(escribir_json_ddjj(payload, Path(destino_json)))
        return resultado

    async def descargar_nomina(self, row_periodo: str, destino: Path) -> Path | None:
        """Baja la nómina (.xls) del período desde su popup (port de V1).

        V1 cambia el select ``opPapeles`` a la opción 2, espera el popup y pulsa
        ``btnBajarCuiles``. Si el popup no aparece, arma la URL de
        ``ver_nomina.aspx``. Todo con tope de tiempo; sin nómina devuelve
        ``None`` y no corta la consulta.
        """
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            filas = await self._filas_ddjj()
        except TargetUnavailableError:
            return None
        fila = next((f for f in filas if f.get("periodo") == row_periodo), None)
        if fila is None:
            return None
        select = fila["row"].locator("select[id*='opPapeles']")
        if await con_timeout(select.count(), 5.0, default=0) == 0:
            return None
        try:
            await select.first.scroll_into_view_if_needed()
        except Exception:
            pass

        popup = None
        try:
            existentes = set(getattr(self.page.context, "pages", []) or [])
        except Exception:
            existentes = set()
        try:
            async with self.page.expect_popup(timeout=5_000) as evento:
                await select.first.select_option("2")
            popup = await evento.value
        except Exception:
            # V1 busca primero una pestana nueva del contexto y, si no hay,
            # arma la URL de ``ver_nomina.aspx``.
            try:
                nuevas = [
                    pagina
                    for pagina in (getattr(self.page.context, "pages", []) or [])
                    if pagina not in existentes
                ]
            except Exception:
                nuevas = []
            if nuevas:
                popup = nuevas[-1]
            else:
                url = await self._url_nomina(fila)
                if not url:
                    return None
                try:
                    popup = await self._pagina_nueva()
                    await self._abrir_url(popup, url)
                except Exception:
                    return None

        confirmacion = None
        try:
            boton = popup.locator("#ctl00_ContentPlaceHolder1_btnBajarCuiles")
            await boton.wait_for(state="visible", timeout=5_000)
            tarea_confirmacion = asyncio.create_task(
                popup.wait_for_event("popup", timeout=5_000)
            )
            try:
                descarga = await self.capturar_descarga(
                    destino, lambda: boton.click(), espera_ms=30_000
                )
            finally:
                try:
                    confirmacion = await tarea_confirmacion
                except Exception:
                    confirmacion = None
                if confirmacion is not None:
                    try:
                        await confirmacion.close()
                    except Exception:
                        pass
            return descarga
        except Exception:
            return None
        finally:
            try:
                await popup.close()
            except Exception:
                pass

    async def _url_nomina(self, fila: dict[str, Any]) -> str | None:
        """URL de ``ver_nomina.aspx`` desde el handler del select o del F931."""
        periodo, sec = None, None
        select = fila["row"].locator("select[id*='opPapeles']")
        onchange = str(await select.first.get_attribute("onchange") or "")
        coincidencia = _RE_NOMINA.search(onchange)
        if coincidencia:
            periodo, sec = coincidencia.group("periodo"), coincidencia.group("sec")
        if not periodo:
            f931 = fila["row"].locator("div[id*='dvVerF931']")
            if await f931.count():
                onclick = str(await f931.first.get_attribute("onclick") or "")
                coincidencia = _RE_F931.search(onclick)
                if coincidencia:
                    periodo, sec = coincidencia.group("periodo"), coincidencia.group("sec")
        periodo = periodo or fila.get("periodo")
        if not periodo:
            return None
        base = str(getattr(self.page, "url", "") or _HOST_FALLBACK)
        return urljoin(base, f"../altadj/ver_nomina.aspx?Periodo={periodo}&SecDJVig={sec or '000'}")

    async def _html_ddjj(self, url: str) -> str:
        """HTML de la DDJJ en modo borrador, con tope de tiempo (port de V1)."""
        _, html = await self._leer_ddjj(url, None)
        return html
