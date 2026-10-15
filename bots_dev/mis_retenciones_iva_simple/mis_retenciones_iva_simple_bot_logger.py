"""Variante logger del bot de descarga Mis Retenciones IVA SIMPLE.

La lógica funcional se mantiene en la variante clean. Este wrapper agrega el
artefacto de ejecución en temp/ para revisar parámetros, resultado y errores sin
persistir credenciales.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from mis_retenciones_iva_simple.mis_retenciones_iva_simple_bot import (
    bot_mis_retenciones_iva_simple,
)


class DebugCapture:
    def __init__(self, base_dir: str = "temp") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"mis_retenciones_iva_simple_{run_id}"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.downloads_dir = self.run_dir / "descargas"
        self.har_dir = self.run_dir / "har"
        for directory in (
            self.screenshots_dir,
            self.html_dir,
            self.downloads_dir,
            self.har_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f"mis_retenciones_iva_simple.{run_id}")
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler(self.run_dir / "mis_retenciones_iva_simple.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)

    def log(self, message: str, **fields: Any) -> None:
        self.logger.info("%s %s", message, json.dumps(fields, ensure_ascii=False, default=str))


async def bot_mis_retenciones_iva_simple_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str],
    denominacion: str,
    desde: str,
    hasta: str,
    carga_minio: bool = False,
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    debug = DebugCapture()
    debug.log("inicio", desde=desde, hasta=hasta, carga_minio=carga_minio)
    results, errors, success = await bot_mis_retenciones_iva_simple(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        denominacion=denominacion,
        desde=desde,
        hasta=hasta,
        carga_minio=carga_minio,
        proxy_request=proxy_request,
    )
    debug.log(
        "fin",
        success=success,
        archivos=len(results.get("archivos", [])),
        errores=errors,
    )
    (debug.run_dir / "resultado.json").write_text(
        json.dumps({"success": success, "archivos": results.get("archivos", []), "errores": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results, errors, success


async def main() -> None:
    import os
    from dotenv import load_dotenv

    load_dotenv()
    await bot_mis_retenciones_iva_simple_logger(
        cuit_representante=os.getenv("CUIT_REPRESENTANTE", ""),
        clave_representante=os.getenv("CLAVE", ""),
        cuit_representado=os.getenv("CUIT_REPRESENTADO"),
        denominacion=os.getenv("DENOMINACION", "MI EMPRESA"),
        desde=os.getenv("DESDE", ""),
        hasta=os.getenv("HASTA", ""),
    )


if __name__ == "__main__":
    asyncio.run(main())
