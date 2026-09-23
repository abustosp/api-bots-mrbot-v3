#!/usr/bin/env python3
"""Detecta incoherencias entre los planes de la V3.

Un conjunto de nueve planes escritos por separado deriva: cada documento
inventa su propio nombre para el mismo estado o la misma ruta. Esa deriva es
silenciosa y llega hasta la implementacion. Este script la convierte en un
fallo de CI.

Comprueba:
  1. Vocabulario de estados de job y de worker contra el canon de plan.md.
  2. Que cada ruta interna aparezca con una sola forma (sin variantes de
     placeholder ni sinonimos de una misma operacion).
  3. Que las rutas publicas /api/v3 no tengan variantes ingles/castellano.
  4. Que las tablas declaradas en plan.md existan en el plan de base de datos.
  5. Que los enlaces relativos entre documentos resuelvan.
  6. Que no haya em dashes (convencion de estilo del proyecto).

Uso: check-consistency.py [raiz]
Salida: 0 si todo es coherente, 1 si hay incoherencias.
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

SKIP_DIRS = {".research", ".git"}

# --- canon ------------------------------------------------------------------
JOB_STATES = {"PENDIENTE", "ASIGNADO", "CORRIENDO", "COMPLETO", "FALLIDO", "CANCELADO"}
JOB_RESULTS = {"OK", "PARCIAL", "ERROR"}
WORKER_STATES = {
    "REGISTRANDO",
    "SANO",
    "SATURADO",
    "DEGRADADO",
    "CAIDO",
    "DRENANDO",
    "RETIRADO",
}

# Vocabulario que algun documento uso en el pasado y que no debe reaparecer.
# Mapea termino prohibido -> termino canonico.
BANNED_STATES = {
    "DRAINING": "DRENANDO",
    "OFFLINE": "CAIDO",
    "NO_DISPONIBLE": "CAIDO",
    "REGISTRADO": "REGISTRANDO",
    "PENDING": "PENDIENTE",
    "RUNNING": "CORRIENDO",
    "ASSIGNED": "ASIGNADO",
    "COMPLETED": "COMPLETO",
    "FAILED": "FALLIDO",
    "CANCELLED": "CANCELADO",
    "CANCELED": "CANCELADO",
    "HEALTHY": "SANO",
}

# Enumeraciones distintas del dominio de estados, documentadas como tales.
# `execution_state` es la fase tecnica interna del worker y no se expone al
# cliente; los codigos de error son otra taxonomia. Sus valores coinciden
# lexicamente con estados prohibidos, asi que se excluyen de forma explicita
# y acotada en vez de relajar la regla general.
ALLOWED_FOREIGN_ENUMS = {
    "ACCEPTED",
    "RUNNING",
    "CANCELLING",
    "UPLOADING",
}

# Contextos en los que un termino en ingles es legitimo: pertenece a otra
# enumeracion, es un codigo de error, o es la propia tabla que contrasta ambos
# vocabularios.
FOREIGN_CONTEXT = re.compile(
    r"execution_state|_CRASHED|_OOM|_FAILED|error_code|reason_code|"
    r"no es .?jobs\.status|jobs\.status|OOMKilled|"
    # La fila de una tabla que contrapone ambos vocabularios cita los dos.
    r"CANCELLING|categor[ií]as de error",
    re.I,
)

# Un documento puede nombrar un termino prohibido para decir que NO se use.
# Esa mencion es documentacion, no deriva. Se reconoce por la construccion
# "y no `X`" o "en vez de `X`", que es como se redacta esa aclaracion.
REJECTS_TERM = re.compile(r"\by no\b|\ben vez de\b|\bno usar\b|\bnunca\b", re.I)

# Rutas publicas: forma canonica por operacion. Detecta sinonimos.
PUBLIC_CANON = {
    "cancelar": "/api/v3/jobs/{job_id}/cancelar",
    "lote": "/api/v3/jobs/estado:lote",
}
PUBLIC_BANNED = {
    "/api/v3/jobs/{job_id}/cancel": "/api/v3/jobs/{job_id}/cancelar",
    "/api/v3/jobs/status:batch": "/api/v3/jobs/estado:lote",
    "/api/v3/history": "/api/v3/jobs",
}

# Rutas de salud. La central usa /health y /ready; el worker, por tener toda su
# superficie privada, usa /internal/v1/health y /internal/v1/status. Las
# variantes con z son de convencion de Kubernetes y no se usan en este
# proyecto, para que el Dockerfile y el plan de API no se contradigan.
HEALTH_BANNED = {
    "/healthz": "/health en la central, /internal/v1/health en el worker",
    "/readyz": "/ready en la central, /internal/v1/status en el worker",
    "/livez": "/health",
    "/live": "/health",
}

# Rutas internas: variantes de placeholder que deben unificarse.
INTERNAL_BANNED = {
    "/internal/v1/workers/{id}/heartbeat": "/internal/v1/workers/{worker_id}/heartbeat",
    "/internal/v1/jobs/{id}/result": "/internal/v1/jobs/{job_id}/result",
    "/internal/v1/jobs/{id}/events": "/internal/v1/jobs/{job_id}/events",
    "/internal/v1/uploads/presign": "/internal/v1/jobs/{job_id}/artifacts/presign",
    "/internal/v1/jobs/{job_id}/uploads": "/internal/v1/jobs/{job_id}/artifacts/presign",
}

V3_TABLES = [
    "users",
    "api_keys",
    "admin_users",
    "bots",
    "bot_operations",
    "jobs",
    "job_events",
    "job_results",
    "job_artifacts",
    "workers",
    "worker_heartbeats",
    "plans",
    "subscriptions",
    "subscription_periods",
    "usage_ledger",
    "credit_ledger",
    "payments",
    "payment_events",
    "audit_log",
]


def docs(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        p
        for p in sorted(root.rglob("*.md"))
        if not any(part in SKIP_DIRS for part in p.parts)
    ]


def line_of(src: str, pos: int) -> int:
    return src[:pos].count("\n") + 1


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    problems: list[str] = []
    files = docs(root)
    if not files:
        print(f"no se encontraron documentos bajo {root}")
        return 1

    # ---- 1. vocabulario de estados -------------------------------------
    for path in files:
        src = path.read_text()
        lines = src.splitlines()
        for banned, canon in BANNED_STATES.items():
            # Solo en mayusculas y como palabra completa, para no marcar prosa.
            for m in re.finditer(rf"\b{banned}\b", src):
                lineno = line_of(src, m.start())
                line = lines[lineno - 1] if lineno <= len(lines) else ""
                # Una linea que habla de otra enumeracion no es deriva.
                if banned in ALLOWED_FOREIGN_ENUMS and FOREIGN_CONTEXT.search(line):
                    continue
                # Nombrar el termino para prohibirlo tampoco es deriva.
                if REJECTS_TERM.search(line):
                    continue
                problems.append(
                    f"{path}:{lineno} estado no canonico "
                    f"{banned!r}, usar {canon!r}"
                )

    # ---- 2 y 3. rutas --------------------------------------------------
    for path in files:
        src = path.read_text()
        lines = src.splitlines()
        for banned, canon in {
            **PUBLIC_BANNED,
            **INTERNAL_BANNED,
            **HEALTH_BANNED,
        }.items():
            idx = 0
            while True:
                idx = src.find(banned, idx)
                if idx < 0:
                    break
                # No marcar si es parte de una ruta mas larga y valida.
                tail = src[idx + len(banned) : idx + len(banned) + 1]
                if tail not in {"", " ", "`", "|", ")", ",", ".", "\n", '"', "'"}:
                    idx += len(banned)
                    continue
                lineno = line_of(src, idx)
                line = lines[lineno - 1] if lineno <= len(lines) else ""
                # Una linea que nombra la ruta para prohibirla, o que documenta
                # un defecto ya corregido, no es deriva. Se reconoce por la
                # construccion "X vs Y" o "en vez de X".
                if REJECTS_TERM.search(line) or " vs " in line:
                    idx += len(banned)
                    continue
                problems.append(
                    f"{path}:{lineno} ruta no canonica "
                    f"{banned!r}, usar {canon!r}"
                )
                idx += len(banned)

    # ---- 4. tablas del plan maestro presentes en el plan de DB ---------
    db_plan = root / "plans" / "01-database" / "plan.md"
    if db_plan.exists():
        db_src = db_plan.read_text()
        for table in V3_TABLES:
            if not re.search(rf"\b{table}\b", db_src):
                problems.append(
                    f"{db_plan}: la tabla {table!r} del plan maestro no aparece"
                )
    else:
        problems.append("falta plans/01-database/plan.md")

    # ---- 5. enlaces relativos ------------------------------------------
    # Las cifras de lineas citadas en plan.md envejecen en silencio con cada
    # edicion. Se verifican contra el contenido real.
    master = root / "plan.md"
    if master.exists():
        msrc = master.read_text()

        # Tabla del indice: | [`plans/x/plan.md`](...) | descripcion | N |
        row = re.compile(
            r"\[`(plans/[\w-]+/plan\.md)`\]\([^)]+\)[^|]*\|[^|]*\| (\d+) \|"
        )
        for m in row.finditer(msrc):
            target = root / m.group(1)
            if not target.exists():
                problems.append(f"{master}: el indice cita {m.group(1)!r}, que no existe")
                continue
            real = len(target.read_text().splitlines())
            if int(m.group(2)) != real:
                problems.append(
                    f"{master}:{line_of(msrc, m.start())} el indice dice "
                    f"{m.group(2)} lineas para {m.group(1)}, tiene {real}"
                )

        # Totales agregados, escritos con punto como separador de millares.
        def total(pattern: str) -> int:
            return sum(
                len(f.read_text().splitlines()) for f in sorted(root.glob(pattern))
            )

        for pattern, label in (
            ("plans/*/plan.md", "planes"),
            (".research/*.md", "investigacion"),
        ):
            real = total(pattern)
            if real == 0:
                continue
            pretty = f"{real:,}".replace(",", ".")
            cited = re.search(
                rf"(?:Total de los {label}|Investigación de base)[^\d]*([\d.]+) líneas",
                msrc,
            )
            if cited and cited.group(1) != pretty:
                problems.append(
                    f"{master}: cita {cited.group(1)} lineas de {label}, "
                    f"el valor real es {pretty}"
                )

    for path in files:
        src = path.read_text()
        base = path.parent
        for m in re.finditer(r"\]\(([^)#][^)]*)\)", src):
            target = m.group(1).split("#")[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (base / target).exists():
                problems.append(
                    f"{path}:{line_of(src, m.start())} enlace roto -> {target}"
                )

    # ---- 6. estilo ------------------------------------------------------
    for path in files:
        src = path.read_text()
        for m in re.finditer("\u2014", src):
            problems.append(
                f"{path}:{line_of(src, m.start())} em dash, usar dos puntos o coma"
            )

    # ---- informe --------------------------------------------------------
    for p in problems:
        print(f"FALLA {p}")

    print()
    print(f"documentos analizados: {len(files)}")
    print(f"estados canonicos: job={len(JOB_STATES)} worker={len(WORKER_STATES)} result={len(JOB_RESULTS)}")
    if problems:
        by_kind: collections.Counter[str] = collections.Counter()
        for p in problems:
            if "estado no canonico" in p:
                by_kind["estados"] += 1
            elif "ruta no canonica" in p:
                by_kind["rutas"] += 1
            elif "enlace roto" in p:
                by_kind["enlaces"] += 1
            elif "em dash" in p:
                by_kind["estilo"] += 1
            else:
                by_kind["otros"] += 1
        print(f"incoherencias: {len(problems)} ({dict(by_kind)})")
        return 1
    print("sin incoherencias entre documentos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
