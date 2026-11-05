# Despliegue de bot-worker

Plano de ejecucion: recibe sobres de job de la central, corre el bot
Playwright, sube artefactos por URL prefirmada y reporta el resultado.
No decide que ejecutar ni guarda nada.
Diseno: [plan de infra](../../plans/06-infra/plan.md).

## Imagen

- [Dockerfile](Dockerfile): sobre la base Playwright interna, usuario
  no root `mrbot`, `/work` efimero, sin variables de entorno de config
  (`--concurrency 5` entra por CLI; el máximo es 10).
- El build verifica que `psycopg`, `sqlalchemy` y `alembic` no son
  importables (invariante W-1). Sin driver de base, sin modelos ORM,
  sin clave RSA ni SMTP.
- Build desde la raiz del monorepo:

```bash
docker build -f services/bot-worker/Dockerfile -t mrbot-bot-worker:dev .
```

- En release fijar el digest base con
  `--build-arg PLAYWRIGHT_IMAGE=...` y publicar por digest inmutable.

## Configuracion: sin entorno, solo CLI

El worker no lee variables de entorno ni guarda secretos: arranca con
`python -m bot_worker --central-url ... --advertised-url ...` (ver
`command:` en el compose) y falla si ve secretos en el entorno. Todo lo
que necesita (credenciales, proxy, captcha, endpoints, URLs prefirmadas)
lo provisiona la central por asignación sellada; el token de servicio y
la clave de verificación llegan en el registro, en memoria. Solo ejecuta
asignaciones firmadas por la central (Ed25519): sin firma válida no corre
nada, aunque su puerto sea alcanzable.

## Arranque

Stack local escalable (los workers no publican puertos, W-3):

```bash
docker compose -f ../../infra/compose/docker-compose.yml up -d --scale bot-worker=2
```

Host dedicado (sin dependencias locales):

```bash
docker compose -f ../../infra/compose/docker-compose.yml up -d --no-deps bot-worker
```
En host dedicado se sobreescribe el `command:` con las URLs propias:

```yaml
command: ["python", "-u", "-m", "bot_worker",
  "--central-url", "https://central.ejemplo.com",
  "--advertised-url", "http://10.0.0.21:8080",
  "--concurrency", "5"]
```

Salud: `GET /internal/v1/health` (vivacidad),
`GET /internal/v1/status` (capacidad y versiones),
`GET /internal/v1/bots` (manifiesto). Requiere `init: true`,
`/dev/shm` de 2 GiB y `stop_grace_period` de 150 segundos (vienen en el
compose). El tope de 10 jobs concurrentes (invariante W-2) lo aplica
`--concurrency`, con rango permitido de 1 a 10. Saturado responde `409`.

## Navegador: real en imagen, stub solo en dev

- En la imagen del [Dockerfile](Dockerfile) hay Playwright + Chromium
  (base `PLAYWRIGHT_IMAGE` fijada por digest) y el worker usa la
  fábrica real (`PlaywrightBrowserFactory`: un Chromium por sesión,
  siempre headless, cierre garantizado).
- Sin Playwright instalado (arranque local, tests) el worker usa
  `_StubBrowserFactory`: modo dev documentado en
  `bot_worker.runtime.browser`, sin procesos ni red. Nunca usar el
  stub en producción; el camino al Chromium real es construir la
  imagen del servicio.
- Ante `SIGTERM` el worker drena: avisa `DRENANDO` a la central,
  rechaza asignaciones nuevas con `409 WORKER_DRENANDO` y espera a los
  jobs en vuelo hasta `--drain-timeout` (120 s por defecto) sin
  perderlos; al vencer pide cancelación cooperativa y reporta.

## Servicio externo: real en producción, stub solo en dev

- El plugin `consulta_cuit` llama al servicio público de constancias
  (`https://api-constancias-de-inscripcion.mrbot.com.ar`, fijado en
  `bots/consulta_cuit/plugin.py`). Sin salida a internet el job termina
  `FALLIDO` por `ConnectError`. Las credenciales del servicio las inyecta la
  central en la asignación; el worker no lee variables de entorno.
- `infra/compose` incluye `stub-constancias` (perfil `stub`) para pruebas
  locales; el worker no tiene variables `CONSULTA_CUIT_*`, así que revisar el
  [runbook de arranque local](../../infra/deploy/runbooks/arranque-local.md)
  antes de depender de él. Nunca usar el stub en producción.

## Produccion

Ver [runbook de worker](../../infra/deploy/runbooks/despliegue-worker.md):
registro, drenaje a DRENANDO antes de reemplazar, rollback por digest.
