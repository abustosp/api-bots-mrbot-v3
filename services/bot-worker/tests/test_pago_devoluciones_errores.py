"""Errores por seccion de pago_devoluciones (port V1 sin navegador real).

Cubre ``_append_section_error``, ``_has_critical_errors`` y la traduccion
de ``errores_por_seccion`` a ``BotResult.result``. La captcha y el cambio
de clave obligatorio viven en ``runtime/arca_login.py`` y no se duplican.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import (
    ArtifactUploadError,
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    TargetUnavailableError,
)
from bot_worker.bots.pago_devoluciones.plugin import (
    PagoDevolucionesPlugin,
    _append_section_error,
    _estado_por_secciones,
    _has_critical_errors,
    nombre_base_archivo,
    safe_filename,
)
from bot_worker.runtime.context import (
    ArtifactSlot,
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
)

CUIT = "20123456789"


class _Sink:
    async def progress(self, phase: str, percent: int, message: str) -> None:
        return None


class _AsyncCM:
    def __init__(self, valor: Any) -> None:
        self._valor = valor

    async def __aenter__(self) -> Any:
        return self._valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ServicioExportador:
    """Servicio falso: escribe el Excel o falla como lo haria el portal."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.llamadas = 0

    async def seleccionar_representado(self, cuit: str) -> None:
        return None

    async def consultar_y_exportar(self, *, cuit_representado: str, destino: Path) -> Path:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        destino.write_bytes(b"PK\x03\x04xlsx-de-prueba")
        return destino


class _Sesion:
    def __init__(self, servicio: _ServicioExportador) -> None:
        self._servicio = servicio

    async def login(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def open_service(self, *args: Any, **kwargs: Any) -> _ServicioExportador:
        return self._servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self._sesion = sesion

    def arca_session(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM(self._sesion)


def _runtime(tmp_path: Path, fabrica: _Fabrica, slots: dict[str, ArtifactSlot]) -> BotRuntime:
    return BotRuntime(
        job_id="job-pago",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=time.time() + 300),
        credentials=FiscalCredentials(cuit_representante=CUIT, clave="secreta"),
        proxy=None,
        artifact_store=ArtifactStore(work_dir=tmp_path, slots=slots),
        event_sink=_Sink(),
        browser_factory=fabrica,  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )


def _slot_xlsx() -> dict[str, ArtifactSlot]:
    return {"pagos_xlsx": ArtifactSlot(artifact_id="pagos_xlsx", put_url="", object_key="obj/p")}


# ---------------------------------------------------------------------------
# Helpers puros (port de V1)


def test_append_section_error_agrupa_y_normaliza_mensaje_vacio() -> None:
    errores: dict[str, list[str]] = {}
    _append_section_error(errores, "login", "  clave rechazada  ")
    _append_section_error(errores, "login", "")
    assert errores == {"login": ["clave rechazada", "Error desconocido"]}


def test_nombre_de_archivo_replica_el_patron_de_v1() -> None:
    nombre = nombre_base_archivo(CUIT)

    assert nombre.startswith(f"pago_devoluciones_{CUIT}_")
    assert safe_filename("  a/b:c *") == "a_b_c"
    assert safe_filename("") == "archivo"


def test_validate_acepta_los_nombres_de_v1() -> None:
    async def _ir() -> None:
        _, entrada = await PagoDevolucionesPlugin().validate({
            "operacion": "consultar",
            "cuit_representado": "20-12345678-9",
            "carga_minio": False,
            "proxy_request": True,
            "cuit_representante": "27123456789",
            "clave_representante": "secreta",
        })
        assert entrada.representado_cuit == CUIT
        assert entrada.subir_archivo is False

        # V1: representado vacio usa el CUIT del representante del sobre.
        _, vacio = await PagoDevolucionesPlugin().validate(
            {"operacion": "consultar", "cuit_representado": ""}
        )
        assert vacio.representado_cuit is None

    asyncio.run(_ir())


def test_secciones_criticas_coinciden_con_v1() -> None:
    for seccion in ("validacion", "proxy", "login", "servicio", "representado", "consulta", "general"):
        assert _has_critical_errors({seccion: ["x"]}) is True
    assert _has_critical_errors({"minio": ["no subio"]}) is False
    assert _has_critical_errors({}) is False
    assert _has_critical_errors({"login": []}) is False


def test_estado_ok_parcial_y_error_segun_seccion() -> None:
    assert _estado_por_secciones({}) == "OK"
    assert _estado_por_secciones({"minio": ["no subio"]}) == "PARCIAL"
    assert _estado_por_secciones({"minio": ["x"], "consulta": ["y"]}) == "ERROR"


# ---------------------------------------------------------------------------
# execute() de punta a punta con fakes


def test_execute_sin_errores_devuelve_ok_y_secciones_vacias(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = PagoDevolucionesPlugin()
        payload = await plugin.validate({"operacion": "consultar", "representado_cuit": CUIT})
        servicio = _ServicioExportador()
        rt = _runtime(tmp_path, _Fabrica(_Sesion(servicio)), _slot_xlsx())
        resultado = await plugin.execute(payload, rt)
        assert resultado.result == "OK"
        assert resultado.data["errores_por_seccion"] == {}
        assert len(resultado.artifacts) == 1
        assert servicio.llamadas == 1

    asyncio.run(_ir())


def test_execute_fallo_de_exportacion_cae_en_seccion_consulta_y_relanza(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = PagoDevolucionesPlugin()
        payload = await plugin.validate({"operacion": "consultar", "representado_cuit": CUIT})
        servicio = _ServicioExportador(
            error=TargetUnavailableError(
                "no se encontro el boton Exportar",
                diagnostic_code="pago_devoluciones_export_missing",
            )
        )
        rt = _runtime(tmp_path, _Fabrica(_Sesion(servicio)), _slot_xlsx())
        with pytest.raises(TargetUnavailableError) as error:
            await plugin.execute(payload, rt)
        assert error.value.diagnostic_code == "pago_devoluciones_export_missing"

    asyncio.run(_ir())


def test_execute_subida_fallida_sigue_siendo_artifact_upload_error(tmp_path: Path) -> None:
    """Contrato V3: subida fallida reintentable, no PARCIAL silencioso."""

    async def _ir() -> None:
        plugin = PagoDevolucionesPlugin()
        payload = await plugin.validate({"operacion": "consultar", "representado_cuit": CUIT})
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioExportador())), {})
        with pytest.raises(ArtifactUploadError):
            await plugin.execute(payload, rt)

    asyncio.run(_ir())


def test_execute_sin_subida_devuelve_archivo_local_con_errores_vacios(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = PagoDevolucionesPlugin()
        payload = await plugin.validate({
            "operacion": "consultar",
            "representado_cuit": CUIT,
            "subir_archivo": False,
        })
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioExportador())), {})
        resultado = await plugin.execute(payload, rt)
        assert resultado.result == "OK"
        assert resultado.artifacts == []
        assert resultado.data["errores_por_seccion"] == {}
        assert resultado.data["detalle_archivo"]["archivo"].endswith(".xlsx")

    asyncio.run(_ir())


def test_captcha_y_clave_no_se_duplican_en_el_portal() -> None:
    """Captcha y cambio de clave los cubre ``arca_login``; el portal no las porta."""
    from bot_worker.runtime import portals

    portal = portals.PagoDevolucionesPortal
    assert not hasattr(portal, "_is_captcha_visible")
    assert not hasattr(portal, "_password_change_required")
    assert issubclass(CaptchaUnsolvableError, Exception)
    assert issubclass(CredentialsRejectedError, Exception)
