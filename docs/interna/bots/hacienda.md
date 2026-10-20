# Auditoría interna: hacienda

## Flujo esquema -> plugin -> portal

1. **Esquema:** estricto (`extra=forbid`); normaliza CUIT y fecha real `DD/MM/AAAA`, valida rango ascendente. Requiere CUIT representado, denominación y fechas. `por_emisor/por_receptor=true`, `incluir_json=true`, `subir_excel=true`, `descargar_pdf=true`. Al menos una dirección y una salida JSON/Excel deben quedar activas. `cuit_representante` es nullable/legacy; `eliminar_descargas`, proxy y headless son aceptados pero no controlan el comportamiento actual.
2. **Plugin:** manifest `consultar`, requiere credenciales y captcha; costo sugerido 2, idempotencia `LECTURA`; publica planilla consolidada y PDF opcionales. Abre primero el servicio de comprobantes, selecciona denominación, luego abre Hacienda. Ejecuta una consulta por emisor y otra por receptor si están activas.
3. **Portal real:** el adaptador deriva desde Mis Comprobantes/Comprobantes en Línea al servicio Hacienda y recorre el rango para leer filas; por defecto obtiene también PDFs individuales. No registra ni presenta operaciones fiscales.

## Resultados y faltantes

`result` contiene CUIT, denominación, fechas y resumen por dirección: filas, muestra opcional, resumen PDF. Consolida filas en una planilla con hojas del consolidado y por dirección. La planilla se genera incluso con `subir_excel=false`; en ese caso no se publica como `files[]`, pero su nombre/filas/hojas siguen en el resultado. PDF publicados son artefactos separados y no dependen de `subir_excel`. Los errores de descarga PDF por comprobante quedan en `pdf.errores` y no detienen otras filas. `incluir_json=false` solo suprime las muestras.

## Catálogo, fuentes y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:349,399`; costo 2, clasificación `CONSULTA` por `:428-439`. Alias legacy `/hacienda/consulta`: `services/central-api/src/central_api/api/bot_compat.py:69`.
- Schema: `services/bot-worker/src/bot_worker/bots/hacienda/schema.py:30-167`; plugin: `services/bot-worker/src/bot_worker/bots/hacienda/plugin.py:185-316,318-473`; portal: `services/bot-worker/src/bot_worker/runtime/portals/hacienda.py:1-5,278-...`.
- Modelo V3/legacy: `services/central-api/src/central_api/api/bot_payloads.py:71-84,173-180,487,529,568`; normalizador de aliases upload: `services/central-api/src/central_api/security/credentials.py:227-281`; compatibilidad omite nulls `services/central-api/src/central_api/api/bot_routes.py:521-529`.
- Pruebas leídas: `services/bot-worker/tests/test_hacienda_pdf_comprobantes.py`, `services/bot-worker/tests/test_portal_hacienda.py`, `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. **Default dependiente de ruta:** V3 público declara `subir_excel=true` (`bot_payloads.py:179`), esquema/plugin también true (`hacienda/schema.py:83-86`, `hacienda/plugin.py:395-409`). En compatibilidad V2, `minio_upload` default `false` (`bot_payloads.py:568`), y el normalizador transforma ese indicador a `subir_excel` (`security/credentials.py:227-274`). Una solicitud V2 omitida publica distinto de una V3 canónica omitida.
2. **PDF no descrito por el modelo público:** ejecución descarga PDFs por defecto (`schema.py:87-88`, `plugin.py:420-444`) y los publica individualmente; la familia central V3 no enumera `descargar_pdf` (`bot_payloads.py:173-180`). La base de request admite extras (`bot_payloads.py:21-34`), de modo que el campo puede ser pasado aunque falte en OpenAPI.
3. El plugin valida la regla cruzada de al menos una consulta y una salida, pero el request model V3 enumera los booleanos sin `anyOf`/validador (`bot_payloads.py:173-180`). Puede encolarse y fallar después.
4. `eliminar_descargas`, `proxy_request`, `headless` aparecen en esquema sin efecto del cliente. Conviene marcarlos legacy/ignorados y no listarlos como controles activos.
5. Aunque llamada `descargar_pdf` desactive archivos PDF, la consulta de filas sigue siendo lectura y la planilla se crea localmente incluso si no se publica. Mantener en pruebas el caso de ambas salidas no publicadas pero `incluir_json=true` para fijar significado.

## Propuestas sin implementar

- Unificar default de subida entre V3 y alias V2 o dejar la diferencia explícita en el catálogo/compatibilidad.
- Publicar `descargar_pdf` y validaciones cruzadas en el modelo central; decidir si los PDF individuales deben tener una bandera de publicación separada de descarga.
- Sustituir los campos de control inactivos por un contrato explícito de compatibilidad.
