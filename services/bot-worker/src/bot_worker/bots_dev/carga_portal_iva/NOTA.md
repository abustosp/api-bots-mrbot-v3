# carga_portal_iva (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/carga_portal_iva_bot.py` al contrato
worker S7 (`manifest` + `validate` + `execute` + `configure`).

- Efecto de escritura: idempotencia `CARGA`. El cliente solicita un ticket
  por archivo en `POST /api/v3/uploads` y envía cada `object_key` en el campo
  `*_key` correspondiente. El worker recibe nombres locales en `*_path`,
  valida los pares completos (≥1 sección) y usa los archivos bajo `work_dir`.
  Servicio `"PORTAL IVA"` (sobrescribible por job con
  `portal_iva_servicio`).
- Secuencia portada de V2: importar TXT ventas/compras (ambos archivos
  → 1 click, reintentos por sección) → aperturas CF/CF-Rest/DF/DF-Rest
  → total de crédito fiscal computable desde el CSV de CF.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, errores por
  categoría con diagnóstico redactado.
- `payload_ejemplo.json`: ejemplo local de la operación `cargar`; las rutas representan archivos ya materializados por el worker, no son campos que deba enviar el cliente.
- Stub dev: `validate` y `calcular_total_cf_csv` son probables offline;
  el flujo con navegador requiere sobre sellado de la central.
