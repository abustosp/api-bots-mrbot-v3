"""Nombres deterministicos para artefactos descargados.

Patron (de user-taste-profile):
  {fin_cuit} - {cuit_representado} - {service} - {subtype} - {period} - {representado}.{ext}

fin_cuit = ultimo digito del cuit_representado.
"""
from __future__ import annotations

import re
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe(value: str) -> str:
    return _SAFE.sub("_", (value or "").strip()).strip("_") or "x"


def build_filename(
    cuit_representado: str,
    service: str,
    subtype: str,
    period: str,
    representado: str,
    ext: str,
) -> str:
    fin = (cuit_representado or "")[-1:] or "0"
    ext = ext.lstrip(".")
    return (
        f"{fin} - {safe(cuit_representado)} - {safe(service)} - "
        f"{safe(subtype)} - {safe(period)} - {safe(representado)}.{ext}"
    )


def build_path(
    base_dir: Path | str,
    cuit_representado: str,
    service: str,
    subtype: str,
    period: str,
    representado: str,
    ext: str,
) -> Path:
    return Path(base_dir) / build_filename(
        cuit_representado, service, subtype, period, representado, ext
    )
