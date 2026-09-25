# carga_portal_iva (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/carga_portal_iva_bot.py` al contrato
worker S7 (`manifest` + `validate` + `execute` + `configure`).

- Efecto de escritura: idempotencia `CARGA`. Los TXT/CSV llegan como
  base64 en el payload (el worker no lee rutas del cliente), se validan
  en el esquema (pares completos, ≥1 sección) y se materializan bajo
  `work_dir`. Servicio `"PORTAL IVA"` (sobrescribible por job con
  `portal_iva_servicio`).
- Secuencia portada de V2: importar TXT ventas/compras (ambos archivos
  → 1 click, reintentos por sección) → aperturas CF/CF-Rest/DF/DF-Rest
  → total de crédito fiscal computable desde el CSV de CF.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, errores por
  categoría con diagnóstico redactado.
- `payload_ejemplo.json`: operación `cargar`, con base64 sintácticamente válido para pruebas de esquema. Los bytes son ilustrativos, no documentos ARCA válidos; reemplazarlos por los archivos reales antes de una carga.
- Stub dev: `validate` y `calcular_total_cf_csv` son probables offline;
  el flujo con navegador requiere sobre sellado de la central.
