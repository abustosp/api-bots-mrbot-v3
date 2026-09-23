"""Espacio de trabajo efimero por (job_id, attempt).

Se crea bajo WORK_DIR con permisos 0700 y se borra siempre en `finally`,
por resultado, excepcion, timeout, cancelacion o SIGTERM. Si el proceso
muere, el volumen /work efimero desaparece con el contenedor.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_segment(value: str) -> str:
    cleaned = _SAFE.sub("-", value).strip("-") or "job"
    return cleaned[:64]


def new_workdir(root: str | Path, job_id: str, attempt: int) -> Path:
    path = (
        Path(root) / safe_segment(job_id) / safe_segment(str(attempt))
    )
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def cleanup_workdir(path: str | Path | None) -> None:
    if path is None:
        return
    shutil.rmtree(str(path), ignore_errors=True)
