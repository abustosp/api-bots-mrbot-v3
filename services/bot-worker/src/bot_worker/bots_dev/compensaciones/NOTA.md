# compensaciones (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/compensaciones_bot.py` al contrato
worker S7 (`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.arca_session`; servicio
  `"SISTEMA DE CUENTAS"` (sobrescribible por job con
  `compensaciones_servicio`).
- Secuencia portada de V2: submodulo Consulta Compensaciones y
  Afectaciones → filtros desde/hasta + CONSULTAR → "sin resultados"
  como OK con mensaje → exportación XLS/CSV/PDF a `work_dir` con nombre
  `sct_compensaciones_<cuit>_<ts>_<slot>` → subida por slots
  `compensaciones.xls/.csv/.pdf`.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, errores por
  categoría con diagnóstico redactado.
- `payload_ejemplo.json`: operación `consultar` (≥1 formato exigido).
- Stub dev: `validate` y el nombrado son probables offline; el flujo
  con navegador requiere sobre sellado + slots de la central.
