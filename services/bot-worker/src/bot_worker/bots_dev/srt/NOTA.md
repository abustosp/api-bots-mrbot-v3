# srt - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/srt_bot.py` (`bot_srt_alicuotas`).

- Operación: `consultar_alicuotas` (lote de 1 a 50 CUIT).
- El CAPTCHA se delega al perfil captcha del runtime (inyectado por la
  central); el plugin nunca lee claves del entorno.
- `payload_ejemplo.json` usa `subir_json: false`: valida esquema sin
  navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/srt_bot.py` y
  `api-bots-mrbot-v2/bots_dev/srt/`.
- Pendiente de integración: el método `consultar_cuit` lo provee la
  sesión de navegador del runtime tras `open_service`.
