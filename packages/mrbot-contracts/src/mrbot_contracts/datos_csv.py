"""Datos scrapeados como archivos CSV separados por ``|``.

El worker guarda en S4 lo que scrapea (facturas de RCEL, saldos y movimientos de
CCMA) como CSV y no como JSON dentro de la base. La central vuelve a armar el
mismo response al pedirlo, y ``reconstruir_*`` es la inversa exacta de
``*_a_csv``: lo que sale es igual a lo que entró (se probó contra todos los
resultados reales de RCEL y CCMA de producción).

Sin red ni dependencias: solo ``csv`` e ``io``. Vive en ``mrbot_contracts``
porque lo usan el worker (al escribir) y la central (al leer y migrar).
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Iterable, Mapping, Sequence

DELIMITADOR = "|"
#: Columnas fijas de los movimientos de CCMA (orden estable del archivo).
COLUMNAS_MOVIMIENTOS_CCMA = (
    "periodo",
    "impuesto",
    "concepto",
    "subconcepto",
    "descripcion",
    "fecha_movimiento",
    "debe",
    "haber",
)
#: Saldos del resumen de CCMA, un solo registro por consulta.
COLUMNAS_SALDOS_CCMA = (
    "cuit",
    "periodo",
    "periodo_desde",
    "deuda_capital",
    "deuda_accesorios",
    "total_deuda",
    "credito_capital",
    "credito_accesorios",
    "total_a_favor",
)
#: Campos técnicos del resumen que no son saldos y viajan en el response.
CLAVES_NO_SALDO_CCMA = ("movimientos_corte", "movimientos_completos")
#: Columnas que se guardan junto a los campos de cada factura de RCEL.
COLUMNA_ARCHIVO_RCEL = "archivo_pdf"

_NOMBRE_SEGURO = re.compile(r"[^A-Za-z0-9._-]+")


def _segmento(valor: object) -> str:
    texto = _NOMBRE_SEGURO.sub("_", str(valor or "").strip()).strip("_")
    return texto or "sin_dato"


def nombre_ccma_movimientos(representado: str) -> str:
    """``ccma_movimientos_{representado}.csv``."""
    return f"ccma_movimientos_{_segmento(representado)}.csv"


def nombre_ccma_saldos(representado: str) -> str:
    """``ccma_saldos_{representado}.csv``."""
    return f"ccma_saldos_{_segmento(representado)}.csv"


def nombre_ccma_resumen(representado: str) -> str:
    """``ccma_resumen_{representado}.pdf``."""
    return f"ccma_resumen_{_segmento(representado)}.pdf"


def nombre_rcel_facturas(representado: str) -> str:
    """``rcel_facturas_{representado}.csv``."""
    return f"rcel_facturas_{_segmento(representado)}.csv"


def a_csv(filas: Iterable[Mapping[str, Any]], columnas: Sequence[str]) -> str:
    """CSV con ``|`` y comillas solo cuando hacen falta (``|``, ``"`` o saltos)."""
    salida = io.StringIO(newline="")
    escritor = csv.writer(
        salida,
        delimiter=DELIMITADOR,
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    escritor.writerow(list(columnas))
    for fila in filas:
        escritor.writerow(["" if fila.get(c) is None else str(fila.get(c)) for c in columnas])
    return salida.getvalue()


def de_csv(texto: str) -> list[dict[str, str]]:
    """Filas del CSV con ``|`` como diccionarios (inversa de :func:`a_csv`)."""
    lector = csv.DictReader(
        io.StringIO(texto, newline=""), delimiter=DELIMITADOR, quotechar='"'
    )
    return [{k: (v if v is not None else "") for k, v in fila.items()} for fila in lector]


def _union_columnas(filas: Sequence[Mapping[str, Any]], base: Sequence[str] = ()) -> list[str]:
    """Columnas base y luego las extra que aparezcan, en orden alfabético estable."""
    extra = sorted({k for f in filas for k in f} - set(base))
    return [*base, *extra]


# --------------------------------------------------------------------------
# CCMA
# --------------------------------------------------------------------------


#: Columna que marca los movimientos del fallback de V1 (texto sin columnas).
COLUMNA_FILA_CRUDA = "fila"


def movimientos_ccma_a_csv(movimientos: Sequence[Mapping[str, Any]]) -> str:
    """CSV de movimientos; el orden original de las filas se conserva.

    Algunos movimientos vienen del fallback de V1 como ``{"fila": "<texto>"}``:
    se guardan en la columna ``fila`` y el resto de las columnas queda vacío.
    """
    columnas = _union_columnas(movimientos, COLUMNAS_MOVIMIENTOS_CCMA)
    return a_csv(movimientos, columnas)


def movimientos_ccma_desde_csv(texto: str) -> list[dict[str, str]]:
    """Inversa de :func:`movimientos_ccma_a_csv`: cada fila recupera su forma original.

    Una fila solo con ``fila`` es del fallback de V1; una normal no tiene
    ``fila`` (o la tiene vacía), y se descartan las columnas que quedaron vacías
    únicamente por la unión de columnas del archivo.
    """
    filas: list[dict[str, str]] = []
    for fila in de_csv(texto):
        if fila.get(COLUMNA_FILA_CRUDA):
            filas.append({COLUMNA_FILA_CRUDA: fila[COLUMNA_FILA_CRUDA]})
        else:
            filas.append({k: v for k, v in fila.items() if k != COLUMNA_FILA_CRUDA})
    return filas


def saldos_ccma_a_csv(saldos: Mapping[str, Any]) -> str:
    columnas = _union_columnas([saldos], COLUMNAS_SALDOS_CCMA)
    return a_csv([saldos], columnas)


def saldos_ccma_desde_csv(texto: str) -> dict[str, str]:
    filas = de_csv(texto)
    return filas[0] if filas else {}


# --------------------------------------------------------------------------
# RCEL
# --------------------------------------------------------------------------

COLUMNAS_PDF_RCEL = ("archivo_pdf", "pdf_sha256", "pdf_size_bytes")


#: Columna con la lista de campos que tenía cada factura (JSON), para que una
#: factura con menos campos que otra no recupere claves vacías de más.
COLUMNA_CAMPOS_RCEL = "campos_presentes"


def facturas_rcel_a_csv(facturas: Sequence[Mapping[str, Any]]) -> str:
    """Una fila por factura: datos del PDF (nombre, hash, tamaño) y sus campos."""
    filas = []
    for f in facturas:
        campos = f.get("campos") or {}
        fila: dict[str, Any] = {
            "archivo_pdf": f.get("archivo", ""),
            "pdf_sha256": f.get("sha256", ""),
            "pdf_size_bytes": f.get("size_bytes", ""),
        }
        fila.update(campos)
        fila[COLUMNA_CAMPOS_RCEL] = json.dumps(list(campos), ensure_ascii=False)
        filas.append(fila)
    return a_csv(filas, _union_columnas(filas, COLUMNAS_PDF_RCEL))


def facturas_rcel_desde_csv(texto: str) -> list[dict[str, Any]]:
    """Inversa de :func:`facturas_rcel_a_csv` (``size_bytes`` vuelve a ser entero)."""
    facturas: list[dict[str, Any]] = []
    for fila in de_csv(texto):
        factura: dict[str, Any] = {"archivo": fila.pop("archivo_pdf", "")}
        sha = fila.pop("pdf_sha256", "")
        tam = fila.pop("pdf_size_bytes", "")
        presentes = json.loads(fila.pop(COLUMNA_CAMPOS_RCEL, "null") or "null")
        if sha:
            factura["sha256"] = sha
        if tam != "":
            factura["size_bytes"] = int(tam)
        factura["campos"] = (
            {k: fila.get(k, "") for k in presentes} if presentes is not None else fila
        )
        facturas.append(factura)
    return facturas


# --------------------------------------------------------------------------
# Separar el dato scrapeado del resto del payload y volver a armarlo
# --------------------------------------------------------------------------

#: Clave del payload reducido que apunta a los archivos con el dato scrapeado.
CLAVE_DATOS_EN_ARCHIVO = "datos_en_archivo"
SALDOS_CCMA = (
    "cuit", "periodo", "deuda_capital", "deuda_accesorios", "total_deuda",
    "credito_capital", "credito_accesorios", "total_a_favor",
)


def separar_ccma(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``(payload_sin_datos, {"movimientos": csv, "saldos": csv})``.

    Los saldos y los movimientos salen del payload; el resto (identificación,
    cortes, hash del PDF) se queda. ``datos_en_archivo`` lista qué hay afuera.
    """
    reducido = dict(payload)
    archivos: dict[str, str] = {}
    saldos = {k: reducido.pop(k) for k in SALDOS_CCMA if k in reducido}
    if saldos:
        archivos["saldos"] = saldos_ccma_a_csv(saldos)
        # Claves presentes en el original: sin esto una clave ausente reaparecería
        # como "" al reconstruir y el response dejaría de ser idéntico.
        reducido["_saldos_claves"] = [k for k in SALDOS_CCMA if k in saldos]
    if isinstance(reducido.get("movimientos"), list):
        archivos["movimientos"] = movimientos_ccma_a_csv(reducido.pop("movimientos"))
    if archivos:
        reducido[CLAVE_DATOS_EN_ARCHIVO] = sorted(archivos)
    return reducido, archivos


def reconstruir_ccma(
    reducido: Mapping[str, Any], textos: Mapping[str, str]
) -> dict[str, Any]:
    """Payload original de CCMA a partir del reducido y los CSV."""
    payload = {k: v for k, v in reducido.items() if k != CLAVE_DATOS_EN_ARCHIVO}
    claves = payload.pop("_saldos_claves", None)
    if "saldos" in textos:
        leidos = saldos_ccma_desde_csv(textos["saldos"])
        permitidas = set(claves) if claves is not None else set(leidos)
        payload.update({k: v for k, v in leidos.items() if k in permitidas})
    if "movimientos" in textos:
        payload["movimientos"] = movimientos_ccma_desde_csv(textos["movimientos"])
    return payload


def separar_rcel(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``(payload_sin_facturas, {"facturas": csv})``; ``cantidad`` queda en el payload."""
    reducido = dict(payload)
    facturas = reducido.get("facturas")
    if not isinstance(facturas, list) or not facturas:
        return reducido, {}
    reducido.pop("facturas")
    reducido[CLAVE_DATOS_EN_ARCHIVO] = ["facturas"]
    return reducido, {"facturas": facturas_rcel_a_csv(facturas)}


def reconstruir_rcel(
    reducido: Mapping[str, Any], textos: Mapping[str, str]
) -> dict[str, Any]:
    payload = {k: v for k, v in reducido.items() if k != CLAVE_DATOS_EN_ARCHIVO}
    if "facturas" in textos:
        payload["facturas"] = facturas_rcel_desde_csv(textos["facturas"])
    return payload
