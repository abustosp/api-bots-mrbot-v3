"""Helpers puros de Declaracion en linea (port de V1 sin BeautifulSoup).

V3 no declara bs4 como dependencia del worker, asi que el parseo de HTML usa
``html.parser`` de la biblioteca estandar. Las funciones reproducen la
semantica de V1 para: secciones del resumen F931 (``extract_ddjj_sections``),
campos de cabecera, filas de VEP (``build_vep_status_map``) y el JSON de
resultados (``save_ddjj_results_json``).

Tambien aqui viven los wrappers de tiempo acotado de V1 (``get_page_content``,
``run_with_timeout``, ``pdf_with_timeout``), porque Playwright no acepta
timeout en ``content()``, ``count()`` ni ``pdf()``.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

CONTENT_TIMEOUT_S = 30.0
VEP_PROBE_TIMEOUT_S = 20.0
VEP_DOWNLOAD_TIMEOUT_S = 90.0
PDF_RENDER_TIMEOUT_S = 60.0

_ESPACIOS = re.compile(r"\s+")
_SECCION_RE = re.compile(r"^[IVX]+\s*-", re.IGNORECASE)
_PAR_CODIGO_RE = re.compile(r"(\d{3}\s*-\s*[^\d]+?)\s+(\d[\d\.,]*)")
_PERIODO_TEXTO_RE = re.compile(r"(\d{2})/(\d{2,4})")
_PERIODO_NUM_RE = re.compile(r"\b(\d{6})\b")

HEADER_FIELD_MAP: dict[str, str] = {
    "ucF931_lblCUIT": "C.U.I.T.",
    "ucF931_lblPeriodo": "Mes - Año",
    "ucF931_lblServiciosEventuales": "Servicios Eventuales",
    "ucF931_lblEmpleadosEnNomina": "Empleados en nómina",
    "ucF931_lblSumaRem1_lblImporte": "Suma de Rem. 1",
    "ucF931_lblSumaRem2_lblImporte": "Suma de Rem. 2",
    "ucF931_lblSumaRem3_lblImporte": "Suma de Rem. 3",
    "ucF931_lblSumaRem4_lblImporte": "Suma de Rem. 4",
    "ucF931_lblSumaRem5_lblImporte": "Suma de Rem. 5",
    "ucF931_lblSumaRem6_lblImporte": "Suma de Rem. 6",
    "ucF931_lblSumaRem7_lblImporte": "Suma de Rem. 7",
    "ucF931_lblSumaRem8_lblImporte": "Suma de Rem. 8",
    "ucF931_lblSumaRem9_lblImporte": "Suma de Rem. 9",
    "ucF931_lblSumaRem10_lblImporte": "Suma de Rem. 10",
    "ucF931_lblRazonSocial": "Apellido y Nombre o Razón Social",
    "ucF931_lblNroVerificador": "Nro. Verificador",
}


def normalizar_texto(texto: str) -> str:
    return _ESPACIOS.sub(" ", texto or "").strip()


# ---------------------------------------------------------------------------
# Parser HTML minimo (stdlib): tablas, filas, celdas e ids


class _TablaParser(HTMLParser):
    """Recorre el HTML y arma tablas con la vista recursiva de V1 (``find_all``).

    Una fila anidada pertenece a la tabla interna y a todas las que la
    contienen, y las celdas anidadas tambien aparecen en la fila externa (igual
    que ``table.find_all('tr')`` / ``tr.find_all(['td','th'])`` de V1). Los
    encabezados salen del primer ``thead`` o, si no hay, de todas las celdas
    ``th`` de la tabla.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tablas: list[dict[str, Any]] = []
        self._abiertas: list[dict[str, Any]] = []
        self._filas: list[dict[str, Any]] = []
        self._celda: dict[str, Any] | None = None
        self._profundidad_thead = 0
        self._thead_siguiente = 0
        self._thead_actual = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        if tag == "table":
            tabla = {
                "id": atributos.get("id") or "",
                "class": atributos.get("class") or "",
                "filas": [],
                "ths": [],
                "thead_headers": None,
                "thead_id": None,
            }
            self._abiertas.append(tabla)
            self.tablas.append(tabla)
        elif tag == "thead":
            self._profundidad_thead += 1
            self._thead_siguiente += 1
            self._thead_actual = self._thead_siguiente
        elif tag == "tr":
            self._filas.append(
                {
                    "celdas": [],
                    "en_thead": self._profundidad_thead > 0,
                    "thead_id": self._thead_actual if self._profundidad_thead > 0 else None,
                }
            )
        elif tag in ("td", "th"):
            self._celda = {
                "tag": tag,
                "texto": "",
                "class": atributos.get("class") or "",
                "colspan": int(atributos.get("colspan") or 1),
            }

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._abiertas:
                self._abiertas.pop()
        elif tag == "thead":
            self._profundidad_thead = max(0, self._profundidad_thead - 1)
        elif tag == "tr":
            fila = self._filas.pop() if self._filas else None
            if fila is None:
                return
            if not any(normalizar_texto(c["texto"]) for c in fila["celdas"]):
                return
            textos = [normalizar_texto(c["texto"]) for c in fila["celdas"]]
            for tabla in self._abiertas:
                if fila["en_thead"]:
                    th_textos = [
                        normalizar_texto(c["texto"]) for c in fila["celdas"] if c["tag"] == "th"
                    ]
                    if tabla["thead_id"] is None:
                        tabla["thead_id"] = fila["thead_id"]
                        tabla["thead_headers"] = list(th_textos)
                    elif tabla["thead_id"] == fila["thead_id"]:
                        tabla["thead_headers"].extend(th_textos)
                else:
                    tabla["filas"].append({"textos": textos, "celdas": fila["celdas"]})
        elif tag in ("td", "th"):
            celda = self._celda
            self._celda = None
            if celda is None:
                return
            if celda["tag"] == "th":
                for tabla in self._abiertas:
                    tabla["ths"].append(celda)
            for fila in self._filas:
                fila["celdas"].append(celda)

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda["texto"] += data


def parsear_tablas(html: str) -> list[dict[str, Any]]:
    """Tablas del documento con encabezados, filas y registros (port de V1).

    Los registros se arman con la misma regla de V1: primero por encabezados
    cuando la fila tiene tantas celdas como encabezados; si no hay ninguno,
    por pares clave/valor de las filas de cantidad par de celdas.
    """
    parser = _TablaParser()
    parser.feed(html or "")
    parser.close()
    resultado: list[dict[str, Any]] = []
    for indice, tabla in enumerate(parser.tablas):
        if tabla["thead_headers"] is not None:
            headers = list(tabla["thead_headers"])
        else:
            headers = [normalizar_texto(c["texto"]) for c in tabla["ths"]]
        filas = [f["textos"] for f in tabla["filas"]]
        if not headers and not filas:
            continue
        registros: list[dict[str, str]] = []
        if headers:
            for fila in filas:
                if len(fila) == len(headers):
                    registros.append({headers[i]: fila[i] for i in range(len(headers))})
        if not registros:
            for fila in filas:
                if len(fila) >= 2 and len(fila) % 2 == 0:
                    registro: dict[str, str] = {}
                    for idx_celda in range(0, len(fila), 2):
                        clave, valor = fila[idx_celda], fila[idx_celda + 1]
                        if clave:
                            registro[clave] = valor
                    if registro:
                        registros.append(registro)
        resultado.append(
            {
                "index": indice,
                "id": tabla["id"],
                "class": tabla["class"],
                "headers": headers,
                "rows": filas,
                "records": registros,
            }
        )
    return resultado


def _ids_con_texto(html: str) -> dict[str, str]:
    """Texto normalizado de cada elemento con id (para cabecera F931)."""
    encontrados: dict[str, str] = {}

    class _IdParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._abiertos: list[tuple[str, list[str]]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            atributos = dict(attrs)
            if atributos.get("id"):
                self._abiertos.append((atributos["id"], []))

        def handle_endtag(self, tag: str) -> None:
            if self._abiertos:
                identificador, textos = self._abiertos.pop()
                encontrados.setdefault(identificador, normalizar_texto(" ".join(textos)))

        def handle_data(self, data: str) -> None:
            for _, textos in self._abiertos:
                textos.append(data)

    parser = _IdParser()
    parser.feed(html or "")
    parser.close()
    return encontrados


# ---------------------------------------------------------------------------
# Cabecera y secciones de la DDJJ (port de V1)


def extraer_cabecera_ddjj(html: str) -> dict[str, str]:
    """Campos de cabecera del F931 por id (port de ``extract_ddjj_header_fields``)."""
    textos = _ids_con_texto(html)
    fields: dict[str, str] = {}
    for element_id, etiqueta in HEADER_FIELD_MAP.items():
        valor = textos.get(element_id, "")
        if valor:
            fields[etiqueta] = valor
    return fields


def id_a_etiqueta(element_id: str) -> str:
    """Etiqueta legible de un id del formulario F931 (port de ``id_to_label``)."""
    etiqueta = re.sub(r"^ucF931_", "", element_id)
    etiqueta = re.sub(r"^lbl", "", etiqueta, flags=re.I)
    etiqueta = re.sub(r"_lblImporte$", "", etiqueta, flags=re.I)
    etiqueta = etiqueta.replace("_", " ")
    etiqueta = re.sub(r"([a-z])([A-Z])", r"\1 \2", etiqueta)
    etiqueta = re.sub(r"([A-Za-z])(\d)", r"\1 \2", etiqueta)
    etiqueta = re.sub(r"(\d)([A-Za-z])", r"\1 \2", etiqueta)
    return normalizar_texto(etiqueta)


def extraer_campos_ddjj(html: str) -> dict[str, str]:
    """Volcado generico de campos ``ucF931_lbl*`` (port de ``extract_ddjj_fields_from_html``).

    El flujo principal usa ``extraer_cabecera_ddjj`` (rotulos fijos de V1);
    esta funcion conserva el mapeo por id de V1 para los ids que aparezcan.
    """
    campos: dict[str, str] = {}
    for element_id, valor in _ids_con_texto(html).items():
        if not re.match(r"^ucF931_[Ll]bl", element_id):
            continue
        if not valor:
            continue
        etiqueta = id_a_etiqueta(element_id)
        if not etiqueta:
            continue
        campos[etiqueta] = valor
    return campos


def extraer_pares_de_texto(texto: str) -> list[tuple[str, str]]:
    """Pares ``codigo - descripcion  importe`` y ``clave: valor`` (port V1)."""
    pares: list[tuple[str, str]] = []
    if not texto:
        return pares
    for coincidencia in _PAR_CODIGO_RE.finditer(texto):
        clave = normalizar_texto(coincidencia.group(1))
        valor = normalizar_texto(coincidencia.group(2))
        if clave and valor:
            pares.append((clave, valor))
    if ":" in texto:
        clave, valor = texto.split(":", 1)
        clave, valor = normalizar_texto(clave), normalizar_texto(valor)
        if clave and valor:
            pares.append((clave, valor))
    return pares


def extraer_secciones_ddjj(html: str) -> dict[str, dict[str, str]]:
    """Secciones I, II, ... del resumen F931 (port de ``extract_ddjj_sections``).

    Usa la tabla que contiene celdas ``F931CeldaResumenHeader``. Cada celda de
    encabezado abre una seccion; las filas siguientes se asignan a ella con la
    semantica de V1 (grupos de 3 columnas, izquierda y derecha).
    """
    tablas = _tablas_con_clase(html, "F931CeldaResumenHeader")
    if not tablas:
        return {}
    secciones: dict[str, dict[str, str]] = {}
    izquierda: str | None = None
    derecha: str | None = None
    for fila in tablas[0]:
        textos = [normalizar_texto(c["texto"]) for c in fila["celdas"]]
        spans = [c["colspan"] for c in fila["celdas"]]
        columnas = [""] * 6
        posicion = 0
        for texto, span in zip(textos, spans):
            if posicion < 6:
                columnas[posicion] = texto
            posicion += span

        indice = 0
        encabezados: list[tuple[str, int, int]] = []
        for celda, texto, span in zip(fila["celdas"], textos, spans):
            es_encabezado = "F931CeldaResumenHeader" in celda["class"].split()
            if not es_encabezado and texto and _SECCION_RE.match(texto):
                es_encabezado = True
            if es_encabezado and texto:
                encabezados.append((texto, indice, span))
            indice += span
        for texto, col, span in encabezados:
            if span >= 6:
                izquierda, derecha = texto, None
                secciones.setdefault(izquierda, {})
            elif col >= 3:
                derecha = texto
                secciones.setdefault(derecha, {})
            else:
                izquierda = texto
                secciones.setdefault(izquierda, {})

        _agregar_grupo(secciones, izquierda, columnas[0:3])
        _agregar_grupo(secciones, derecha, columnas[3:6])

        if izquierda and derecha is None and len(fila["celdas"]) == 1:
            for clave, valor in extraer_pares_de_texto(textos[0]):
                _agregar(secciones, izquierda, clave, valor)
        if izquierda and derecha is None and len(fila["celdas"]) > 1:
            no_vacios = [t for t in textos if t]
            if len(no_vacios) % 2 == 0:
                for idx in range(0, len(no_vacios), 2):
                    _agregar(secciones, izquierda, no_vacios[idx], no_vacios[idx + 1])
    return secciones


def _agregar(secciones: dict[str, dict[str, str]], seccion: str | None, clave: str, valor: str) -> None:
    if not seccion or not clave or not valor:
        return
    secciones.setdefault(seccion, {})[normalizar_texto(clave)] = normalizar_texto(valor)


def _agregar_grupo(secciones: dict[str, dict[str, str]], seccion: str | None, grupo: list[str]) -> None:
    codigo = grupo[0] if len(grupo) > 0 else ""
    descripcion = grupo[1] if len(grupo) > 1 else ""
    valor = grupo[2] if len(grupo) > 2 else ""
    etiqueta = normalizar_texto(" ".join(p for p in (codigo, descripcion) if p))
    if valor:
        _agregar(secciones, seccion, etiqueta or descripcion or codigo, valor)
        return
    for texto in (codigo, descripcion):
        for clave, val in extraer_pares_de_texto(texto):
            _agregar(secciones, seccion, clave, val)


def _tablas_con_clase(html: str, clase: str) -> list[list[dict[str, Any]]]:
    """Filas (con celdas y clases) de las tablas que contienen una celda con ``clase``."""

    class _Clases(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.tablas: list[list[dict[str, Any]]] = []
            self._tabla: list[dict[str, Any]] | None = None
            self._fila: list[dict[str, Any]] | None = None
            self._celda: dict[str, Any] | None = None
            self._tiene_clase = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            atributos = dict(attrs)
            if tag == "table":
                self._tabla = []
                self._tiene_clase = False
            elif tag == "tr" and self._tabla is not None:
                self._fila = []
            elif tag in ("td", "th") and self._fila is not None:
                clases = atributos.get("class") or ""
                if clase in clases.split():
                    self._tiene_clase = True
                self._celda = {
                    "texto": "",
                    "class": clases,
                    "colspan": int(atributos.get("colspan") or 1),
                }

        def handle_endtag(self, tag: str) -> None:
            if tag in ("td", "th") and self._celda is not None and self._fila is not None:
                self._fila.append(self._celda)
                self._celda = None
            elif tag == "tr" and self._fila is not None and self._tabla is not None:
                if self._fila:
                    self._tabla.append({"celdas": self._fila})
                self._fila = None
            elif tag == "table" and self._tabla is not None:
                if self._tiene_clase:
                    self.tablas.append(self._tabla)
                self._tabla = None

        def handle_data(self, data: str) -> None:
            if self._celda is not None:
                self._celda["texto"] += data

    parser = _Clases()
    parser.feed(html or "")
    parser.close()
    return parser.tablas


# ---------------------------------------------------------------------------
# Periodos y VEP (port de V1)


def periodo_desde_texto(texto: str) -> str | None:
    """``MM/AAAA`` o ``AAAAMM`` dentro de un texto (port de ``parse_periodo_from_text``)."""
    if not texto:
        return None
    coincidencia = _PERIODO_TEXTO_RE.search(texto)
    if coincidencia:
        anio = coincidencia.group(2)
        if len(anio) == 2:
            anio = f"20{anio}"
        return f"{anio}{coincidencia.group(1)}"
    coincidencia = _PERIODO_NUM_RE.search(texto)
    return coincidencia.group(1) if coincidencia else None


def es_vep_pagado(estado: str) -> bool:
    return "pagado" in (estado or "").lower()


def normalizar_clave_encabezado(encabezado: str) -> str:
    """Clave de fila a partir del rotulo de la columna (port de ``normalize_header_key``)."""
    return re.sub(r"[^a-z0-9]+", "_", (encabezado or "").lower()).strip("_")


def mapa_estados_vep(tablas: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Estado del VEP por periodo (port de ``build_vep_status_map``).

    Cada candidato lleva estado, descripcion, nro_vep, cuit, importe y
    fecha_de_pago tomados de las columnas rotuladas de la fila. Si varias filas
    son del mismo periodo (volante vencido y comprobante pagado), se informa la
    pagada.
    """
    mapa: dict[str, dict[str, str]] = {}
    for tabla in tablas:
        encabezados_raw = tabla.get("headers", [])
        encabezados = [str(h).lower() for h in encabezados_raw]
        indice_estado = None
        indice_desc = None
        for indice, encabezado in enumerate(encabezados):
            # V1 recorre todos los rotulos: si hay mas de uno que matchea, vale el ultimo.
            if "estado" in encabezado:
                indice_estado = indice
            if "descrip" in encabezado:
                indice_desc = indice

        for fila in tabla.get("rows", []):
            fila_datos: dict[str, str] = {}
            for indice_celda, encabezado in enumerate(encabezados_raw):
                if indice_celda < len(fila):
                    fila_datos[normalizar_clave_encabezado(encabezado)] = fila[indice_celda]

            estado = (
                fila[indice_estado]
                if indice_estado is not None and indice_estado < len(fila)
                else fila_datos.get("estado", "")
            )
            desc = (
                fila[indice_desc]
                if indice_desc is not None and indice_desc < len(fila)
                else fila_datos.get("descripcion", "")
            )
            periodo = periodo_desde_texto(desc)
            if not periodo:
                periodo = periodo_desde_texto(" ".join(fila))
            if not periodo:
                continue
            candidato = {
                "estado": estado,
                "descripcion": desc,
                "nro_vep": fila_datos.get("nro_vep", ""),
                "cuit": fila_datos.get("cuit", ""),
                "importe": fila_datos.get("importe", ""),
                "fecha_de_pago": fila_datos.get("fecha_de_pago", ""),
            }
            previo = mapa.get(periodo)
            if previo is None or (
                not es_vep_pagado(previo.get("estado", ""))
                and es_vep_pagado(candidato["estado"])
            ):
                mapa[periodo] = candidato
    return mapa


class _FilasParser(HTMLParser):
    """Filas del documento con el tag de cada celda (para ``extract_vep_info_from_html``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.filas: list[list[dict[str, str]]] = []
        self._pila: list[list[dict[str, str]]] = []
        self._celda: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._pila.append([])
        elif tag in ("td", "th"):
            self._celda = {"tag": tag, "texto": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            celda = self._celda
            self._celda = None
            if celda is not None and self._pila:
                self._pila[-1].append(celda)
        elif tag == "tr":
            fila = self._pila.pop() if self._pila else None
            if fila is not None:
                self.filas.append(fila)

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda["texto"] += data


def extraer_info_vep(html: str, periodo: str | None = None) -> dict[str, str]:
    """Datos del VEP a partir del detalle (port de ``extract_vep_info_from_html``).

    Primero busca la fila del periodo pedido en el listado de VEPs; si no la
    encuentra, arma pares clave/valor con las filas que no son de encabezado y
    clasifica por rotulo (estado, nro_vep, cuit, importe, fecha_de_pago,
    descripcion).
    """
    if periodo:
        mapa = mapa_estados_vep(parsear_tablas(html))
        if periodo in mapa:
            return mapa[periodo]

    parser = _FilasParser()
    parser.feed(html or "")
    parser.close()

    campos: dict[str, str] = {}
    for fila in parser.filas:
        if fila and all(celda["tag"] == "th" for celda in fila):
            # Fila de encabezado: sus celdas son rotulos, no datos.
            continue
        celdas = [normalizar_texto(celda["texto"]) for celda in fila]
        celdas = [celda for celda in celdas if celda]
        if not celdas:
            continue
        if len(celdas) % 2 == 0:
            for idx in range(0, len(celdas), 2):
                campos[celdas[idx]] = celdas[idx + 1]
        else:
            for celda in celdas:
                if ":" in celda:
                    clave, valor = celda.split(":", 1)
                    clave, valor = normalizar_texto(clave), normalizar_texto(valor)
                    if clave and valor:
                        campos[clave] = valor

    info: dict[str, str] = {}
    for clave, valor in campos.items():
        clave_min = clave.lower()
        if "estado" in clave_min and not info.get("estado"):
            info["estado"] = valor
        elif ("nro" in clave_min or "número" in clave_min) and "vep" in clave_min and not info.get("nro_vep"):
            info["nro_vep"] = valor
        elif "cuit" in clave_min and not info.get("cuit"):
            info["cuit"] = valor
        elif "importe" in clave_min and not info.get("importe"):
            info["importe"] = valor
        elif "fecha" in clave_min and ("pago" in clave_min or "venc" in clave_min) and not info.get("fecha_de_pago"):
            info["fecha_de_pago"] = valor
        elif ("descripcion" in clave_min or "concepto" in clave_min) and not info.get("descripcion"):
            info["descripcion"] = valor

    if info:
        return info

    mapa = mapa_estados_vep(parsear_tablas(html))
    if periodo:
        # Periodo pedido y sin fila propia: no se atribuye el VEP de otro periodo.
        return mapa.get(periodo, {})
    if len(mapa) == 1:
        return next(iter(mapa.values()))
    return {}


def extraer_estados_vep(tablas: list[dict[str, Any]]) -> list[str]:
    """Estados presentes en las tablas (port de ``extract_vep_statuses``)."""
    estados: list[str] = []
    for tabla in tablas:
        encabezados = [str(h).lower() for h in tabla.get("headers", [])]
        indice_estado: int | None = None
        for indice, encabezado in enumerate(encabezados):
            if "estado" in encabezado:
                indice_estado = indice
                break
        if indice_estado is not None:
            for fila in tabla.get("rows", []):
                if indice_estado < len(fila):
                    estados.append(fila[indice_estado])

    if not estados:
        for tabla in tablas:
            for fila in tabla.get("rows", []):
                for celda in fila:
                    if re.search(r"\b(pagado|pendiente|vencido|rechazado|parcial)\b", celda, re.I):
                        estados.append(celda)
    return estados


def combinar_vep(primario: dict[str, str], respaldo: dict[str, str]) -> dict[str, str]:
    combinado = dict(primario)
    for clave, valor in respaldo.items():
        if not combinado.get(clave) and valor:
            combinado[clave] = valor
    return combinado


def periodo_to_sijpdj(periodo: str | None) -> str | None:
    """``202503`` -> ``SIJPDJ03/25`` (port de V1)."""
    if not periodo or not re.fullmatch(r"\d{6}", periodo):
        return None
    return f"SIJPDJ{periodo[4:]}/{periodo[2:4]}"


def periodo_a_yyyymm(valor: str | None) -> str | None:
    """Normaliza ``AAAAMM``, ``MM/AAAA`` o ``AAAA-MM`` a ``AAAAMM`` (port de V1)."""
    texto = re.sub(r"\s+", "", (valor or "").strip())
    if not texto:
        return None
    if re.fullmatch(r"\d{6}", texto):
        return texto
    coincidencia = re.fullmatch(r"(\d{2})[./-](\d{4})", texto)
    if coincidencia:
        return f"{coincidencia.group(2)}{coincidencia.group(1)}"
    coincidencia = re.fullmatch(r"(\d{4})[./-](\d{2})", texto)
    if coincidencia:
        return f"{coincidencia.group(1)}{coincidencia.group(2)}"
    return None


def variantes_periodo(periodo: str) -> list[str]:
    """Formas en que el portal puede escribir el periodo (port de ``build_periodo_variants``)."""
    periodo = (periodo or "").strip()
    if not periodo:
        return []
    if not re.fullmatch(r"\d{6}", periodo):
        return [periodo]
    anio, mes = periodo[:4], periodo[4:]
    return [
        periodo,
        f"{mes}/{anio}",
        f"{anio}/{mes}",
        f"{mes}-{anio}",
        f"{anio}-{mes}",
        f"{mes}.{anio}",
    ]


# ---------------------------------------------------------------------------
# JSON de resultados (port de ``save_ddjj_results_json``)


def construir_json_ddjj(
    *,
    cabecera: dict[str, str],
    secciones: dict[str, dict[str, str]],
    periodo: str,
    vep: dict[str, str] | None,
    ahora: datetime | None = None,
) -> dict[str, Any]:
    """Payload con la misma forma que V1: periodo, scraped_at, vep, datos, tablas."""
    marca = (ahora or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return {
        "periodo": periodo,
        "scraped_at": marca,
        "vep": vep or {},
        "datos": cabecera,
        "tablas": secciones,
    }


def escribir_json_ddjj(payload: dict[str, Any], destino: Path) -> Path:
    """Escribe el JSON en UTF-8 (``ensure_ascii=False``, como V1)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


# ---------------------------------------------------------------------------
# Tiempo acotado (port de V1 ``get_page_content``, ``run_with_timeout``,
# ``pdf_with_timeout``)


async def contenido_con_timeout(page: Any, timeout_s: float = CONTENT_TIMEOUT_S) -> str:
    """HTML de la pagina con tope propio; cadena vacia si no responde a tiempo."""
    if page is None:
        return ""
    try:
        return await asyncio.wait_for(page.content(), timeout=timeout_s)
    except Exception:
        return ""


async def con_timeout(coro: Any, timeout_s: float, default: Any = None) -> Any:
    """Acota una operacion sin timeout propio y degrada a ``default`` solo en timeout.

    Otros errores (navegador caido, pagina cerrada) se propagan: V1 los
    tragaba y ocultaba la causa real.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        return default


async def pdf_con_timeout(page: Any, destino: Path, timeout_s: float = PDF_RENDER_TIMEOUT_S) -> None:
    """Imprime la pagina a PDF con tope; propaga el timeout para el respaldo."""
    await asyncio.wait_for(
        page.pdf(path=str(destino), format="A4", print_background=True),
        timeout=timeout_s,
    )
