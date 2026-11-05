# worker-updater

Contenedor auxiliar que corre en cada host de worker **manual** y permite el
botón **Actualizar** / **Actualizar todos** del panel central.

El worker no puede actualizarse solo (sin docker socket, sin entorno, W-3). El
updater consulta a la central (`GET /internal/v1/updater/command`, solo
conexiones salientes) y, cuando hay una orden para su nodo, ejecuta:

1. valida que la imagen pertenezca a `UPDATER_ALLOWED_REPOS`;
2. `docker pull`;
3. reescribe `WORKER_IMAGE`, `APP_VERSION` y `WORKER_IMAGE_DIGEST` en el env file;
4. `docker compose up -d --no-deps bot-worker` (el worker drena con SIGTERM);
5. espera a que el contenedor esté `healthy`;
6. si falla, restaura los valores anteriores y reporta `REVERTIDO`.

Se activa con el perfil `updater` (`--profile updater`). Variables: ver
`infra/compose/worker.env.example` y el servicio `worker-updater` de
`infra/compose/worker.compose.yaml`. En la central: `UPDATER_TOKEN`,
`WORKER_IMAGE_REF`, `WORKER_LATEST_VERSION`, `WORKER_UPDATE_MAX_PARALLEL`.

Pruebas: `python -m pytest services/worker-updater/tests`.

## Autoactualización (`UPDATER_AUTO=1`)

Opt-in por host. Además del botón del panel, el updater pide `?auto=true` al consultar y la
central decide (un solo punto de control, mismas validaciones y rollback):

- Objetivo: la última versión del registry (`WORKER_IMAGE_REPO`) o `WORKER_IMAGE_REF`.
- **Se ignoran las versiones `-dev`**, tanto el objetivo como la instalada: un worker `-dev`
  nunca se toca solo.
- Si el worker está desactualizado y hay cupo (`WORKER_UPDATE_MAX_PARALLEL`), la central lo
  **pausa de inmediato** (sin jobs nuevos) y espera a que quede **ocioso**, sin el tope corto
  del botón. Sin cupo queda `ENCOLADA` y sigue trabajando hasta su turno: nunca se pausan más
  nodos que el cupo, así la flota conserva capacidad.
- Si no queda ocioso en `WORKER_UPDATE_AUTO_DRAIN_SECONDS` (2 h), se abandona y se reanuda.
- **Canario:** si una versión falla (`FALLIDO`/`REVERTIDO`) en cualquier nodo, no se propaga a
  otros ni se reintenta durante 1 h (`AUTO_REINTENTO`).
- Si el updater deja de consultar durante `WORKER_UPDATE_TIMEOUT_SECONDS`, la solicitud vence y
  el nodo se reanuda. También se puede cancelar desde el panel.
- Un `UPDATER_NODE` que no coincide con un worker registrado no hace nada: revisar que sea el
  mismo `host:puerto` que muestra el panel.
- **Ventana horaria:** `WORKER_UPDATE_AUTO_WINDOW="02:00-05:00"` (hora de Buenos Aires, admite
  cruzar medianoche) limita cuándo puede *iniciar* una autoactualización; no corta una en curso.
  Vacío = sin restricción; un valor inválido bloquea (no actualiza).
- **Registry caído:** con `WORKER_IMAGE_REPO` definido, si no se puede obtener la última versión
  no se cae a `WORKER_IMAGE_REF` (podría estar atrasado): simplemente no actualiza.
- **Reinicio de la central:** el historial de solicitudes (en memoria) se pierde, pero la pausa
  de actualización también, así que ningún nodo queda pausado; si sigue desactualizado se
  vuelve a evaluar en el siguiente poll.

### Workers relay (detrás de NAT)

`worker-relay.compose.yaml` incluye el servicio `worker-updater` (perfil `updater`). Completar
las variables `UPDATER_*` de `worker-relay.env.example` y levantar con `--profile updater`.
`UPDATER_NODE` es `relay:<puerto>`, tal como lo ve la central.
