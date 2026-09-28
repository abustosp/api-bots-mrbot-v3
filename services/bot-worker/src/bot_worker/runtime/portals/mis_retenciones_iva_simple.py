"""Portal Mis Retenciones en modo IVA Simple, port de V1/V2."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .mis_retenciones import MisRetencionesPortal, _clave_fecha, _recortar_a_hoy

_OPERACION_IVA_SIMPLE = "RETENCIONES IMPOSITIVAS"


def _rango_iva_simple_valido(desde: str, hasta: str) -> bool:
    """Aplica el límite de período que exige el portal IVA Simple de V2."""
    try:
        fecha_desde = datetime.strptime((desde or "").strip(), "%d/%m/%Y")
        fecha_hasta = datetime.strptime((hasta or "").strip(), "%d/%m/%Y")
    except ValueError:
        return False
    if fecha_hasta < fecha_desde:
        return False
    if fecha_desde.month == 12:
        primer_dia_siguiente = datetime(fecha_desde.year + 1, 1, 1)
    else:
        primer_dia_siguiente = datetime(fecha_desde.year, fecha_desde.month + 1, 1)
    ultimo_dia_maximo = (primer_dia_siguiente + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    return fecha_hasta <= ultimo_dia_maximo


class MisRetencionesIvaSimplePortal(MisRetencionesPortal):
    """Consulta el modo IVA Simple y exporta el CSV de retenciones."""

    nombre = "mis_retenciones_iva_simple"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._iva_simple_activo = False

    async def activar_modo_iva_simple(self) -> None:
        """Activa IVA Simple y desactiva el formato SIAP (port exacto de V2)."""
        await self._asegurar_formulario()
        await self.paso("activar_modo_iva_simple", self._activar_modo_iva_simple())
        await self._esperar(1_000)
        checkbox = self.page.locator("input[type='checkbox'][value='ivaSimple']")
        try:
            if await checkbox.count() and not await checkbox.is_checked():
                await checkbox.locator("xpath=ancestor::label[1]").click(timeout=5_000)
                await self._esperar(500)
            if not await checkbox.count() or not await checkbox.is_checked():
                raise TargetUnavailableError(
                    "el portal no mantuvo activo el modo IVA Simple",
                    diagnostic_code="iva_simple_mode_not_selected",
                )
        except TargetUnavailableError:
            raise
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo confirmar el modo IVA Simple",
                diagnostic_code="iva_simple_mode_not_selected",
            ) from exc
        self._iva_simple_activo = True

    async def _activar_modo_iva_simple(self) -> None:
        for valor in ("siap", "ivaSimple"):
            checkbox = self.page.locator(f"input[type='checkbox'][value='{valor}']")
            try:
                if await checkbox.count() == 0:
                    raise TargetUnavailableError(
                        "no se encontró el control de modo IVA Simple",
                        diagnostic_code="iva_simple_mode_control_missing",
                    )
                if valor == "ivaSimple" and not await checkbox.is_checked():
                    await checkbox.check(timeout=5_000)
                    if not await checkbox.is_checked():
                        await checkbox.locator("xpath=ancestor::label[1]").click(timeout=5_000)
                    if not await checkbox.is_checked():
                        raise TargetUnavailableError(
                            "el portal no confirmó la activación del modo IVA Simple",
                            diagnostic_code="iva_simple_mode_activation_failed",
                        )
                elif valor == "siap" and await checkbox.is_checked():
                    await checkbox.uncheck(timeout=5_000)
            except TargetUnavailableError:
                raise
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo activar el modo IVA Simple",
                    diagnostic_code="iva_simple_mode_activation_failed",
                ) from exc

    async def descargar_csv(
        self,
        *,
        destino: Path,
        desde: str,
        hasta: str,
    ) -> Path:
        """Consulta el rango en IVA Simple y descarga su exportación CSV."""
        await self._asegurar_formulario()
        if not self._iva_simple_activo:
            await self.activar_modo_iva_simple()
        await self.paso("seleccionar_operacion_iva_simple", self._seleccionar_operacion_iva_simple())
        await self.paso("seleccionar_fechas_iva_simple", self._seleccionar_fechas_iva_simple(desde, hasta))
        archivo = await self.paso(
            "exportar_csv_iva_simple",
            self._consultar_y_descargar(
                Path(destino),
                export_option="iva_simple",
            ),
        )
        self._normalizar_exportacion_csv(Path(archivo))
        return Path(archivo)

    async def _seleccionar_operacion_iva_simple(self) -> None:
        """Selecciona retenciones impositivas en el selector V2 o su UI actualizada."""
        selector = self.page.locator("#selectTipoOperacionIvaSimple")
        try:
            await selector.wait_for(state="visible", timeout=4_000)
            await selector.click(timeout=4_000)
            opciones = (
                self.page.get_by_role("option", name=re.compile(rf"^{re.escape(_OPERACION_IVA_SIMPLE)}$", re.I)),
                self.page.locator("#selectTipoOperacionIvaSimple-dropdown").get_by_text(
                    _OPERACION_IVA_SIMPLE, exact=True
                ),
            )
            for opcion in opciones:
                try:
                    if await opcion.count():
                        await opcion.first.click(timeout=4_000)
                        return
                except Exception:
                    continue
        except Exception:
            pass
        # La SPA vigente reemplazó el selector IVA Simple por el filtro normal
        # de impuestos y tipo de operación. SICORE 217 es el equivalente actual
        # de la primera categoría V2, “Retenciones Impositivas”.
        tipo = self.page.locator("#tipoOperacion")
        if not await tipo.count():
            if not await self._seleccionar_impuesto(
                "217 - SICORE-IMPTO.A LAS GANANCIAS", "217"
            ):
                raise TargetUnavailableError(
                    "no se encontraron filtros de operación IVA Simple",
                    diagnostic_code="iva_simple_operation_control_missing",
                )
            try:
                await tipo.wait_for(state="visible", timeout=8_000)
            except Exception as exc:
                raise TargetUnavailableError(
                    "no apareció el tipo de operación tras elegir los impuestos",
                    diagnostic_code="iva_simple_operation_control_missing",
                ) from exc
        if await self._seleccionar_tipo("Retencion"):
            return
        raise TargetUnavailableError(
            "no se encontró la selección de operación de retenciones IVA Simple",
            diagnostic_code="iva_simple_operation_control_missing",
        )

    async def _seleccionar_fechas_iva_simple(self, desde: str, hasta: str) -> None:
        # El portal no admite fechas futuras: se recorta "hasta" a hoy.
        hasta = _recortar_a_hoy(hasta)
        desde = min(desde, hasta, key=_clave_fecha)
        if not _rango_iva_simple_valido(desde, hasta):
            raise TargetUnavailableError(
                "el rango ingresado no cumple el límite del portal IVA Simple",
                diagnostic_code="iva_simple_date_range_invalid",
            )
        # V2 opera sobre el par de datepickers por placeholder, carga ambos
        # valores y confirma con Enter. En la SPA actual, la validación estricta
        # del value tras cada evento puede reportar fallo aunque el componente
        # haya aceptado la fecha. Mantener el orden y el contrato efectivos de V2.
        campos = self.page.locator("input[placeholder='dd/mm/aaaa']")
        try:
            await campos.first.wait_for(state="visible", timeout=5_000)
            if await campos.count() >= 2:
                for campo, valor in ((campos.nth(0), desde), (campos.nth(1), hasta)):
                    await campo.fill(valor)
                    await campo.press("Enter")
                return
        except Exception:
            pass
        if await self._seleccionar_rango_fechas(desde, hasta):
            return
        raise TargetUnavailableError(
            "no se pudo ingresar el rango de fechas IVA Simple",
            diagnostic_code="iva_simple_date_range_failed",
        )

    async def _seleccionar_formato(self, export_option: str | None) -> bool:
        """Usa el formato IVA Simple de V2 y su alternativa XLS histórica."""
        if export_option == "iva_simple":
            if await super()._seleccionar_formato("iva_simple"):
                return True
            return await super()._seleccionar_formato("xls")
        return await super()._seleccionar_formato(export_option)
