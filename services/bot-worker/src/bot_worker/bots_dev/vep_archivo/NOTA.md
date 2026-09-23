# vep_archivo - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/vep_archivo_bot.py`.

- Operación: `generar` (VEP desde .txt; efecto fiscal, clase EFECTO).
- Sin pandas en el worker: el .txt se valida en stdlib (cabecera `01`
  + registros `02`, hasta 600 por lote).
- `payload_ejemplo.json` trae un lote mínimo válido (1 registro) con
  `subir_pdf: false`: valida esquema y archivo sin navegador.
- Referencia V2: `api-bots-mrbot-v2/app/bot/vep_archivo_bot.py` y
  `api-bots-mrbot-v2/bots_dev/vep_archivo/`.
- Pendiente de integración: métodos del servicio
  (`generar_vep_desde_archivo`, `descargar_detalle`) los provee la
  sesión de navegador del runtime.
