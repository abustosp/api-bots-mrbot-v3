#!/usr/bin/env bash
# Despliegue blue/green de la central en un host con nginx-v2.
# Uso: CENTRAL_IMAGE=<imagen> CENTRAL_DOMAIN=<host> ./bluegreen.sh
# Primer corte desde el contenedor del stack (sin colores): LEGACY_CONTAINER=<nombre>
# (p. ej. mrbot-v3-central-api-1); se detiene cuando el color nuevo esta healthy.
# Pasos: migrar (one-off) -> levantar color inactivo -> esperar healthy ->
# drenar color activo (healthcheck falla) -> pararlo. Si el nuevo no queda
# healthy, se baja y el activo sigue sirviendo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$HERE/../compose/central.bluegreen.compose.yaml}"
ENV_FILE="${ENV_FILE:-$(dirname "$COMPOSE_FILE")/.env}"
STACK="${STACK:-mrbot-v3}"   # proyecto compose que posee Postgres y las redes
LEGACY_CONTAINER="${LEGACY_CONTAINER:-}"
WAIT_SECONDS="${WAIT_SECONDS:-180}"
DRAIN_SECONDS="${DRAIN_SECONDS:-20}"   # margen para que nginx-v2 saque al drenado
: "${CENTRAL_IMAGE:?definir CENTRAL_IMAGE}"
: "${CENTRAL_DOMAIN:?definir CENTRAL_DOMAIN}"
export CENTRAL_IMAGE CENTRAL_DOMAIN STACK

dc() { docker compose -p "mrbot-central-$1" -f "$COMPOSE_FILE" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"} --env-file "$ENV_FILE" "${@:2}"; }
cid() { dc "$1" ps -q central-api 2>/dev/null | head -1; }
state() { local c; c=$(cid "$1"); [ -n "$c" ] && docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo none; }

ACTIVE=none
for c in blue green; do [ "$(state "$c")" = healthy ] && ACTIVE=$c; done
NEW=blue; [ "$ACTIVE" = blue ] && NEW=green
echo "activo=$ACTIVE nuevo=$NEW imagen=$CENTRAL_IMAGE"

docker network inspect "${STACK}_control" >/dev/null
echo "== migrate"
dc "$NEW" --profile migrate run --rm migrate

echo "== up $NEW"
dc "$NEW" up -d --force-recreate central-api
end=$((SECONDS + WAIT_SECONDS))
until [ "$(state "$NEW")" = healthy ]; do
  if [ $SECONDS -ge $end ]; then
    echo "ERROR: $NEW no quedo healthy; se baja y $ACTIVE sigue activo" >&2
    dc "$NEW" logs --tail 30 central-api >&2 || true
    dc "$NEW" down
    exit 1
  fi
  sleep 3
done

if [ "$ACTIVE" = none ] && [ -n "$LEGACY_CONTAINER" ]; then
  echo "== detener $LEGACY_CONTAINER"
  docker stop -t 60 "$LEGACY_CONTAINER"
fi
if [ "$ACTIVE" != none ]; then
  echo "== drenar $ACTIVE"
  docker exec "$(cid "$ACTIVE")" touch /tmp/drain
  until [ "$(state "$ACTIVE")" = unhealthy ]; do sleep 2; done
  sleep "$DRAIN_SECONDS"
  dc "$ACTIVE" down
fi
echo "OK: $NEW activo"
