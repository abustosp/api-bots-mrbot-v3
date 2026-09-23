#!/usr/bin/env python3
"""Extrae la consulta de claim de la cola desde plans/01-database/plan.md.

El script de verificacion no lleva una copia a mano de la consulta: la lee del
plan. Asi, si alguien edita el plan y rompe la semantica del claim, la
verificacion lo detecta en vez de seguir probando una version obsoleta.

Uso: _extract_claim.py <plan.md> <salida.sql>
"""
import pathlib
import re
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    plan = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    src = plan.read_text()

    for block in re.findall(r"```sql\n(.*?)```", src, re.S):
        if "SKIP LOCKED" in block and "reserved_worker" in block:
            # El plan parametriza el worker por :worker_id. El test lo resuelve
            # por nombre, que es estable entre corridas.
            query = block.replace("WHERE id = :worker_id", "WHERE name = :'wname'")
            query = query.replace(":worker_app_version", "'v3.0.0'")
            query = query.replace(":protocol_version", "'1'")
            out.write_text(query)
            return 0

    print(
        "no se encontro un bloque sql con SKIP LOCKED y reserved_worker en "
        f"{plan}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
