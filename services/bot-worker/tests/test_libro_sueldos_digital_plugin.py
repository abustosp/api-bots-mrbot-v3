"""Contrato del plugin ``libro_sueldos_digital`` (descarga por rango).

Fija el flujo de acceso: cuando el servicio pide elegir representado
(``IndexContribuyente``) el plugin delega la selección en el portal y verifica
que el empleador resultante sea el esperado antes de descargar. Sin selector
y sin representado explícito rige el empleador de la sesión. También fija la
subida por slot ``lsd_pdf`` y el resultado PARCIAL con liquidaciones fallidas.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError
from bot_worker.bots.libro_sueldos_digital.plugin import (
    LibroSueldosDigitalPlugin,
    nombre_lsd_pdf,
)
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.libro_sueldos_digital import LibroSueldosDigitalPortal

REPRESENTADO = "30650940667"
REPRESENTANTE = "20374730429"
OTRO_REPRESENTADO = "33712370829"
PDF = b"%PDF-1.4 libro de sueldos"


class _Servicio:
    """Doble del portal: registra la selección y sirve liquidaciones."""

    def __init__(
        self,
        *,
        empleador_cuit: str = REPRESENTADO,
        seleccionado: str = "",
        objetivos: list[dict[str, Any]] | None = None,
        fallar_en: set[str] | None = None,
    ) -> None:
        self.empleador_cuit = empleador_cuit
        self.seleccionado = seleccionado
        self.selecciones: list[str] = []
        self.descargas: list[str] = []
        self.objetivos = objetivos if objetivos is not None else [_objetivo("202601")]
        self.fallar_en = fallar_en or set()

    async def seleccionar_representado(self, cuit: str | None = None) -> str:
        self.selecciones.append(cuit or "")
        return self.seleccionado

    async def empleador_actual(self) -> dict[str, str]:
        return {"cuit": self.empleador_cuit, "denominacion": "EMPLEADOR EJEMPLO"}

    async def listar_liquidaciones(self, desde: str, hasta: str) -> list[dict[str, Any]]:
        return list(self.objetivos)

    async def descargar_libro(self, *, objetivo: dict[str, Any], destino: Path) -> Path:
        periodo = str(objetivo["periodo"])
        if periodo in self.fallar_en:
            raise TargetUnavailableError(
                "falla simulada de liquidación",
                diagnostic_code="lsd_download_error",
            )
        Path(destino).write_bytes(PDF)
        self.descargas.append(periodo)
        return Path(destino)


def _objetivo(periodo: str, liquidacion: str = "1") -> dict[str, Any]:
    return {
        "pagina": 1,
        "periodo": periodo,
        "periodo_label": f"{periodo[4:6]}/{periodo[:4]}",
        "liquidacion": liquidacion,
        "verls_id": f"ctl00_{periodo}_{liquidacion}_VerLS",
        "verls_title": "Generar Libro de Sueldos",
    }


class _Sesion:
    def __init__(self, servicio: _Servicio) -> None:
        self.servicio = servicio

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **_: Any) -> _Servicio:
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Presign:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(
        self, artifact_id: str, content_type: str, size: int, filename: str = ""
    ) -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _entrada(**extra: Any) -> dict[str, Any]:
    return {
        "operacion": "descargar",
        "periodo_desde": "202601",
        "periodo_hasta": "202603",
        **extra,
    }


def _ejecutar(
    tmp_path: Path,
    *,
    servicio: _Servicio | None = None,
    **extra: Any,
) -> tuple[Any, _Servicio, _Presign]:
    plugin = LibroSueldosDigitalPlugin()
    validado = asyncio.run(plugin.validate(_entrada(**extra)))
    servicio = servicio or _Servicio()
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-lsd",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante=REPRESENTANTE),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={"lsd_pdf": (("application/pdf",), 52_428_800)},
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_portal_registrado_por_descubrimiento() -> None:
    assert portal_para("libro_sueldos_digital") is LibroSueldosDigitalPortal


def test_con_representado_explicito_lo_pide_al_portal(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path, representado_cuit=REPRESENTADO
    )

    assert servicio.selecciones == [REPRESENTADO]
    assert servicio.descargas == ["202601"]
    assert resultado.result == "OK"
    assert resultado.data["representado_cuit"] == REPRESENTADO


def test_selector_elije_el_cuit_pedido_y_verifica(tmp_path: Path) -> None:
    servicio = _Servicio(
        empleador_cuit=OTRO_REPRESENTADO, seleccionado=OTRO_REPRESENTADO
    )

    resultado, _, _ = _ejecutar(
        tmp_path, servicio=servicio, representado_cuit=OTRO_REPRESENTADO
    )

    assert servicio.selecciones == [OTRO_REPRESENTADO]
    assert resultado.result == "OK"
    assert resultado.data["representado_cuit"] == OTRO_REPRESENTADO
    assert resultado.data["empleador"]["cuit"] == OTRO_REPRESENTADO


def test_sin_representado_explicito_verifica_contra_lo_seleccionado(
    tmp_path: Path,
) -> None:
    servicio = _Servicio(
        empleador_cuit=OTRO_REPRESENTADO, seleccionado=OTRO_REPRESENTADO
    )

    resultado, _, _ = _ejecutar(tmp_path, servicio=servicio)

    assert servicio.selecciones == [""]
    assert resultado.result == "OK"
    assert resultado.data["representado_cuit"] == OTRO_REPRESENTADO


def test_empleador_distinto_al_pedido_falla_cerrado(tmp_path: Path) -> None:
    servicio = _Servicio(empleador_cuit="30111111118")

    with pytest.raises(TargetUnavailableError) as info:
        _ejecutar(tmp_path, servicio=servicio, representado_cuit=REPRESENTADO)

    assert info.value.diagnostic_code == "lsd_represented_cuit_mismatch"
    assert servicio.descargas == []


def test_seleccionado_distinto_al_empleador_falla_cerrado(tmp_path: Path) -> None:
    servicio = _Servicio(
        empleador_cuit=REPRESENTADO, seleccionado=OTRO_REPRESENTADO
    )

    with pytest.raises(TargetUnavailableError) as info:
        _ejecutar(tmp_path, servicio=servicio)

    assert info.value.diagnostic_code == "lsd_represented_cuit_mismatch"
    assert servicio.descargas == []


def test_empleador_sin_cuit_falla_cerrado(tmp_path: Path) -> None:
    servicio = _Servicio(empleador_cuit="")

    with pytest.raises(TargetUnavailableError) as info:
        _ejecutar(tmp_path, servicio=servicio, representado_cuit=REPRESENTADO)

    assert info.value.diagnostic_code == "lsd_represented_cuit_unverified"
    assert servicio.descargas == []


def test_empleador_sin_cuit_sin_expectativa_tambien_falla_cerrado(
    tmp_path: Path,
) -> None:
    """Sin selector y sin representado igual se exige un CUIT válido visible."""
    servicio = _Servicio(empleador_cuit="")

    with pytest.raises(TargetUnavailableError) as info:
        _ejecutar(tmp_path, servicio=servicio)

    assert info.value.diagnostic_code == "lsd_represented_cuit_unverified"
    assert servicio.descargas == []


def test_empleador_con_cuit_incompleto_falla_cerrado(tmp_path: Path) -> None:
    servicio = _Servicio(empleador_cuit="30-6509406")

    with pytest.raises(TargetUnavailableError) as info:
        _ejecutar(tmp_path, servicio=servicio, representado_cuit=REPRESENTADO)

    assert info.value.diagnostic_code == "lsd_represented_cuit_unverified"
    assert servicio.descargas == []


def test_sin_expectativa_rige_el_empleador_de_la_sesion(tmp_path: Path) -> None:
    servicio = _Servicio(empleador_cuit=REPRESENTADO, seleccionado="")

    resultado, _, _ = _ejecutar(tmp_path, servicio=servicio)

    assert servicio.descargas == ["202601"]
    assert resultado.result == "OK"
    assert resultado.data["representado_cuit"] == REPRESENTADO


def test_subir_archivos_usa_el_slot_lsd_pdf(tmp_path: Path) -> None:
    resultado, _, presign = _ejecutar(
        tmp_path, representado_cuit=REPRESENTADO
    )

    assert presign.ids == ["lsd_pdf"]
    assert [a["artifact_id"] for a in resultado.artifacts] == ["lsd_pdf"]
    assert resultado.data["detalle"][0]["sha256"]


def test_subir_archivos_false_no_sube_nada(tmp_path: Path) -> None:
    resultado, _, presign = _ejecutar(
        tmp_path, representado_cuit=REPRESENTADO, subir_archivos=False
    )

    assert presign.ids == []
    assert resultado.artifacts == []
    assert resultado.data["detalle"][0]["archivo"] == nombre_lsd_pdf(
        REPRESENTADO, "202601", "1"
    )


def test_liquidacion_fallida_deja_resultado_parcial(tmp_path: Path) -> None:
    servicio = _Servicio(
        objetivos=[_objetivo("202601"), _objetivo("202602")],
        fallar_en={"202602"},
    )

    resultado, _, _ = _ejecutar(
        tmp_path, servicio=servicio, representado_cuit=REPRESENTADO
    )

    assert resultado.result == "PARCIAL"
    assert resultado.data["liquidaciones_descargadas"] == 1
    assert resultado.data["periodos_descargados"] == ["202601"]


def test_rango_invalido_falla_antes_del_navegador() -> None:
    plugin = LibroSueldosDigitalPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(
            plugin.validate(_entrada(periodo_desde="202612", periodo_hasta="202601"))
        )


def test_nombre_del_pdf_es_deterministico() -> None:
    assert nombre_lsd_pdf(REPRESENTADO, "202601", "7") == f"{REPRESENTADO}_202601_00007.pdf"
