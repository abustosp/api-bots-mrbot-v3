"""Portal de Liquidación Primaria de Granos de ARCA.

Porta la consulta de V1/V2 a la interfaz ``PortalArca``. Las planillas se
construyen a partir de las tablas consultadas y los comprobantes se obtienen
por el endpoint de reportes del propio portal, con click como respaldo.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import re
import unicodedata
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlparse

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

_SELECTOR_DESCARGA = (
    "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, "
    "a[onclick*='.pdf'], a[href*='.pdf'], "
    "a[onclick*='descarg'], a[href*='descarg'], a[title*='Descargar']"
)
_SELECTOR_CERTIFICADOS = "a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]"
_MENU_LPG = re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I)
_MENU_LSG = re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I)
_MENU_CERTIFICADOS = re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I)
_MENU_CONSULTA_CERTIFICADOS = re.compile(r"Consulta\s+de\s+Certificados\s+de", re.I)
_SECCIONES = {
    "lpg_emitidas": (
        _MENU_LPG,
        re.compile(r"Consulta\s+de\s+Liquidaciones(?!\s+Recibidas)", re.I),
    ),
    "lpg_recibidas": (_MENU_LPG, re.compile(r"Consulta\s+Liquidaciones\s+Recibidas", re.I)),
    "lsg_emitidas": (_MENU_LSG, re.compile(r"Consulta\s+de\s+Liquidaciones\s+Emitidas", re.I)),
    "lsg_recibidas": (_MENU_LSG, re.compile(r"Consulta\s+de\s+Liquidaciones\s+Recibidas", re.I)),
}
_TIPOS = {
    "lpg_emitidas": ("LPG", "emitidas"),
    "lpg_recibidas": ("LPG", "recibidas"),
    "lsg_emitidas": ("LSG", "emitidas"),
    "lsg_recibidas": ("LSG", "recibidas"),
    "certificados_deposito": ("CD", "deposito"),
}
_MAX_PDF_BYTES = 10 * 1024 * 1024
#: similitud mínima para aceptar una denominación (mismo umbral que V2).
_UMBRAL_DENOMINACION = 0.90
#: Expresión JS que enumera los botones de representado de la pantalla
#: "Seleccione la Empresa". Se usa para listar y para clickear por índice,
#: de modo que ambos usos recorran exactamente los mismos elementos.
_JS_BOTONES_EMPRESA = """
(() => {
    const forma = document.querySelector("form[name='seleccionaEmpresaForm']")
        || document.querySelector("form[action*='setearContribuyente']");
    const ambito = forma || document;
    let botones = Array.from(
        ambito.querySelectorAll("input[type='button'], input[type='submit'], button")
    );
    if (!forma) {
        botones = botones.filter((b) => {
            const clase = String(b.className || '').toLowerCase();
            const onclick = String(b.getAttribute('onclick') || '');
            return clase.includes('botonempresa') || onclick.includes('continuar(');
        });
    }
    return botones;
})()
""".strip()
#: ``(...)`` explícito: sin él, el salto de línea tras ``return`` activa la
#: inserción automática de punto y coma y la expresión queda sin efecto.
_JS_LISTAR_EMPRESAS = f"""
() => {{
    return ({_JS_BOTONES_EMPRESA}).map((b, i) => ({{
        index: i,
        text: String(b.value || b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim(),
        visible: !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length),
    }}));
}}
"""
_JS_CLICK_EMPRESA = f"""
(indice) => {{
    const botones = ({_JS_BOTONES_EMPRESA});
    const boton = botones[indice];
    if (!boton) return false;
    boton.click();
    return true;
}}
"""
#: Tablas "hoja" (sin tablas anidadas) con sus encabezados y filas de datos.
#: El portal de Granos envuelve todo el cuerpo en tablas de maquetado; la
#: grilla real (``class="jig_table"``) es una tabla hoja cuyas filas de datos
#: llevan el link de descarga del comprobante. Igual que V2, la planilla se
#: arma con esas filas y no con el contenedor.
_JS_TABLAS_HOJA = """
() => {
    const DESCARGA = "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, "
        + "a[onclick*='.pdf'], a[href*='.pdf'], a[onclick*='descarg'], "
        + "a[href*='descarg'], a[title*='Descargar']";
    const limpiar = (valor) => String(valor || '').replace(/\\s+/g, ' ').trim();
    const tablas = [];
    document.querySelectorAll('table').forEach((tabla, indice) => {
        if (tabla.querySelector('table')) return;
        const encabezados = [];
        const rows = [];
        const descargas = [];
        let titulo = '';
        tabla.querySelectorAll('tr').forEach((tr) => {
            const ths = Array.from(tr.querySelectorAll(':scope > th'));
            const tds = Array.from(tr.querySelectorAll(':scope > td'));
            if (ths.length && !tds.length) {
                const textos = ths.map((celda) => limpiar(celda.innerText)).filter((t) => t);
                if (textos.length >= 2) encabezados.push(textos);
                else if (!titulo && textos.length === 1) titulo = textos[0];
                return;
            }
            if (!tds.length) return;
            const celdas = tds.map((celda) => limpiar(celda.innerText));
            if (!celdas.some((celda) => celda)) return;
            rows.push(celdas);
            descargas.push(!!tr.querySelector(DESCARGA));
        });
        if (rows.length || encabezados.length) {
            tablas.push({
                title: titulo || ('tabla_' + (indice + 1)),
                encabezados: encabezados,
                rows: rows,
                descargas: descargas,
            });
        }
    });
    return tablas;
}
"""
#: CUITs visibles en el encabezado del portal ("Usuario:" / "Representando a:"
#: / "Bienvenido ... CUIT:"). Solo mira tablas hoja con texto de encabezado,
#: así una grilla de resultados con CUITs de terceros no ensucia la guarda.
_JS_CUITS_ENCABEZADO = """
() => {
    const PATRON = /usuario|representando\\s+a|bienvenido|cuit/i;
    const textos = [];
    document.querySelectorAll('table, .footer, .contenido').forEach((nodo) => {
        if (nodo.querySelector('table')) return;
        const texto = String(nodo.innerText || '');
        if (PATRON.test(texto)) textos.push(texto);
    });
    return textos.join(' ').match(/\\d{11}/g) || [];
}
"""


def _limpiar_xml(valor: Any) -> str:
    texto = str(valor or "")
    return "".join(
        caracter
        for caracter in texto
        if caracter in "\t\n\r" or ord(caracter) >= 32
    )


def _celda_excel(columna: int, fila: int, valor: Any) -> str:
    numero = columna
    letras = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    contenido = html.escape(_limpiar_xml(valor), quote=False)
    return (
        f'<c r="{letras}{fila}" t="inlineStr"><is>'
        f'<t xml:space="preserve">{contenido}</t></is></c>'
    )


def _guardar_xlsx(destino: Path, encabezados: list[str], filas: list[list[Any]]) -> Path:
    """Escribe un XLSX mínimo usando solo la biblioteca estándar."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not encabezados:
        encabezados = ["Resultado"]
    if not filas and encabezados == ["Resultado"]:
        filas = [["Sin registros"]]
    max_columnas = max([len(encabezados), *(len(fila) for fila in filas)], default=1)
    if len(encabezados) < max_columnas:
        encabezados.extend(f"Columna {i}" for i in range(len(encabezados) + 1, max_columnas + 1))
    filas_xml = []
    for num_fila, valores in enumerate([encabezados, *filas], start=1):
        valores = list(valores) + [""] * (max_columnas - len(valores))
        celdas = "".join(_celda_excel(idx, num_fila, valor) for idx, valor in enumerate(valores, 1))
        filas_xml.append(f'<row r="{num_fila}">{celdas}</row>')
    hoja = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(filas_xml)}</sheetData></worksheet>'
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    raiz_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Liquidaciones" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr("[Content_Types].xml", tipos)
        archivo.writestr("_rels/.rels", raiz_rels)
        archivo.writestr("xl/workbook.xml", workbook)
        archivo.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archivo.writestr("xl/worksheets/sheet1.xml", hoja)
    return destino


def _limpiar_nombre(valor: str) -> str:
    limpio = re.sub(r"[\\/:*?\"<>|]+", "_", str(valor or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio or "archivo"


def _normalizar_denominacion(texto: str) -> str:
    """Mayúsculas, sin acentos ni puntuación, para comparar denominaciones."""
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    sin_acentos = "".join(caracter for caracter in plano if not unicodedata.combining(caracter))
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", sin_acentos).upper().split())


def _similitud_denominacion(esperado: str, encontrado: str) -> float:
    """Puntúa denominaciones aunque el portal agregue el CUIT al botón.

    Misma métrica que el resto de los portales portados de V2: coincidencia
    exacta o contención valen 1, y si no se combina cobertura por token.
    """
    pedido = _normalizar_denominacion(esperado)
    candidato = _normalizar_denominacion(encontrado)
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


def _ruta_unica(destino: Path) -> Path:
    if not destino.exists():
        return destino
    contador = 2
    while True:
        candidata = destino.with_name(f"{destino.stem}_{contador}{destino.suffix}")
        if not candidata.exists():
            return candidata
        contador += 1


class LiquidacionGranosPortal(PortalArca):
    """Navegación de planillas y comprobantes de Liquidación de Granos."""

    nombre = "liquidacion_granos"

    def __init__(self, page: Any, *, captcha: Any = None, context: Any = None, service_name: str = "") -> None:
        super().__init__(page, captcha=captcha, context=context, service_name=service_name)
        self._representado_cuit = ""
        self._representado_nombre = ""

    # ------------------------------------------------------------------
    # Selección del representado

    def _menu_lpg_localizadores(self) -> tuple[Any, ...]:
        """Localizadores del botón de menú que prueba que estamos en LPG."""
        return (
            self.page.get_by_role("button", name=_MENU_LPG),
            self.page.get_by_role("link", name=_MENU_LPG),
            self.page.locator("button, a, [role='button'], .usarManito").filter(
                has_text=_MENU_LPG
            ),
        )

    async def _esta_en_lpg(self) -> bool:
        """True si el documento ya es el menú del servicio de Granos.

        Es la misma señal que usa V2 para decidir que el representado ya está
        activo; se mira ``count()`` (no visibilidad) porque el menú puede
        estar plegado.
        """
        for localizador in self._menu_lpg_localizadores():
            try:
                if await localizador.count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _esperar_entrada_lpg(self, total_ms: int = 20_000) -> bool:
        await self._esperar_red()
        limite = asyncio.get_running_loop().time() + total_ms / 1000
        while True:
            if await self._esta_en_lpg():
                return True
            if asyncio.get_running_loop().time() >= limite:
                return False
            await self._esperar(500)

    async def _cuits_del_encabezado(self) -> list[str]:
        """CUITs de 11 dígitos visibles en el encabezado del portal."""
        try:
            datos = await self.page.evaluate(_JS_CUITS_ENCABEZADO)
        except Exception:
            return []
        if not isinstance(datos, list):
            return []
        vistos: list[str] = []
        for valor in datos:
            digitos = solo_digitos(valor)
            if len(digitos) == 11 and digitos not in vistos:
                vistos.append(digitos)
        return vistos

    async def _verificar_representado(self) -> None:
        """Guarda de corrección tras elegir la empresa.

        El portal muestra el CUIT del representado activo en el encabezado
        (``Usuario:`` / ``Representando a:`` / ``Bienvenido ... CUIT:``). Si
        hay CUITs visibles y ninguno es el pedido, el job se detiene antes de
        descargar: vale más no descargar nada que entregar datos de otro
        contribuyente con el nombre de archivo del representado pedido. Si el
        encabezado no expone ningún CUIT, se continúa (no se puede verificar).
        """
        cuits = await self._cuits_del_encabezado()
        if cuits and self._representado_cuit not in cuits:
            raise TargetUnavailableError(
                "el portal quedó en un representado distinto al pedido",
                diagnostic_code="granos_representado_incorrecto",
            )

    async def _botones_empresa(self) -> list[dict[str, Any]]:
        """Botones de la pantalla "Seleccione la Empresa" leídos del DOM."""
        try:
            datos = await self.page.evaluate(_JS_LISTAR_EMPRESAS)
        except Exception:
            return []
        if not isinstance(datos, list):
            return []
        return [dato for dato in datos if isinstance(dato, dict)]

    async def _click_boton_empresa(self, texto: str, indice: int) -> bool:
        if texto:
            try:
                boton = self.page.get_by_role(
                    "button", name=re.compile(rf"^{re.escape(texto)}$", re.I)
                )
                if await boton.count() > 0:
                    objetivo = boton.first
                    try:
                        await objetivo.scroll_into_view_if_needed(timeout=2_000)
                    except Exception:
                        pass
                    try:
                        await objetivo.click(timeout=8_000)
                        return True
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            return bool(await self.page.evaluate(_JS_CLICK_EMPRESA, indice))
        except Exception:
            return False

    async def _elegir_empresa_por_denominacion(self, nombre: str) -> bool:
        """Clic en el botón del representado cuya denominación coincide.

        El portal de LPG no expone un combo por CUIT: la pantalla "Seleccione
        la Empresa" lista un ``input[type=button]`` por representado (con la
        denominación en ``value`` y ``onclick="continuar('<id>')"``).
        """
        botones = await self._botones_empresa()
        if not botones:
            return False
        candidatos: list[tuple[float, bool, int, str]] = []
        for boton in botones:
            texto = str(boton.get("text") or "")
            if not texto:
                continue
            candidatos.append(
                (
                    _similitud_denominacion(nombre, texto),
                    bool(boton.get("visible")),
                    int(boton.get("index") or 0),
                    texto,
                )
            )
        candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for puntaje, _visible, indice, texto in candidatos:
            if puntaje < _UMBRAL_DENOMINACION:
                break
            if not await self._click_boton_empresa(texto, indice):
                continue
            if await self._esperar_entrada_lpg():
                await self._verificar_representado()
                return True
        return False

    async def seleccionar_representado(self, cuit: str, denominacion: str = "") -> None:
        """Selecciona al representado del portal de Granos.

        Port de ``seleccionar_denominacion`` de V2: si el menú de LPG ya está
        visible, no hay nada que elegir; si no, se elige al representado por
        denominación (``representado_nombre``) y, como respaldo, se conserva
        el camino genérico por CUIT del portal base.
        """
        self._representado_cuit = solo_digitos(cuit)
        self._representado_nombre = str(denominacion or "").strip()
        await self.paso("seleccionar_representado", self._seleccionar_representado())

    async def _seleccionar_representado(self) -> None:
        digits = self._representado_cuit
        if await self._esta_en_lpg():
            # V1/V2 llegan directamente al servicio para el representado activo.
            await self._verificar_representado()
            return
        if self._representado_nombre and await self._elegir_empresa_por_denominacion(
            self._representado_nombre
        ):
            return
        if await self._seleccionar_en_combo(digits):
            return
        if self._representado_nombre:
            botones = len(await self._botones_empresa())
            raise TargetUnavailableError(
                "no se encontró el representado por su denominación "
                f"({botones} botones de empresa en la página)",
                diagnostic_code="granos_company_not_found",
            )
        if await self._ya_esta_seleccionado(digits):
            return
        mapa = await self._mapa_de_selectores()
        raise TargetUnavailableError(
            f"no se pudo seleccionar el CUIT representado ({mapa})",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def tablas_hoja(self) -> list[dict[str, Any]]:
        """Tablas sin anidar del documento: las grillas de resultados."""
        try:
            datos = await self.page.evaluate(_JS_TABLAS_HOJA)
        except Exception:
            return []
        return [tabla for tabla in (datos or []) if isinstance(tabla, dict)]

    async def descargar_planilla(
        self, *, seccion: str, destino: Path, desde: str, hasta: str
    ) -> Path:
        """Consulta una sección y guarda su tabla como XLSX válido."""
        if seccion not in _TIPOS:
            raise TargetUnavailableError(
                "sección desconocida de Liquidación de Granos",
                diagnostic_code="granos_section_unknown",
            )
        if seccion == "certificados_deposito":
            await self._abrir_certificados()
            tipos = await self._tipos_certificado()
            encabezados_certificados: list[str] = []
            filas_certificados: list[list[Any]] = []
            for valor, _ in tipos:
                await self._consultar_certificados(desde, hasta, valor)
                datos = self._tabla_de_datos(await self.tablas_hoja())
                if datos is None:
                    continue
                encabezados, filas = datos
                if not encabezados_certificados:
                    encabezados_certificados = encabezados
                filas_certificados.extend([[valor, *fila] for fila in filas])
            if filas_certificados:
                encabezados, filas = self._completar_tabla(
                    ["Tipo de consulta", *encabezados_certificados], filas_certificados
                )
            else:
                encabezados, filas = ["Resultado"], [["Sin registros"]]
        else:
            await self._abrir_seccion(seccion)
            await self._consultar_fechas(desde, hasta)
            encabezados, filas = self._filas_de_tablas(await self.tablas_hoja())
        return await self.paso(
            "descargar_planilla",
            asyncio.to_thread(_guardar_xlsx, Path(destino), encabezados, filas),
        )

    async def descargar_comprobantes(
        self, *, seccion: str, destino_dir: Path, desde: str, hasta: str
    ) -> list[Path]:
        """Descarga los PDF de una consulta, sin exponer cookies al plugin."""
        if seccion not in _TIPOS:
            raise TargetUnavailableError(
                "sección desconocida de Liquidación de Granos",
                diagnostic_code="granos_section_unknown",
            )
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        tipo, consulta = _TIPOS[seccion]
        if seccion == "certificados_deposito":
            await self._abrir_certificados()
            descargados: list[Path] = []
            for valor, _ in await self._tipos_certificado():
                await self._consultar_certificados(desde, hasta, valor)
                descargados.extend(await self._descargar_certificados(destino_dir, valor))
            return descargados
        await self._abrir_seccion(seccion)
        await self._consultar_fechas(desde, hasta)
        return await self._descargar_comprobantes_de_pagina(
            destino_dir, tipo, consulta
        )

    @staticmethod
    def _filas_de_tablas(tablas: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
        """Planilla de una consulta: filas de datos de la grilla real.

        Port de ``parse_table_records`` de V2: la grilla de resultados es la
        tabla cuyas filas traen el link de descarga del comprobante; el resto
        de las tablas del portal son maquetado con el encabezado y el menú.
        Sin filas de datos se devuelve una planilla informativa.
        """
        datos = LiquidacionGranosPortal._tabla_de_datos(tablas)
        if datos is None:
            return ["Resultado"], [["Sin registros"]]
        return LiquidacionGranosPortal._completar_tabla(*datos)

    @staticmethod
    def _columnas_encabezado(tabla: dict[str, Any]) -> list[list[str]]:
        """Filas de encabezado candidatas (tolerante al formato V3 anterior)."""
        filas = tabla.get("encabezados")
        if isinstance(filas, list) and filas and isinstance(filas[0], (list, tuple)):
            return [
                [str(celda or "") for celda in fila]
                for fila in filas
                if isinstance(fila, (list, tuple))
            ]
        cabecera = tabla.get("headers") or tabla.get("encabezados")
        if isinstance(cabecera, (list, tuple)) and cabecera:
            return [[str(celda or "") for celda in cabecera]]
        return []

    @classmethod
    def _mejor_encabezado(cls, tabla: dict[str, Any], ancho_datos: int) -> list[str]:
        """Encabezado más parecido al ancho de las filas (regla de V2)."""
        candidatas = [
            fila for fila in cls._columnas_encabezado(tabla) if len(fila) >= 2
        ]
        if not candidatas:
            return []
        return min(candidatas, key=lambda fila: abs(len(fila) - ancho_datos))

    @staticmethod
    def _filas_con_descarga(tabla: dict[str, Any]) -> list[list[Any]]:
        """Filas de datos: las que tienen link de descarga (port de V2)."""
        filas = [
            list(fila) for fila in tabla.get("rows") or [] if isinstance(fila, (list, tuple))
        ]
        marcas = tabla.get("descargas")
        if not isinstance(marcas, list) or len(marcas) != len(filas):
            # Formato sin marcas: todas las filas valen como datos.
            return filas
        return [fila for fila, marca in zip(filas, marcas) if marca]

    @classmethod
    def _tabla_de_datos(
        cls, tablas: list[dict[str, Any]]
    ) -> tuple[list[str], list[list[Any]]] | None:
        """Grilla de resultados más rica, o ``None`` si no hay filas de datos."""
        candidatas: list[tuple[int, int, dict[str, Any], list[list[Any]]]] = []
        for tabla in tablas or []:
            if not isinstance(tabla, dict):
                continue
            filas = cls._filas_con_descarga(tabla)
            if not filas:
                continue
            anchos = [len(fila) for fila in filas]
            anchos.extend(len(fila) for fila in cls._columnas_encabezado(tabla))
            candidatas.append((len(filas), max(anchos, default=0), tabla, filas))
        if not candidatas:
            return None
        _, _, tabla, filas = max(candidatas, key=lambda item: (item[0], item[1]))
        encabezados = cls._mejor_encabezado(tabla, max(len(fila) for fila in filas))
        return encabezados, filas

    @staticmethod
    def _completar_tabla(
        encabezados: list[str], filas: list[list[Any]]
    ) -> tuple[list[str], list[list[Any]]]:
        """Alinea encabezados y filas al mismo ancho, sin celdas faltantes."""
        ancho = max([len(encabezados), *(len(fila) for fila in filas)], default=0)
        if ancho == 0:
            return ["Resultado"], [["Sin registros"]]
        cabecera = list(encabezados) + [""] * (ancho - len(encabezados))
        cabecera = [
            str(texto or "").strip() or f"Columna {indice}"
            for indice, texto in enumerate(cabecera, 1)
        ]
        completas = [
            [*fila, *[""] * (ancho - len(fila))] if len(fila) < ancho else list(fila)
            for fila in filas
        ]
        return cabecera, completas

    async def _click_patron(self, patron: re.Pattern[str], nombre_paso: str) -> None:
        candidatos = (
            self.page.get_by_role("button", name=patron),
            self.page.get_by_role("link", name=patron),
            self.page.locator("button, a, [role='button'], .usarManito").filter(has_text=patron),
        )
        elemento = await self._primero_visible(candidatos, total_ms=8_000)
        if elemento is None:
            raise TargetUnavailableError(
                f"no se encontró la opción de menú {nombre_paso}",
                diagnostic_code=f"granos_menu_{nombre_paso}",
            )
        try:
            await elemento.click(timeout=10_000)
        except Exception as exc:
            raise TargetUnavailableError(
                f"no se pudo abrir la opción de menú {nombre_paso}",
                diagnostic_code=f"granos_menu_{nombre_paso}",
            ) from exc
        await self._esperar_red()

    async def _volver_menu(self) -> None:
        try:
            menu = self.page.get_by_role("button", name=re.compile(r"Men[úu]\s+principal", re.I))
            if await menu.count() > 0 and await menu.first.is_visible(timeout=500):
                await menu.first.click(timeout=8_000)
                await self._esperar_red()
        except Exception:
            # Al entrar por primera vez ya puede estar visible el menú principal.
            return

    async def _abrir_seccion(self, seccion: str) -> None:
        modulo, consulta = _SECCIONES[seccion]
        await self._volver_menu()
        await self._click_patron(modulo, f"modulo_{seccion}")
        await self._click_patron(consulta, f"consulta_{seccion}")

    async def _abrir_certificados(self) -> None:
        await self._volver_menu()
        await self._click_patron(_MENU_CERTIFICADOS, "certificacion_electronica")
        await self._click_patron(_MENU_CONSULTA_CERTIFICADOS, "consulta_certificados")

    async def _esperar_red(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=12_000)

    async def _consultar_fechas(self, desde: str, hasta: str) -> None:
        try:
            await self.page.locator("input[name='fechaStr']").fill(desde)
            await self.page.locator("input[name='fechaHastaStr']").fill(hasta)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se encontraron los campos de fecha de Liquidación de Granos",
                diagnostic_code="granos_date_fields_missing",
            ) from exc
        await self._click_patron(
            re.compile(r"Consultar\s+Por\s+Criterio", re.I), "consultar"
        )

    async def _tipos_certificado(self) -> list[tuple[str, str]]:
        selector = self.page.locator("#tipoConsulta")
        try:
            opciones = await selector.first.evaluate(
                "el => Array.from(el.options).map(o => ({value:(o.value||'').trim(), text:(o.textContent||'').trim()}))"
            )
        except Exception as exc:
            raise TargetUnavailableError(
                "no se encontró el tipo de consulta de certificados",
                diagnostic_code="granos_certificate_type_missing",
            ) from exc
        resultado: list[tuple[str, str]] = []
        vistos: set[str] = set()
        for opcion in opciones or []:
            valor = str(opcion.get("value") or "").strip()
            texto = str(opcion.get("text") or "").strip()
            if not valor or valor == "-1" or valor in vistos:
                continue
            vistos.add(valor)
            resultado.append((valor, texto or valor))
        if not resultado:
            raise TargetUnavailableError(
                "no hay tipos de consulta de certificados disponibles",
                diagnostic_code="granos_certificate_type_empty",
            )
        return resultado

    async def _consultar_certificados(self, desde: str, hasta: str, tipo: str) -> None:
        try:
            await self.page.locator("#tipoConsulta").first.select_option(value=tipo)
            await self.page.locator("#fechaDesde").first.fill(desde)
            await self.page.locator("#fechaHasta").first.fill(hasta)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudieron completar las fechas de certificados",
                diagnostic_code="granos_certificate_date_fields_missing",
            ) from exc
        await self._click_patron(re.compile(r"Consultar", re.I), "consultar_certificados")

    async def _descargar_comprobantes_de_pagina(
        self, destino_dir: Path, tipo: str, consulta: str
    ) -> list[Path]:
        filas = self.page.locator("tr").filter(has=self.page.locator(_SELECTOR_DESCARGA))
        try:
            cantidad = await filas.count()
        except Exception:
            return []
        resultado: list[Path] = []
        procesadas: set[str] = set()
        for indice in range(cantidad):
            fila = filas.nth(indice)
            try:
                if await fila.locator("table").count() > 0:
                    continue
                enlace = fila.locator(_SELECTOR_DESCARGA).first
                onclick = str(await enlace.get_attribute("onclick") or "")
                href = str(await enlace.get_attribute("href") or "")
                raw = onclick or href
                llave = self._llave_descarga(raw)
                if llave and llave in procesadas:
                    continue
                if llave:
                    procesadas.add(llave)
                texto = str(await fila.inner_text() or "")
            except Exception:
                continue
            digitos = re.findall(r"\b\d{11,14}\b", texto)
            cuit = next((token for token in digitos if len(token) == 11), "SIN_CUIT_VENDEDOR")
            coe = next((token for token in digitos if len(token) >= 12), "SIN_COE")
            etiqueta = "Emitida" if "emit" in consulta else "Recibida"
            nombre = _limpiar_nombre(
                f"{tipo} - {etiqueta} - {self._representado_cuit or 'representado'} - {cuit} - {coe}.pdf"
            )
            destino = _ruta_unica(destino_dir / nombre)
            guardado = await self._pdf_desde_enlace(raw, destino)
            if guardado is None:
                guardado = await self._pdf_por_click(enlace, destino)
            if guardado is not None:
                resultado.append(guardado)
        return resultado

    @staticmethod
    def _llave_descarga(raw: str) -> str:
        for patron, etiqueta in (
            (r"liqCodigo=(\d+)", "liqCodigo"),
            (r"coe=(\d+)", "coe"),
            (r"certificado(?:Id)?=(\d+)", "certificadoId"),
            (r"codigo=(\d+)", "codigo"),
            (r"id=(\d+)", "id"),
            (r"\b(\d{6,})\b", "numero"),
        ):
            match = re.search(patron, raw or "", flags=re.I)
            if match:
                return f"{etiqueta}={match.group(1)}"
        return ""

    async def _descargar_certificados(self, destino_dir: Path, tipo: str) -> list[Path]:
        enlaces = self.page.locator(_SELECTOR_CERTIFICADOS)
        try:
            cantidad = await enlaces.count()
        except Exception:
            return []
        resultado: list[Path] = []
        vistos: set[tuple[str, str, str]] = set()
        for indice in range(cantidad):
            enlace = enlaces.nth(indice)
            try:
                clase = str(await enlace.get_attribute("data-tipocertificado") or "").strip().upper()
                codigo = solo_digitos(await enlace.get_attribute("data-liqcodigo"))
                coe = solo_digitos(await enlace.get_attribute("data-coe"))
            except Exception:
                continue
            llave = (clase, codigo, coe)
            if not all(llave) or llave in vistos:
                continue
            vistos.add(llave)
            nombre = _limpiar_nombre(
                f"CD - {tipo} - deposito - {self._representado_cuit or 'representado'} - SIN_CUIT_VENDEDOR - {coe}.pdf"
            )
            destino = _ruta_unica(destino_dir / nombre)
            url = urljoin(
                str(self.page.url or ""),
                "generarReporteCEG.do?" + urlencode(
                    {"tipo": clase, "ceeCodigo": codigo, "coe": coe}
                ),
            )
            guardado = await self._descargar_pdf_url(url, destino, str(self.page.url or ""))
            if guardado is None:
                guardado = await self._pdf_por_click(enlace, destino)
            if guardado is not None:
                resultado.append(guardado)
        return resultado

    async def _pdf_desde_enlace(self, raw: str, destino: Path) -> Path | None:
        valor = html.unescape(raw or "")
        pdf_match = re.search(r"pd\.jsp\?[^'\";\s]+", valor, flags=re.I)
        reporte_match = re.search(
            r"generarReporte(?:DebitoCredito)?\.do\?[^'\";\s]+", valor, flags=re.I
        )
        pd_path = pdf_match.group(0).strip() if pdf_match else ""
        reporte = reporte_match.group(0).strip() if reporte_match else ""
        if pd_path:
            query = urlparse("https://dummy/" + pd_path.lstrip("/")).query
            acciones = parse_qs(query).get("action", [])
            if acciones:
                reporte = unquote(acciones[0]).strip()
        if not reporte:
            return None
        pagina_url = str(self.page.url or "")
        pd_url = urljoin(pagina_url, pd_path) if pd_path else ""
        reporte_url = urljoin(pagina_url, reporte)
        http_saved_id = ""
        if pd_url:
            if not self._misma_origen(pagina_url, pd_url):
                return None
            try:
                previa = await self.page.request.get(
                    pd_url, headers={"Referer": pagina_url}, timeout=30_000
                )
                if previa.status >= 400:
                    return None
                cuerpo = (await previa.body()).decode("latin-1", errors="ignore")
                encontrado = re.search(
                    r"name\s*=\s*['\"]HTTP_SAVED_ID['\"][^>]*value\s*=\s*['\"]([^'\"]+)['\"]",
                    cuerpo,
                    flags=re.I,
                )
                if encontrado:
                    http_saved_id = encontrado.group(1).strip()
            except Exception:
                return None
        if not self._misma_origen(pagina_url, reporte_url):
            return None
        if not http_saved_id:
            try:
                for selector in (
                    "form[name='frmGo'] input[name='HTTP_SAVED_ID']",
                    "form[name='form'] input[name='HTTP_SAVED_ID']",
                    "input[name='HTTP_SAVED_ID']",
                ):
                    campo = self.page.locator(selector)
                    if await campo.count() > 0:
                        http_saved_id = str(await campo.first.get_attribute("value") or "").strip()
                        if http_saved_id:
                            break
            except Exception:
                pass
        return await self._descargar_pdf_url(reporte_url, destino, pd_url or pagina_url, http_saved_id)

    async def _descargar_pdf_url(
        self, url: str, destino: Path, referer: str, http_saved_id: str = ""
    ) -> Path | None:
        pagina_url = str(self.page.url or "")
        if not self._misma_origen(pagina_url, url):
            return None
        parsed = urlparse(pagina_url)
        headers = {"Referer": referer or pagina_url}
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        try:
            if http_saved_id:
                respuesta = await self.page.request.post(
                    url,
                    headers=headers,
                    form={"HTTP_SAVED_ID": http_saved_id},
                    timeout=30_000,
                )
            else:
                respuesta = await self.page.request.post(
                    url, headers=headers, timeout=30_000
                )
            if respuesta.status >= 400:
                return None
            cuerpo = await respuesta.body()
            tipo = str((respuesta.headers or {}).get("content-type", "")).lower()
            if len(cuerpo) > _MAX_PDF_BYTES or not ("pdf" in tipo or cuerpo.startswith(b"%PDF")):
                return None
            destino = _ruta_unica(destino)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(cuerpo)
            return destino
        except Exception:
            return None

    async def _pdf_por_click(self, enlace: Any, destino: Path) -> Path | None:
        try:
            async with self.page.expect_download(timeout=30_000) as info:
                await enlace.click(timeout=8_000)
            descarga = await info.value
            destino = _ruta_unica(destino)
            await descarga.save_as(str(destino))
            if not destino.is_file() or destino.stat().st_size == 0:
                return None
            if destino.stat().st_size > _MAX_PDF_BYTES:
                destino.unlink(missing_ok=True)
                return None
            with destino.open("rb") as archivo:
                if not archivo.read(5).startswith(b"%PDF"):
                    destino.unlink(missing_ok=True)
                    return None
            return destino
        except Exception:
            return None

    @staticmethod
    def _misma_origen(origen: str, destino: str) -> bool:
        base, solicitada = urlparse(origen), urlparse(destino)
        return bool(
            base.scheme == "https"
            and solicitada.scheme == "https"
            and base.hostname
            and base.hostname.lower() == (solicitada.hostname or "").lower()
            and base.port == solicitada.port
        )
