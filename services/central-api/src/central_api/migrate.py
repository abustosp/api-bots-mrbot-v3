"""Job one-off de migraciones: ``python -m central_api.migrate``.

Aplica ``alembic upgrade head`` contra la ``DATABASE_URL`` de settings.
Es la única forma soportada de migrar (nunca en el arranque del servicio).
En compose/K8s corre como job efímero con la misma imagen de la central:

.. code-block:: bash

    docker compose -f infra/compose/docker-compose.yml \\
        --profile migrate run --rm migrate
"""

from __future__ import annotations

import sys
from pathlib import Path


def _dir_alembic() -> Path:
    """Localiza el directorio Alembic (``env.py`` + ``versions/``).

    En la imagen viaja en ``/app/alembic`` (ver Dockerfile); en el checkout
    en ``services/central-api/alembic``. Sin él no hay nada que aplicar.
    """
    aqui = Path(__file__).resolve()
    candidatos = [
        Path("/app/alembic"),
        aqui.parents[2] / "alembic",
        Path.cwd() / "alembic",
    ]
    for cand in candidatos:
        if (cand / "env.py").is_file():
            return cand
    raise RuntimeError("directorio alembic no encontrado (env.py ausente)")


def main() -> int:
    """Aplica migraciones pendientes y devuelve el código de salida."""
    raiz = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(raiz / "src"))
    from alembic.config import Config

    from central_api.db import normalizar_dsn
    from central_api.settings import get_settings

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL no configurada", file=sys.stderr)
        return 2
    dir_alembic = _dir_alembic()
    ini = dir_alembic / "alembic.ini"
    cfg = Config(str(ini) if ini.is_file() else None)
    cfg.set_main_option("script_location", str(dir_alembic))
    cfg.set_main_option(
        "sqlalchemy.url", normalizar_dsn(settings.database_url)
    )
    from alembic import command

    command.upgrade(cfg, "head")
    print("migraciones aplicadas (head)")
    _seed_catalog()
    return 0


def _seed_catalog() -> None:
    """Siembra el catálogo ``bots``/``bot_operations`` desde el código.

    Las migraciones crean el esquema pero ningún flujo insertaba el dato, y
    sin esas filas la FK ``fk_jobs_operation`` rechaza cada job (503). Es
    idempotente (upsert) y usa los mismos costos del catálogo público.
    """
    import asyncio

    from central_api.api.bots import catalog_seed_rows
    from central_api.db import nueva_sesion
    from central_api.repositories.catalog import CatalogRepository

    async def _aplicar() -> tuple[int, int]:
        filas = catalog_seed_rows()
        bots = 0
        operaciones = 0
        vistos: set[str] = set()
        async with nueva_sesion() as sesion:
            repo = CatalogRepository(sesion)  # type: ignore[arg-type]
            for fila in filas:
                if fila["bot"] not in vistos:
                    await repo.upsert_bot(
                        code=fila["bot"], display_name=fila["display_name"]
                    )
                    vistos.add(fila["bot"])
                    bots += 1
                await repo.upsert_operation(
                    bot_code=fila["bot"],
                    code=fila["operation"],
                    input_schema_version="1",
                    unit_cost=fila["unit_cost"],
                    effect_class=fila["effect_class"],
                )
                operaciones += 1
        return bots, operaciones

    bots, operaciones = asyncio.run(_aplicar())
    print(f"catálogo sembrado: {bots} bots, {operaciones} operaciones")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
