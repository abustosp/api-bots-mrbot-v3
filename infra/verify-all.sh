#!/usr/bin/env bash
# Verificaciones de los archivos presentes en el repositorio.
# Requiere python3 con pyyaml y Docker Compose; no requiere Docker daemon ni secretos.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

printf '\n== sintaxis de bloques de documentación ==\n'
python3 infra/validate-code-blocks.py
printf '\n== aislamiento del worker ==\n'
./infra/verify-worker-isolation.sh
printf '\n== configuración Compose de desarrollo ==\n'
make compose-config
