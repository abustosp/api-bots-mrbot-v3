# rcel - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/rcel_bot.py` (`descargar_facturas`).

- Operación: `descargar` (facturas del rango en 0..N PDF).
- Sin cupo previo de artefactos: cada PDF se sube con `artifact_id`
  propio (`rcel_000.pdf`…); los slots sin URL se resuelven por presign
  contra la central antes del PUT. Con 0 facturas la corrida es OK.
- `payload_ejemplo.json` usa `subir_pdf: false`: valida esquema y
  nomenclatura sin navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/rcel_bot.py` y
  `api-bots-mrbot-v2/bots_dev/rcel/`.
- Pendiente de integración: métodos del servicio tras `open_service`
  (`seleccionar_representado`, `descargar_facturas`) los provee la
  sesión de navegador del runtime.
