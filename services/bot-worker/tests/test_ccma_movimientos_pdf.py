"""CCMA: recorrido paginado con corte por no progreso y deadline, y PDF consolidado.

Las pruebas usan una pagina falsa que sigue el protocolo de ``movimientos.py``.
Cada caso fija una condicion de corte de V1 (cambio de estado, estado repetido,
tope) y la de V3 (deadline y tope de paginas). Sin navegador ni red.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from bot_worker.bots.ccma.movimientos import (
    CORTE_DEADLINE,
    CORTE_ESTADO_REPETIDO,
    CORTE_FALLBACK,
    CORTE_FIN,
    CORTE_SIN_PROGRESO,
    CORTE_TOPE_MOVIMIENTOS,
    CORTE_TOPE_PAGINAS,
    recorrer_movimientos,
    recorrer_movimientos_con_fallback,
)
from bot_worker.bots.ccma.pdf import build_ccma_pdf_html

GENERADO = datetime(2026, 10, 8, 9, 30, 0)


def _mov(periodo: str, descripcion: str = "Pago") -> dict[str, str]:
    return {
        "periodo": periodo,
        "impuesto": "37",
        "concepto": "1",
        "subconcepto": "0",
        "descripcion": descripcion,
        "fecha_movimiento": "01/02/2026",
        "debe": "0,00",
        "haber": "100,00",
    }


class _PaginaFalsa:
    """Paginas en orden. ``avanza`` indica si el clic cambia la tabla."""

    def __init__(
        self,
        paginas: list[list[dict[str, str]]],
        *,
        avanza: bool = True,
        siguiente: bool = True,
        clic_ok: bool = True,
        estados: list[dict[str, Any]] | None = None,
    ) -> None:
        self._paginas = paginas
        self._indice = 0
        self._avanza = avanza
        self._siguiente = siguiente
        self._clic_ok = clic_ok
        self._estados = estados
        self.clics = 0
        self.esperas = 0

    def _estado_base(self) -> dict[str, Any]:
        return {
            "tablePresent": True,
            "primerPeriodo": "01/2019",
            "ultimoPeriodo": "12/2025",
            "firstVisiblePeriodo": f"p{self._indice}",
            "lastVisiblePeriodo": f"u{self._indice}",
            "nextVisible": self._siguiente and self._indice < len(self._paginas) - 1,
        }

    async def estado(self) -> dict[str, Any]:
        if self._estados is not None:
            return self._estados[min(self._indice, len(self._estados) - 1)]
        return self._estado_base()

    async def filas(self) -> list[dict[str, str]]:
        return list(self._paginas[min(self._indice, len(self._paginas) - 1)])

    async def siguiente_disponible(self) -> bool:
        return self._siguiente

    async def avanzar(self) -> bool:
        self.clics += 1
        if not self._clic_ok:
            return False
        if self._avanza and self._indice < len(self._paginas) - 1:
            self._indice += 1
        return True

    async def esperar_cambio(self, estado_previo: dict[str, Any], timeout_ms: int) -> bool:
        self.esperas += 1
        return self._avanza


def _correr(pagina: Any, **kwargs: Any) -> Any:
    return asyncio.run(recorrer_movimientos(pagina, restante_segundos=lambda: 60, **kwargs))


def test_recorre_todas_las_paginas_hasta_que_no_hay_siguiente() -> None:
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")], [_mov("03/2026")]])

    resultado = _correr(pagina)

    assert resultado.corte == CORTE_FIN
    assert resultado.paginas == 3
    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026", "02/2026", "03/2026"]


def test_corta_si_el_clic_no_cambia_la_tabla() -> None:
    """Caso central del pedido: #mas1 existe pero la tabla no avanza."""
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]], avanza=False)

    resultado = _correr(pagina)

    assert resultado.corte == CORTE_SIN_PROGRESO
    assert resultado.paginas == 1
    assert pagina.clics == 1
    assert len(resultado.movimientos) == 1


def test_corta_si_el_clic_no_se_puede_hacer() -> None:
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]], clic_ok=False)

    resultado = _correr(pagina)

    assert resultado.corte == CORTE_SIN_PROGRESO
    assert resultado.paginas == 1


def test_corta_cuando_el_estado_vuelve_a_aparecer() -> None:
    """V1 ``states_seen``: un estado repetido indica un ciclo de paginacion."""
    estado_a = {"tablePresent": True, "primerPeriodo": "x", "ultimoPeriodo": "y",
                "firstVisiblePeriodo": "a", "lastVisiblePeriodo": "b", "nextVisible": True}
    estado_b = {**estado_a, "firstVisiblePeriodo": "c"}
    pagina = _PaginaFalsa(
        [[_mov("01/2026")], [_mov("02/2026")], [_mov("03/2026")]],
        estados=[estado_a, estado_b, estado_a],
    )

    resultado = _correr(pagina)

    assert resultado.corte == CORTE_ESTADO_REPETIDO
    assert resultado.paginas == 2


def test_no_duplica_movimientos_repetidos_entre_paginas() -> None:
    repetido = _mov("01/2026", "Mismo")
    pagina = _PaginaFalsa([[repetido, _mov("02/2026")], [repetido, _mov("03/2026")]])

    resultado = _correr(pagina)

    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026", "02/2026", "03/2026"]


def test_respeta_el_tope_de_paginas() -> None:
    pagina = _PaginaFalsa([[_mov(f"0{i}/2026")] for i in range(1, 6)])

    resultado = _correr(pagina, max_paginas=2)

    assert resultado.corte == CORTE_TOPE_PAGINAS
    assert resultado.paginas == 2


def test_respeta_el_tope_de_movimientos_sin_pasar_del_maximo() -> None:
    pagina = _PaginaFalsa([[_mov("01/2026"), _mov("01/2026", "b"), _mov("01/2026", "c")]])

    resultado = _correr(pagina, max_movimientos=2)

    assert resultado.corte == CORTE_TOPE_MOVIMIENTOS
    assert len(resultado.movimientos) == 2


def test_corta_por_deadline_antes_de_paginar() -> None:
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]])

    resultado = asyncio.run(recorrer_movimientos(pagina, restante_segundos=lambda: 0))

    assert resultado.corte == CORTE_DEADLINE
    assert resultado.paginas == 0
    assert pagina.clics == 0


def test_corta_por_deadline_despues_de_leer_la_pagina() -> None:
    """El presupuesto se agota despues de leer la pagina 1: no se hace el clic a la 2.

    Orden de llamadas en ``recorrer_movimientos``: inicio de vuelta 1 (60),
    chequeo previo al clic (0). Debe cortar sin avanzar.
    """
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]])
    restante = iter([60.0, 0.0])

    resultado = asyncio.run(
        recorrer_movimientos(pagina, restante_segundos=lambda: next(restante, 0.0))
    )

    assert resultado.corte == CORTE_DEADLINE
    assert resultado.paginas == 1
    assert pagina.clics == 0
    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026"]


def test_sin_tabla_termina_sin_movimientos() -> None:
    pagina = _PaginaFalsa([[]])
    pagina._estado_base = lambda: {"tablePresent": False}  # type: ignore[method-assign]

    resultado = _correr(pagina)

    assert resultado.movimientos == []
    assert resultado.corte == CORTE_FIN


def test_el_callback_recibe_cada_pagina_con_sus_movimientos_nuevos() -> None:
    vistos: list[tuple[int, int]] = []
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]])

    _correr(pagina, al_cargar_pagina=lambda n, _e, nuevos: vistos.append((n, len(nuevos))))

    assert vistos == [(1, 1), (2, 1)]


def test_pdf_incluye_resumen_movimientos_y_escapa_html() -> None:
    resumen = {
        "cuit": "20123456786",
        "periodo": "04/2000",
        "deuda_capital": "$ 10,00",
        "deuda_accesorios": "$ 2,00",
        "total_deuda": "$ 12,00",
        "credito_capital": "$ 0,00",
        "credito_accesorios": "$ 0,00",
        "total_a_favor": "$ 0,00",
    }
    movimientos = [{**_mov("01/2026", "<script>x</script>")}]

    html = build_ccma_pdf_html(resumen, movimientos, generado=GENERADO)

    assert "CCMA consolidado" in html
    assert "08/10/2026 09:30:00" in html
    assert "Movimientos consolidados: 1" in html
    assert "Total deuda" in html and "$ 12,00" in html
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "<th>Fecha movimiento</th>" in html


def test_pdf_sin_movimientos_muestra_estado_vacio() -> None:
    html = build_ccma_pdf_html({"cuit": "1"}, [], generado=GENERADO)

    assert "No se encontraron movimientos para consolidar." in html
    assert "Movimientos consolidados: 0" in html
    assert "<table class=\"movimientos-table\">" not in html


def test_pdf_reemplaza_campos_vacios_por_guion() -> None:
    html = build_ccma_pdf_html({"cuit": "1"}, [], generado=GENERADO)

    assert "<div class='summary-value'>-</div>" in html


class _PaginaQueFalla(_PaginaFalsa):
    """La lectura del DOM se cae: V1 cae a los movimientos visibles."""

    def __init__(self, visibles: list[dict[str, str]]) -> None:
        super().__init__([visibles])
        self._visibles = visibles

    async def filas(self) -> list[dict[str, str]]:
        return list(self._visibles)

    async def estado(self) -> dict[str, Any]:
        raise RuntimeError("pagina caida")


class _PaginaSinTablaConFilas(_PaginaFalsa):
    """El estado dice que no hay tabla pero las filas visibles existen (V1)."""

    async def estado(self) -> dict[str, Any]:
        return {"tablePresent": False}

    async def filas(self) -> list[dict[str, str]]:
        return [_mov("01/2026")]


class _PaginaSinNada(_PaginaFalsa):
    async def filas(self) -> list[dict[str, str]]:
        raise RuntimeError("sin filas")

    async def estado(self) -> dict[str, Any]:
        raise RuntimeError("sin estado")


def test_fallback_devuelve_los_visibles_si_el_recorrido_falla() -> None:
    pagina = _PaginaQueFalla([_mov("01/2026")])

    resultado = asyncio.run(
        recorrer_movimientos_con_fallback(pagina, restante_segundos=lambda: 60)
    )

    assert resultado.corte == CORTE_FALLBACK
    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026"]


def test_fallback_devuelve_los_visibles_si_el_recorrido_no_reune_nada() -> None:
    pagina = _PaginaSinTablaConFilas([[]], siguiente=False)

    resultado = asyncio.run(
        recorrer_movimientos_con_fallback(pagina, restante_segundos=lambda: 60)
    )

    assert resultado.corte == CORTE_FALLBACK
    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026"]


def test_fallback_no_pisa_el_recorrido_completo() -> None:
    pagina = _PaginaFalsa([[_mov("01/2026")], [_mov("02/2026")]])

    resultado = asyncio.run(
        recorrer_movimientos_con_fallback(pagina, restante_segundos=lambda: 60)
    )

    assert resultado.corte == CORTE_FIN
    assert [m["periodo"] for m in resultado.movimientos] == ["01/2026", "02/2026"]


def test_fallback_sin_visibles_la_excepcion_no_escapa() -> None:
    pagina = _PaginaSinNada([[[]]], siguiente=False)

    resultado = asyncio.run(
        recorrer_movimientos_con_fallback(pagina, restante_segundos=lambda: 60)
    )

    assert resultado.corte == CORTE_FALLBACK
    assert resultado.paginas == 0
    assert resultado.movimientos == []
