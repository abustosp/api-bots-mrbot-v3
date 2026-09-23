# controladores_fiscales (stub dev)

Port de `api-bots-mrbot-v2/app/bot/controladores_fiscales_bot.py` al contrato
worker V3 (`bots/controladores_fiscales/`): operacion `presentar` sobre
Presentación DDJJ y Pagos, con descarga de constancias PDF.

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `ControladoresFiscalesPlugin().validate(payload_ejemplo.json)` valida
   nombres de archivo simples (sin rutas) antes del navegador.
2. En V3 los archivos a presentar llegan como adjuntos del sobre bajo
   `work_dir` (ya no `archivos_dir`/`descargas_dir` del host): para una
   prueba real, el archivo del ejemplo debe existir en `work_dir`.
3. `configure({"servicio": "..."})` aplica la seccion `service` del sobre
   sobre una copia del plugin.

## Notas del port

- La presentacion tiene efecto fiscal: `idempotency_class="EFECTO"`.
- Cada constancia se sube por slot prefirmado (`constancia_pdf`).
- Errores por categoria, diagnostico redactado con `sin_secretos`.
