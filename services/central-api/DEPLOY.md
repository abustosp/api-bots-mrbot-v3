# Despliegue de central-api

Plano de control: unica imagen con acceso a PostgreSQL y unica que
ejecuta migraciones (job one-off `migrate`, nunca en el `CMD`).
Diseno: [plan de infra](../../plans/06-infra/plan.md).

## Imagen

- [Dockerfile](Dockerfile): multi-stage sobre Python slim, usuario no
  root `mrbot` (UID 10001), sin Playwright ni Chromium.
- Build desde la raiz del monorepo (pyproject mas `src` mas
  `packages/mrbot-contracts`; entrypoint `central_api.main:app`):

```bash
docker build -f services/central-api/Dockerfile -t mrbot-central-api:dev .
```

- En release fijar el digest base con `--build-arg PYTHON_IMAGE=...`
  y publicar por digest inmutable, con SBOM y provenance. Nunca `latest`.

## Configuracion

La central recibe solo secretos de control, identidad, base, cobros,
avisos y broker de artefactos. Variables en
[ejemplo de entorno](../../infra/compose/.env.example):

- `DATABASE_URL` (via secreto `database_url`), `API_KEY_HMAC_SECRET`,
  `SESSION_SIGNING_KEY`, clave privada RSA, `SMTP_*`, `MERCADOPAGO_*`,
  credenciales MinIO de firma.
- `WORKER_NODES`: lista de IPs o DNS de workers permitidos, unica
  informacion que la central necesita de la flota.

## Arranque local

```bash
cp infra/compose/.env.example infra/compose/.env
docker compose -f infra/compose/docker-compose.yml up -d central-api
docker compose -f infra/compose/docker-compose.yml --profile migrate run --rm migrate
```

Salud: `GET /health` (vivacidad) y `GET /ready` (base y esquema).
Logs JSON a stdout, sin bind de `./logs`.

El job `migrate` (`alembic upgrade head`) corre con esta misma imagen
por digest. Requiere el arbol Alembic en la imagen cuando el equipo de
aplicacion lo agregue a `services/central-api/`.

## Produccion

Ver [runbook de central](../../infra/deploy/runbooks/despliegue-central.md):
backup previo, migracion one-off, despliegue por digest, rollback
compatible con expand/contract.
