"""Paridad central <-> worker: catálogo contra registry (plan 07).

Todo bot del ``CATALOGUE`` de la central existe en el ``REGISTRY`` del
worker y viceversa; cada operacion publica valida contra ``OPERATIONS``
y traduce (via dispatcher) a un verbo del manifiesto del plugin, y cada
verbo del manifiesto es alcanzable desde el catálogo. Sin listas
paralelas: la verdad vive en ``REGISTRY`` + ``CATALOGUE``.
"""
from __future__ import annotations


def test_bots_catalogo_registry_paridad():
    """Los conjuntos de bots coinciden en ambas direcciones."""
    from bot_worker.bots.registry import REGISTRY
    from central_api.api.bots import CATALOGUE

    en_catalogo = [e["bot"] for e in CATALOGUE]
    assert len(en_catalogo) == len(set(en_catalogo)), "bot duplicado en CATALOGUE"
    assert set(en_catalogo) == set(REGISTRY), (
        f"solo catálogo={sorted(set(en_catalogo) - set(REGISTRY))} "
        f"solo registry={sorted(set(REGISTRY) - set(en_catalogo))}"
    )


def test_operaciones_publicas_traducen_a_manifiesto():
    """Cada ``OPERATIONS`` llega a un verbo del manifiesto del plugin."""
    from bot_worker.bots.registry import REGISTRY
    from central_api.api.bots import OPERATIONS
    from central_api.scheduler.dispatcher import worker_operation

    for (bot, operacion) in OPERATIONS:
        assert bot in REGISTRY, f"operación huérfana sin plugin: {bot}"
        verbo = worker_operation(bot, operacion)
        manifiesto = REGISTRY[bot].manifest.operaciones
        assert verbo in manifiesto, (
            f"{bot}/{operacion} traduce a {verbo!r}, "
            f"fuera del manifiesto {list(manifiesto)}"
        )


def test_manifiestos_alcanzables_desde_catalogo():
    """Cada verbo del manifiesto tiene una operacion publica que lo cubre."""
    from bot_worker.bots.registry import REGISTRY
    from central_api.api.bots import OPERATIONS
    from central_api.scheduler.dispatcher import worker_operation

    cubiertos: dict[str, set[str]] = {}
    for (bot, operacion) in OPERATIONS:
        cubiertos.setdefault(bot, set()).add(worker_operation(bot, operacion))
    for nombre, plugin in REGISTRY.items():
        for verbo in plugin.manifest.operaciones:
            assert verbo in cubiertos.get(nombre, set()), (
                f"verbo {nombre}/{verbo} sin operación pública en OPERATIONS"
            )


def test_catalogo_consistente_con_operations_y_costos():
    """``CATALOGUE`` cuadra con ``OPERATIONS`` y los costos con billing."""
    from central_api.api.bots import CATALOGUE, OPERATIONS
    from central_api.billing.entitlements import BOT_CREDIT_COST

    for entrada in CATALOGUE:
        bot = entrada["bot"]
        assert entrada["operaciones"], f"{bot} sin operaciones en CATALOGUE"
        assert len(entrada["operaciones"]) == len(set(entrada["operaciones"]))
        for operacion in entrada["operaciones"]:
            assert (bot, operacion) in OPERATIONS, (
                f"{bot}/{operacion} en CATALOGUE pero no en OPERATIONS"
            )
    for (bot, operacion), definicion in OPERATIONS.items():
        assert definicion["costo_creditos"] == BOT_CREDIT_COST[(bot, operacion)], (
            f"costo divergente en {bot}/{operacion}"
        )
    assert set(OPERATIONS) == set(BOT_CREDIT_COST), (
        f"OPERATIONS={len(OPERATIONS)} BOT_CREDIT_COST={len(BOT_CREDIT_COST)}"
    )
