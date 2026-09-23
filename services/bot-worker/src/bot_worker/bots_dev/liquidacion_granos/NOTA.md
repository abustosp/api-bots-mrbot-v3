# liquidacion_granos - stub dev (no productivo)

Directorio de desarrollo del bot `liquidacion_granos` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/liquidacion_granos_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT y denominacion ficticios). Las credenciales fiscales nunca van en
  el payload: las provee el sobre sellado via `runtime.credentials`.
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/liquidacion_granos/`.
  Requiere sesion ARCA con el servicio "LIQUIDACIÓN PRIMARIA DE GRANOS".

Nada de este directorio se ejecuta en el worker.
