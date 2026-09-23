# syntax=docker/dockerfile:1.7
# Imagen de bot-worker: plano de ejecucion.
# Diseno: plans/06-infra/plan.md seccion 4.4, adaptada al layout real
# del servicio (pyproject + src/, paquete bot_worker con entrypoint
# bot_worker.main:app y contratos en packages/mrbot-contracts).
#
# Playwright y Chromium los provee la base interna; el paquete declara
# solo FastAPI, Uvicorn, Pydantic y httpx. No instala psycopg,
# SQLAlchemy ni Alembic: el worker nunca toca PostgreSQL (invariante
# W-1). La barrera de imports al final del build es defensa adicional;
# CI tambien inspecciona el entorno final con pip list.
#
# Build desde la raiz del monorepo (el contexto raiz es obligatorio
# porque tambien se copia packages/mrbot-contracts):
#   docker build -f services/bot-worker/Dockerfile -t mrbot-bot-worker:dev .
# Fijar el digest base en release:
#   docker build -f services/bot-worker/Dockerfile \
#     --build-arg PLAYWRIGHT_IMAGE=docker.abp.net.ar/abustosp/bb:py3.14.7-pw1.62-20260818@sha256:<PINNED_DIGEST> \
#     -t mrbot-bot-worker:<version> .

ARG PLAYWRIGHT_IMAGE=docker.abp.net.ar/abustosp/bb:py3.14.7-pw1.62-20260818

FROM ${PLAYWRIGHT_IMAGE} AS builder
ARG PIP_DISABLE_PIP_VERSION_CHECK=1
ARG PIP_NO_CACHE_DIR=1
ARG PIP_ROOT_USER_ACTION=ignore
WORKDIR /build
COPY services/bot-worker/pyproject.toml ./bot-worker/pyproject.toml
COPY services/bot-worker/README.md ./bot-worker/README.md
COPY services/bot-worker/src ./bot-worker/src
COPY packages/mrbot-contracts/pyproject.toml ./mrbot-contracts/pyproject.toml
COPY packages/mrbot-contracts/README.md ./mrbot-contracts/README.md
COPY packages/mrbot-contracts/src ./mrbot-contracts/src
RUN python -m pip install --upgrade pip \
 && python -m pip wheel --wheel-dir /wheels ./bot-worker ./mrbot-contracts

FROM ${PLAYWRIGHT_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/mrbot
RUN addgroup --system --gid 10001 mrbot \
 && adduser --system --uid 10001 --ingroup mrbot --home /home/mrbot mrbot \
 && mkdir -p /app /work /home/mrbot \
 && chown -R mrbot:mrbot /app /work /home/mrbot
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
 && rm -rf /wheels \
 && ! python -c "import psycopg" \
 && ! python -c "import sqlalchemy" \
 && ! python -c "import alembic"
USER mrbot
EXPOSE 8080
# mTLS central<->worker (plan 03 seccion 4.4): los certificados se montan
# en /certs en tiempo de ejecucion (ver infra/certs y compose), nunca se
# hornean en la imagen. Sin TLS el worker sirve HTTP; con la
# superposicion docker-compose.mtls.yml, uvicorn exige certificado de
# cliente con CN MrBotCentral (ver bot_worker.security.tls). W-1: aqui
# no hay rutas de CA ni secretos de la central, solo identidad propia.
HEALTHCHECK --interval=30s --timeout=3s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health', timeout=2)"
# Entrypoint por CLI: el worker no lee entorno; el orquestador pasa
# --central-url/--advertised-url en `command:` (ver compose y DEPLOY.md).
# Sin argumentos falla rapido con el uso, en vez de servir sin configurar.
CMD ["python", "-u", "-m", "bot_worker"]
