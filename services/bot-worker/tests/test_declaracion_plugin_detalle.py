"""Plugin declaracion_en_linea: detalle por periodo, nomina y JSON (fakes, sin navegador)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from bot_worker.bots.declaracion_en_linea.plugin import (
    ID_ARTEFACTO_JSON,
    ID_ARTEFACTO_NOMINA,
    DeclaracionEnLineaPlugin,
)
from bot_worker.runtime.context import (
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


class _ServicioDDJJ:
    """Portal falso: escribe PDF, JSON y nomina como lo haria el portal real."""

    def __init__(self, periodos_sin_ddjj: set[str] | None = None, sin_nomina: bool = False) -> None:
        self.periodos_sin_ddjj = periodos_sin_ddjj or set()
        self.sin_nomina = sin_nomina
        self.detalle_llamadas: list[str] = []
        self.nomina_llamadas: list[str] = []

    async def seleccionar_representado(self, cuit: str) -> None:
        return None

    async def consultar_periodo_detalle(
        self,
        *,
        periodo: str,
        destino_ddjj: Path,
        destino_vep: Path,
        destino_json: Path | None = None,
    ) -> dict[str, Any]:
        from bot_worker.bots.errors import TargetUnavailableError

        self.detalle_llamadas.append(periodo)
        if periodo in self.periodos_sin_ddjj:
            raise TargetUnavailableError(
                "no se encontr\u00f3 una DDJJ generada",
                diagnostic_code="declaracion_en_linea_period_not_found",
            )
        Path(destino_ddjj).write_bytes(b"%PDF-1.7 ddjj")
        Path(destino_vep).write_bytes(b"%PDF-1.7 vep")
        if destino_json is not None:
            Path(destino_json).write_text('{"periodo": "%s"}' % periodo, encoding="utf-8")
        return {
            "estado_vep": "pagado",
            "pagado": True,
            "cabecera": {"C.U.I.T.": CUIT},
            "secciones": {"I - Remuneraciones": {"110 Sueldos": "1.000,50"}},
            "advertencias": [],
        }

    async def descargar_nomina(self, row_periodo: str, destino: Path) -> Path | None:
        self.nomina_llamadas.append(row_periodo)
        if self.sin_nomina:
            return None
        Path(destino).write_bytes(b"\xd0\xcf\x11\xe0 nomina")
        return Path(destino)


class _Sesion:
    def __init__(self, servicio: _ServicioDDJJ) -> None:
        self._servicio = servicio

    async def login(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def open_service(self, *args: Any, **kwargs: Any) -> _ServicioDDJJ:
        return self._servicio


class _AsyncCM:
    def __init__(self, valor: Any) -> None:
        self._valor = valor

    async def __aenter__(self) -> Any:
        return self._valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self._sesion = sesion

    def arca_session(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM(self._sesion)


def _runtime(tmp_path: Path, fabrica: _Fabrica) -> tuple[BotRuntime, list[str]]:
    subidos: list[str] = []

    async def presign(artifact_id: str, content_type: str, size_bytes: int, filename: str = "") -> dict[str, Any]:
        return {"upload_url": "", "object_key": f"obj/{artifact_id}"}

    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=presign,
        declarados={
            spec.nombre: (spec.content_types, spec.max_bytes)
            for spec in DeclaracionEnLineaPlugin.manifest.artefactos_produce
        },
    )
    original = store.upload

    async def upload_registrado(artifact_id: str, rel_path: str, client: Any = None) -> dict[str, Any]:
        subidos.append(artifact_id)
        return await original(artifact_id, rel_path, client)

    store.upload = upload_registrado  # type: ignore[method-assign]
    rt = BotRuntime(
        job_id="job-decl",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=time.time() + 300),
        credentials=FiscalCredentials(cuit_representante=CUIT, clave="secreta"),
        proxy=None,
        artifact_store=store,
        event_sink=_Sink(),
        browser_factory=fabrica,  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )
    return rt, subidos


def _payload(**extra: Any) -> dict[str, Any]:
    base = {
        "operacion": "consultar",
        "representado_cuit": CUIT,
        "periodo_desde": "202503",
        "periodo_hasta": "202504",
    }
    base.update(extra)
    return base


def test_consulta_sube_ddjj_vep_nomina_y_json_por_periodo(tmp_path: Path) -> None:
    async def _ir() -> None:
        servicio = _ServicioDDJJ()
        plugin = DeclaracionEnLineaPlugin()
        validado = await plugin.validate(_payload())
        rt, subidos = _runtime(tmp_path, _Fabrica(_Sesion(servicio)))

        resultado = await plugin.execute(validado, rt)

        assert resultado.result == "OK"
        assert servicio.detalle_llamadas == ["202503", "202504"]
        assert servicio.nomina_llamadas == ["202503", "202504"]
        assert subidos.count(ID_ARTEFACTO_NOMINA) == 2
        assert subidos.count(ID_ARTEFACTO_JSON) == 2
        detalle = resultado.data["detalle"]
        assert detalle[0]["cabecera"] == {"C.U.I.T.": CUIT}
        assert detalle[0]["secciones"] == {"I - Remuneraciones": {"110 Sueldos": "1.000,50"}}
        assert detalle[0]["estado_vep"] == "pagado"

    asyncio.run(_ir())


def test_sin_nomina_el_periodo_sigue_y_no_se_sube_nomina(tmp_path: Path) -> None:
    async def _ir() -> None:
        servicio = _ServicioDDJJ(sin_nomina=True)
        plugin = DeclaracionEnLineaPlugin()
        validado = await plugin.validate(_payload())
        rt, subidos = _runtime(tmp_path, _Fabrica(_Sesion(servicio)))

        resultado = await plugin.execute(validado, rt)

        assert resultado.result == "OK"
        assert ID_ARTEFACTO_NOMINA not in subidos
        assert subidos.count(ID_ARTEFACTO_JSON) == 2
        assert resultado.data["periodos_consultados"] == 2

    asyncio.run(_ir())


def test_periodo_sin_ddjj_se_saltea_y_se_informa(tmp_path: Path) -> None:
    async def _ir() -> None:
        servicio = _ServicioDDJJ(periodos_sin_ddjj={"202503"})
        plugin = DeclaracionEnLineaPlugin()
        validado = await plugin.validate(_payload())
        rt, _ = _runtime(tmp_path, _Fabrica(_Sesion(servicio)))

        resultado = await plugin.execute(validado, rt)

        assert resultado.data["periodos_sin_ddjj"] == ["202503"]
        assert resultado.data["periodos_consultados"] == 1
        assert servicio.nomina_llamadas == ["202504"]

    asyncio.run(_ir())


def test_manifiesto_declara_nomina_y_json() -> None:
    nombres = {spec.nombre for spec in DeclaracionEnLineaPlugin.manifest.artefactos_produce}
    assert {"ddjj.pdf", "vep.pdf", "nomina.xls", "ddjj.json"} <= nombres
    assert ID_ARTEFACTO_NOMINA == "nomina_xls"
    assert ID_ARTEFACTO_JSON == "ddjj_json"
