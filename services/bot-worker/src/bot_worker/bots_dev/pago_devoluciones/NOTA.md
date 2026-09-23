# pago_devoluciones - stub dev (no productivo)

Directorio de desarrollo del bot `pago_devoluciones` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/pago_devoluciones_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT ficticio). Las credenciales fiscales nunca van en el payload:
  las provee el sobre sellado via `runtime.credentials`.
  Si se omite `representado_cuit`, se usa el CUIT representante.
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/pago_devoluciones/`.
  Requiere sesion ARCA con el servicio "PAGO DEVOLUCIONES".

Nada de este directorio se ejecuta en el worker.
