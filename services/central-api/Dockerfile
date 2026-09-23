# syntax=docker/dockerfile:1.7
# Imagen de central-api: plano de control.
# Diseno: plans/06-infra/plan.md seccion 4.3, adaptada al layout real
# del servicio (pyproject + src/, paquete central_api con entrypoint
# central_api.main:app y contratos en packages/mrbot-contracts).
#
# Incluye FastAPI, Uvicorn, SQLAlchemy, Alembic, psycopg y el paquete
# de contratos. No incluye Playwright, Chromium ni bots. Es la unica
# imagen con acceso a PostgreSQL y la unica que ejecuta migraciones
# (job one-off `migrate`, nunca en el `CMD`).
#
# Build desde la raiz del monorepo (el contexto raiz es obligatorio
# porque tambien se copia packages/mrbot-contracts):
#   docker build -f services/central-api/Dockerfile -t mrbot-central-api:dev .
# Fijar el digest base en release:
#   docker build -f services/central-api/Dockerfile \
#     --build-arg PYTHON_IMAGE=python:3.14-slim@sha256:<PINNED_DIGEST> \
#     -t mrbot-central-api:<version> .

ARG PYTHON_IMAGE=python:3.14-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /build
COPY services/central-api/pyproject.toml ./central-api/pyproject.toml
COPY services/central-api/README.md ./central-api/README.md
COPY services/central-api/src ./central-api/src
COPY packages/mrbot-contracts/pyproject.toml ./mrbot-contracts/pyproject.toml
COPY packages/mrbot-contracts/README.md ./mrbot-contracts/README.md
COPY packages/mrbot-contracts/src ./mrbot-contracts/src
RUN python -m pip install --upgrade pip \
 && python -m pip wheel --wheel-dir /wheels ./central-api ./mrbot-contracts

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    HOME=/app
RUN addgroup --system --gid 10001 mrbot \
 && adduser --system --uid 10001 --ingroup mrbot --home /app mrbot \
 && mkdir -p /app /tmp/mrbot \
 && chown -R mrbot:mrbot /app /tmp/mrbot
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
 && rm -rf /wheels
# Migraciones one-off: el job `migrate` corre `python -m central_api.migrate`
# en esta misma imagen (nunca en el CMD); necesita env.py + versions.
COPY services/central-api/alembic /app/alembic
USER mrbot
EXPOSE 8000
# mTLS central<->worker (plan 02 seccion 4.8, camino a mTLS del plan 03
# seccion 4.4): los certificados se montan en /certs en tiempo de
# ejecucion (ver infra/certs y compose), nunca se hornean en la imagen.
# Sin TLS_* la central sirve HTTP; con la superposicion
# docker-compose.mtls.yml, uvicorn exige certificado de cliente firmado
# por la CA conocida (ver central_api.security.tls).
ENV TLS_CA_FILE=/certs/ca.pem \
    TLS_CERT_FILE=/certs/central-server.pem \
    TLS_KEY_FILE=/certs/central-server.key \
    TLS_CLIENT_CERT_FILE=/certs/central-client.pem \
    TLS_CLIENT_KEY_FILE=/certs/central-client.key \
    TLS_REQUIRE_PEER=1 \
    TLS_EXPECTED_PEER_CN=worker-local-01
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "central_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
