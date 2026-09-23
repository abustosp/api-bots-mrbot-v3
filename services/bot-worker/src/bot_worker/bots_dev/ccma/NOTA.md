# ccma (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/ccma_bot.py` al contrato worker S7
(`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.arca_session`; servicio
  `"CCMA"` (sobrescribible por job con `ccma_servicio`).
- Secuencia portada de V2: Elegir CUIT → período desde + CALCULO DE
  DEUDA → resumen de saldos (deuda/crédito capital+accesorios) →
  movimientos paginados (tope 500) → PDF opcional por slot
  `ccma_resumen.pdf`. `periodo_desde` configurable (default `04/2000`,
  igual que V2); `incluir_pdf` activa movimientos automáticamente.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, sin HTML crudo
  en el resultado, errores por categoría con diagnóstico redactado.
- `payload_ejemplo.json`: operación `consultar`.
- Stub dev: `validate` es probable offline; el flujo con navegador
  requiere sobre sellado + slots de la central.
