# Runbook: arranque local del stack V3

Levanta en local `postgres 17 + central-api + bot-worker escalable + MinIO`
desde `infra/compose`, aplica migraciones y siembra un admin/API key de
desarrollo. Diseño en
[`plans/06-infra/plan.md`](../../../plans/06-infra/plan.md) §5.

> Solo desarrollo. Ningún valor aquí es un secreto real: `.env` y
> `infra/compose/secrets/` no se versionan (ver
> [`infra/compose/.gitignore`](../../compose/.gitignore)).

## 0. Requisitos

- Docker Engine + Compose v2 (`docker compose version`).
- Puertos libres en loopback: `8000` (central), `9000`/`9001` (MinIO).
- Imágenes `mrbot-central-api:dev` y `mrbot-bot-worker:dev` construidas
  (ver `services/central-api/Dockerfile` y `services/bot-worker/Dockerfile`).

## 1. Configurar entorno

Desde la raíz del repo:

```bash
cd infra/compose
cp -n .env.example .env
```

Opciones del `.env` y del CLI que sí hay que revisar (el resto puede quedar por defecto). El worker no lee entorno: Compose le pasa sus opciones de arranque por argumentos CLI.

| Variable/opción | Efecto |
|---|---|
| `CENTRAL_IMAGE` / `WORKER_IMAGE` | imágenes locales (`:dev` admite tag; prod exige digest) |
| `POSTGRES_DB` / `POSTGRES_USER` | base y rol de la app (el worker jamás las ve, W-1) |
| `WORKER_NODES` | allowlist de workers; en Compose local incluir `bot-worker:8080` para que el registro se persista |
| `WORKER_ADVERTISED_URL` | URL anunciada por el worker dedicado, debe coincidir con una entrada de `WORKER_NODES` |
| `ADMIN_TOKEN` | Bearer [REDACTED] mutaciones de `/admin/*`; sin él son `403` |
| `--concurrency` | argumento CLI del worker; Compose usa `5` por defecto y el máximo permitido es `5` (W-2) |
| `--central-url` | argumento CLI; en el stack local apunta a `http://central-api:8000` |
| `WORKER_NUMBER` | número usado por nginx-v2 en `worker-{numero}.mrbot.com.ar` |

## 2. Secretos locales de desarrollo

Cada secreto vive en `infra/compose/secrets/`, excluido de Git, y se monta solo
en los servicios que lo necesitan. En Compose local, protege el directorio del
host y deja los archivos legibles por el UID no privilegiado de la central
(`10001`). Con secretos basados en archivos, `chmod 600` impide que la API los
lea.

```bash
mkdir -p secrets
[ -s secrets/postgres_app_password.txt ] || openssl rand -hex 32 > secrets/postgres_app_password.txt
[ -s secrets/minio_root_user.txt ] || printf 'mrbot-local\n' > secrets/minio_root_user.txt
[ -s secrets/minio_root_password.txt ] || openssl rand -hex 32 > secrets/minio_root_password.txt
for s in api_key_hmac_secret session_signing_key smtp_password \
  mercadopago_access_token minio_central_credentials worker_auth_token \
  proxy_credentials capmonster_arca_key capmonster_srt_key \
  cuit_service_key ai_api_key oidc_client_secret; do
  [ -s secrets/$s.txt ] || openssl rand -hex 32 > secrets/$s.txt
done
# DATABASE_URL_FILE debe contener la URL real de la base local.
. .env
printf 'postgresql+psycopg://%s:%s@postgres:5432/%s\n' \
  "${POSTGRES_USER:-mrbot_app}" "$(cat secrets/postgres_app_password.txt)" \
  "${POSTGRES_DB:-mrbot}" > secrets/database_url.txt
# La clave Ed25519 debe ser persistente para que los workers acepten asignaciones
# después de reiniciar la central. No crear una clave efímera por arranque.
[ -s secrets/assignment_signing_key.pem ] || openssl genpkey -algorithm Ed25519 -out secrets/assignment_signing_key.pem
# La clave RSA se monta como .pem (ver `secrets:` en docker-compose.yml).
[ -f secrets/rsa_private_key.pem ] || openssl genrsa -out secrets/rsa_private_key.pem 2048
# Los archivos se montan individualmente; el directorio privado evita acceso de
# otros usuarios del host mientras el UID 10001 de la central puede leerlos.
chmod 700 secrets
chmod 644 secrets/*
```

## 3. PKI local para mTLS (opcional salvo overlay mTLS)

```bash
./infra/certs/gen-certs.sh   # CA + 4 identidades en infra/certs (solo local)
```

El compose base monta `../certs:/certs:ro` en central y worker y expone las
rutas por `TLS_*_FILE`, pero arranca en HTTP. El mTLS mutuo se activa con la
superposición (ver §8). Cableado verificado contra `infra/certs`:

| Lado | Servidor | Cliente que presenta | CN esperado por el par |
|---|---|---|---|
| central-api | `central-server.pem` (`CN=central-api`) | `central-client.pem` (`CN=MrBotCentral`) | `worker-local-01` |
| bot-worker | `worker-server.pem` (`CN=bot-worker`) | `worker-client.pem` (`CN=worker-local-01`) | `MrBotCentral` |

SAN de ambos servidores: `central-api`, `bot-worker`, `localhost`, `127.0.0.1`.

## 4. Levantar el stack

MinIO local vive bajo el perfil `local-storage` (en producción el broker es
externo, ver `docker-compose.prod.yml`). Escala de workers con `--scale`:

```bash
cd infra/compose
docker network inspect proxy-edge >/dev/null 2>&1 || docker network create proxy-edge
docker compose --profile local-storage up -d --scale bot-worker=2
docker compose ps
```

Topología resultante: `postgres` (red `control`, sin puertos al host, I-4) +
`central-api` (`127.0.0.1:8000`, redes `edge`/`control`/`storage`) +
`bot-worker` réplicas (red `control` y `proxy-edge`, sin puertos publicados, W-3) +
`minio` (`127.0.0.1:9000` API S3, `127.0.0.1:9001` consola).

Las labels de nginx-v2 crean `central-api.mrbot.com.ar` y
`worker-${WORKER_NUMBER:-1}.mrbot.com.ar` cuando el proxy tiene DNS y ACME
habilitados. Para publicar la UI de inspección PostgreSQL, activar únicamente
el perfil explícito:

```bash
docker compose --profile database-ui up -d database-bots
```

La URL `database-bots.mrbot.com.ar` es Adminer sobre la red interna hacia
PostgreSQL. No se publica el puerto TCP 5432 y el login requiere las
credenciales de la base.

## 4b. Modo stub local (sin salida a internet)

El plugin `consulta_cuit` llama al servicio público de constancias. Si el
entorno no tiene egreso (red `control` interna, DNS sin resolver), el job
llega al worker pero termina `FALLIDO` por `ConnectError`. Para el ciclo
E2E local se usa el stub de desarrollo (solo stdlib, red `control`, sin
puertos al host):

```bash
cd infra/compose
docker compose --profile local-storage --profile stub up -d
```

El worker ya apunta al stub por defecto (`CONSULTA_CUIT_BASE_URL` y
`CONSULTA_CUIT_MASIVA_URL` en `docker-compose.yml`); con el perfil `stub`
apagado esas variables no resuelven y el plugin usa el endpoint público.
Nunca va a producción: el overlay `docker-compose.prod.yml` no lo activa.

## 5. Migrar: `alembic upgrade head` como job one-off

Las migraciones nunca corren en el `CMD` del contenedor. Se aplican una sola
vez con el servicio `migrate` (perfil `migrate`):

```bash
cd infra/compose
docker compose --profile migrate run --rm migrate
# salida esperada: "migraciones aplicadas (head)"
```

Si el volumen `postgres_data` es nuevo, antes corrió `postgres/init`
(`pgcrypto` opcional; el esquema lo crean las migraciones, nunca el init).

## 6. Verificar salud

```bash
curl -sf http://127.0.0.1:8000/health                 # liveness central
curl -sf http://127.0.0.1:8000/ready                  # readiness: base + esquema
curl -sf http://127.0.0.1:8080/no-existe 2>/dev/null; # (puerto solo dentro del worker)
docker compose exec bot-worker python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health', timeout=2).read())"
# detalle con capacidad (exige token; sin él el 401 es lo esperado):
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/ready
curl -sf http://127.0.0.1:9000/minio/health/live      # liveness propia de MinIO, en vez de /health del proyecto
docker compose exec postgres pg_isready -U mrbot_app -d mrbot
```

Semántica (igual que los `HEALTHCHECK` del compose): `/health` es vivacidad
del proceso; `/ready` es capacidad de servir (falla sin `DATABASE_URL` o sin
esquema); en el worker `/internal/v1/health` es anónima y
`/internal/v1/status` exige el Bearer [REDACTED] worker.

## 7. Crear usuario cliente de prueba y API key de desarrollo

El alta integrada de V3 permite definir la identidad, una API key opcional, el
estado inicial y si se intentan enviar las credenciales por SMTP. Los alias
`habilitado` y `send_api_key_email` conservan la forma usada por V1/V2:

```bash
curl -sf -X POST http://127.0.0.1:8000/admin/users \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "email":"dev@example.com",
    "display_name":"desarrollo",
    "api_key":"testing-api-key",
    "estado":"habilitado",
    "enviar_credenciales":false,
    "motivo":"alta local de desarrollo con credencial definida"
  }'
```

Cuando `enviar_credenciales` es `true`, la central intenta enviar la API key
por SMTP STARTTLS. Si SMTP no está configurado o falla, el usuario igualmente
se crea y la respuesta informa el motivo para permitir una entrega manual.

1. Fijar `ADMIN_TOKEN` en `infra/compose/.env` y recrear la central:
   `docker compose up -d central-api`.
2. Crear un usuario de desarrollo (el `motivo` es obligatorio, 10–500
   caracteres; el token va como `Authorization: Bearer <ADMIN_TOKEN>`):

```bash
curl -sf -X POST http://127.0.0.1:8000/admin/users \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"email":"dev@example.com","display_name":"desarrollo","plan":"free","motivo":"siembra local de desarrollo"}'
# → {"success":true,"usuario":{"id":"<USER_ID>",...}}
```

3. Emitir su API key (**se muestra en la respuesta administrativa y puede
   mostrarse/copiarse en la consola mientras esa pestaña siga abierta**; solo
   quedan prefijo y verificador HMAC en el servidor). El endpoint agrega
   `Cache-Control: private, no-store`. Las claves manuales deben tener al menos
   3 caracteres sin espacios. No es posible recuperar claves anteriores: emite
   una clave nueva si necesitas volver a verla.

```bash
curl -sf -X POST http://127.0.0.1:8000/admin/users/<USER_ID>/api-keys \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"scopes":[],"motivo":"siembra local de desarrollo"}'
# → {"success":true,"clave":{...},"valor_unica_vez":"...","aviso":"..."}
```

> Este endpoint crea un usuario cliente y su API key se conserva solo en memoria
> de `central-api`; no crea un administrador ni datos durables. Un reinicio o
> recreación de la central invalida ambos. En entornos persistentes use una
> ruta de aprovisionamiento respaldada por base de datos.

4. Guardar `valor_unica_vez` en el gestor de secretos local (no en el repo) y
   humo contra la API pública:

```bash
curl -sf http://127.0.0.1:8000/api/v3/bots -H "X-API-Key: $DEV_API_KEY"
```

La creación de jobs es `POST /api/v3/bots/{bot}/{operacion}` (`202` +
`job_id`, con `Idempotency-Key` obligatoria) y el estado
`GET /api/v3/jobs/{job_id}`. Los nombres soportados salen de
`GET /api/v3/bots` (canarios de referencia: `consulta_cuit`,
`mis_comprobantes`).

## 8. mTLS end-to-end (opcional)

Con `infra/certs` generado (§3):

```bash
cd infra/compose
docker compose -f docker-compose.yml -f docker-compose.mtls.yml \
  --profile local-storage up -d --scale bot-worker=2
```

Ambos servidores exigen certificado de cliente firmado por la CA local
(`--ssl-cert-reqs 2`); el token de servicio se mantiene como defensa en
profundidad. Los healthchecks usan contexto TLS sin verificar (solo
vivacidad local). W-1/SEC-3, W-2 y W-3 intactos.

## 9. Parada y limpieza

```bash
docker compose --profile local-storage --profile migrate down        # conserva volúmenes
docker compose --profile local-storage --profile migrate down -v    # borra postgres_data y minio_data
```

Borrar el volumen `postgres_data` re-ejecuta `postgres/init` y exige repetir
§5. Rotar la PKI local: `./infra/certs/gen-certs.sh && docker compose up -d
--force-recreate` (detalle en `infra/certs/README.md`).

## 10. Problemas comunes

| Síntoma | Causa probable | Acción |
|---|---|---|
| `migrate` sale con `DATABASE_URL no configurada` | falta el secreto `database_url` | repetir §2 (placeholder) |
| worker `unhealthy` | se consultó `/status` sin token | usar `/internal/v1/health` para liveness (§6) |
| `401` en `/internal/v1/status` | falta `Authorization: Bearer <WORKER_TOKEN>` | es lo esperado sin token |
| `403` en `POST /admin/*` | `ADMIN_TOKEN` vacío o distinto | fijarlo en `.env` y recrear central |
| `409` del worker al asignar | 5 jobs en curso (SATURADO, W-2) | escalar con `--scale bot-worker=N` |
| Chromium se cae | `/dev/shm` pequeño | no tocar: el compose ya fija `shm_size: 2gb` + `tmpfs` |
| MinIO `unhealthy` | consola en `:9001` ≠ API en `:9000` | el healthcheck usa la liveness propia de MinIO en `:9000` (§6), en vez de /health del proyecto |
| `alembic_version` ausente en `/ready` | migraciones sin aplicar | repetir §5 |

## Documentos relacionados

- [`compose de desarrollo`](../../compose/docker-compose.yml)
- [`superposición mTLS`](../../compose/docker-compose.mtls.yml)
- [`ejemplo de entorno`](../../compose/.env.example)
- [`certificados locales`](../../certs/README.md)
- [`despliegue-central.md`](despliegue-central.md) /
  [`despliegue-worker.md`](despliegue-worker.md) (producción)
