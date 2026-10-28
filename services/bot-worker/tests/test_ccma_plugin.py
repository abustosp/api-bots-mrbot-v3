"""CCMA plugin ensamblado: el corte de paginacion llega a ``data`` y el PDF usa el HTML de V1.

Se ejecuta ``CcmaPlugin.execute`` con una sesion y un portal falsos. El portal
simula una tabla que no avanza al hacer clic en ``#mas1``: el resultado debe
seguir siendo OK con los movimientos reunidos y el motivo del corte.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.ccma.plugin import CcmaPlugin
from bot_worker.bots.ccma.movimientos import CORTE_FALLBACK, CORTE_SIN_PROGRESO
from bot_worker.bots.errors import InvalidInputError
from bot_worker.runtime.context import ArtifactStore, CancellationToken

CUIT = "20123456786"
RESUMEN = {
    "cuit": CUIT,
    "periodo": "04/2000",
    "deuda_capital": "$ 10,00",
    "deuda_accesorios": "$ 2,00",
    "total_deuda": "$ 12,00",
    "credito_capital": "$ 0,00",
    "credito_accesorios": "$ 0,00",
    "total_a_favor": "$ 0,00",
}


def _mov(periodo: str) -> dict[str, str]:
    return {
        "periodo": periodo,
        "impuesto": "37",
        "concepto": "1",
        "subconcepto": "0",
        "descripcion": "Pago",
        "fecha_movimiento": "01/02/2026",
        "debe": "0,00",
        "haber": "100,00",
    }


class _PortalCcma:
    """Portal falso: tabla con dos paginas, pero el clic no la cambia."""

    def __init__(self, context: Any) -> None:
        self._context = context
        self.pdf_html: str | None = None
        self.pdf_destino: Path | None = None
        self.clics = 0

    async def seleccionar_cuit(self, cuit: str) -> None:
        return None

    async def calcular_deuda(self, periodo_desde: str) -> None:
        return None

    async def ir_a_cuenta_corriente(self, url: str) -> None:
        return None

    async def seleccionar_rango(self, valor: str) -> None:
        return None

    async def resumen(self, cuit: str, periodo_desde: str) -> dict[str, str]:
        return {**RESUMEN, "periodo_desde": periodo_desde}

    async def estado(self) -> dict[str, Any]:
        return {
            "tablePresent": True,
            "primerPeriodo": "01/2019",
            "ultimoPeriodo": "12/2025",
            "firstVisiblePeriodo": "p",
            "lastVisiblePeriodo": "u",
            "nextVisible": True,
        }

    async def filas(self) -> list[dict[str, str]]:
        return [_mov("01/2026")]

    async def siguiente_disponible(self) -> bool:
        return True

    async def avanzar(self) -> bool:
        self.clics += 1
        return True

    async def esperar_cambio(self, estado_previo: dict[str, Any], timeout_ms: int) -> bool:
        return False

    async def imprimir_pdf(self, html: str, destino: Path) -> Path:
        self.pdf_html = html
        self.pdf_destino = Path(destino)
        Path(destino).write_bytes(b"%PDF-1.4 fake")
        return Path(destino)


class _Sesion:
    def __init__(self, portal: _PortalCcma) -> None:
        self.portal = portal
        self.portales: list[str | None] = []

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **kwargs: Any) -> _PortalCcma:
        self.portales.append(kwargs.get("portal"))
        return self.portal


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

    async def __call__(self, artifact_id: str, content_type: str, size: int) -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def movimientos_de(tmp_path: Path) -> list[dict[str, str]]:
    from mrbot_contracts.datos_csv import movimientos_ccma_desde_csv

    return movimientos_ccma_desde_csv(
        (tmp_path / f"CCMA_movimientos_{CUIT}_job-ccma.csv").read_text(encoding="utf-8")
    )


def _ejecutar(
    tmp_path: Path, *, portal: Any | None = None, **entrada_extra: Any
) -> tuple[Any, _PortalCcma, _Presign]:
    plugin = CcmaPlugin()
    entrada = asyncio.run(
        plugin.validate(
            {"operacion": "consultar", "representado_cuit": CUIT, "incluir_movimientos": True, **entrada_extra}
        )
    )
    presign = _Presign()
    portal = portal if portal is not None else _PortalCcma(context=None)
    runtime = SimpleNamespace(
        job_id="job-ccma",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={
                "ccma_resumen.pdf": (("application/pdf",), 10_485_760),
                "ccma_saldos.csv": (("text/csv",), 10_485_760),
                "ccma_movimientos.csv": (("text/csv",), 52_428_800),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(portal)),
        cancellation=CancellationToken(),
    )
    return asyncio.run(plugin.execute(entrada, runtime)), portal, presign


def test_corte_por_no_progreso_queda_en_data_y_el_resultado_sigue_ok(tmp_path: Path) -> None:
    resultado, portal, _ = _ejecutar(tmp_path)

    assert resultado.result == "OK"
    assert resultado.data["movimientos_corte"] == CORTE_SIN_PROGRESO
    assert resultado.data["movimientos_completos"] is False
    # Los movimientos salieron del JSON y viajan como CSV con "|" en S4.
    assert "movimientos" not in resultado.data
    assert resultado.data["datos_en_archivo"] == ["movimientos", "saldos"]
    nombres = {a["name"] for a in resultado.artifacts}
    assert nombres == {
        f"CCMA_saldos_{CUIT}_job-ccma.csv", f"CCMA_movimientos_{CUIT}_job-ccma.csv",
    }
    mov = (tmp_path / f"CCMA_movimientos_{CUIT}_job-ccma.csv").read_text(encoding="utf-8")
    assert mov.splitlines()[0].startswith("periodo|impuesto|")
    assert [m["periodo"] for m in movimientos_de(tmp_path)] == ["01/2026"]
    assert portal.clics == 1


def test_el_plugin_pide_el_portal_ccma(tmp_path: Path) -> None:
    plugin = CcmaPlugin()
    entrada = asyncio.run(plugin.validate({"operacion": "consultar", "representado_cuit": CUIT}))
    portal = _PortalCcma(context=None)
    sesion = _Sesion(portal)
    runtime = SimpleNamespace(
        job_id="job-ccma",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path, slots={}, presign=_Presign(),
            declarados={
                "ccma_saldos.csv": (("text/csv",), 10_485_760),
                "ccma_movimientos.csv": (("text/csv",), 52_428_800),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(sesion),
        cancellation=CancellationToken(),
    )

    asyncio.run(plugin.execute(entrada, runtime))

    assert sesion.portales == ["ccma"]


def test_pdf_se_genera_con_el_html_consolidado_y_se_sube_por_slot(tmp_path: Path) -> None:
    resultado, portal, presign = _ejecutar(tmp_path, incluir_pdf=True, subir=True)

    assert resultado.result == "OK"
    assert portal.pdf_html is not None
    assert "CCMA consolidado" in portal.pdf_html
    assert "Movimientos consolidados: 1" in portal.pdf_html
    assert portal.pdf_destino is not None and portal.pdf_destino.parent == tmp_path
    assert presign.ids == ["ccma_saldos.csv", "ccma_movimientos.csv", "ccma_resumen.pdf"] or "ccma_resumen.pdf" in presign.ids
    assert "ccma_resumen.pdf" in [a["artifact_id"] for a in resultado.artifacts]
    assert "sha256" in resultado.data


def test_pdf_sin_subir_deja_el_nombre_del_archivo_en_data(tmp_path: Path) -> None:
    resultado, portal, presign = _ejecutar(tmp_path, incluir_pdf=True, subir=False)

    # Sin subir el PDF, igual se guardan los CSV con el dato scrapeado.
    assert "ccma_resumen.pdf" not in [a["artifact_id"] for a in resultado.artifacts]
    assert "ccma_resumen.pdf" not in presign.ids
    assert resultado.data["archivo"].endswith(".pdf")
    assert portal.pdf_destino is not None and portal.pdf_destino.exists()


def test_el_pdf_usa_el_nombre_de_v1() -> None:
    from datetime import datetime

    from bot_worker.bots.ccma.plugin import nombre_pdf_ccma

    assert nombre_pdf_ccma(CUIT, ahora=datetime(2026, 10, 8, 9, 30, 0)) == (
        f"ccma_{CUIT}_20261008_093000.pdf"
    )


class _PortalQueFalla(_PortalCcma):
    """La tabla se rompe al paginar: V1 cae a los movimientos visibles."""

    async def estado(self) -> dict[str, Any]:
        raise RuntimeError("pagina caida")


def test_validate_acepta_los_nombres_de_v1() -> None:
    plugin = CcmaPlugin()

    _, entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "cuit_representado": "20-12345678-6",
                "movimientos": True,
                "pdf": True,
                "carga_minio": False,
                "proxy_request": True,
                "cuit_representante": "27123456789",
                "clave": "secreta",
            }
        )
    )

    assert entrada.representado_cuit == CUIT
    assert entrada.incluir_movimientos is True
    assert entrada.incluir_pdf is True
    assert entrada.subir is False
    # V1 usa 04/2000 fijo para el calculo de deuda.
    assert entrada.periodo_desde == "04/2000"


def test_validate_pdf_activa_movimientos_como_v1() -> None:
    _, entrada = asyncio.run(
        CcmaPlugin().validate(
            {"operacion": "consultar", "representado_cuit": CUIT, "pdf": True}
        )
    )

    assert entrada.incluir_pdf is True and entrada.incluir_movimientos is True


def test_validate_rechaza_alias_de_cuit_en_conflicto() -> None:
    with pytest.raises(InvalidInputError):
        asyncio.run(
            CcmaPlugin().validate(
                {
                    "operacion": "consultar",
                    "cuit": "20123456786",
                    "cuit_representado": "20999999999",
                }
            )
        )


def test_fallback_visible_queda_en_data_y_marca_incompleto(tmp_path: Path) -> None:
    resultado, _, _ = _ejecutar(tmp_path, portal=_PortalQueFalla(context=None))

    assert resultado.result == "OK"
    assert resultado.data["movimientos_corte"] == CORTE_FALLBACK
    assert resultado.data["movimientos_completos"] is False
    assert "movimientos" not in resultado.data
    assert [m["periodo"] for m in movimientos_de(tmp_path)] == ["01/2026"]
