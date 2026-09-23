# mis_retenciones - stub dev (no productivo)

Directorio de desarrollo del bot `mis_retenciones` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/mis_retenciones_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT y denominacion ficticios). Las credenciales fiscales nunca van en
  el payload: las provee el sobre sellado via `runtime.credentials`.
  Codigos de impuesto validos: 216, 217, 219, 353, 767 y 787
  (con `exportar_para_aplicativo` rige el set SIAP, sin 787).
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/mis_retenciones/`.
  Requiere sesion ARCA con el servicio "MIS RETENCIONES".

Nada de este directorio se ejecuta en el worker.
