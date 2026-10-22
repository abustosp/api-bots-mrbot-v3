"""Portal de Certificación Primaria de Granos de ARCA.

Reutiliza el servicio "Liquidación Primaria de Granos" (selección de
representado, menú y descarga de certificados) y fija la consulta de
certificados de depósito en el tipo ``Certificados como Depositante``.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .liquidacion_granos import LiquidacionGranosPortal, _guardar_xlsx

_TIPO_DEPOSITANTE = re.compile(r"como\s+depositante", re.I)


class CertificacionPrimariaGranosPortal(LiquidacionGranosPortal):
    """Certificados de depósito emitidos al representado como depositante."""

    nombre = "certificacion_primaria_granos"

    async def _consultar_depositante(self, desde: str, hasta: str) -> str:
        await self._abrir_certificados()
        valor = ""
        for codigo, texto in await self._tipos_certificado():
            if _TIPO_DEPOSITANTE.search(texto):
                valor = codigo
                break
        if not valor:
            raise TargetUnavailableError(
                "no existe el tipo de consulta 'Certificados como Depositante'",
                diagnostic_code="granos_depositor_type_missing",
            )
        await self._consultar_certificados(desde, hasta, valor)
        return valor

    async def descargar_certificados_depositante(
        self, *, destino_xlsx: Path, destino_dir: Path, desde: str, hasta: str
    ) -> tuple[Path, list[Path]]:
        """Consulta el rango, guarda la grilla como XLSX y baja cada PDF."""
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        valor = await self._consultar_depositante(desde, hasta)
        datos = self._tabla_de_datos(await self.tablas_hoja())
        if datos is None:
            encabezados, filas = ["Resultado"], [["Sin registros"]]
        else:
            encabezados, filas = self._completar_tabla(*datos)
        planilla: Any = await self.paso(
            "descargar_planilla",
            asyncio.to_thread(_guardar_xlsx, Path(destino_xlsx), encabezados, filas),
        )
        pdfs = await self._descargar_certificados(destino_dir, valor)
        return planilla, pdfs
