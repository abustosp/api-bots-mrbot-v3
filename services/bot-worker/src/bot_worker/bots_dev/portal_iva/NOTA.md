# portal_iva - stub dev (no productivo)

Directorio de desarrollo del bot `portal_iva` (ola 3, plan 07).
Porta `api-bots-mrbot-v2/app/bot/portal_iva_bot.py`.

- `payload_ejemplo.json`: ejemplo de entrada para la operacion `descargar`
  (CUIT ficticio, periodo `06/2025` que se normaliza a `202506`).
  Las credenciales fiscales nunca van en el payload: las provee el sobre
  sellado via `runtime.credentials`. Si se omite `representado_cuit`,
  se usa el CUIT representante.
  Operaciones: `descargar` (CSV ventas/compras), `importar` (requiere
  `ventas_txt` y/o `compras_txt` inline) y `gestionar` (ambas fases).
- El plugin productivo vive en
  `services/bot-worker/src/bot_worker/bots/portal_iva/`.
  Requiere sesion ARCA con el servicio "Portal IVA". La operacion
  `importar`/`gestionar` muta el organismo: idempotencia `CARGA`.

Nada de este directorio se ejecuta en el worker.
