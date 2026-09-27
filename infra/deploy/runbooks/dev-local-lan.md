# Runbook: stack local de desarrollo (worker + central en la LAN)

Sirve para iterar sobre los portales sin depender del host de pruebas remoto.
El ciclo es: editar → `rsync` de dos carpetas → `docker restart` del worker →
correr el caso. No hay que reconstruir imágenes por cambio de código.

## 1. Host

- `abp-server` (`192.168.0.200`): Docker Compose v5.5.1, 28 vCPU, 62 GiB RAM.
- Repo copiado en `/home/abp/mrbot-v3-local` (no es un clon de git).
- El worker es alcanzable por la LAN; por eso la central también corre ahí
  (una central en otro host no podría despacharle jobs).

## 2. Servicios

```text
docker compose -p mrbot-local -f docker-compose.yml -f docker-compose.local.yml \
  up -d postgres migrate central-api bot-worker
```

- `postgres`: base local, usuario/clave de `secrets/postgres_app_password.txt`.
- `migrate`: aplica alembic y siembra el catálogo (32 bots, 42 operaciones).
- `central-api`: publicada en `192.168.0.200:8100` (el 8000 del host está ocupado
  por el stack V2; `docker-compose.local.yml` usa `!override` para reemplazar el
  mapeo del compose base).
- `bot-worker`: mismo `command` que producción, con el código montado.

## 3. Montaje de código (lo que hace rápido el ciclo)

`docker-compose.local.yml` monta:

- `services/bot-worker/src/bot_worker` → `/opt/mrbot_src/bot_worker`
- `services/central-api/src/central_api` → `/opt/mrbot_src/central_api`

con `PYTHONPATH=/opt/mrbot_src`, que tiene prioridad sobre el paquete instalado
en la imagen. `PYTHONPATH` no está en la lista de variables prohibidas del worker.

```text
rsync -a --delete --exclude __pycache__ services/bot-worker/src/bot_worker/ \
  abp-server:/home/abp/mrbot-v3-local/services/bot-worker/src/bot_worker/
ssh abp-server 'docker restart mrbot-local-bot-worker-1'   # ~8 s
```

## 4. Secretos: modos y contenidos

Los `*_FILE` de `infra/compose/secrets/` deben ser legibles por el usuario de la
aplicación (uid 10001). Con modo `600` y dueño `abp` (uid 1000) el proceso no los
lee y la central firma "tickets de desarrollo" (`storage.example`), lo que hace
fallar la subida de artefactos con `ConnectError`. En este host quedaron en `604`.

Contenidos que hay que fijar a mano al armar el stack local:

- `database_url.txt`: `postgresql+psycopg://<POSTGRES_USER>:<clave>@postgres:5432/<POSTGRES_DB>`
  usando exactamente el usuario y la base del `.env` (el del compose base son
  `mrbot_app_v3` / `mrbot_v3`; un DSN con otros nombres da `role does not exist`).
- `capmonster_arca_key.txt` y `capmonster_srt_key.txt`: copiados del entorno V2
  del mismo host (el `.env` de `~/api-bots-mrbot`), porque los del repo son
  placeholders vacíos.
- `minio_root_user.txt` / `minio_root_password.txt`: los mismos del entorno de
  pruebas remoto, para que la central local firme contra el MinIO existente
  (`OBJECT_STORAGE_ENDPOINT=http://166.1.85.240:9000`).

No se levanta MinIO local: este host no puede descargar las imágenes
(quay.io/Docker Hub responden `unauthorized`) y el almacenamiento del entorno de
pruebas ya es alcanzable por la LAN. Las imágenes base y del stack se cargaron
con `docker save | gzip -1 | ssh abp-server 'gunzip | docker load'` desde el host
de pruebas (postgres 17.6, central y worker).

## 5. Usuario de pruebas

```text
TOKEN=$(grep -E '^ADMIN_TOKEN=' infra/compose/.env | tail -1 | cut -d= -f2-)
# POST /admin/users con {"email":"abp","api_key":"abp","motivo":"..."} (motivo >= 3)
#   Authorization: Bearer $TOKEN
```

Luego el runner usa `MRBOT_BASE=http://192.168.0.200:8100 MRBOT_USER=abp MRBOT_KEY=abp`.

## 6. Diagnóstico

- El supervisor loguea la traza de todo error de plugin
  (`WARNING:bot_worker.api:error de plugin: ...`).
- Los portales etiquetan el paso que falla (`portal_step_<paso>`) y la selección
  de representado informa un mapa de selectores con la cantidad encontrada.
