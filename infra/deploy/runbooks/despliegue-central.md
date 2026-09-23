# Runbook: despliegue de central-api

Despliega el plano de control (postgres 17 mas central-api) en el VPS.
La central solo necesita la lista de workers permitidos (`WORKER_NODES`,
IPs o DNS separados por coma) ademas de sus propios secretos. No recibe
ningun secreto de worker (ver [plan de infra](../../../plans/06-infra/plan.md)
seccion 6).

## 1. Requisitos del host

- Docker Engine y Compose actualizados, volumen persistente para
  PostgreSQL, proxy TLS gestionado por separado, administracion por VPN
  o allowlist, sincronizacion NTP y destino externo para backups.
- MinIO externo existente o perfil `local-storage` para desarrollo.

## 2. Secuencia de release

1. Fijar digests publicados y hacer backup verificado previo.
2. Ejecutar la migracion una unica vez (job one-off con lock):
   `docker compose -f infra/compose/docker-compose.yml --profile migrate run --rm migrate`
3. Verificar revision Alembic esperada y smoke query.
4. Desplegar la central nueva con `WORKER_NODES` actualizado.
5. Verificar `GET /health` (vivacidad) y `GET /ready` (base, esquema).
6. Drenar y reemplazar workers segun [despliegue-worker.md](despliegue-worker.md).
7. Smoke tests y metricas (cola, asignacion, errores).

```text
CENTRAL_IMAGE='registry/grupo/mrbot-central-api@sha256:<digest>' \
WORKER_IMAGE='registry/grupo/mrbot-bot-worker@sha256:<digest>' \
WORKER_NODES='192.0.2.10,192.0.2.11' \
docker compose -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.prod.yml up -d
```

Las migraciones siguen expand/contract: agregar opcional, desplegar,
rellenar y contraer en un despliegue posterior. Una migracion
irreversible exige backup probado, ventana y aprobacion explicita.

## 3. Rollback

1. Poner el worker nuevo en DRENANDO y guardar evidencia de cola.
2. Desplegar el digest previo compatible (una migracion expand permite
   bajar codigo; una contract bloquea el rollback hasta migrar atras).
3. Verificar salud, protocolo y metricas de jobs.
4. Documentar el incidente y prohibir promocion automatica hasta el analisis.

## 4. Backup

`pg_dump --format=custom` diario a las 02:00 UTC y previo a toda
migracion, destino en object storage separado y cifrado, retencion de
30 diarios mas 12 mensuales mas 4 anuales, checksum SHA-256 y prueba de
restauracion mensual en instancia aislada.

## Documentos relacionados

- [despliegue-worker.md](despliegue-worker.md)
- [plan de infra](../../../plans/06-infra/plan.md)
- [compose de desarrollo](../../../infra/compose/docker-compose.yml)
- [ejemplo de entorno](../../../infra/compose/.env.example)
