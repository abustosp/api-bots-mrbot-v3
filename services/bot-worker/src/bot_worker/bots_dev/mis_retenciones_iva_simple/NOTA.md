# mis_retenciones_iva_simple - stub dev (no productivo)

Directorio de desarrollo del bot `mis_retenciones_iva_simple` (ola 3).
Porta `api-bots-mrbot-v2/app/bot/mis_retenciones_iva_simple_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `consultar`
  (CUIT y denominacion ficticios). Las credenciales fiscales nunca van en
  el payload: las provee el sobre sellado via `runtime.credentials`.
  Regla propia de IVA Simple: `fecha_hasta` no puede superar el ultimo
  dia del mes siguiente a `fecha_desde`.
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/mis_retenciones_iva_simple/`.
  Requiere sesion ARCA con el servicio "MIS RETENCIONES" en modo
  IVA Simple.

Nada de este directorio se ejecuta en el worker.
