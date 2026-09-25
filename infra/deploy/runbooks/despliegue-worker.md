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
# Edit .env.worker: CENTRAL_URL, WORKER_ADVERTISED_URL, WORKER_CONCURRENCY y WORKER_NUMBER.
```

`WORKER_TOKEN` no forma parte del protocolo actual y no se consume desde `.env`;
Compose usa las variables solo para interpolar los argumentos CLI, no las pasa al
proceso worker. Agrega la URL anunciada exacta a `WORKER_NODES` de la central
(o registra el nodo desde el panel). `WORKER_CONCURRENCY` tiene tope duro de 5
(invariante W-2).

Con nginx-v2, el worker queda disponible en
`https://worker-${WORKER_NUMBER}.mrbot.com.ar`. El dominio no sustituye la
firma Ed25519 de las asignaciones y no se debe agregar un `ports:` al servicio.

## 3. Levantar el worker sin la central

Desde la raíz del repo, usando la sección de worker dedicada (sin variables de
entorno dentro del contenedor y sin arrancar central o PostgreSQL en este host):

```text
cp infra/compose/worker.env.example infra/compose/.env.worker
# Editar .env.worker y fijar CENTRAL_URL, WORKER_ADVERTISED_URL y WORKER_CONCURRENCY.
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
3. Recrear con la imagen nueva por digest y confirmar estado SANO.
4. Repetir worker por worker, conservando capacidad de la flota.

```text
docker compose -f infra/compose/worker.compose.yaml \
  --env-file infra/compose/.env.worker up -d
```

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
