# retper_iibb_misiones - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/retper_iibb_misiones_bot.py`.

- Operación: `consultar` (retenciones/percepciones ATM Misiones).
- El CAPTCHA se delega al perfil captcha del runtime (inyectado por la
  central); el plugin nunca lee claves del entorno.
- `payload_ejemplo.json` usa `subir_archivo: false`: valida esquema y
  nomenclatura sin navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/retper_iibb_misiones_bot.py`
  y `api-bots-mrbot-v2/bots_dev/retper_iibb_misiones_capmonster/`.
- Pendiente de integración: métodos del servicio (`ingresar`,
  `consultar_retper`, `generar_pdf`) los provee la sesión del runtime.
