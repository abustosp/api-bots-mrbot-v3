#!/usr/bin/env bash
# Funciones comunes de los build-and-push-{worker,central,both}.sh (se hace source).
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -f .env ] || { echo "[ERROR] No se encuentra .env"; exit 1; }
source <(grep '^REGISTRY_' .env | sed 's/ = /=/g')
: "${REGISTRY_URL:?falta REGISTRY_URL en .env}" "${REGISTRY_USER:?}" "${REGISTRY_PASS:?}"
TAG="${1:-$(date +%Y%m%d_%H%M%S)}"

echo "$REGISTRY_PASS" | docker login "$REGISTRY_URL" -u "$REGISTRY_USER" --password-stdin

# bp <servicio> <repo>: build, push de $TAG y latest, imprime digest
bp() {
    local repo="$REGISTRY_URL/abustosp/$2"
    docker build -f "services/$1/Dockerfile" -t "$repo:$TAG" -t "$repo:latest" .
    docker push "$repo:$TAG"
    docker push "$repo:latest"
    echo "[$1] $TAG -> $(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$repo:$TAG" | grep @sha256: | head -1)"
}
bp_worker()  { bp bot-worker mrbot-bot-worker; }
bp_central() { bp central-api mrbot-central-api; }
