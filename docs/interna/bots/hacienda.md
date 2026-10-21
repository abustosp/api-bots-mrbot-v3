# Auditoría interna: hacienda

## Flujo esquema -> plugin -> portal

1. **Esquema:** estricto (`extra=forbid`); normaliza CUIT y fecha real `DD/MM/AAAA`, valida rango ascendente. Requiere CUIT representado, denominación y fechas. `por_emisor/por_receptor=true`, `incluir_json=true`, `subir_excel=true`, `descargar_pdf=true`. Al menos una dirección y una salida JSON/Excel deben quedar activas. `cuit_representante` es nullable/legacy; `eliminar_descargas`, proxy y headless son aceptados pero no controlan el comportamiento actual.
2. **Plugin:** manifest `consultar`, requiere credenciales y captcha; costo sugerido 2, idempotencia `LECTURA`; publica planilla consolidada y PDF opcionales. Abre primero el servicio de comprobantes, selecciona denominación, luego abre Hacienda. Ejecuta una consulta por emisor y otra por receptor si están activas.
3. **Portal real:** el adaptador deriva desde Mis Comprobantes/Comprobantes en Línea al servicio Hacienda y recorre el rango para leer filas; por defecto obtiene también PDFs individuales. No registra ni presenta operaciones fiscales.

## Resultados y faltantes

El estado superior es `result` (`OK`/`PARCIAL`/`ERROR`); `data` contiene CUIT, denominación, fechas y resumen por dirección: filas, muestra opcional, resumen PDF. Consolida filas en una planilla con hojas del consolidado y por dirección. La planilla se genera incluso con `subir_excel=false`; en ese caso no se publica como `files[]`, pero su nombre/filas/hojas siguen en `data`. PDF publicados son artefactos separados y no dependen de `subir_excel`. Los errores de descarga PDF por comprobante quedan en `pdf.errores` y no detienen otras filas. `incluir_json=false` solo suprime las muestras.

## Catálogo, fuentes y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:349,399,428-439`; costo publicado como metadata, clase `CONSULTA`. Alias plano `/hacienda/consulta`: `services/central-api/src/central_api/api/bot_compat.py:69`.
- Schema: `services/bot-worker/src/bot_worker/bots/hacienda/schema.py:30-167`; plugin: `services/bot-worker/src/bot_worker/bots/hacienda/plugin.py:185-316,318-473`; portal: `services/bot-worker/src/bot_worker/runtime/portals/hacienda.py:1-5,278-473`.
- Modelo descriptivo del catálogo/alias plano: `services/central-api/src/central_api/api/bot_payloads.py:71-84,173-180,487,529,568`; normalizador canónico de aliases upload: `services/central-api/src/central_api/security/credentials.py:227-281`; solo el alias plano omite nulls mediante su DTO en `services/central-api/src/central_api/api/bot_routes.py:521-529`.
- Pruebas leídas: `services/bot-worker/tests/test_hacienda_pdf_comprobantes.py`, `services/bot-worker/tests/test_portal_hacienda.py`, `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. **Default dependiente de ruta:** el descriptor V3 publica `subir_excel=true` (`services/central-api/src/central_api/api/bot_payloads.py:179`) y el esquema efectivo también default a true (`services/bot-worker/src/bot_worker/bots/hacienda/schema.py:83-86`, `services/bot-worker/src/bot_worker/bots/hacienda/plugin.py:395-409`). En el alias plano, `minio_upload` default a false (`services/central-api/src/central_api/api/bot_payloads.py:568`) y el normalizador lo transforma a `subir_excel` (`services/central-api/src/central_api/security/credentials.py:227-274`). La ruta canónica usa el default del esquema real; el alias plano puede producir distinto al omitir el flag.
2. **PDF no descrito por el catálogo:** la ejecución descarga PDFs por defecto (`services/bot-worker/src/bot_worker/bots/hacienda/schema.py:87-88`, `services/bot-worker/src/bot_worker/bots/hacienda/plugin.py:420-444`) y los publica individualmente; el esquema descriptivo del catálogo no enumera `descargar_pdf` (`services/central-api/src/central_api/api/bot_payloads.py:173-180`). La ruta genérica no valida contra ese DTO, pero la omisión pública dificulta descubrir y controlar esta salida.
3. El esquema efectivo valida la regla cruzada de al menos una consulta y una salida, pero el catálogo describe booleanos independientes sin `anyOf`/validador (`services/bot-worker/src/bot_worker/bots/hacienda/schema.py:30-167`, `services/central-api/src/central_api/api/bot_payloads.py:173-180`). La solicitud puede encolarse y fallar después al validarse para ejecución.
4. `eliminar_descargas`, `proxy_request`, `headless` aparecen en esquema sin efecto del cliente.
5. Aunque llamada `descargar_pdf` desactive archivos PDF, la consulta de filas sigue siendo lectura y la planilla se crea localmente incluso si no se publica.

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
