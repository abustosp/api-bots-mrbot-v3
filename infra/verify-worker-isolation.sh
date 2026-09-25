#!/usr/bin/env bash
# Verifica el aislamiento real del worker (invariantes W-1, SEC-3 e I-4).
#
# Diseno: plans/06-infra/plan.md secciones 4 y 6. A diferencia de
# verify-packaging.sh, que valida el compose y los Dockerfiles citados en
# el plan, este script inspecciona los archivos reales del repositorio:
# el manifiesto de dependencias del worker, su Dockerfile, su codigo y
# los compose de infra. Falla si la imagen del worker podria traer un
# driver de PostgreSQL o si el worker recibe secretos de la central.
#
# Uso:      ./infra/verify-worker-isolation.sh
# Requiere: bash, grep, python3 con pyyaml
#
# Comprueba:
#   - services/bot-worker/pyproject.toml no declara psycopg, SQLAlchemy
#     ni Alembic, ni dependencias que los traigan por nombre.
#   - services/bot-worker/Dockerfile mantiene la barrera de imports que
#     impide publicar una imagen con driver de base de datos.
#   - services/bot-worker/src no importa modulos de base de datos ni lee
#     DATABASE_URL fuera de comentarios.
#   - infra/compose/*.yml no otorga al worker ningun secreto de la central
#     ni variables de base de datos, pagos, RSA o SMTP.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
FAILED=0

ok() { printf "%s  OK  %s%s\n" "$GREEN" "$1" "$RESET"; }
bad() { printf "%s FALLA %s%s\n" "$RED" "$1" "$RESET"; FAILED=$((FAILED+1)); }

WORKER_PYPROJECT="services/bot-worker/pyproject.toml"
WORKER_DOCKERFILE="services/bot-worker/Dockerfile"
WORKER_SRC="services/bot-worker/src"

FORBIDDEN_DEPS="psycopg|psycopg2|asyncpg|sqlalchemy|alembic"

# 1. Manifiesto de dependencias del worker -------------------------------
if [ ! -f "$WORKER_PYPROJECT" ]; then
    bad "no existe $WORKER_PYPROJECT"
else
    if grep -Eiq "$FORBIDDEN_DEPS" "$WORKER_PYPROJECT"; then
        bad "W-1: $WORKER_PYPROJECT declara driver de base de datos:"
        grep -Ein "$FORBIDDEN_DEPS" "$WORKER_PYPROJECT" | sed 's/^/    /'
    else
        ok "W-1: el manifiesto del worker no declara driver de PostgreSQL"
    fi
fi

# 2. Barrera de imports en el Dockerfile del worker -----------------------
if [ ! -f "$WORKER_DOCKERFILE" ]; then
    bad "no existe $WORKER_DOCKERFILE"
else
    MISSING=0
    for mod in psycopg sqlalchemy alembic; do
        if grep -Fq "! python -c \"import $mod\"" "$WORKER_DOCKERFILE"; then
            :
        else
            bad "W-1: $WORKER_DOCKERFILE perdio la barrera contra import $mod"
            MISSING=1
        fi
    done
    [ "$MISSING" -eq 0 ] && ok "W-1: el Dockerfile del worker bloquea el build con driver de base de datos"
    if grep -Eiq "^\s*RUN.*pip install.*($FORBIDDEN_DEPS)" "$WORKER_DOCKERFILE"; then
        bad "W-1: el Dockerfile del worker instala driver de base de datos"
    else
        ok "W-1: el Dockerfile del worker no instala driver de base de datos"
    fi
fi

# 3. Codigo del worker: sin imports de base de datos ni DSN ---------------
if [ ! -d "$WORKER_SRC" ]; then
    bad "no existe $WORKER_SRC"
else
    HITS="$(grep -rEn --include='*.py' \
        -e '^[[:space:]]*(import|from)[[:space:]]+(psycopg|psycopg2|asyncpg|sqlalchemy|alembic)' \
        "$WORKER_SRC" || true)"
    if [ -n "$HITS" ]; then
        bad "W-1: el codigo del worker importa modulos de base de datos:"
        printf '%s\n' "$HITS" | sed 's/^/    /'
    else
        ok "W-1: el codigo del worker no importa modulos de base de datos"
    fi
    DSN_HITS="$(grep -rEn --include='*.py' 'DATABASE_URL' "$WORKER_SRC" | grep -v '^[^(]*#[^:]*DATABASE_URL' || true)"
    # La segunda pasada admite la mencion documentada en config.py, que solo
    # existe para rechazarla: lo prohibido es leerla como valor efectivo.
    DSN_REAL="$(printf '%s\n' "$DSN_HITS" | grep -Ev 'prohibid|rechaz|nunca|jamas|Forbidden|forbidden' || true)"
    if [ -n "$DSN_REAL" ]; then
        bad "W-1: el codigo del worker menciona DATABASE_URL como valor efectivo:"
        printf '%s\n' "$DSN_REAL" | sed 's/^/    /'
    else
        ok "W-1: el codigo del worker no consume DATABASE_URL"
    fi
fi

# 4. Compose real: el worker no recibe secretos de la central -------------
python3 - <<'PY' || FAILED=$((FAILED+1))
import glob
import pathlib
import sys

try:
    import yaml
except ImportError:
    print("FALLA falta pyyaml (pip install pyyaml)")
    sys.exit(1)

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
fail = []

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
SUSPICIOUS = ("DATABASE", "POSTGRES", "RSA", "SMTP", "MERCADOPAGO")

for path in sorted(glob.glob("infra/compose/*.yml")):
    spec = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    worker = (spec.get("services") or {}).get("bot-worker")
    if worker is None:
        continue
    secrets = {
        (i.get("source") if isinstance(i, dict) else i)
        for i in (worker.get("secrets") or [])
    }
    leaked = secrets & CENTRAL_ONLY
    if leaked:
        fail.append(f"{path}: el worker recibe secretos de la central: {sorted(leaked)}")
        continue
    env = worker.get("environment") or {}
    if not isinstance(env, dict):
        env = dict(e.split("=", 1) for e in env if "=" in e)
    bad_keys = [k for k in env if any(t in k.upper() for t in SUSPICIOUS)]
    # CENTRAL_URL es la unica variable de control esperada ademas del
    # token y la capacidad; el resto ya se valido por nombre prohibido.
    if bad_keys:
        fail.append(f"{path}: el entorno del worker tiene claves prohibidas: {bad_keys}")
        continue
    print(f"{GREEN}  OK  W-1/SEC-3/I-4: {path} no entrega secretos de la central al worker{RESET}")

for f in fail:
    print(f"{RED} FALLA {f}{RESET}")
sys.exit(1 if fail else 0)
PY

echo
if [ "$FAILED" -eq 0 ]; then
    printf "%sEl worker real esta aislado: sin driver PG ni secretos de la central.%s\n" "$GREEN" "$RESET"
    exit 0
fi
printf "%s%s comprobacion(es) de aislamiento fallaron.%s\n" "$RED" "$FAILED" "$RESET"
exit 1
