# mis_facilidades - stub dev (no productivo)

Directorio de desarrollo del bot `mis_facilidades` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/mis_facilidades_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT y denominacion ficticios). Las credenciales fiscales nunca van en
  el payload: las provee el sobre sellado via `runtime.credentials`.
  Si se omite `representado_cuit`, se usa el CUIT representante.
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/mis_facilidades/`.
  Requiere sesion ARCA con el servicio "MIS FACILIDADES".

Nada de este directorio se ejecuta en el worker.
