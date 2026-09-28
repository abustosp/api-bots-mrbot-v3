"""Lectura de planillas que el organismo entrega como CSV o ZIP.

ARCA exporta los CSV de comprobantes en un ZIP con un unico miembro ``.csv``
en algunas instalaciones y como CSV directo en otras. Los formatos de fecha
tambien varian: hay exportes con ``dd/mm/aaaa`` y otros con ISO
(``aaaa-mm-dd``). Estas funciones concentran esa tolerancia para que los
plugins no la dupliquen ni diverjan.

Sin red, sin secretos y sin dependencias del runtime: solo ``pathlib``,
``csv``, ``zipfile`` y ``datetime``.
"""

from __future__ import annotations

import csv
import io
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

COLUMNAS_FECHA = ("Fecha", "Fecha de Emisión", "Fecha de Emision")
FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y")


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
