#!/usr/bin/env bash
# Valida el empaquetado descrito en plans/06-infra/plan.md contra las
# herramientas reales de Docker, y comprueba que las invariantes de aislamiento
# se cumplan en el artefacto, no solo en la prosa.
#
# Uso:      ./infra/verify-packaging.sh
# Requiere: docker, python3 con pyyaml
#
# Comprueba:
#   - El docker-compose del plan pasa `docker compose config`.
#   - Los ajustes criticos de Playwright sobreviven la resolucion.
#   - W-1 y SEC-3: el worker no recibe ningun secreto de la central.
#   - W-3: el worker no publica puertos ni esta en la red de borde.
#   - W-2: la capacidad declarada es 5.
#   - Los Dockerfiles declaran usuario no root y la barrera de import de DB.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_PLAN="$REPO_ROOT/plans/06-infra/plan.md"
WORKER_PLAN="$REPO_ROOT/plans/03-worker/plan.md"
SANDBOX="$(mktemp -d)"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
FAILED=0

ok()   { printf "%s  OK  %s%s\n" "$GREEN" "$1" "$RESET"; }
bad()  { printf "%s FALLA %s%s\n" "$RED" "$1" "$RESET"; FAILED=$((FAILED+1)); }
info() { printf "%s  ..  %s%s\n" "$YELLOW" "$1" "$RESET"; }

cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { bad "docker no esta disponible"; exit 1; }
python3 -c "import yaml" 2>/dev/null || { bad "falta pyyaml (pip install pyyaml)"; exit 1; }

# ------------------------------------------------ extraer compose del plan
info "Extrayendo docker-compose de $(basename "$INFRA_PLAN")"
python3 - "$INFRA_PLAN" "$SANDBOX/docker-compose.yml" <<'PY'
import pathlib, re, sys
src = pathlib.Path(sys.argv[1]).read_text()
for _, body in re.findall(r"```(yaml)\n(.*?)```", src, re.S):
    if "services:" in body and "bot-worker" in body:
        pathlib.Path(sys.argv[2]).write_text(body)
        break
else:
    sys.exit("no se encontro un bloque yaml de compose con bot-worker")
PY

# Marcadores de posicion para secretos y env declarados por el plan. Se generan
# a partir del propio compose para no mantener una lista paralela.
cd "$SANDBOX"
mkdir -p env secrets
python3 - <<'PY'
import pathlib, yaml
spec = yaml.safe_load(pathlib.Path("docker-compose.yml").read_text())
for name, body in (spec.get("secrets") or {}).items():
    target = body.get("file") if isinstance(body, dict) else None
    if target:
        p = pathlib.Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("placeholder\n")
# `env_file` admite forma escalar y forma de lista. Se recorre el spec
# parseado en vez de adivinar con una expresion regular.
for service in (spec.get("services") or {}).values():
    if not isinstance(service, dict):
        continue
    ef = service.get("env_file")
    if ef is None:
        continue
    entries = [ef] if isinstance(ef, str) else ef
    for entry in entries:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path:
            continue
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n")
PY

# ------------------------------------------------- 1. compose config valido
export CENTRAL_IMAGE="central-api:verify" WORKER_IMAGE="bot-worker:verify"
if docker compose config >resolved.yml 2>err.txt; then
    ok "docker compose config acepta el compose del plan"
else
    bad "docker compose config rechaza el compose del plan"
    sed 's/^/    /' err.txt
    exit 1
fi

# --------------------------------- 2..6 invariantes sobre el config resuelto
python3 - <<'PY' || exit 1
import sys, yaml

spec = yaml.safe_load(open("resolved.yml"))
svc = spec["services"]
fail = []
GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"


def ok(msg):
    print(f"{GREEN}  OK  {msg}{RESET}")


def bad(msg):
    print(f"{RED} FALLA {msg}{RESET}")
    fail.append(msg)


def secret_names(s):
    out = set()
    for item in s.get("secrets") or []:
        out.add(item.get("source") if isinstance(item, dict) else item)
    return out


def net_names(s):
    n = s.get("networks")
    return set(n.keys()) if isinstance(n, dict) else set(n or [])


for required in ("central-api", "bot-worker", "postgres"):
    if required not in svc:
        bad(f"falta el servicio {required!r} en el compose")
if fail:
    sys.exit(1)

worker, central, pg = svc["bot-worker"], svc["central-api"], svc["postgres"]

# --- Playwright: sin esto Chromium se cae bajo concurrencia -------------
limits = worker.get("deploy", {}).get("resources", {}).get("limits", {})
checks = {
    "init": worker.get("init") is True,
    "shm_size": bool(worker.get("shm_size")),
    "/dev/shm en tmpfs": any("/dev/shm" in t for t in (worker.get("tmpfs") or [])),
    "stop_grace_period": bool(worker.get("stop_grace_period")),
    "limite de pids": bool(limits.get("pids")),
    "limite de memoria": bool(limits.get("memory")),
}
missing = [k for k, v in checks.items() if not v]
if missing:
    bad(f"el worker no fija ajustes criticos de Playwright: {missing}")
else:
    shm_gib = int(worker["shm_size"]) / 1024**3
    mem_gib = int(limits["memory"]) / 1024**3
    ok(
        "ajustes de Playwright presentes: init, "
        f"/dev/shm {shm_gib:.0f} GiB, memoria {mem_gib:.0f} GiB, "
        f"pids {limits['pids']}, drenaje {worker['stop_grace_period']}"
    )

# --- W-2: capacidad 5 ---------------------------------------------------
# El worker no tiene variables de entorno: la concurrencia entra por CLI
# (`command: [... --concurrency 5]`), con tope duro 1..5 en el código.
env = worker.get("environment") or {}
if not isinstance(env, dict):
    env = dict(e.split("=", 1) for e in env if "=" in e)
cmd = worker.get("command") or []
if isinstance(cmd, str):
    cmd = cmd.split()
cap = None
for i, token in enumerate(cmd):
    if token == "--concurrency" and i + 1 < len(cmd):
        cap = cmd[i + 1]
if env:
    bad(f"W-2: el worker no debe tener entorno (tiene {sorted(env)})")
elif str(cap) == "5":
    ok("W-2: el worker fija --concurrency 5 por CLI, sin entorno")
elif cap is None:
    bad("W-2: el worker no fija --concurrency en command:")
else:
    bad(f"W-2: --concurrency={cap}, deberia ser 5")

# --- W-1 y SEC-3: aislamiento de secretos -------------------------------
CENTRAL_ONLY = {
    "database_url",
    "postgres_app_password",
    "rsa_private_key",
    "smtp_password",
    "mercadopago_access_token",
    "minio_central_credentials",
    "session_signing_key",
    "api_key_hmac_secret",
}
leaked = secret_names(worker) & CENTRAL_ONLY
if leaked:
    bad(f"W-1/SEC-3: el worker recibe secretos de la central: {sorted(leaked)}")
else:
    ok(
        "W-1/SEC-3: el worker no recibe ningun secreto de la central "
        f"(tiene {len(secret_names(worker))} propios)"
    )

suspicious = [
    k
    for k in env
    if any(t in k.upper() for t in ("DATABASE", "POSTGRES", "RSA", "SMTP", "MERCADOPAGO"))
]
if suspicious:
    bad(f"W-1: el entorno del worker tiene claves prohibidas: {suspicious}")
else:
    ok("W-1: el entorno del worker no menciona base de datos ni pagos")

# --- W-3: el worker no es alcanzable desde afuera -----------------------
if worker.get("ports"):
    bad(f"W-3: el worker publica puertos: {worker['ports']}")
else:
    ok("W-3: el worker no publica ningun puerto al host")

wnets, cnets = net_names(worker), net_names(central)
edge = {n for n in cnets if "edge" in n or "borde" in n}
if edge & wnets:
    bad(f"W-3: el worker esta en la red de borde {sorted(edge & wnets)}")
elif not wnets:
    bad("W-3: el worker no declara red, quedaria en la red por defecto")
else:
    ok(f"W-3: el worker solo esta en {sorted(wnets)}, sin la red de borde")

# --- I-4: solo la central llega a Postgres ------------------------------
if pg.get("ports"):
    bad(f"I-4: postgres publica puertos al host: {pg['ports']}")
else:
    ok("I-4: postgres no publica puertos al host")

if fail:
    sys.exit(1)
PY

# --------------------------------------------- 7. Dockerfiles del plan
info "Verificando los Dockerfiles del plan"
python3 - "$INFRA_PLAN" <<'PY' || FAILED=$((FAILED+1))
import pathlib, re, sys

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
src = pathlib.Path(sys.argv[1]).read_text()
blocks = [b for _, b in re.findall(r"```(dockerfile)\n(.*?)```", src, re.S)]
full = [b for b in blocks if "FROM" in b and "CMD" in b]
fail = []

if len(full) < 2:
    print(f"{RED} FALLA se esperaban 2 Dockerfiles completos, hay {len(full)}{RESET}")
    sys.exit(1)

for body in full:
    is_worker = "bot-worker" in body or "PLAYWRIGHT_IMAGE" in body
    name = "bot-worker" if is_worker else "central-api"

    if "USER mrbot" not in body:
        fail.append(f"{name}: no cambia a usuario no root")

    if is_worker:
        # La barrera de W-1 debe estar en la propia imagen.
        if not all(f"! python -c \"import {m}\"" in body for m in ("psycopg", "sqlalchemy")):
            fail.append("bot-worker: la imagen no verifica la ausencia de driver de DB")
        for forbidden in ("psycopg", "sqlalchemy", "alembic"):
            if re.search(rf"^\s*(RUN\s+)?pip install.*\b{forbidden}\b", body, re.M):
                fail.append(f"bot-worker: instala {forbidden}, viola W-1")
    else:
        if "playwright" in body.lower():
            fail.append("central-api: referencia Playwright, no deberia")

for f in fail:
    print(f"{RED} FALLA {f}{RESET}")
if not fail:
    print(f"{GREEN}  OK  Dockerfiles: usuario no root, y el worker verifica en build que no tiene driver de DB{RESET}")
sys.exit(1 if fail else 0)
PY

# --------------------------------------------------------------- resultado
echo
if [ "$FAILED" -eq 0 ]; then
    printf "%sEl empaquetado del plan es valido y respeta las invariantes de aislamiento.%s\n" "$GREEN" "$RESET"
    exit 0
fi
printf "%s%s comprobacion(es) de empaquetado fallaron.%s\n" "$RED" "$FAILED" "$RESET"
exit 1
