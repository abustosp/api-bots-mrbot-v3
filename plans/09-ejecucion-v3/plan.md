# Plan 09: ejecución de la V3 y entorno de prueba

> **Estado:** activo para el primer ciclo de implementación.
>
> **Objetivo:** convertir el diseño de `api-bots-mrbot-v3` en un vertical slice
> ejecutable, versionado y desplegable en un host aislado sin afectar la V2.

## 1. Estado de partida

La base ya contiene las fundaciones F0 y una implementación inicial del plano
central y del worker:

- contratos Pydantic compartidos en `packages/mrbot-contracts`;
- `central-api` con API pública, API interna, store de desarrollo, repositorios
  PostgreSQL, scheduler push, leases y seguridad de asignaciones;
- `bot-worker` sin driver ORM, con límite de cinco ejecuciones, drenaje,
  manifiesto de plugins y reporte de salud/resultados;
- Compose de desarrollo, migraciones Alembic, controles de aislamiento y
  certificados mTLS locales;
- un stub de `consulta_cuit` para probar el transporte sin depender de un
  servicio externo.

La suite local disponible en este corte pasa **145 pruebas y 12 subtests**. Esa
señal valida contratos y fronteras, pero todavía no reemplaza una ejecución E2E
con PostgreSQL y Compose en un host limpio.

## 2. Orden de implementación

### F2.1: vertical slice de control y ejecución

1. levantar PostgreSQL, central y un worker en Compose;
2. aplicar la migración como job one-off;
3. registrar el worker y comprobar heartbeat;
4. crear un job `consulta_cuit` con una clave de desarrollo;
5. asignarlo por scheduler push;
6. ejecutar el stub de constancias;
7. recibir resultado, consultar el job y comprobar lease, idempotencia y
   aislamiento de secretos;
8. repetir con dos workers y verificar que la capacidad máxima por worker sigue
   siendo cinco.

### F2.2: persistencia y operación

- eliminar los caminos silenciosos de fallback en los flujos que deben ser
  durables cuando `DATABASE_URL` está configurada;
- agregar una prueba E2E que mate un worker y observe el reencolado por lease;
- agregar una prueba E2E de idempotencia después de un estado terminal;
- documentar métricas mínimas: jobs pendientes, asignados, fallidos, latencia
  de heartbeat y workers sin capacidad.

### F3: portado controlado de bots

El orden inicial es `consulta_cuit`, `mis_comprobantes` y luego los bots por
familia. Cada portado debe conservar el contrato de entrada validado, usar
únicamente `BotRuntime`, declarar sus artefactos y tener una prueba de
validación sin navegador más una prueba de ejecución aislada.

### F4-F6

Facturación, panel administrativo, observabilidad, pruebas de carga, caos y
cutover V2 quedan bloqueados hasta que F2.1 tenga evidencia en el host de
prueba.

## 3. Host de prueba `abp@conciliabot2`

El despliegue de prueba debe ser paralelo a la V2 y no debe reutilizar su
directorio, volumen, red ni nombre de proyecto Compose.

Directorio previsto:

```text
$HOME/api-bots-mrbot-v3/
```

Reglas operativas:

1. clonar el repositorio por SSH dentro de esa carpeta;
2. usar un nombre de proyecto Compose exclusivo, `mrbot-v3`;
3. crear `.env` y `secrets/` desde los ejemplos, nunca copiarlos al Git;
4. generar certificados locales o provisionar certificados del entorno;
5. validar `docker compose config` antes de arrancar;
6. aplicar migraciones con `--profile migrate`;
7. arrancar primero central y luego worker;
8. publicar central únicamente en un bind local o detrás del proxy de prueba;
9. no ejecutar comandos de cutover, migración V2 ni limpieza de volúmenes en
   este ciclo.

El primer despliegue debe usar el stub de constancias. El endpoint público de
constancias solo se habilita en un segundo paso explícito y con credenciales
del entorno de prueba.

## 4. Puertas de aceptación

Antes de cada push:

```bash
make check
```

Antes de arrancar el host remoto:

```bash
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.yml --profile migrate config
```

Después del arranque:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
docker compose -p mrbot-v3 ps
docker compose -p mrbot-v3 logs --no-color --tail=200 central-api bot-worker
```

La evidencia de cada ciclo debe registrar commit, imagen, resultado de
migración, salud de la central, heartbeat del worker, job de prueba y estado de
los contenedores. No se deben registrar claves, credenciales ni payloads
sellados.

