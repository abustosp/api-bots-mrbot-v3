# Compensaciones: auditoría interna

## Flujo efectivo

1. Catálogo central expone `compensaciones/consultar` y ruta anidada de jobs V3 (`services/central-api/src/central_api/api/bots.py:329-339,378-386,748-817`). Su cuerpo sólo tipa `payload`/`credentials` como diccionarios genéricos (`services/central-api/src/central_api/api/bots.py:194-208`).
2. Worker acepta CUIT aliases, fechas `dd/mm/aaaa`, formatos y `subir`; exige al menos un formato y fechas reales ascendentes (`services/bot-worker/src/bot_worker/bots/compensaciones/schema.py:60-132`). También acepta campos legacy credencial/proxy/headless (`services/bot-worker/src/bot_worker/bots/compensaciones/schema.py:87-103`). Central traslada credenciales legacy hacia sobre separado (`services/central-api/src/central_api/security/credentials.py:303-370`).
3. Plugin inicia sesión, selecciona representado si difiere del titular, consulta y exporta (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:171-220,228-277`). El adaptador llena fechas, consulta y desencadena descarga XLS/CSV/PDF (`services/bot-worker/src/bot_worker/runtime/portals/compensaciones.py:18-44,46-113`). No inicia trámites ni modifica saldos.
4. Si falla un formato, intenta los demás. Si fallan todos, devuelve error. Con datos sin filas devuelve éxito y mensaje (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:213-227,228-273`).

## Envelope de salida

`data` siempre incluye `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta` (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:216-221`). Sin resultados añade `mensaje` y `message` de tipo texto (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:223-227`). Cada exportación exitosa añade `data.excel`, `data.csv` o `data.pdf` como objeto con `archivo` texto; si se sube suma `sha256` texto y `size_bytes` entero. También añade `compensaciones_{excel|csv|pdf}_minio_url` como string nullable (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:247-264`). Exportación incompleta incluye `data.warnings: string[]` y warnings del resultado (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:265-283`).

## Discrepancias concretas

- Espejo de `bot_payloads.py` para alias planos restringe patrón/formato de fecha, pero no valida fecha real, orden ni al menos un formato. Ruta anidada V3 no aplica ese modelo DTO por bot antes de encolar, así que reglas operativas llegan al worker (`services/central-api/src/central_api/api/bot_payloads.py:133-139`; `services/central-api/src/central_api/api/bots.py:194-208,748-817`; worker `services/bot-worker/src/bot_worker/bots/compensaciones/schema.py:49-57,118-132`).
- El mirror flat no refleja formato mínimo, condición de un archivo ni output urls; worker admite aliases `cuit`, `cuit_representado`, `desde`, `hasta`, `carga_minio`, `subir_archivo` (`services/central-api/src/central_api/api/bot_payloads.py:133-139`; `services/bot-worker/src/bot_worker/bots/compensaciones/schema.py:63-103`).
- Las URLs `*_minio_url` se crean con `null` antes de subir y se rellenan sólo al publicar; `files` del job es vía de recuperación (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:247-264`).
- Si un formato falla y otro no, `result` sigue `OK` con warning, no `PARCIAL` (`services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:265-283`).

## Evidencia revisada

- Catálogo/ruta: `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- DTO flat/credentials: `services/central-api/src/central_api/api/bot_payloads.py:133-139`; `services/central-api/src/central_api/security/credentials.py:303-370`.
- Esquema/plugin/portal: `services/bot-worker/src/bot_worker/bots/compensaciones/schema.py:31-153`; `services/bot-worker/src/bot_worker/bots/compensaciones/plugin.py:158-283`; `services/bot-worker/src/bot_worker/runtime/portals/compensaciones.py:18-113`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_portal_compensaciones.py`. Este análisis no ejecutó el portal; pruebas locales coordinadas en [validación](../validacion.md).
## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
