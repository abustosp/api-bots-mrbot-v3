# bot-worker

Plano de ejecución de la V3. Recibe de la central un sobre de job firmado, corre
el bot Playwright correspondiente, sube los artefactos por URL prefirmada y
reporta el resultado. **No decide qué ejecutar ni guarda estado.**

> Implementado. El registro actual (`bot_worker.bots.registry.REGISTRY`) tiene
> **35 plugins**. Si este README y el código difieren, manda el código.

## Reglas que definen el servicio

- **Sin base de datos (W-1).** No instala `psycopg`, `sqlalchemy` ni `alembic`;
  el build de la imagen lo comprueba.
- **Sin secretos propios ni entorno.** Se configura solo por CLI y falla si ve
  secretos en el entorno. Credenciales, proxy, captcha y URLs prefirmadas llegan
  en cada asignación sellada de la central.
- **Solo ejecuta asignaciones firmadas** (Ed25519) por la central.
- **Tope de concurrencia (W-2):** `--concurrency` de 1 a 10 (por defecto 5). Con
  el worker saturado responde `409`.
- **Puerto no publicado (W-3):** solo la central lo alcanza.

## API secundaria (privada)

Definida en `src/bot_worker/main.py`. Rutas bajo `/internal/v1`:

- `POST /jobs` recibe una asignación (`202`, o `409` si está saturado o drenando).
- `GET /jobs` lista los jobs en curso.
- `POST /jobs/{job_id}/cancel` cancela un job en vuelo.
- `GET /health` vivacidad, sin dependencias.
- `GET /status` capacidad, jobs en ejecución y versiones.
- `GET /bots` manifiesto de los plugins de la imagen.

Además el worker empuja latidos, eventos y resultados a la central
(`reporting/`). Ante `SIGTERM` drena: pasa a `DRENANDO`, rechaza trabajo nuevo y
espera a los jobs en vuelo (`--drain-timeout`, 120 s por defecto).

## Contrato de plugin

Cada bot es un paquete en `src/bot_worker/bots/<bot>/` con un `plugin.py` que
implementa `BotPlugin` (`manifest`, `validate`, `execute`) y se registra en
`bots/_plugins.py`. El plugin escribe solo en `runtime.work_dir`, no abre
conexiones a base de datos, no lee el entorno, no loguea credenciales y no arma
el mensaje de error público (devuelve categoría y diagnóstico). Diseño original:
[`plans/03-worker/plan.md`](../../plans/03-worker/plan.md) §7.

## Estructura

```
services/bot-worker/
├── Dockerfile
├── DEPLOY.md
├── pyproject.toml
├── tests/                       # una prueba por plugin y por capa del runtime
└── src/bot_worker/
    ├── __main__.py, main.py     # CLI y aplicación FastAPI (bot_worker.main:app)
    ├── config.py                # argumentos CLI y validación
    ├── security/                # firma de asignaciones (assignments), TLS/mTLS
    ├── runtime/                 # contexto, navegador, login ARCA, captcha,
    │                            # proxies, espacio de trabajo, portals/
    ├── scheduler/               # supervisor de jobs en ejecución
    ├── reporting/               # latidos, resultados, comunicación con la central
    ├── bots/                    # un paquete por bot + registry.py, _plugins.py
    ├── bots_dev/                # NOTA.md y payload_ejemplo.json por plugin
    └── bots_logger/             # scripts de logger por bot
```

## Cómo correrlo y probarlo

Pruebas (desde la raíz del monorepo):

```bash
make install     # crea .venv e instala contratos, central y worker
make test        # toda la suite
.venv/bin/python -m pytest -q services/bot-worker/tests
```

Los tests usan fakes de Playwright (`tests/fakes_playwright.py`) y no abren
navegador ni llaman a organismos. Sin Playwright instalado el worker usa un
navegador stub, solo válido para desarrollo.

Imagen y stack local:

```bash
docker build -f services/bot-worker/Dockerfile -t mrbot-bot-worker:dev .
docker compose -f infra/compose/docker-compose.yml up -d --scale bot-worker=2
```

Arranque manual: `python -m bot_worker --central-url <url> --advertised-url <url>`
(ver `config.py` para todas las opciones).

## Recursos

Cada Chromium consume 300-500 MB. Mínimo 2.5 GB de memoria y 1 núcleo;
recomendado 4 GB y 2 núcleos. `/dev/shm` debe ser de memoria (el compose usa
2 GB); sin eso Chromium se cae bajo concurrencia.

## Documentos relacionados

- [`DEPLOY.md`](DEPLOY.md): imagen, arranque, drenaje y producción.
- [`../worker-updater/README.md`](../worker-updater/README.md): actualización de workers manuales.
- [`../../infra/deploy/runbooks/despliegue-worker.md`](../../infra/deploy/runbooks/despliegue-worker.md): runbook de despliegue.
- Diseño histórico: [`plans/03-worker/plan.md`](../../plans/03-worker/plan.md) y [`plans/07-migracion/plan.md`](../../plans/07-migracion/plan.md).
