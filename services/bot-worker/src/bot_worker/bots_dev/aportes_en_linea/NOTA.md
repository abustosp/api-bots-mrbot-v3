# aportes_en_linea (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/aportes_en_linea_bot.py` al contrato
worker S7 (`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.arca_session`; servicio
  `"APORTES EN LÍNEA"` (sobrescribible por job con `aportes_servicio` en
  la sección `service` del sobre sellado).
- Secuencia portada de V2: INGRESAR → "Archivo Histórico" (popup que se
  cierra) → descarga `.xls` a `work_dir` con nombre `AEL - <cuit> -
  <AAAAMMDD>.xls` → subida por slot `historico.xls`.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, sin credenciales
  en logs (errores por categoría).
- `payload_ejemplo.json`: operación `descargar`. Sin `representado_cuit`
  se usa el CUIT de la credencial, igual que V2.
- Stub dev: `validate` y el nombrado son probables offline; el flujo con
  navegador requiere sobre sellado + slots de la central.
