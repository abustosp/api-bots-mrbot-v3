"""Descarga de archivos de entrada desde el bucket uploads al work_dir.

La central entrega referencias temporales por URL en ``input_files``. El
worker descarga cada archivo a su directorio efímero y los plugins reciben
solo nombres de archivos, nunca el contenido en sus payloads.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
import re
from typing import Any

from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError

MAX_ARCHIVO_BYTES = 52_428_800
MAX_DESCOMPRIMIDO_BYTES = 200 * 1024 * 1024
MAX_ENTRADAS_ZIP = 500


def _nombre_simple(nombre: str) -> str:
    texto = str(nombre).strip()
    if (
        not texto or "/" in texto or "\\" in texto
        or texto in (".", "..") or texto.startswith(".")
    ):
        raise InvalidInputError("nombre de archivo de entrada invalido")
    return texto


def expandir_zip(ruta: Path, destino: Path) -> list[str]:
    """Extrae miembros regulares de un ZIP de forma segura en ``destino``."""
    extraidos: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(ruta) as zf:
            miembros = [m for m in zf.infolist() if not m.is_dir()]
            if len(miembros) > MAX_ENTRADAS_ZIP:
                raise InvalidInputError("zip con demasiados archivos")
            for miembro in miembros:
                nombre = miembro.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if not nombre or nombre.startswith(".") or nombre.startswith("__MACOSX"):
                    continue
                if (miembro.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InvalidInputError("zip con symlinks no permitido")
                _nombre_simple(nombre)
                total += miembro.file_size
                if total > MAX_DESCOMPRIMIDO_BYTES:
                    raise InvalidInputError("zip excede el tamano permitido")
                with zf.open(miembro) as src, open(destino / nombre, "wb") as out:
                    out.write(src.read(MAX_ARCHIVO_BYTES + 1))
                if (destino / nombre).stat().st_size > MAX_ARCHIVO_BYTES:
                    raise InvalidInputError(f"archivo de entrada excede el maximo: {nombre}")
                extraidos.append(nombre)
    except zipfile.BadZipFile as exc:
        raise InvalidInputError("zip invalido") from exc
    return extraidos


async def _descargar(item: dict[str, Any], campo: str, client: Any) -> bytes:
    url = str(item.get("url") or "")
    if not url:
        raise InvalidInputError(f"archivo de entrada sin URL: {campo}")
    try:
        response = await client.get(url)
        response.raise_for_status()
        contenido = response.content
    except Exception as exc:  # noqa: BLE001 - la URL prefirmada es secreta
        raise TargetUnavailableError(
            f"no se pudo descargar {campo}: {type(exc).__name__}"
        ) from None
    if len(contenido) > MAX_ARCHIVO_BYTES:
        raise InvalidInputError(f"archivo de entrada excede el maximo: {campo}")
    if not contenido:
        raise InvalidInputError(f"archivo de entrada vacio: {campo}")
    return bytes(contenido)


async def descargar_entradas(
    archivos: list[dict[str, Any]],
    work_dir: Path,
    client: Any,
) -> list[str]:
    """Descarga archivos de una lista y expande ZIP; devuelve nombres utilizables."""
    if client is None:
        raise TargetUnavailableError("sin canal http para descargar entradas")
    work_dir = Path(work_dir)
    nombres: list[str] = []
    for item in archivos:
        nombre = _nombre_simple(item.get("nombre", ""))
        contenido = await _descargar(item, nombre, client)
        destino = work_dir / nombre
        destino.write_bytes(contenido)
        if nombre.lower().endswith(".zip"):
            nombres.extend(expandir_zip(destino, work_dir))
            destino.unlink(missing_ok=True)
        else:
            nombres.append(nombre)
    return nombres


async def descargar_entradas_por_campo(
    archivos: list[dict[str, Any]],
    work_dir: Path,
    client: Any,
) -> dict[str, str]:
    """Descarga archivos referenciados por campo y devuelve ``campo -> nombre``."""
    if client is None:
        raise TargetUnavailableError("sin canal http para descargar entradas")
    work_dir = Path(work_dir)
    por_campo: dict[str, str] = {}
    nombres_usados: set[str] = set()
    for item in archivos:
        campo = str(item.get("campo") or "")
        if not campo or campo in por_campo:
            raise InvalidInputError("campo de archivo de entrada vacio o repetido")
        nombre = _nombre_simple(item.get("nombre", ""))
        if nombre in nombres_usados:
            prefijo = re.sub(r"[^A-Za-z0-9_.-]+", "_", campo).strip("._-") or "archivo"
            nombre = _nombre_simple(f"{prefijo}_{nombre}")
        if nombre in nombres_usados:
            raise InvalidInputError("nombres de archivo de entrada repetidos")
        contenido = await _descargar(item, campo, client)
        (work_dir / nombre).write_bytes(contenido)
        por_campo[campo] = nombre
        nombres_usados.add(nombre)
    return por_campo
