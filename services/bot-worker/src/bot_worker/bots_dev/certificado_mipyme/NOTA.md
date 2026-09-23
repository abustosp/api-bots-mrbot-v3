# certificado_mipyme (stub dev)

Port V3 real de
`api-bots-mrbot-v2/app/bot/certificado_mipyme_bot.py` al contrato worker
S7 (`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.arca_session`; servicio
  `"LUFE"` (sobrescribible por job con `mipyme_servicio`).
- Secuencia portada de V2: combobox de representado → Seleccionar →
  link "Descargar Certificado MiPyME" → PDF `sepyme_<cuit>_<ts>.pdf` →
  subida por slot `certificado.pdf` (obligatorio, como el manifiesto F2).
- Sin `SessionLocal`, sin entorno, sin MinIO directo, sin `local_path`
  en el resultado, errores por categoría con diagnóstico redactado.
- `payload_ejemplo.json`: operación `descargar` (representante y clave
  viajan en las credenciales del sobre, no en el payload).
- Registro: `registry.py` conserva el stub F2 (no se toca por regla);
  el cableado del port real lo hace la central / el dueño del registro.
- Stub dev: `validate` y el nombrado son probables offline; el flujo
  con navegador requiere sobre sellado + slots de la central.
