# compensaciones (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/compensaciones_bot.py` al contrato
worker S7 (`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.arca_session`; servicio
  `"SISTEMA DE CUENTAS"` (sobrescribible por job con
  `compensaciones_servicio`).
- Secuencia portada de V2: submodulo Consulta Compensaciones y
  Afectaciones → filtros desde/hasta + CONSULTAR → "sin resultados"
  como OK con mensaje → exportación XLS/CSV/PDF a `work_dir` con nombre
  `sct_compensaciones_<cuit>_<AAAAMMDD_HHMMSS>.<ext>` (patron V1) →
  subida por slots `compensaciones.xls/.csv/.pdf`. Campos de resultado de
  V1: `compensaciones_<excel|csv|pdf>_minio_url` (object_key del slot) y
  `message` cuando no hay resultados; un formato que falla no aborta el
  resto (V1 seguia con los demas).
- Sin `SessionLocal`, sin entorno, sin MinIO directo, errores por
  categoría con diagnóstico redactado.
- `payload_ejemplo.json`: operación `consultar` (≥1 formato exigido).
- Stub dev: `validate` y el nombrado son probables offline; el flujo
  con navegador requiere sobre sellado + slots de la central.
