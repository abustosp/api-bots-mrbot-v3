#!/usr/bin/env bash
# Prueba mensual de restauracion del respaldo de PostgreSQL 17.
#
# Diseno: plans/06-infra/plan.md seccion 9.4. Un respaldo sin prueba de
# restauracion no es un respaldo: este script levanta un postgres:17
# efimero, restaura el .dump indicado, verifica el checksum previo,
# comprueba que las tablas canonicas existen y reporta la duracion.
# No toca la base de produccion en ningun caso.
#
# Uso:
#   ./infra/postgres/backup/restore-test.sh backups/mrbot-20260919T020000Z.dump
#
# Requiere: docker.

set -euo pipefail

[ $# -eq 1 ] || { echo "uso: restore-test.sh <archivo.dump>" >&2; exit 1; }
DUMP="$1"
[ -f "$DUMP" ] || { echo "ERROR: no existe $DUMP" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker no esta disponible" >&2; exit 1; }

if [ -f "$DUMP.sha256" ]; then
    sha256sum -c "$DUMP.sha256" || exit 1
else
    echo "AVISO: sin archivo .sha256, se calcula solo como referencia"
    sha256sum "$DUMP"
fi

NAME="mrbot-restore-test-$$"
START="$(date +%s)"
cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" \
    -e POSTGRES_DB=mrbot -e POSTGRES_USER=mrbot_app \
    -e POSTGRES_PASSWORD=restore-test-temporal \
    postgres:17.6-bookworm >/dev/null

for _ in $(seq 1 30); do
    docker exec "$NAME" pg_isready -U mrbot_app -d mrbot >/dev/null 2>&1 && break
    sleep 2
done

docker cp "$DUMP" "$NAME:/tmp/restore.dump"
docker exec "$NAME" pg_restore --no-owner --dbname=mrbot \
    -U mrbot_app /tmp/restore.dump

TABLES="$(docker exec "$NAME" psql -U mrbot_app -d mrbot -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
echo "tablas en public tras restaurar: $TABLES"

for t in users jobs workers plans subscriptions; do
    n="$(docker exec "$NAME" psql -U mrbot_app -d mrbot -tAc \
        "SELECT count(*) FROM $t" 2>&1)" || { echo "FALLA tabla $t: $n"; exit 1; }
    echo "tabla $t: $n filas"
done

END="$(date +%s)"
echo "OK restauracion en $((END - START)) segundos (RTO objetivo: 4 horas)"
