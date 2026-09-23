#!/usr/bin/env bash
# Respaldo de PostgreSQL 17 para la V3.
#
# Diseno: plans/06-infra/plan.md seccion 9.4.
# Politica: pg_dump con formato custom, diario a las 02:00 UTC y previo a
# toda migracion, destino en object storage separado y cifrado, retencion
# de 30 diarios mas 12 mensuales mas 4 anuales, checksum SHA-256 por objeto.
# El respaldo no incluye secretos de runtime.
#
# Uso:
#   ./infra/postgres/backup/backup.sh [destino]
#   DATABASE_URL=postgresql://... ./infra/postgres/backup/backup.sh /var/backups/mrbot
#
# El DSN se toma de DATABASE_URL, o del secreto DATABASE_URL_FILE cuando
# existe (compose monta /run/secrets/database_url). La subida al object
# storage la hace el operador con mc o aws tras verificar el checksum.
#
# Uso previo a una migracion (job one-off ya publicado por digest):
#   docker compose -f infra/compose/docker-compose.yml \
#     --profile migrate run --rm migrate
#   # solo si el backup de este script termino en OK:
#   alembic upgrade head   # dentro del job migrate, nunca en el CMD

set -euo pipefail

DEST="${1:-${BACKUP_DEST:-./backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"

resolve_dsn() {
    if [ -n "${DATABASE_URL:-}" ]; then
        printf '%s' "$DATABASE_URL"
    elif [ -n "${DATABASE_URL_FILE:-}" ] && [ -f "$DATABASE_URL_FILE" ]; then
        tr -d '\r\n' < "$DATABASE_URL_FILE"
    elif [ -f /run/secrets/database_url ]; then
        tr -d '\r\n' < /run/secrets/database_url
    else
        echo "ERROR: definir DATABASE_URL o DATABASE_URL_FILE" >&2
        exit 1
    fi
}

command -v pg_dump >/dev/null 2>&1 || {
    echo "ERROR: pg_dump no esta disponible (instalar postgresql-client)" >&2
    exit 1
}

DSN="$(resolve_dsn)"
OUT="$DEST/mrbot-$STAMP.dump"

echo "respaldo: $OUT"
pg_dump --format=custom --no-password --dbname="$DSN" --file="$OUT"

sha256sum "$OUT" > "$OUT.sha256"
sha256sum -c "$OUT.sha256"

# Poda simple de diarios locales: conserva los ultimos 30 archivos .dump.
# Los mensuales y anuales los conserva la lifecycle policy del bucket.
ls -1t "$DEST"/mrbot-*.dump 2>/dev/null | tail -n +31 | while IFS= read -r old; do
    rm -f "$old" "$old.sha256"
    echo "poda local: $old"
done

echo "OK respaldo $OUT"
echo "siguiente paso: subir $OUT y $OUT.sha256 al bucket de respaldos y"
echo "verificar el checksum en destino antes de cualquier migracion."
