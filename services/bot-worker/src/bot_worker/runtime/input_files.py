"""Descarga de archivos de entrada (bucket ``uploads``) al ``work_dir``.

La central entrega ``input_files`` en el sobre sellado: ``nombre`` simple y
una URL GET prefirmada. El worker nunca recibe credenciales de bucket. Los
``.zip`` se expanden de forma segura (sin rutas fuera del directorio, sin
symlinks, con tope de tamaño) y el zip se elimina, como hacia V1.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
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
    """Extrae los miembros regulares de ``ruta`` en ``destino`` (aplanados)."""
    extraidos: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(ruta) as zf:
            miembros = [m for m in zf.infolist() if not m.is_dir()]
            if len(miembros) > MAX_ENTRADAS_ZIP:
                raise InvalidInputError("zip con demasiados archivos")
            for m in miembros:
                nombre = m.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if not nombre or nombre.startswith(".") or nombre.startswith("__MACOSX"):
                    continue
                if (m.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InvalidInputError("zip con symlinks no permitido")
                total += m.file_size
                if total > MAX_DESCOMPRIMIDO_BYTES:
                    raise InvalidInputError("zip excede el tamano permitido")
                with zf.open(m) as src, open(destino / nombre, "wb") as out:
                    out.write(src.read(MAX_ARCHIVO_BYTES + 1))
                extraidos.append(nombre)
    except zipfile.BadZipFile as exc:
        raise InvalidInputError("zip invalido") from exc
    return extraidos


async def descargar_entradas(
    archivos: list[dict[str, Any]],
    work_dir: Path,
    client: Any,
) -> list[str]:
    """Baja cada archivo a ``work_dir`` y expande los zip. Devuelve los nombres."""
    if client is None:
        raise TargetUnavailableError("sin canal http para descargar entradas")
    work_dir = Path(work_dir)
    nombres: list[str] = []
    for item in archivos:
        nombre = _nombre_simple(item.get("nombre", ""))
        url = str(item.get("url") or "")
        if not url:
            raise InvalidInputError("archivo de entrada sin URL")
        destino = work_dir / nombre
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            contenido = resp.content
        except Exception as exc:  # noqa: BLE001 - la URL prefirmada es secreta
            raise TargetUnavailableError(
                f"no se pudo descargar {nombre}: {type(exc).__name__}"
            ) from None
        if len(contenido) > MAX_ARCHIVO_BYTES:
            raise InvalidInputError(f"archivo de entrada excede el maximo: {nombre}")
        destino.write_bytes(contenido)
        if nombre.lower().endswith(".zip"):
            nombres.extend(expandir_zip(destino, work_dir))
            destino.unlink(missing_ok=True)
        else:
            nombres.append(nombre)
    return nombres
