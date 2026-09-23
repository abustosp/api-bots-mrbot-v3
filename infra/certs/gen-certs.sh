#!/usr/bin/env bash
# Genera la PKI local de desarrollo para el mTLS central<->worker.
#
# Uso:
#   ./infra/certs/gen-certs.sh [DIR]
#   DIR por defecto: el directorio de este script (infra/certs).
#
# Crea (solo local, nunca se versiona: ver .gitignore):
#   ca.pem               CA local autofirmada (solo desarrollo).
#   central-server.pem/.key  servidor de central-api (CN central-api).
#   worker-server.pem/.key   servidor de bot-worker (CN bot-worker).
#   central-client.pem/.key  cliente de la central (CN MrBotCentral).
#   worker-client.pem/.key   cliente del worker (CN worker-local-01).
#
# Los SAN incluyen DNS central-api/bot-worker/localhost e IP 127.0.0.1
# para que el handshake verifique tambien contra localhost.
# Produccion usa secret manager con CA separadas; este script es solo
# para desarrollo y verificacion local end-to-end.
set -euo pipefail

OUT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
mkdir -p "$OUT"
umask 077

DAYS_CA=3650
DAYS_LEAF=825

mk_ca() {
  openssl req -x509 -newkey rsa:4096 -sha384 -days "$DAYS_CA" -nodes \
    -keyout "$OUT/ca.key" -out "$OUT/ca.pem" \
    -subj "/CN=MrBot Local Dev CA/O=MrBot/C=AR" 2>/dev/null
  chmod 600 "$OUT/ca.key"
  chmod 644 "$OUT/ca.pem"
}

mk_leaf() {
  # $1=nombre base, $2=CN, $3=SAN, $4=O
  local name="$1" cn="$2" san="$3" org="$4"
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$OUT/${name}.key" -out "$OUT/${name}.csr" \
    -subj "/CN=${cn}/O=${org}/C=AR" 2>/dev/null
  cat > "$OUT/${name}.ext.cnf" <<EOF
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = ${san}
EOF
  openssl x509 -req -in "$OUT/${name}.csr" \
    -CA "$OUT/ca.pem" -CAkey "$OUT/ca.key" -CAcreateserial \
    -days "$DAYS_LEAF" -sha256 -extfile "$OUT/${name}.ext.cnf" \
    -out "$OUT/${name}.pem" 2>/dev/null
  chmod 600 "$OUT/${name}.key"
  chmod 644 "$OUT/${name}.pem"
  rm -f "$OUT/${name}.csr" "$OUT/${name}.ext.cnf"
}

SAN_LOCALHOST="DNS:localhost,IP:127.0.0.1"
mk_ca
mk_leaf "central-server" "central-api" \
  "DNS:central-api,${SAN_LOCALHOST}" "MrBot Central"
mk_leaf "worker-server" "bot-worker" \
  "DNS:bot-worker,${SAN_LOCALHOST}" "MrBot Workers"
mk_leaf "central-client" "MrBotCentral" \
  "DNS:central-api,${SAN_LOCALHOST}" "MrBot Central"
mk_leaf "worker-client" "worker-local-01" \
  "DNS:bot-worker,${SAN_LOCALHOST}" "MrBot Workers"
rm -f "$OUT/ca.srl"

for c in central-server worker-server central-client worker-client; do
  openssl verify -CAfile "$OUT/ca.pem" "$OUT/${c}.pem" > /dev/null
done

echo "PKI local lista en: $OUT"
echo "Huellas SHA-256 (para documentar pinning, no son secretos):"
for c in ca central-server worker-server central-client worker-client; do
  printf '  %-15s %s\n' "$c" "$(openssl x509 -in "$OUT/${c}.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
done
