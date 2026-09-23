# vep_ccma - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/vep_ccma_bot.py` (`bot_vep_ccma`).

- Operación: `generar` (VEP desde CCMA con volante y QR; efecto
  fiscal, clase EFECTO).
- PDF y QR viajan como artefactos con sha256; el JSON lleva totales,
  selección y metadatos (sin b64).
- `payload_ejemplo.json` usa `subir_pdf: false`: valida esquema sin
  navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/vep_ccma_bot.py` y
  `api-bots-mrbot-v2/bots_dev/vep_ccma/`.
- Pendiente de integración: métodos del servicio
  (`seleccionar_representado`, `listar_obligaciones`,
  `generar_volante`, `descargar_detalle`, `descargar_qr`) los provee
  la sesión de navegador del runtime.
