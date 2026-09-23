# libros_portal_iva (stub dev)

Port de `api-bots-mrbot-v2/app/bot/libros_portal_iva_bot.py`
(`bot_libros_portal_iva`) al contrato worker V3
(`bots/libros_portal_iva/`): operaciones `descargar_libros` y
`descargar_ddjj` sobre el Portal IVA (`siapweb.cloud.afip.gob.ar/iva`).

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `validate(payload_ejemplo.json)` falla antes del navegador si el rango
   `AAAAMM` es invalido; `generar_rango_periodos(desde, hasta)` es puro.
2. `configure({"portal_iva_url": "..."})` aplica la seccion `service`.
3. `execute` requiere `runtime` real (sesion ARCA + slots
   `libros_iva_archivo` / `ddjj_archivo`); con `subir_archivos=false`
   no se necesitan slots.

## Notas del port

- El resultado trae nombres, periodos y hashes, nunca URLs firmadas
  (V2 las recortaba con `_strip_minio_links`).
- Errores por categoria, diagnostico redactado con `sin_secretos`.
