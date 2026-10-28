"""Lectura de planillas que el organismo entrega como CSV o ZIP.

ARCA exporta los CSV de comprobantes en un ZIP con un unico miembro ``.csv``
en algunas instalaciones y como CSV directo en otras. Los formatos de fecha
tambien varian: hay exportes con ``dd/mm/aaaa`` y otros con ISO
(``aaaa-mm-dd``). Estas funciones concentran esa tolerancia para que los
plugins no la dupliquen ni diverjan.

Sin red, sin secretos y sin dependencias del runtime: solo la librería estándar.
Vive en ``mrbot_contracts`` porque lo usan el worker (al descargar) y la central
(al reconstruir el JSON desde el ZIP guardado).
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

COLUMNAS_FECHA = ("Fecha", "Fecha de Emisión", "Fecha de Emision")
FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y")

# Nombres estándar con los que V1/V2 renombran, por posición, la planilla de
# Mis Comprobantes (``mce_xlsx``/``mcr_xlsx`` en ``comprobantes_bot.py``).
COLUMNAS_MCE = (
    "Fecha",
    "Tipo",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Cód. Autorización",
    "Tipo Doc. Receptor",
    "Nro. Doc. Receptor",
    "Denominación Receptor",
    "Tipo Cambio",
    "Moneda",
    "Neto Grav. IVA 0%",
    "IVA 2,5%",
    "Neto Grav. IVA 2,5%",
    "IVA 5%",
    "Neto Grav. IVA 5%",
    "IVA 10,5%",
    "Neto Grav. IVA 10,5%",
    "IVA 21%",
    "Neto Grav. IVA 21%",
    "IVA 27%",
    "Neto Grav. IVA 27%",
    "Neto Gravado Total",
    "Neto No Gravado",
    "Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
)
COLUMNAS_MCR = (
    "Fecha",
    "Tipo",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Cód. Autorización",
    "Tipo Doc. Emisor",
    "Nro. Doc. Emisor",
    "Denominación Emisor",
    "Tipo Doc. Receptor",
    "Nro. Doc. Receptor",
    "Tipo Cambio",
    "Moneda",
    "Neto Grav. IVA 0%",
    "IVA 2,5%",
    "Neto Grav. IVA 2,5%",
    "IVA 5%",
    "Neto Grav. IVA 5%",
    "IVA 10,5%",
    "Neto Grav. IVA 10,5%",
    "IVA 21%",
    "Neto Grav. IVA 21%",
    "IVA 27%",
    "Neto Grav. IVA 27%",
    "Neto Gravado Total",
    "Neto No Gravado",
    "Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
)
# Columnas de importes que V1 rellena con 0 cuando el portal no las informa.
COLUMNAS_MONETARIAS_MC = (
    "Neto Grav. IVA 0%",
    "IVA 2,5%",
    "Neto Grav. IVA 2,5%",
    "IVA 5%",
    "Neto Grav. IVA 5%",
    "IVA 10,5%",
    "Neto Grav. IVA 10,5%",
    "IVA 21%",
    "Neto Grav. IVA 21%",
    "IVA 27%",
    "Neto Grav. IVA 27%",
    "Neto Gravado Total",
    "Neto No Gravado",
    "Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
)
# Columnas que V1 agrega al JSON de respuesta (nunca al CSV filtrado).
COLUMNAS_AUXILIARES_MC = ("MC", "CUIT Cliente", "MC AUX", "Factura AUX")
# V1 espera el boton ``CSV`` de Mis Comprobantes ``timeout_mc`` segundos, con 30
# como valor por defecto (``comprobantes_bot.py``).
TIMEOUT_MC_SEGUNDOS_POR_DEFECTO = 30
TIMEOUT_MC_MS_POR_DEFECTO = TIMEOUT_MC_SEGUNDOS_POR_DEFECTO * 1000
_COLUMNA_ISO = "Fecha"
_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_COMA_RE = re.compile(r"^-?\d+,\d+$")
_ENTERO_MAX = 2**63 - 1
_ENTERO_MIN = -(2**63)


def materializar_csv_descargado(origen: Path) -> None:
    """Convierte en CSV una descarga directa o el ZIP que entrega ARCA.

    El botón ``CSV`` de Mis Comprobantes entrega un ZIP en producción. Algunas
    instalaciones de ARCA entregan el CSV directo, por lo que se aceptan ambos
    formatos. El miembro se copia a un temporal dentro del mismo directorio y
    luego se reemplaza de forma atómica para no leer un archivo parcialmente
    escrito.
    """
    if not zipfile.is_zipfile(origen):
        return
    with zipfile.ZipFile(origen) as archivo_zip:
        miembros = [
            miembro
            for miembro in archivo_zip.infolist()
            if not miembro.is_dir() and miembro.filename.lower().endswith(".csv")
        ]
        if not miembros:
            raise ValueError("descarga ZIP sin archivo CSV")
        miembro = miembros[0]
        with archivo_zip.open(miembro) as entrada, tempfile.NamedTemporaryFile(
            mode="wb",
            dir=origen.parent,
            prefix=f".{origen.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporal:
            shutil.copyfileobj(entrada, temporal)
            temporal_path = Path(temporal.name)
    temporal_path.replace(origen)


def leer_texto_planilla(ruta: Path) -> str:
    """Texto de una planilla del organismo, tolerando codificaciones locales.

    Los CSV de ARCA no siempre vienen en UTF-8 (hay exportes en cp1252); sin
    esta tolerancia el filtrado falla con ``UnicodeDecodeError`` y el job
    termina como caída del portal.
    """
    crudo = Path(ruta).read_bytes()
    for codec in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return crudo.decode(codec)
        except UnicodeDecodeError:
            continue
    return crudo.decode("utf-8", errors="replace")


def parsear_fecha_planilla(valor: object) -> date | None:
    """Fecha de una celda, aceptando los formatos conocidos del organismo."""
    texto = str(valor or "").strip()
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def filtrar_csv_por_rango(
    origen: Path,
    destino: Path,
    desde: str,
    hasta: str,
    columnas_fecha: tuple[str, ...] = COLUMNAS_FECHA,
) -> int:
    """Filtra un CSV por rango inclusive y devuelve las filas conservadas.

    Detecta la columna de fecha, el delimitador real de la planilla y acepta
    tanto ``dd/mm/aaaa`` como ISO. Levanta ``ValueError`` con diagnóstico
    corto cuando el archivo no es una planilla reconocible; cada plugin lo
    traduce a su error tipado.
    """
    inicio = datetime.strptime(_normalizar_desde(desde), "%d/%m/%Y").date()
    fin = datetime.strptime(_normalizar_desde(hasta), "%d/%m/%Y").date()
    with io.StringIO(leer_texto_planilla(origen), newline="") as fh:
        muestra = fh.read(8192)
        fh.seek(0)
        try:
            delimitador = csv.Sniffer().sniff(muestra, delimiters=";,\t|").delimiter
        except csv.Error:
            delimitador = ";" if ";" in muestra else ","
        lector = csv.DictReader(fh, delimiter=delimitador)
        if lector.fieldnames is None:
            raise ValueError("la planilla del portal no tiene encabezado")
        columna = next((c for c in columnas_fecha if c in lector.fieldnames), None)
        if columna is None:
            raise ValueError("la planilla del portal no trae columna de fecha conocida")
        filas = list(lector)
        campos = lector.fieldnames
    # ``DictReader`` acumula en la clave ``None`` los campos que sobran (pasa
    # cuando un importe viene con coma decimal sin comillas): se descartan para
    # que el CSV filtrado conserve solo las columnas del encabezado.
    filas = [{k: v for k, v in fila.items() if k is not None} for fila in filas]
    conservadas = [
        fila
        for fila in filas
        if (dia := parsear_fecha_planilla(fila.get(columna))) is not None
        and inicio <= dia <= fin
    ]
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=delimitador)
        escritor.writeheader()
        escritor.writerows(conservadas)
    return len(conservadas)


def _normalizar_desde(valor: str) -> str:
    """Normaliza ``dd/mm/aaaa``; acepta ISO para llamadas internas."""
    texto = str(valor).strip()
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato).strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise ValueError(f"fecha debe tener formato dd/mm/aaaa: {texto!r}")


def _detectar_delimitador(muestra: str) -> str:
    """Delimitador real de la planilla, con el ``;`` de ARCA como fallback."""
    try:
        return csv.Sniffer().sniff(muestra, delimiters=";,\t|").delimiter
    except csv.Error:
        return ";" if ";" in muestra else ","


def columnas_mis_comprobantes(tipo: str) -> tuple[str, ...]:
    """Nombres estándar que V1 aplica por posición según el tipo de planilla."""
    return COLUMNAS_MCE if tipo == "emitidos" else COLUMNAS_MCR


def _normalizar_columna_pandas(
    valores: list[str], *, rellenar_cero: bool, es_fecha: bool = False
) -> list[str]:
    """Replica la inferencia de tipos de pandas al leer y reescribir la planilla.

    V1 lee la planilla con ``pd.read_csv(sep=';', decimal=',')`` y la reescribe
    con ``to_csv(sep=';')``: los números vuelven sin ceros a la izquierda
    (``00001`` -> ``1``) y con punto decimal (``1234,56`` -> ``1234.56``),
    mientras que los textos se conservan tal cual. ``rellenar_cero`` reproduce
    el ``fillna(0)`` que V1 aplica a las columnas de importes; en el resto de
    las columnas una celda vacía se escribe vacía (``na_rep=''``).
    """
    if es_fecha:
        return [
            dia.strftime("%Y-%m-%d") if (dia := parsear_fecha_planilla(valor)) else ""
            for valor in valores
        ]
    parseados: list[int | float | str | None] = []
    todo_entero = True
    hay_ausente = False
    hay_texto = False
    for valor in valores:
        texto = valor.strip()
        if not texto:
            parseados.append(None)
            hay_ausente = True
            continue
        if _INT_RE.match(texto):
            numero = int(texto)
            if _ENTERO_MIN <= numero <= _ENTERO_MAX and not hay_texto:
                parseados.append(numero)
                continue
            hay_texto = True
            parseados.append(texto)
            continue
        if _DECIMAL_COMA_RE.match(texto):
            parseados.append(float(texto.replace(",", ".")))
            todo_entero = False
            continue
        hay_texto = True
        parseados.append(texto)
    if hay_texto:
        # Columna de objetos: ``fillna(0)`` escribe 0, el resto queda textual.
        return [
            ("0" if rellenar_cero else "") if valor is None else str(valor)
            for valor in parseados
        ]
    if todo_entero and not hay_ausente:
        return [str(valor) for valor in parseados]
    return [
        ("0.0" if rellenar_cero else "") if valor is None else repr(float(valor))
        for valor in parseados
    ]


def filtrar_csv_comprobantes(
    origen: Path,
    destino: Path,
    desde: str,
    hasta: str,
    tipo: str,
) -> int:
    """Filtra una planilla de Mis Comprobantes con el criterio de V1.

    Porta ``filtrar_csv_por_rango`` de ``comprobantes_bot.py``: renombra las
    columnas por posición con los nombres estándar del tipo (``mce_xlsx`` o
    ``mcr_xlsx``), rellena con 0 los importes que el portal no informa, recorta
    el rango inclusive y reescribe la planilla separada por ``;``. A diferencia
    de V1, tolera que el portal agregue o quite columnas: en ese caso conserva
    los nombres originales y solo aplica el filtro por fecha.
    """
    inicio = datetime.strptime(_normalizar_desde(desde), "%d/%m/%Y").date()
    fin = datetime.strptime(_normalizar_desde(hasta), "%d/%m/%Y").date()
    texto = leer_texto_planilla(origen)
    delimitador = _detectar_delimitador(texto[:8192])
    with io.StringIO(texto, newline="") as fh:
        lector = csv.reader(fh, delimiter=delimitador)
        filas_crudas = [fila for fila in lector if fila]
    if not filas_crudas:
        raise ValueError("la planilla del portal no tiene encabezado")
    encabezado, datos = filas_crudas[0], filas_crudas[1:]
    esperadas = columnas_mis_comprobantes(tipo)
    renombrada = len(encabezado) == len(esperadas)
    columnas = esperadas if renombrada else tuple(encabezado)
    indice_fecha = next(
        (i for i, nombre in enumerate(columnas) if nombre in COLUMNAS_FECHA), None
    )
    if indice_fecha is None:
        raise ValueError("la planilla del portal no trae columna de fecha conocida")

    def _en_rango(fila: list[str]) -> bool:
        if len(fila) <= indice_fecha:
            return False
        dia = parsear_fecha_planilla(fila[indice_fecha])
        return dia is not None and inicio <= dia <= fin

    conservadas = [fila for fila in datos if _en_rango(fila)]
    monetarias = set(COLUMNAS_MONETARIAS_MC)
    salida: list[list[str]] = []
    for indice, nombre in enumerate(columnas):
        valores = [
            fila[indice] if indice < len(fila) else "" for fila in conservadas
        ]
        salida.append(
            _normalizar_columna_pandas(
                valores,
                rellenar_cero=nombre in monetarias,
                es_fecha=nombre == _COLUMNA_ISO,
            )
        )
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.writer(fh, delimiter=";", lineterminator="\n")
        escritor.writerow(list(columnas))
        for fila in zip(*salida):
            escritor.writerow(fila)
    return len(conservadas)


def normalizar_numero_comprobante(valor: Any) -> str:
    """Entero textual como lo imprime pandas: ``00001`` -> ``1``.

    V1 arma las columnas auxiliares con ``df['...'].astype(str)``: los valores
    numéricos del portal pierden los ceros a la izquierda.
    """
    texto = str(valor or "").strip()
    if _INT_RE.match(texto):
        numero = int(texto)
        if _ENTERO_MIN <= numero <= _ENTERO_MAX:
            return str(numero)
    return texto


def columnas_auxiliares_mc(
    fila: Mapping[str, str], tipo: str, cuit: str
) -> dict[str, str]:
    """Columnas que V1 agrega al JSON: MC, CUIT Cliente, MC AUX y Factura AUX."""
    tipo_doc_receptor = normalizar_numero_comprobante(fila.get("Nro. Doc. Receptor"))
    comun = "-".join(
        (
            cuit,
            normalizar_numero_comprobante(fila.get("Tipo")),
            normalizar_numero_comprobante(fila.get("Punto de Venta")),
            normalizar_numero_comprobante(fila.get("Número Desde")),
            tipo_doc_receptor,
        )
    )
    if tipo == "emitidos":
        mc_aux = f"{comun}-{cuit}"
        factura_aux = (
            f"{cuit}-{normalizar_numero_comprobante(fila.get('Tipo'))}"
            f"-{normalizar_numero_comprobante(fila.get('Punto de Venta'))}"
            f"-{normalizar_numero_comprobante(fila.get('Número Desde'))}"
        )
    else:
        mc_aux = f"{comun}-{normalizar_numero_comprobante(fila.get('Nro. Doc. Emisor'))}"
        # V1 deja ``NaN`` en Factura AUX para recibidos; el JSON lo serializa "nan".
        factura_aux = "nan"
    mc = "MCE" if tipo == "emitidos" else "MCR"
    return {"MC": mc, "CUIT Cliente": cuit, "MC AUX": mc_aux, "Factura AUX": factura_aux}


def leer_muestra_mis_comprobantes(
    archivo: Path, tipo: str, cuit: str, limite: int
) -> list[dict[str, str]]:
    """Primeras ``limite`` filas de la planilla ya filtrada, con auxiliares."""
    texto = leer_texto_planilla(archivo)
    delimitador = _detectar_delimitador(texto[:8192])
    with io.StringIO(texto, newline="") as fh:
        lector = csv.DictReader(fh, delimiter=delimitador)
        filas = list(lector)[:limite]
    muestra: list[dict[str, str]] = []
    for fila in filas:
        limpia = {k: v for k, v in fila.items() if k is not None}
        limpia = {clave: str(valor) for clave, valor in limpia.items()}
        limpia.update(columnas_auxiliares_mc(limpia, tipo, cuit))
        muestra.append(limpia)
    return muestra


# --------------------------------------------------------------------------
# ZIP de Mis Comprobantes: se guarda crudo y el JSON se reconstruye al leerlo
# --------------------------------------------------------------------------

#: Tope de bytes descomprimidos al reconstruir (defensa ante ZIP bombas).
MAX_BYTES_DESCOMPRIMIDOS = 100 * 1024 * 1024
#: Tope de filas que se devuelven en el JSON reconstruido.
MAX_FILAS_JSON = 10_000


def empaquetar_zip(origen: Path, destino: Path) -> Path:
    """Copia ``origen`` a ``destino`` como ZIP sin tocar el original.

    Si la descarga ya es un ZIP se copia byte a byte (es exactamente lo que
    entregó el organismo). Si ARCA entregó el CSV directo se lo envuelve en un
    ZIP de un miembro, de modo que el artefacto guardado sea siempre un ZIP.
    """
    origen, destino = Path(origen), Path(destino)
    if zipfile.is_zipfile(origen):
        shutil.copyfile(origen, destino)
        return destino
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
        archivo.write(origen, arcname=f"{origen.stem}.csv")
    return destino


def reconstruir_filas_desde_zip(
    zip_path: Path,
    *,
    tipo: str,
    cuit: str,
    desde: str,
    hasta: str,
    trabajo: Path,
    limite: int = MAX_FILAS_JSON,
) -> tuple[list[dict[str, str]], int, bool]:
    """Filas JSON de un ZIP de Mis Comprobantes, con el mismo criterio del worker.

    Extrae el CSV a ``trabajo``, aplica ``filtrar_csv_comprobantes`` (columnas,
    rango, importes) y lee las filas con ``leer_muestra_mis_comprobantes`` (las
    columnas auxiliares de V1). Devuelve ``(filas, total, truncado)``. Los
    temporales quedan en ``trabajo``: el llamador es dueño de ese directorio y
    lo elimina al terminar.
    """
    zip_path, trabajo = Path(zip_path), Path(trabajo)
    crudo = trabajo / "crudo.csv"
    if zipfile.is_zipfile(zip_path):
        with zipfile.ZipFile(zip_path) as archivo:
            miembros = [
                m
                for m in archivo.infolist()
                if not m.is_dir() and m.filename.lower().endswith(".csv")
            ]
            if not miembros:
                raise ValueError("el ZIP no contiene un archivo CSV")
            miembro = miembros[0]
            if miembro.file_size > MAX_BYTES_DESCOMPRIMIDOS:
                raise ValueError("el CSV descomprimido supera el límite permitido")
            escrito = 0
            with archivo.open(miembro) as entrada, open(crudo, "wb") as salida:
                while bloque := entrada.read(1 << 20):
                    escrito += len(bloque)
                    if escrito > MAX_BYTES_DESCOMPRIMIDOS:
                        raise ValueError("el CSV descomprimido supera el límite permitido")
                    salida.write(bloque)
    else:
        shutil.copyfile(zip_path, crudo)
    final = trabajo / "final.csv"
    total = filtrar_csv_comprobantes(crudo, final, desde, hasta, tipo)
    tope = max(0, min(int(limite), MAX_FILAS_JSON))
    filas = leer_muestra_mis_comprobantes(final, tipo, cuit, tope)
    return filas, total, total > len(filas)
