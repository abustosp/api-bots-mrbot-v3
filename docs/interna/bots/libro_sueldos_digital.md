# Auditoría interna: libro_sueldos_digital

## Flujo esquema -> plugin -> portal

1. **Esquema:** valida períodos `AAAAMM` con mes 01–12, inclusivos y ascendentes; genera como máximo 1200 períodos, cortando el rango más largo sin error. CUIT elimina separadores; `representado_cuit` opcional y CUIT de sesión se normalizan a 11 dígitos. `incluir_json=true`, `subir_archivos=true`; `extra=allow` y `operacion` no aparece como requerido en el JSON Schema del bot.
2. **Plugin:** solo `descargar`; requiere credenciales fiscales y captcha; costo sugerido 2, clase de idempotencia `LECTURA`. Usa CUIT explícito o el CUIT de sesión, selecciona representado, lee el empleador efectivo y falla cerrado si no es CUIT válido o no coincide. Recorre todas las liquidaciones listadas y descarga el PDF de cada una.
3. **Portal real:** servicio “LIBRO DE SUELDOS DIGITAL”, apartado “Liquidaciones y DDJJ”; para cada período se pueden descargar varias liquidaciones. Descarga PDF existente, sin presentar/modificar declaraciones.

## Resultados y fallos parciales

El estado superior `result` es `OK`/`PARCIAL`/`ERROR`; `data` incorpora CUIT del empleador, rango, `periodos_solicitados`, `periodos_encontrados`, `liquidaciones_encontradas`, `liquidaciones_descargadas` y períodos con resultados. `data.detalle[]` opcional por liquidación contiene período, número, nombre de PDF y, si se publicó, hash, tamaño y `object_key`; cada fallo queda en el registro. No encontrar liquidaciones hace fallar; errores en todas las liquidaciones encontradas dan `ERROR`; mezcla éxito/error da `PARCIAL`, conserva artefactos exitosos; éxito completo da `OK`. `subir_archivos=false` suprime publicación pero no lectura/descarga; `incluir_json=false` omite detalle, no contadores.

## Catálogo, referencias y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:353,402,428-439`; `descargar` queda como `EFECTO` por regla de prefijo, aunque el manifest del bot declara `LECTURA` (`services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/plugin.py:91-120`) y el portal solo descarga. Alias `/libro-sueldos-digital/consulta`: `services/central-api/src/central_api/api/bot_compat.py:100`.
- Schema: `services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/schema.py:16-151`; plugin: `services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/plugin.py:91-120,139-253,255-368`; portal: `services/bot-worker/src/bot_worker/runtime/portals/libro_sueldos_digital.py:1-55,120-370`.
- Request model: `services/central-api/src/central_api/api/bot_payloads.py:71-84,181-186`; compatibilidad/credenciales: `services/central-api/src/central_api/api/bot_payloads.py:472-553,559-608`; nulls se omiten en `services/central-api/src/central_api/api/bot_routes.py:521-529`.
- Pruebas leídas: `services/bot-worker/tests/test_libro_sueldos_digital_plugin.py`, `services/bot-worker/tests/test_portal_libro_sueldos_digital.py`, `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. La clase usa `extra=allow` (`services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/schema.py:20-22`), a diferencia de otros bots estrictos; entradas no declaradas se conservan y pueden ocultar errores tipográficos.
2. `esquema_entrada()` requiere períodos, pero no lista `operacion` entre `required` (`services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/schema.py:138-151`); la operación sí queda fijada por la ruta y se incorpora antes de validar.
3. El rango se trunca a 1200 meses (`services/bot-worker/src/bot_worker/bots/libro_sueldos_digital/schema.py:49-66`) sin reflejar cantidad truncada.
4. El comentario del portal dice que se seleccionan 5 períodos por página (`services/bot-worker/src/bot_worker/runtime/portals/libro_sueldos_digital.py:10-12,26`), pero la constante es 10 y se usa en los enlaces de páginas (`services/bot-worker/src/bot_worker/runtime/portals/libro_sueldos_digital.py:27,268,325`).
5. Catálogo central clasifica `descargar` como `EFECTO` por no empezar con `consulta/consultar/historial`, pero manifest marca `LECTURA`; es una diferencia concreta de semántica de riesgo, no un cambio en el portal.
6. Aunque credenciales se requieren en ejecución, el schema interno de entrada no incluye `clave`; los campos viven en el sobre/contexto de autenticación.

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
