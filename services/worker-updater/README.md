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

Variables: ver `infra/compose/worker.env.example` y el servicio `worker-updater`
de `infra/compose/worker.compose.yaml`. En la central: `UPDATER_TOKEN`,
`WORKER_IMAGE_REF`, `WORKER_LATEST_VERSION`, `WORKER_UPDATE_MAX_PARALLEL`.

Pruebas: `python -m pytest services/worker-updater/tests`.
