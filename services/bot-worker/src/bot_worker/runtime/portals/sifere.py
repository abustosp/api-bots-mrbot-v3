"""Portal COMARB SIFERE Web: retenciones y percepciones por jurisdicción.

Port de ``api-bots-mrbot/app/bot/sifere_bot.py`` (V1, ``sifere_consultas``) y
de ``api-bots-mrbot-v2/app/bot/sifere_bot.py`` (V2). El plugin V3 pide:

- ``consultar_sircar(metodo, jurisdiccion, ...)``: Excel y PDF de SIRCAR por
  jurisdicción, entrando por ``retencionIn`` y ``percepcionIn`` (V1 recorre
  ambos métodos). En V1 cada descarga falla en silencio: los errores se
  acumulan en ``errores`` sin cortar la corrida.
- ``consultar_consolidados(...)``: SIRCREB (tablas ``retencion``/``coef`` más
  las descargas "Retenciones y coeficientes" y "Exportar archivo con") y
  SIRTAC (tabla combinada más los xlsx separados de jurisdicciones y
  operaciones), como V1.
"""
from __future__ import annotations

import os
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

BASE_COMARB = "https://app1.comarb.gob.ar/siferewebconsultas"
#: Entrada por método (V1: ``srcRetencion.do?method=retencionIn``), necesaria
#: para que Struts inicialice la colección de jurisdicciones.
URL_SIRCAR_ENTRADA = BASE_COMARB + "/srcRetencion.do?method={metodo}In"
#: Consulta de SIRCAR por jurisdicción y período (V1: mismo rango desde/hasta).
URL_SIRCAR_CONSULTA = (
    BASE_COMARB + "/srcRetencion.do?method={metodo}&juris={juris}"
    "&cuitAgente=&mesDesde={mes}&anioDesde={anio}"
    "&mesHasta={mes}&anioHasta={anio}"
)
METODOS_SIRCAR = ("retencion", "percepcion")
ETIQUETAS_SIRCAR = {"retencion": "Retenciones", "percepcion": "Percepciones"}


def representado_para_nombre(nombre: str | None, cuit: str) -> str:
    """V1: el nombre del representado o, si no hay, el CUIT.

    V1 (``bot_sifere``) usa ``(representado or cuit_representado).strip()`` en
    el nombre de archivo y reemplaza los separadores de ruta.
    """
    texto = (nombre or "").strip()
    if texto:
        return texto.replace("/", "_").replace("\\", "_")
    return solo_digitos(cuit)


def nombre_sifere(
    cuit: str,
    periodo: str,
    representado: str,
    partes: tuple[str, ...],
    ext: str = "",
) -> str:
    """Nombre de archivo V1: ``{fin} - {cuit} - SIFERE - ...{ext}``.

    ``partes`` son los tramos entre ``SIFERE`` y el período: por ejemplo
    ``("SIRCAR", "Retenciones", "901")`` o ``("SIRCREB", "Tablas")``.
    """
    digitos = solo_digitos(cuit)
    return (
        " - ".join((f"{digitos[-1:]} - {digitos} - SIFERE", *partes, periodo, representado))
        + ext
    )


class SiferePortal(PortalArca):
    """Consultas SIFERE Web (COMARB) por jurisdicción."""

    nombre = "sifere"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._representado_listo = False
        #: Métodos de SIRCAR cuya pantalla ``*In`` ya se abrió (V1 abre una vez
        #: por método antes de recorrer las jurisdicciones).
        self._sircar_entradas: set[str] = set()

    # ------------------------------------------------------------------

    async def seleccionar_representado(self, cuit: str) -> None:
        """Elige el representado en el selector ``#selectedCuit`` del portal."""
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        await self.paso("entrar_al_portal", self._resolver_entry_point())
        selector, ambito = await self._buscar_selector_cuit()
        if selector is None:
            # El click del catálogo puede disparar el SSO sin abrir el popup
            # en Chromium. Entrar por mainMenu.do evita la redirección HTTP
            # de la raíz COMARB y conserva la sesión del mismo contexto.
            await self.paso(
                "navegar_sifere",
                self.abrir_url(f"{BASE_COMARB}/mainMenu.do", timeout_ms=30_000),
            )
            await self.paso("resolver_entry_point", self._resolver_entry_point())
            selector, ambito = await self._buscar_selector_cuit()
        if selector is None and await self._representado_vigente() == digits:
            # Sin otros representados, COMARB entra directo a mainMenu.do con
            # "X (CUIT) representando a X (CUIT)" y no muestra el selector.
            self._representado_listo = True
            return
        if selector is None:
            raise TargetUnavailableError(
                "el portal SIFERE no mostró el selector de representado",
                diagnostic_code="sifere_selector_missing",
            )
        try:
            await selector.select_option(digits)
        except Exception:
            try:
                await selector.select_option(value=digits)
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo elegir el CUIT representado en SIFERE",
                    diagnostic_code="represented_cuit_not_selectable",
                ) from exc
        try:
            enviado = await selector.evaluate(
                "select => {"
                "const form = select.form;"
                "if (!form) return false;"
                # Avoid Playwright waiting for a slow navigation triggered by
                # click(), and let evaluate() resolve before navigation starts.
                "setTimeout(() => {"
                "if (typeof form.requestSubmit === 'function') form.requestSubmit();"
                "else form.submit();"
                "}, 0);"
                "return true;"
                "}"
            )
        except Exception:
            enviado = False
        if not enviado and not await self._clickear(
            (
                ambito.get_by_role("button", name=re.compile(r"Seleccionar", re.I)),
                ambito.get_by_text("Seleccionar", exact=True),
                ambito.locator("button:has-text('Seleccionar')"),
                ambito.locator("input[type='submit'][value*='Seleccionar' i]"),
                ambito.locator("input[type='button'][value*='Seleccionar' i]"),
            ),
            total_ms=6_000,
        ):
            try:
                diagnostico = await selector.evaluate(
                    "select => {"
                    "const form = select.form;"
                    "const controls = Array.from(document.querySelectorAll("
                    "'button,input[type=submit],input[type=button],input[type=image],a'"
                    ")).map(el => ({"
                    "tag: el.tagName.toLowerCase(),"
                    "type: el.getAttribute('type') || '',"
                    "label: (el.innerText || el.value || el.title || '').trim().slice(0, 80)"
                    "})).filter(el => /seleccionar|continuar|ingresar|aceptar|entrar|submit/i.test(el.label));"
                    "return JSON.stringify({form: Boolean(form), form_controls: form ? form.querySelectorAll('button,input, a').length : 0, in_frame: window !== window.top, labels: controls});"
                    "}"
                )
            except Exception as exc:
                diagnostico = type(exc).__name__
            raise TargetUnavailableError(
                f"el portal SIFERE no confirmó la selección del representado ({diagnostico})",
                diagnostic_code="sifere_seleccionar_missing",
            )
        try:
            await ambito.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass
        await self._esperar(2_500)
        self._representado_listo = True

    async def _resolver_entry_point(self) -> None:
        """Acepta la pantalla intermedia ``entryPoint.login`` cuando aparece."""
        try:
            if "entryPoint.login" not in await self._url():
                return
        except Exception:
            return
        for etiqueta in ("Ingresar", "Continuar", "Aceptar"):
            if await self._clickear(
                (self.page.get_by_role("button", name=etiqueta),), total_ms=3_000
            ):
                await self._esperar(1_500)
                return
        try:
            await self.page.evaluate("document.forms[0].submit()")
        except Exception:
            pass
        await self._esperar(1_500)

    async def _representado_vigente(self) -> str | None:
        """CUIT que el encabezado muestra después de "representando a"."""
        ambitos = [self.page, *list(getattr(self.page, "frames", []) or [])]
        for ambito in ambitos:
            try:
                texto = await ambito.locator("body").inner_text(timeout=3_000)
            except Exception:
                continue
            coincidencia = re.search(
                r"representando\s+a\s+[^()]*\((\d{2}-?\d{8}-?\d)\)", str(texto), re.I
            )
            if coincidencia:
                return solo_digitos(coincidencia.group(1))
        return None

    async def _buscar_selector_cuit(self) -> tuple[Any | None, Any]:
        """Selector ``#selectedCuit`` en la página o dentro de un iframe."""
        for _ in range(6):
            try:
                directo = self.page.locator("#selectedCuit")
                if await directo.count():
                    return directo.first, self.page
            except Exception:
                pass
            for marco in list(getattr(self.page, "frames", []) or []):
                try:
                    en_frame = marco.locator("#selectedCuit")
                    if await en_frame.count():
                        return en_frame.first, marco
                except Exception:
                    continue
            await self._esperar(1_000)
        return None, self.page

    # ------------------------------------------------------------------

    def _exigir_representado(self) -> None:
        if not self._representado_listo:
            raise TargetUnavailableError(
                "no se seleccionó el representado antes de consultar SIFERE",
                diagnostic_code="sifere_representado_faltante",
            )

    def _normalizar_periodo(self, periodo: str) -> tuple[str, str, str]:
        texto = re.sub(r"\D", "", str(periodo))
        if len(texto) != 6:
            raise TargetUnavailableError(
                "período SIFERE inválido (se espera AAAAMM)",
                diagnostic_code="sifere_periodo_invalido",
            )
        return texto[:4], texto[4:], texto

    # ------------------------------------------------------------------

    async def consultar_sircar(
        self,
        metodo: str,
        jurisdiccion: int | str,
        periodo: str,
        destino_dir: Path,
        representado_cuit: str,
        nombre_representado: str | None = None,
    ) -> dict[str, Any]:
        """Descarga Excel y PDF de SIRCAR para un método y una jurisdicción.

        Port del bucle de V1 (``sifere_consultas``): se entra por
        ``retencionIn``/``percepcionIn`` una vez por método, se abre la consulta
        de la jurisdicción y se bajan los dos enlaces ("Excel" y "PDF") con la
        extensión que sugiere el portal. Cada descarga que falla se registra en
        ``errores`` y la corrida sigue (en V1 fallaban en silencio).
        """
        self._exigir_representado()
        metodo = str(metodo).strip().lower()
        if metodo not in METODOS_SIRCAR:
            raise TargetUnavailableError(
                f"método SIRCAR inválido: {metodo!r}",
                diagnostic_code="sifere_metodo_invalido",
            )
        etiqueta = ETIQUETAS_SIRCAR[metodo]
        anio, mes, texto = self._normalizar_periodo(periodo)
        try:
            juris = int(str(jurisdiccion).strip())
        except (TypeError, ValueError) as exc:
            raise TargetUnavailableError(
                f"jurisdicción SIRCAR inválida: {jurisdiccion!r}",
                diagnostic_code="sifere_jurisdiccion_invalida",
            ) from exc
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        digitos = solo_digitos(representado_cuit)
        representado = representado_para_nombre(nombre_representado, digitos)

        if metodo not in self._sircar_entradas:
            entrada_ok = False
            try:
                enlace = self.page.locator(
                    f"a[href='srcRetencion.do?method={metodo}In']"
                )
                entrada_ok = await self._clickear((enlace,), total_ms=5_000)
            except Exception:
                entrada_ok = False
            if not entrada_ok:
                await self.paso(
                    "abrir_sircar_entrada",
                    self.abrir_url(URL_SIRCAR_ENTRADA.format(metodo=metodo)),
                )
            self._sircar_entradas.add(metodo)
            await self._esperar(1_000)

        await self.paso(
            "abrir_sircar",
            self.abrir_url(
                URL_SIRCAR_CONSULTA.format(
                    metodo=metodo, juris=juris, mes=mes, anio=anio
                )
            ),
        )
        await self._esperar(1_200)

        salida: dict[str, Any] = {
            "metodo": metodo,
            "etiqueta": etiqueta,
            "jurisdiccion": juris,
            "periodo": texto,
            "archivos": [],
            "errores": [],
        }
        nombre_base = nombre_sifere(
            digitos, texto, representado, ("SIRCAR", etiqueta, str(juris))
        )
        for tipo, accion in (("xlsx", self._click_excel), ("pdf", self._click_pdf)):
            try:
                ruta = await self.descargar_con_nombre_sugerido(
                    destino_dir, nombre_base, accion
                )
            except Exception as exc:
                salida["errores"].append(
                    f"No se pudo descargar SIRCAR {etiqueta} ({tipo}) "
                    f"jurisdicción {juris}: {exc}"
                )
                continue
            salida["archivos"].append(
                {"nombre": ruta.name, "tipo": tipo, "filas": _contar_filas(ruta)}
            )
        return salida

    async def consultar_consolidados(
        self,
        periodo: str,
        destino_dir: Path,
        representado_cuit: str,
        nombre_representado: str | None = None,
    ) -> dict[str, Any]:
        """SIRCREB y SIRTAC del período, como V1 (``sifere_consultas``).

        SIRCREB: tablas ``retencion`` y ``coef`` en un xlsx, más las descargas
        "Retenciones y coeficientes" y "Exportar archivo con". V1 guarda ambas
        descargas con el MISMO nombre base, así que la segunda pisa a la primera
        cuando el portal sugiere la misma extensión; se replica tal cual (queda
        una sola entrada en ``archivos``).
        SIRTAC: tabla combinada, más los xlsx separados de jurisdicciones y de
        operaciones generados desde las tablas HTML (V1: el sitio no los sirve
        como descarga).

        Cada descarga/escritura falla en silencio en V1: acá el error se acumula
        en ``errores`` y la corrida sigue. Devuelve ``archivos``, ``errores`` y
        el conteo de ``filas`` por hoja.
        """
        self._exigir_representado()
        anio, mes, texto = self._normalizar_periodo(periodo)
        # ``detalleMensual`` interpreta ``mes``/``anio`` como el mes anterior
        # al "Mes/Año" que muestra la página: con el período tal cual
        # (202502) devolvía 03/2025. Hay que retroceder un mes.
        mes_sircreb = int(mes) - 1
        anio_sircreb = int(anio)
        if mes_sircreb == 0:
            mes_sircreb = 12
            anio_sircreb -= 1
        mes_sircreb_texto = f"{mes_sircreb:02d}"
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        digitos = solo_digitos(representado_cuit)
        representado = representado_para_nombre(nombre_representado, digitos)
        salida: dict[str, Any] = {"archivos": [], "errores": [], "filas": {}}

        # ------------------------------ SIRCREB ------------------------------
        await self.paso(
            "abrir_retenciones_sircreb",
            self.abrir_url(
                f"{BASE_COMARB}/srbPadron.do?method=detalleMensual"
                f"&anio={anio_sircreb}&mes={mes_sircreb_texto}"
            ),
        )
        html_sircreb = await self.paso("leer_sircreb", self._html_actual())
        hojas_sircreb: list[tuple[str, list[str], list[list[str]]]] = []
        for tabla, hoja in (("retencion", "Retenciones"), ("coef", "Coeficientes")):
            encabezados, filas = extraer_tablas_sifere(html_sircreb, tabla)
            if filas:
                hojas_sircreb.append((hoja, encabezados, filas))
                salida["filas"][hoja] = len(filas)
        if hojas_sircreb:
            ruta = destino_dir / nombre_sifere(
                digitos, texto, representado, ("SIRCREB", "Tablas"), ext=".xlsx"
            )
            try:
                escribir_xlsx(ruta, hojas_sircreb)
                salida["archivos"].append(ruta.name)
            except Exception as exc:
                salida["errores"].append(f"No se pudo generar SIRCREB Tablas: {exc}")

        # V1: ambas descargas comparten nombre base; la segunda pisa a la primera
        # si el portal sugiere la misma extensión (una sola entrada en archivos).
        base_sircreb = nombre_sifere(digitos, texto, representado, ("SIRCREB",))
        for accion, etiqueta in (
            (self._click_retenciones_coeficientes, "Retenciones y coeficientes"),
            (self._click_exportar_archivo, "Exportar archivo con"),
        ):
            try:
                ruta = await self.descargar_con_nombre_sugerido(
                    destino_dir, base_sircreb, accion
                )
            except Exception as exc:
                salida["errores"].append(f"No se pudo descargar {etiqueta}: {exc}")
                continue
            if ruta.name not in salida["archivos"]:
                salida["archivos"].append(ruta.name)

        # ------------------------------ SIRTAC -------------------------------
        await self.paso(
            "abrir_sirtac",
            self.abrir_url(
                f"{BASE_COMARB}/srtPadron.do?method=retenciones&cuit={digitos}&periodo={texto}"
            ),
        )
        html_sirtac = await self.paso("leer_sirtac", self._html_actual())
        enc_jur, filas_jur = extraer_tablas_sifere(html_sirtac, "inclusion")
        enc_ope, filas_ope = extraer_tablas_sifere(html_sirtac, "retencion")
        for clave, filas in (
            ("Distribución Jurisdicciones", filas_jur),
            ("Operaciones Informadas", filas_ope),
        ):
            if filas:
                salida["filas"][clave] = len(filas)

        hojas_sirtac: list[tuple[str, list[str], list[list[str]]]] = []
        if filas_jur:
            hojas_sirtac.append(("Distribución Jurisdicciones", enc_jur, filas_jur))
        if filas_ope:
            hojas_sirtac.append(("Operaciones Informadas", enc_ope, filas_ope))
        if hojas_sirtac:
            ruta = destino_dir / nombre_sifere(
                digitos, texto, representado, ("SIRTAC", "Tablas"), ext=".xlsx"
            )
            try:
                escribir_xlsx(ruta, hojas_sirtac)
                salida["archivos"].append(ruta.name)
            except Exception as exc:
                salida["errores"].append(f"No se pudo generar SIRTAC Tablas: {exc}")

        # El sitio sirve los exports de SIRTAC inline (sin descarga directa), por
        # lo que V1 genera los archivos separados desde las tablas HTML.
        for partes, hoja, encabezados, filas, etiqueta_error in (
            (
                ("SIRTAC", "jurisdicciones"),
                "Distribución Jurisdicciones",
                enc_jur,
                filas_jur,
                "jurisdicciones",
            ),
            (
                ("SIRTAC", "operaciones"),
                "Operaciones Informadas",
                enc_ope,
                filas_ope,
                "operaciones",
            ),
        ):
            if not filas:
                continue
            ruta = destino_dir / nombre_sifere(
                digitos, texto, representado, partes, ext=".xlsx"
            )
            try:
                escribir_xlsx(ruta, [(hoja, encabezados, filas)])
                salida["archivos"].append(ruta.name)
            except Exception as exc:
                salida["errores"].append(
                    f"No se pudo generar SIRTAC {etiqueta_error}: {exc}"
                )
        return salida

    async def _html_actual(self) -> str:
        try:
            return str(await self.page.content() or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Descargas (port de los ``expect_download`` de V1)

    async def descargar_con_nombre_sugerido(
        self,
        destino_dir: Path,
        base: str,
        accion: Any,
        *,
        espera_ms: int = 30_000,
    ) -> Path:
        """Captura la descarga de ``accion`` y respeta la extensión del portal.

        V1 arma el nombre con ``os.path.splitext(suggested_filename)[1]``: la
        extensión real la decide el organismo (puede ser ``.xls`` aunque el
        enlace diga "Excel"). Lanza ``TargetUnavailableError`` si la descarga no
        llega o llega vacía; cada llamador decide si eso corta la corrida.
        """
        destino_dir = Path(destino_dir)
        try:
            async with self.page.expect_download(timeout=espera_ms) as info:
                await accion()
            descarga = await info.value
            sugerido = getattr(descarga, "suggested_filename", "") or ""
            ext = os.path.splitext(sugerido)[1] or ""
            ruta = destino_dir / f"{base}{ext}"
            await descarga.save_as(str(ruta))
            if not ruta.exists() or ruta.stat().st_size == 0:
                raise TargetUnavailableError(
                    "la descarga SIFERE llegó vacía",
                    diagnostic_code="sifere_download_empty",
                )
            return ruta
        except TargetUnavailableError:
            raise
        except Exception as exc:
            raise TargetUnavailableError(
                f"el portal SIFERE no entregó la descarga esperada ({type(exc).__name__})",
                diagnostic_code="sifere_download_missing",
            ) from exc

    async def _click_retenciones_coeficientes(self) -> None:
        """V1: ``get_by_role("link", name="Retenciones y coeficientes")``."""
        if not await self._clickear(
            (
                self.page.get_by_role(
                    "link", name=re.compile(r"Retenciones y [Cc]oeficientes", re.I)
                ),
                self.page.locator("a:has-text('Retenciones y coeficientes')"),
            ),
            total_ms=15_000,
        ):
            raise TargetUnavailableError(
                "el portal SIFERE no ofreció 'Retenciones y coeficientes'",
                diagnostic_code="sifere_sircreb_link_missing",
            )

    async def _click_exportar_archivo(self) -> None:
        """V1: ``get_by_role("link", name="Exportar archivo con").nth(1)``.

        Se prefiere el segundo enlace (el de V1) y, si no existe, se cae al
        primer visible: un portal con un solo enlace no debe cortar la corrida.
        """
        rol = self.page.get_by_role(
            "link", name=re.compile(r"Exportar archivo con", re.I)
        )
        texto = self.page.locator("a:has-text('Exportar archivo con')")
        try:
            if await self._clickear((rol.nth(1), texto.nth(1), rol, texto), total_ms=15_000):
                return
        except Exception:
            pass
        raise TargetUnavailableError(
            "el portal SIFERE no ofreció 'Exportar archivo con'",
            diagnostic_code="sifere_sircreb_export_missing",
        )

    async def _click_excel(self) -> None:
        """V1: ``get_by_role("link", name="Excel")``."""
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"^Excel$", re.I)),
                self.page.locator("a:has-text('Excel')"),
                self.page.locator("input[type='submit'][value*='Excel' i]"),
                self.page.get_by_role("link", name=re.compile(r"Excel", re.I)),
            ),
            total_ms=15_000,
        ):
            raise TargetUnavailableError(
                "el portal SIFERE no ofreció la descarga Excel",
                diagnostic_code="sifere_excel_missing",
            )

    async def _click_pdf(self) -> None:
        """V1: ``get_by_role("link", name="PDF")``."""
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"^PDF$", re.I)),
                self.page.locator("a:has-text('PDF')"),
                self.page.get_by_role("link", name=re.compile(r"PDF", re.I)),
            ),
            total_ms=15_000,
        ):
            raise TargetUnavailableError(
                "el portal SIFERE no ofreció la descarga PDF",
                diagnostic_code="sifere_pdf_missing",
            )


class _TablaHTML(HTMLParser):
    """Extrae encabezados y filas de la tabla con ``id`` dado (port de ``extraer_tabla_html``).

    V1 lee los ``th`` de la primera fila del ``thead`` y los ``td`` de cada fila
    del ``tbody``; las tablas sin esa estructura se descartan. Sin dependencias
    externas.
    """

    def __init__(self, table_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._id = table_id
        self._en_tabla = False
        self._profundidad = 0
        self._seccion = ""
        self._en_fila = False
        self._celda: list[str] | None = None
        self._fila: list[str] = []
        self.encabezados: list[str] = []
        self.filas: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._en_tabla:
                self._profundidad += 1
            elif dict(attrs).get("id") == self._id:
                self._en_tabla = True
            return
        if not self._en_tabla or self._profundidad:
            return
        if tag in ("thead", "tbody"):
            self._seccion = tag
        elif tag == "tr":
            self._en_fila = True
            self._fila = []
        elif tag == "th" and self._en_fila and self._seccion == "thead":
            self._celda = []
        elif tag == "td" and self._en_fila and self._seccion == "tbody":
            self._celda = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._profundidad:
                self._profundidad -= 1
            elif self._en_tabla:
                self._en_tabla = False
            return
        if not self._en_tabla or self._profundidad:
            return
        if tag in ("th", "td") and self._celda is not None:
            self._fila.append(" ".join("".join(self._celda).split()))
            self._celda = None
        elif tag == "tr" and self._en_fila:
            self._en_fila = False
            if self._seccion == "thead" and not self.encabezados and self._fila:
                self.encabezados = list(self._fila)
            elif self._seccion == "tbody" and any(self._fila):
                self.filas.append(list(self._fila))
        elif tag in ("thead", "tbody"):
            self._seccion = ""

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda.append(data)


def extraer_tablas_sifere(html: str, table_id: str) -> tuple[list[str], list[list[str]]]:
    """Encabezados y filas de la tabla ``table_id`` con las reglas de V1.

    Port exacto de ``extraer_tabla_html`` (V1 ``sifere_bot.py``):

    - sin ``thead`` o sin filas la tabla se descarta;
    - si las filas tienen anchos distintos la tabla se descarta (en V1 el
      DataFrame de pandas fallaba y devolvía vacío);
    - si la fila tiene más celdas que encabezados se antepone ``Acciones``
      (V1: columna de botones que agrega el portal);
    - si sobran encabezados se recortan al ancho de la fila.
    """
    parser = _TablaHTML(table_id)
    parser.feed(html or "")
    parser.close()
    if not parser.encabezados or not parser.filas:
        return [], []
    ancho = len(parser.filas[0])
    if any(len(fila) != ancho for fila in parser.filas):
        return [], []
    encabezados = list(parser.encabezados)
    if len(encabezados) < ancho:
        encabezados = ["Acciones"] + encabezados
    elif len(encabezados) > ancho:
        encabezados = encabezados[:ancho]
    if len(encabezados) != ancho:
        # V1: un desajuste mayor a una columna hacía fallar el DataFrame.
        return [], []
    return encabezados, parser.filas


def escribir_xlsx(destino: Path, hojas: list[tuple[str, list[str], list[list[str]]]]) -> None:
    """Escribe un .xlsx minimo con una hoja por tupla ``(nombre, encabezados, filas)``.

    Sin pandas: el worker no depende de librerias de hojas de calculo. Las
    celdas son texto, porque los importes llegan como texto desde el portal.
    """
    from xml.sax.saxutils import escape as xml_escape

    def letra(indice: int) -> str:
        nombre = ""
        indice += 1
        while indice:
            indice, resto = divmod(indice - 1, 26)
            nombre = chr(65 + resto) + nombre
        return nombre

    def celda(fila: int, columna: int, valor: str) -> str:
        ref = f"{letra(columna)}{fila}"
        texto = xml_escape(str(valor))
        return f'<c r="{ref}" t="inlineStr"><is><t>{texto}</t></is></c>'

    hojas_xml: list[str] = []
    for _, encabezados, filas in hojas:
        todas = [encabezados] + filas if encabezados else filas
        filas_xml = []
        for numero, fila in enumerate(todas, start=1):
            celdas = "".join(celda(numero, col, val) for col, val in enumerate(fila))
            filas_xml.append(f'<row r="{numero}">{celdas}</row>')
        hojas_xml.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(filas_xml)}</sheetData></worksheet>"
        )

    nombres = [xml_escape(nombre[:31], {'"': "&quot;"}) for nombre, _, _ in hojas]
    hojas_entrada = "".join(
        f'<sheet name="{nombre}" sheetId="{i}" r:id="rId{i}"/>'
        for i, nombre in enumerate(nombres, start=1)
    )
    relaciones = "".join(
        '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>'
        % (i, i)
        for i in range(1, len(hojas) + 1)
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(hojas) + 1)
    )
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as libro:
        libro.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>",
        )
        libro.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        libro.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{hojas_entrada}</sheets></workbook>",
        )
        libro.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relaciones}</Relationships>",
        )
        for indice, xml in enumerate(hojas_xml, start=1):
            libro.writestr(f"xl/worksheets/sheet{indice}.xml", xml)


def _contar_filas(ruta: Path) -> int:
    """Cantidad de filas si el archivo es texto; 0 si es binario."""
    try:
        with open(ruta, "rb") as fh:
            crudo = fh.read(200_000)
        if crudo[:2] in (b"PK", b"\xd0\xcf"):
            return 0
        for codec in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = crudo.decode(codec)
                break
            except UnicodeDecodeError:
                continue
        else:
            return 0
        return max(0, len([l for l in texto.splitlines() if l.strip()]) - 1)
    except Exception:
        return 0
