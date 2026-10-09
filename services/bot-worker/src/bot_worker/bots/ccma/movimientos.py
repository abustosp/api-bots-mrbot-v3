"""Recorrido paginado de la cuenta corriente CCMA (port de V1 ``_collect_all_ccma_movimientos``).

V1 no confia en que el clic en ``#mas1`` cambie la tabla: despues de cada clic
compara el estado de la pagina (periodos visibles y ``nextVisible``) con el
anterior. Si no cambia, o si un estado ya fue visto, corta. V3 agrega un
deadline y un tope de paginas para que el job no quede colgado.

El modulo no importa Playwright. La pagina llega como un objeto con cuatro
metodos async (``estado``, ``filas``, ``siguiente_disponible``, ``avanzar``),
asi el recorrido se prueba con fakes sin navegador.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

MAX_PAGINAS = 100
MAX_MOVIMIENTOS = 500
ESPERA_CAMBIO_MS = 8_000

# Codigos de corte; el plugin los traduce a un resultado PARCIAL con motivo.
CORTE_FIN = "fin_paginado"
CORTE_SIN_PROGRESO = "sin_progreso"
CORTE_ESTADO_REPETIDO = "estado_repetido"
CORTE_TOPE_PAGINAS = "tope_paginas"
CORTE_TOPE_MOVIMIENTOS = "tope_movimientos"
CORTE_DEADLINE = "deadline"
#: V1 cae a los movimientos visibles cuando el recorrido falla o no reune nada.
CORTE_FALLBACK = "fallback_visible"


class PaginaCcma(Protocol):
    async def estado(self) -> dict[str, Any]: ...

    async def filas(self) -> list[dict[str, str]]: ...

    async def siguiente_disponible(self) -> bool: ...

    async def avanzar(self) -> bool: ...

    async def esperar_cambio(self, estado_previo: dict[str, Any], timeout_ms: int) -> bool: ...


@dataclass
class ResultadoPaginado:
    movimientos: list[dict[str, str]]
    paginas: int
    corte: str


def _clave_estado(estado: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(estado.get("primerPeriodo", "")),
        str(estado.get("ultimoPeriodo", "")),
        str(estado.get("firstVisiblePeriodo", "")),
        str(estado.get("lastVisiblePeriodo", "")),
    )


def _clave_movimiento(movimiento: dict[str, str]) -> tuple[str, ...]:
    return (
        movimiento.get("periodo", ""),
        movimiento.get("impuesto", ""),
        movimiento.get("concepto", ""),
        movimiento.get("subconcepto", ""),
        movimiento.get("descripcion", ""),
        movimiento.get("fecha_movimiento", ""),
        movimiento.get("debe", ""),
        movimiento.get("haber", ""),
    )


async def recorrer_movimientos(
    pagina: PaginaCcma,
    *,
    restante_segundos: Callable[[], float],
    max_paginas: int = MAX_PAGINAS,
    max_movimientos: int = MAX_MOVIMIENTOS,
    espera_ms: int = ESPERA_CAMBIO_MS,
    al_cargar_pagina: Callable[[int, dict[str, Any], list[dict[str, str]]], None] | None = None,
) -> ResultadoPaginado:
    """Recorre todas las paginas de movimientos y devuelve lo recolectado.

    Corta en cuanto ocurre alguna de estas condiciones, sin lanzar excepcion:

    - ``fin_paginado``: no hay mas paginas visibles.
    - ``sin_progreso``: el clic no cambio el estado de la tabla.
    - ``estado_repetido``: el estado ya se habia visto (ciclo de paginacion).
    - ``tope_paginas`` / ``tope_movimientos``: limite de seguridad alcanzado.
    - ``deadline``: no queda presupuesto de tiempo para otra pagina.

    Un movimiento repetido entre paginas se cuenta una sola vez.
    """
    movimientos: list[dict[str, str]] = []
    vistos: set[tuple[str, ...]] = set()
    estados_vistos: set[tuple[str, str, str, str]] = set()
    paginas = 0
    while True:
        if restante_segundos() <= 0:
            return ResultadoPaginado(movimientos, paginas, CORTE_DEADLINE)
        if paginas >= max_paginas:
            return ResultadoPaginado(movimientos, paginas, CORTE_TOPE_PAGINAS)

        estado = await pagina.estado()
        if not estado.get("tablePresent"):
            return ResultadoPaginado(movimientos, paginas, CORTE_FIN)
        clave = _clave_estado(estado)
        if clave in estados_vistos:
            return ResultadoPaginado(movimientos, paginas, CORTE_ESTADO_REPETIDO)
        estados_vistos.add(clave)

        filas = await pagina.filas()
        paginas += 1
        nuevos: list[dict[str, str]] = []
        for fila in filas:
            k = _clave_movimiento(fila)
            if k in vistos:
                continue
            vistos.add(k)
            movimientos.append(fila)
            nuevos.append(fila)
            if len(movimientos) >= max_movimientos:
                break
        if al_cargar_pagina is not None:
            al_cargar_pagina(paginas, estado, nuevos)
        if len(movimientos) >= max_movimientos:
            return ResultadoPaginado(movimientos[:max_movimientos], paginas, CORTE_TOPE_MOVIMIENTOS)

        if not estado.get("nextVisible") or not await pagina.siguiente_disponible():
            return ResultadoPaginado(movimientos, paginas, CORTE_FIN)
        if restante_segundos() <= 0:
            return ResultadoPaginado(movimientos, paginas, CORTE_DEADLINE)
        if not await pagina.avanzar():
            return ResultadoPaginado(movimientos, paginas, CORTE_SIN_PROGRESO)
        # El cambio se verifica contra el estado previo, no contra el tiempo.
        if not await pagina.esperar_cambio(estado, espera_ms):
            return ResultadoPaginado(movimientos, paginas, CORTE_SIN_PROGRESO)


async def recorrer_movimientos_con_fallback(
    pagina: PaginaCcma,
    *,
    restante_segundos: Callable[[], float],
    max_paginas: int = MAX_PAGINAS,
    max_movimientos: int = MAX_MOVIMIENTOS,
    espera_ms: int = ESPERA_CAMBIO_MS,
) -> ResultadoPaginado:
    """Recorre las paginas con el fallback de V1 ``_collect_ccma_movimientos_with_fallback``.

    V1 primero lee los movimientos visibles de la pagina actual y despues
    intenta el recorrido completo: si el recorrido arroja una excepcion o no
    reune nada, devuelve lo visible. El resultado nunca es un error: la
    consulta sigue siendo OK con lo que se pudo leer.
    """
    try:
        visibles = list(await pagina.filas())
    except Exception:
        visibles = []

    try:
        resultado = await recorrer_movimientos(
            pagina,
            restante_segundos=restante_segundos,
            max_paginas=max_paginas,
            max_movimientos=max_movimientos,
            espera_ms=espera_ms,
        )
    except Exception:
        return ResultadoPaginado(visibles, 0, CORTE_FALLBACK)

    if resultado.movimientos:
        return resultado
    # V1: ``return movimientos or visible_movimientos``.
    if visibles:
        return ResultadoPaginado(visibles, resultado.paginas, CORTE_FALLBACK)
    return resultado
