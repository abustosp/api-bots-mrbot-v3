# Runbook: despliegue de bot-worker

Despliega workers en hosts dedicados o escala los del stack local.
Un host de worker solo necesita dos datos: `CENTRAL_URL` mas el token.
Jamas recibe `DATABASE_URL`, clave RSA, credenciales MinIO, SMTP ni
MercadoPago (ver [plan de infra](../../../plans/06-infra/plan.md) seccion 6).

## 1. Requisitos del host

- Docker Engine y Compose actualizados, sincronizacion NTP.
- Conectividad HTTPS o HTTP interna hacia `CENTRAL_URL`.
- 3 GiB RAM y 2.5 vCPU por worker (5 Chromium de 300 a 500 MB mas
  Python y temporales), `/dev/shm` respaldado en memoria de 2 GiB
  (lo provee el compose con `shm_size` y `tmpfs`, no tocar).
- `init: true` activo (viene en el compose, recolecta hijos Chromium).

## 2. Configurar solo CENTRAL_URL mas token

```bash
mkdir -p worker-deploy/secrets && cd worker-deploy
cat > .env <<EOF
CENTRAL_URL=https://central.ejemplo.com
WORKER_TOKEN=cambiar-por-token-generado
MAX_CONCURRENT_JOBS=5
EOF
printf '%s\n' "$WORKER_TOKEN" > secrets/worker_auth_token.txt
# Solo si el plugin los usa: proxy, captcha, CUIT, IA.
# printf '%s\n' '...' > secrets/proxy_credentials.txt
chmod 600 .env secrets/*
```

`MAX_CONCURRENT_JOBS` tiene tope duro de 5 (invariante W-2). Un valor
mayor se rechaza: la central degrada al worker a SATURADO.

## 3. Levantar el worker sin la central

Desde la raiz del repo, sin arrancar dependencias (`--no-deps` evita
levantar postgres y central en este host):

```text
CENTRAL_URL=https://central.ejemplo.com WORKER_TOKEN=... \
docker compose -f infra/compose/docker-compose.yml up -d --no-deps bot-worker
```

En el stack local (mismo host que la central) en cambio:

```text
docker compose -f infra/compose/docker-compose.yml up -d --scale bot-worker=2
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
docker compose -f infra/compose/docker-compose.yml up -d --no-deps bot-worker
```

## 6. Rollback

Desplegar el digest anterior conocido y verificar registro SANO mas
`GET /internal/v1/bots` con la version esperada. La compatibilidad la
define el protocolo versionado, no el tag de imagen.

## 7. Problemas comunes

| Sintoma | Causa probable | Accion |
|---|---|---|
| No se registra | `CENTRAL_URL` o token mal | Revisar `.env` y rotar token |
| Chromium se cae | `/dev/shm` pequeno | Verificar `shm_size: 2gb` y `tmpfs` en el compose resuelto |
| `409` constante | 5 jobs en curso | Normal: SATURADO, escalar con otro worker |
| PIDs agotados | Fuga de procesos | Revisar limite `pids: 512` y reiniciar el contenedor |

## Documentos relacionados

- [despliegue-central.md](despliegue-central.md)
- [plan de infra](../../../plans/06-infra/plan.md)
- [compose de desarrollo](../../../infra/compose/docker-compose.yml)
- [ejemplo de entorno](../../../infra/compose/.env.example)
