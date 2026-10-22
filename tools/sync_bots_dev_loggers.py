#!/usr/bin/env python3
"""Sincroniza los scripts ``_logger`` de la raíz ``bots_dev/`` con el worker.

La raíz ``bots_dev/`` es la fuente (no se empaqueta: tiene borradores y entornos).
``services/bot-worker/src/bot_worker/bots_dev/<bot>/`` guarda, junto a ``NOTA.md`` y
``payload_ejemplo.json``, la copia sin modificar de cada script que ejecuta la variante
``_logger`` del worker (``runtime/logger_script.py``), y ``shared/`` guarda la copia de
``bots_dev/shared/``.

Uso:
    tools/sync_bots_dev_loggers.py            # copia raíz -> worker
    tools/sync_bots_dev_loggers.py --check    # no copia; sale con 1 si hay desvíos
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAIZ = REPO / "bots_dev"
DESTINO = REPO / "services" / "bot-worker" / "src" / "bot_worker" / "bots_dev"

#: carpeta (nombre del bot registrado, o ``shared``) -> archivos que se copian.
MANIFIESTO: dict[str, tuple[str, ...]] = {
    "ccma": ("ccma_bot_logger.py",),
    "siper": ("siper_bot_logger.py",),
    "facturometro": ("facturometro_bot_logger.py",),
    "libro_sueldos_digital": ("libro_sueldos_digital_bot_logger.py",),
    "mis_retenciones": ("mis_retenciones_bot_logger.py",),
    "certificacion_primaria_granos": (
        "__init__.py",
        "certificacion_primaria_granos_bot.py",
        "certificacion_primaria_granos_bot_logger.py",
    ),
    "declaracion_en_linea": (
        "__init__.py",
        "declaracion_en_linea_bot.py",
        "declaracion_en_linea_bot_logger.py",
    ),
    "retper_iibb_agip": ("agip_bot_logger.py",),
    "shared": (
        "__init__.py",
        "arca_captcha.py",
        "arca_login.py",
        "proxy.py",
        "public_errors.py",
    ),
}


def pares() -> list[tuple[Path, Path]]:
    return [
        (RAIZ / carpeta / nombre, DESTINO / carpeta / nombre)
        for carpeta, nombres in MANIFIESTO.items()
        for nombre in nombres
    ]


def desvios() -> list[str]:
    """Descripción de cada copia ausente o distinta de su fuente."""
    problemas: list[str] = []
    for origen, copia in pares():
        if not origen.is_file():
            problemas.append(f"falta en la raíz: {origen.relative_to(REPO)}")
        elif not copia.is_file():
            problemas.append(f"falta la copia: {copia.relative_to(REPO)}")
        elif origen.read_bytes() != copia.read_bytes():
            problemas.append(f"distinta de la raíz: {copia.relative_to(REPO)}")
    return problemas


def sincronizar() -> int:
    copiados = 0
    for origen, copia in pares():
        if not origen.is_file():
            raise SystemExit(f"falta en la raíz: {origen.relative_to(REPO)}")
        if not copia.is_file() or origen.read_bytes() != copia.read_bytes():
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, copia)
            copiados += 1
    return copiados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="solo verifica; no copia")
    args = parser.parse_args()
    if args.check:
        problemas = desvios()
        for linea in problemas:
            print(linea)
        print("sincronizado" if not problemas else f"{len(problemas)} desvío(s)")
        return 1 if problemas else 0
    print(f"{sincronizar()} archivo(s) copiado(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
