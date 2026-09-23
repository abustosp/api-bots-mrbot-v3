# siper - nota dev (stub)

Port V3 real de `api-bots-mrbot-v2/app/bot/siper_bot.py` (`bot_siper`).

- Reemplaza al stub F2 `SiperPlugin` de `registry.py` (ola 3): la
  clase se llama `SiperRealPlugin` para no colisionar hasta que la
  central la registre bajo el nombre canónico `siper`.
- Operación: `consultar` (vistas detalle/categorías como PNG).
- Las capturas viajan como artefactos con sha256; el JSON lleva solo
  metadatos (sin b64).
- `payload_ejemplo.json` usa `subir: false`: valida esquema sin
  navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/siper_bot.py` y
  `api-bots-mrbot-v2/bots_dev/siper/`.
- Pendiente de integración: métodos del servicio
  (`seleccionar_representado`, `capturar_vista`) los provee la sesión
  de navegador del runtime.
