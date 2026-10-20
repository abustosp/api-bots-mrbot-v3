# Runbook: despliegue de bot-worker

Despliega workers en hosts dedicados o escala los del stack local.
Un host de worker usa la URL de la central y su propia URL anunciada por CLI;
la central debe incluir esa URL (`host:puerto`) en `WORKER_NODES` o darla de alta
mediante el panel de flota. El worker no recibe un `WORKER_TOKEN` estático ni
variables de entorno.

## 1. Requisitos del host

- Docker Engine y Compose actualizados, sincronizacion NTP.
- Conectividad HTTPS o HTTP interna hacia `CENTRAL_URL`.
- 3 GiB RAM y 2.5 vCPU por worker (5 Chromium de 300 a 500 MB mas
  Python y temporales), `/dev/shm` respaldado en memoria de 2 GiB
  (lo provee el compose con `shm_size` y `tmpfs`, no tocar).
- `init: true` activo (viene en el compose, recolecta hijos Chromium).
- Si se publica mediante nginx-v2, la red externa `proxy-edge` debe existir y
  cada despliegue debe usar un `WORKER_NUMBER` distinto.

## 2. Configurar URL de la central, URL anunciada y capacidad

```bash
cp infra/compose/worker.env.example infra/compose/.env.worker
# Edit .env.worker: CENTRAL_URL, WORKER_ADVERTISED_URL, WORKER_CONCURRENCY,
# WORKER_NUMBER y APP_VERSION. Use a release tag or git SHA, not `dev`.
# Optional: WORKER_IMAGE_DIGEST=sha256:<digest-resuelto-de-la-imagen>.
```

`WORKER_TOKEN` no forma parte del protocolo actual y no se consume desde `.env`;
Compose usa las variables solo para interpolar los argumentos CLI, no las pasa al
proceso worker. Agrega la URL anunciada exacta a `WORKER_NODES` de la central
(o registra el nodo desde el panel). `WORKER_CONCURRENCY` tiene tope duro de 10
(invariante W-2).

`APP_VERSION` llega al registro y a cada heartbeat como versión de aplicación.
Para identificar el commit incluido en la imagen, configura el tag de release o
el SHA del commit en `APP_VERSION` antes de ejecutar Compose. Por ejemplo:

```bash
export APP_VERSION="$(git rev-parse --short=12 HEAD)"
docker compose -f infra/compose/worker.compose.yaml \
  --env-file infra/compose/.env.worker up -d
```

Compose lo convierte en `--image-version`; el proceso worker sigue sin recibir
variables de entorno. Si el pipeline conoce el digest resuelto, configura también
`WORKER_IMAGE_DIGEST=sha256:<64 hex>` para que la UI compare imágenes incluso
cuando comparten el mismo tag. En cada despliegue, fija en el `.env` de central
`WORKER_LATEST_VERSION` al mismo `APP_VERSION`; si tienes digest, usa
`APP_VERSION@WORKER_IMAGE_DIGEST`. Así los workers antiguos siguen marcados como
obsoletos durante un rollout. Si se deja vacío, la central infiere el mayor valor
observado entre workers registrados.

Con nginx-v2, el worker queda disponible en
`https://worker-${WORKER_NUMBER}.mrbot.com.ar`. El dominio no sustituye la
firma Ed25519 de las asignaciones y no se debe agregar un `ports:` al servicio.

## 3. Levantar el worker sin la central

Desde la raíz del repo, usando la sección de worker dedicada (sin variables de
entorno dentro del contenedor y sin arrancar central o PostgreSQL en este host):

```text
cp infra/compose/worker.env.example infra/compose/.env.worker
# Editar .env.worker y fijar CENTRAL_URL, WORKER_ADVERTISED_URL,
# WORKER_CONCURRENCY y APP_VERSION (tag o git SHA del build).
docker compose -f infra/compose/worker.compose.yaml \
  --env-file infra/compose/.env.worker up -d
```

En el stack local (mismo host que la central) en cambio:

```text
docker compose -f infra/compose/docker-compose.yml \
  --env-file infra/compose/.env up -d --scale bot-worker=2
```

## 4. Verificar registro y salud

1. El worker se registra solo ante la central y empieza a emitir
   latidos cada 10 segundos con jitter de 2.
2. En la central, el worker aparece como SANO y acepta asignaciones
   con `POST /internal/v1/jobs` (`202` acepta, `409` saturado).
3. Detalle local de capacidad:
   `GET /internal/v1/status` (en ejecucion, en cola, versiones).
   Vivacidad simple: `GET /internal/v1/health`.
4. Manifiesto de bots soportados: `GET /internal/v1/bots`.

## 5. Actualizar sin perder trabajo (drenaje)

Nunca matar primero un worker con jobs en vuelo:

1. Marcar el worker como DRENANDO en la central: deja de recibir jobs.
2. Esperar que termine los jobs en vuelo (hasta 120 segundos) o que
   la central los reencole por vencimiento del lease.
3. Recrear con la imagen nueva por digest, actualizar `APP_VERSION` y confirmar estado SANO.
4. Repetir worker por worker, conservando capacidad de la flota.

```text
docker compose -f infra/compose/worker.compose.yaml \
  --env-file infra/compose/.env.worker up -d
```

### Actualizar desde el panel (workers manuales)

Con el updater de host instalado, el panel de flota actualiza los workers
manuales sin acceso SSH: botón **Actualizar** por fila (solo si el worker está
desactualizado) y **Actualizar todos**. Los efímeros EC2 no se actualizan así.

Requisitos en la central (`.env`):

- `WORKER_IMAGE_REF`: imagen completa a desplegar (`repo:tag` o `repo@sha256:...`).
  `build-and-push.sh` imprime el valor al terminar.
- `WORKER_LATEST_VERSION`: el mismo `APP_VERSION` (define qué workers están desactualizados).
- `UPDATER_TOKEN`: secreto compartido con los updaters. Opcional `WORKER_UPDATE_MAX_PARALLEL` (1 por defecto).

Instalar el updater en cada host (una sola vez), en `.env.worker`: `UPDATER_TOKEN`,
`UPDATER_NODE` (el `host:puerto` exacto del nodo en la central), `UPDATER_ALLOWED_REPOS`,
`UPDATER_HOST_DIR` (ruta absoluta de `infra/compose`), `UPDATER_DOCKER_CONFIG`
(`config.json` del `docker login`) y `UPDATER_IMAGE`; luego:

```text
docker compose -f infra/compose/worker.compose.yaml \
  --env-file infra/compose/.env.worker --profile updater up -d
```

Cada actualización: el worker se pausa y termina lo que tiene en vuelo (hasta
`WORKER_UPDATE_DRAIN_SECONDS`, 180 s), el updater hace `pull`, reescribe `WORKER_IMAGE`,
`APP_VERSION` y `WORKER_IMAGE_DIGEST` en `.env.worker`, recrea solo `bot-worker` y
espera a que esté `healthy`. Si no lo está, restaura los valores anteriores
(estado `REVERTIDO`). El updater es el único contenedor con docker socket; solo
acepta imágenes de `UPDATER_ALLOWED_REPOS`. El estado de las solicitudes vive en
memoria de la central: si esta se reinicia a mitad de camino, el updater termina
igual y la versión nueva se ve por latido.

## 6. Rollback

Desplegar el digest anterior conocido y verificar registro SANO mas
`GET /internal/v1/bots` con la version esperada. La compatibilidad la
define el protocolo versionado, no el tag de imagen.

## 7. Problemas comunes

| Sintoma | Causa probable | Accion |
|---|---|---|
| No se registra | `CENTRAL_URL`, `WORKER_ADVERTISED_URL` o allowlist incorrectos | Verificar conectividad y que la URL anunciada esté en `WORKER_NODES` o en el panel |
| Chromium se cae | `/dev/shm` pequeno | Verificar `shm_size: 2gb` y `tmpfs` en el compose resuelto |
| `409` constante | 5 jobs en curso | Normal: SATURADO, escalar con otro worker |
| PIDs agotados | Fuga de procesos | Revisar limite `pids: 512` y reiniciar el contenedor |

## Documentos relacionados

- [despliegue-central.md](despliegue-central.md)
- [plan de infra](../../../plans/06-infra/plan.md)
- [compose de desarrollo](../../../infra/compose/docker-compose.yml)
- [ejemplo de entorno](../../../infra/compose/.env.example)
