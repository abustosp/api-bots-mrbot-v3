#!/usr/bin/env bash
# Ejecuta todas las verificaciones del repositorio V3.
#
# Uso:      ./infra/verify-all.sh
# Requiere: docker, python3 con pyyaml
#
# Este es el punto de entrada unico. Corre, en orden de costo creciente:
#   1. Coherencia entre documentos (rapido, sin dependencias externas).
#   2. Sintaxis de todos los bloques de codigo de los planes.
#   3. Empaquetado: compose y Dockerfiles contra las herramientas de Docker.
#   4. Esquema: el DDL y la consulta de cola contra PostgreSQL 17 real.
#
# Devuelve 0 solo si todo pasa. Pensado para correr en CI y antes de dar por
# cerrada una edicion de los planes.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; RESET=$'\033[0m'

declare -a NAMES=(
    "coherencia entre documentos"
    "sintaxis de bloques de codigo"
    "empaquetado docker"
    "esquema postgresql"
)
declare -a CMDS=(
    "python3 infra/check-consistency.py"
    "python3 infra/validate-code-blocks.py"
    "./infra/verify-packaging.sh"
    "./infra/postgres/verify-ddl.sh"
)

FAILED=0
declare -a RESULTS=()

for i in "${!CMDS[@]}"; do
    printf "%s\n== %s ==%s\n" "$BOLD" "${NAMES[$i]}" "$RESET"
    if eval "${CMDS[$i]}"; then
        RESULTS+=("OK   ${NAMES[$i]}")
    else
        RESULTS+=("FALLA ${NAMES[$i]}")
        FAILED=$((FAILED+1))
    fi
    echo
done

printf "%s== resumen ==%s\n" "$BOLD" "$RESET"
for line in "${RESULTS[@]}"; do
    if [[ $line == OK* ]]; then
        printf "%s  %s%s\n" "$GREEN" "$line" "$RESET"
    else
        printf "%s  %s%s\n" "$RED" "$line" "$RESET"
    fi
done
echo

if [ "$FAILED" -eq 0 ]; then
    printf "%sTodas las verificaciones pasaron.%s\n" "$GREEN" "$RESET"
    exit 0
fi
printf "%s%s verificacion(es) fallaron.%s\n" "$RED" "$FAILED" "$RESET"
exit 1
