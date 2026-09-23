# sct - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/sct_bot.py` (`bot_sct`).

- Operación: `consultar` (secciones vencimientos/deudas/ddjj_pendientes
  en formatos xlsx/csv/pdf).
- Los reportes viajan como artefactos con sha256; el JSON lleva solo
  el resumen (sin b64).
- `payload_ejemplo.json` usa `subir: false`: valida esquema y
  nomenclatura sin navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/sct_bot.py` y
  `api-bots-mrbot-v2/bots_dev/sct/`.
- Pendiente de integración: métodos del servicio
  (`seleccionar_representado`, `descargar_reporte`) los provee la
  sesión de navegador del runtime.
