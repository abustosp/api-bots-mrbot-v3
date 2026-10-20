#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# build-and-push.sh — V3 (central-api + bot-worker)
#
# Build, tag (con timestamp YYYYMMDD_HHMMSS) y push de las dos imagenes de
# la V3 al registry privado, igual que la V1 y la V2:
#   - $REGISTRY_URL/abustosp/mrbot-central-api
#   - $REGISTRY_URL/abustosp/mrbot-bot-worker
#   - $REGISTRY_URL/abustosp/mrbot-worker-updater (updater de host, opcional)
#
# Uso:      ./build-and-push.sh [TAG]
#           (sin TAG usa la fecha actual YYYYMMDD_HHMMSS)
# Requiere: .env con REGISTRY_URL, REGISTRY_USER, REGISTRY_PASS
#
# El contexto de build es la raiz del monorepo (los Dockerfiles copian
# packages/mrbot-contracts ademas de su propio servicio). Al final imprime
# el digest publicado de cada imagen: es el valor inmutable que espera el
# overlay docker-compose.prod.yml (infra/compose).
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Cargar credenciales ──────────────────────────────────────
if [ ! -f .env ]; then
    echo "[ERROR] No se encuentra .env en $SCRIPT_DIR"
    exit 1
fi

# Cargar solo las variables REGISTRY_* del .env (ignora espacios alrededor de =)
source <(grep '^REGISTRY_' .env | sed 's/ = /=/g')

if [ -z "${REGISTRY_URL:-}" ] || [ -z "${REGISTRY_USER:-}" ] || [ -z "${REGISTRY_PASS:-}" ]; then
    echo "[ERROR] Faltan variables REGISTRY_URL / REGISTRY_USER / REGISTRY_PASS en .env"
    exit 1
fi

# ── Variables ────────────────────────────────────────────────
TAG="${1:-$(date +%Y%m%d_%H%M%S)}"
CENTRAL_REPO="$REGISTRY_URL/abustosp/mrbot-central-api"
WORKER_REPO="$REGISTRY_URL/abustosp/mrbot-bot-worker"
UPDATER_REPO="$REGISTRY_URL/abustosp/mrbot-worker-updater"

echo "=============================================="
echo "  Build & Push — V3 (central-api + bot-worker)"
echo "  Registry:   $REGISTRY_URL"
echo "  Tag:        $TAG"
echo "=============================================="

# ── 1. Login ─────────────────────────────────────────────────
echo ""
echo "[1/6] Login en $REGISTRY_URL..."
echo "$REGISTRY_PASS" | docker login "$REGISTRY_URL" -u "$REGISTRY_USER" --password-stdin

# ── 2. Build central-api ─────────────────────────────────────
echo ""
echo "[2/6] Build de central-api..."
docker build -f services/central-api/Dockerfile \
    -t "$CENTRAL_REPO:$TAG" \
    -t "$CENTRAL_REPO:latest" \
    .

# ── 3. Build bot-worker ──────────────────────────────────────
echo ""
echo "[3/6] Build de bot-worker..."
docker build -f services/bot-worker/Dockerfile \
    -t "$WORKER_REPO:$TAG" \
    -t "$WORKER_REPO:latest" \
    .

# ── 3b. Build worker-updater ─────────────────────────────────
echo ""
echo "[3b/6] Build de worker-updater..."
docker build -f services/worker-updater/Dockerfile \
    -t "$UPDATER_REPO:$TAG" \
    -t "$UPDATER_REPO:latest" \
    .

# ── 4. Push central-api ──────────────────────────────────────
echo ""
echo "[4/6] Push de central-api ($TAG y latest)..."
docker push "$CENTRAL_REPO:$TAG"
docker push "$CENTRAL_REPO:latest"

# ── 5. Push bot-worker ───────────────────────────────────────
echo ""
echo "[5/6] Push de bot-worker ($TAG y latest)..."
docker push "$WORKER_REPO:$TAG"
docker push "$WORKER_REPO:latest"

echo ""
echo "[5b/6] Push de worker-updater ($TAG y latest)..."
docker push "$UPDATER_REPO:$TAG"
docker push "$UPDATER_REPO:latest"

# ── 6. Digests ───────────────────────────────────────────────
echo ""
echo "[6/6] Digests publicados (valor inmutable para el overlay prod):"
CENTRAL_DIGEST="$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$CENTRAL_REPO:$TAG" | grep "@sha256:" | head -1)"
WORKER_DIGEST="$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$WORKER_REPO:$TAG" | grep "@sha256:" | head -1)"
echo "  CENTRAL_IMAGE='$CENTRAL_DIGEST'"
echo "  WORKER_IMAGE='$WORKER_DIGEST'"
echo ""
echo "Para el boton Actualizar del panel, en el .env de la central:"
echo "  WORKER_IMAGE_REF='$WORKER_REPO:$TAG'"
echo "  WORKER_LATEST_VERSION='$TAG'"

# ── Resumen ──────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  ✅ Build & Push V3 completado"
echo "  Tags: latest, $TAG"
echo "=============================================="
