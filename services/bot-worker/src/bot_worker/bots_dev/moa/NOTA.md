# moa - stub dev (no productivo)

Directorio de desarrollo del bot `moa` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/moa_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT y despachos ficticios). Las credenciales fiscales nunca van en
  el payload: las provee el sobre sellado via `runtime.credentials`.
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/moa/`.
  Requiere sesion ARCA con el servicio "MOA – REINGENIERIA"
  (apertura por popup desde "Ver todos" en V2).

Nada de este directorio se ejecuta en el worker.
