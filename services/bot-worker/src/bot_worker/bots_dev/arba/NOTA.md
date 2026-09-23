# arba (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/arba_bot.py` al contrato worker S7
(`manifest` + `validate` + `execute` + `configure`).

- Con navegador vía `runtime.browser_factory.new_context()` (ARBA usa
  login SSO propio, no sesión ARCA). URLs inyectables por job:
  `arba_login_url`, `arba_consulta_url` en la sección `service`.
- Secuencia portada de V2: login SSO → rol Contribuyente → consulta
  año/mes (doble intento) → "No existen archivos" como OK vacío →
  descarga zip `"<fin> - <cuit> - RETPER ARBA - <AAAAMM> -
  <denominación>.zip"` → subida por slot `retper_arba.zip`.
- Sin `SessionLocal`, sin entorno, sin MinIO directo, credenciales
  rechazadas como categoría (no texto del organismo en crudo).
- `payload_ejemplo.json`: operación `descargar` con período `AAAAMM`.
- Stub dev: `validate` y el nombrado son probables offline; el flujo con
  navegador requiere sobre sellado + slots de la central.
