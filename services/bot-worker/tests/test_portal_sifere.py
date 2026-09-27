"""Pruebas del contrato entre el plugin SIFERE y el catálogo ARCA."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot_worker.bots.sifere.plugin import SERVICIO_ARCA, SiferePlugin


class _ContextoAsync:
    def __init__(self, valor: Any) -> None:
        self.valor = valor

    async def __aenter__(self) -> Any:
        return self.valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ServicioSifere:
    def __init__(self) -> None:
        self.servicio_abierto: tuple[str, str | None] | None = None

    async def login(self) -> None:
        pass

    async def open_service(self, service: str, *, portal: str | None = None) -> Any:
        self.servicio_abierto = (service, portal)
        return self

    async def seleccionar_representado(self, cuit: str) -> None:
        assert cuit == "20123456789"

    async def consultar_jurisdiccion(
        self, codigo: int, periodo: str, destino: Path
    ) -> dict[str, int]:
        destino.write_text("jurisdiccion,importe\n901,0\n", encoding="utf-8")
        return {"filas": 1}


class _BrowserFactory:
    def __init__(self, servicio: _ServicioSifere) -> None:
        self.servicio = servicio

    def arca_session(self, **kwargs: Any) -> _ContextoAsync:
        return _ContextoAsync(self.servicio)


class _Cancelacion:
    async def raise_if_cancelled(self) -> None:
        pass


class _Eventos:
    async def progress(self, **kwargs: Any) -> None:
        pass


def test_execute_abre_el_nombre_visible_de_sifere_en_arca(tmp_path: Path) -> None:
    async def _ejecutar() -> tuple[str, str | None]:
        servicio = _ServicioSifere()
        plugin = SiferePlugin()
        entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "periodo": "202401",
                "jurisdicciones": [901],
                "subir": False,
            }
        )
        runtime = SimpleNamespace(
            credentials=SimpleNamespace(clave="clave-ficticia"),
            cancellation=_Cancelacion(),
            event_sink=_Eventos(),
            deadline=SimpleNamespace(remaining_seconds=lambda: 60),
            browser_factory=_BrowserFactory(servicio),
            proxy=None,
            artifact_store=SimpleNamespace(resolve=lambda name: tmp_path / name),
        )
        resultado = await plugin.execute(entrada, runtime)
        assert resultado.result == "OK"
        assert servicio.servicio_abierto is not None
        return servicio.servicio_abierto

    servicio, portal = asyncio.run(_ejecutar())
    assert servicio == "CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS"
    assert servicio == SERVICIO_ARCA
    assert portal == "sifere"
