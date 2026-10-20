# Auditoría interna: facturometro

## Flujo esquema -> servicio -> portal

1. **Esquema:** `extra=forbid`; CUIT quita separadores y exige 11 dígitos. `representado_cuit` es nullable internamente; `incluir_captura=true` con aliases `carga_minio/subir_captura`; CUIT de sesión opcional con aliases; `proxy_request/headless` nullable. Los valores vacíos explícitos de CUIT pasan por limpieza y fallan, mientras null se permite.
2. **Plugin:** manifest declara `consultar`, requiere credenciales fiscales, sin captcha, costo 1 e idempotencia `LECTURA`. La ejecución, pese a lo que dice el encabezado del schema, requiere `runtime.credentials`; el CUIT objetivo se toma de `representado_cuit`, luego del CUIT de sesión en payload y por último del CUIT sellado. Lee `monto/tope/categoria`; por defecto captura panel y sube un PNG opcional.
3. **Portal:** abre el servicio ARCA `MONOTRIBUTO`, selecciona/verifica CUIT, lee los nodos visibles del Facturómetro y recurre al método AJAX del mismo sitio si no aparece monto. Si no encuentra monto, falla.

## Resultado

`result=OK` devuelve `operacion`, `representado_cuit`, `monto`, `tope`, `categoria`, `screenshot_url` inicialmente null, y si se obtiene PNG incluye metadatos en `captura` y artefacto `files[]`. El campo `screenshot_url` se rellena con `object_key`, no con URL firmada. Si el panel no está presente y se solicitó captura, agrega warning sin artefacto. La lectura del servicio puede devolver valor string/null según respuesta del organismo.

## Fuentes, catálogo y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:348,398`; costo 1, clase `CONSULTA` (`:428-439`). Alias `/facturometro/consulta`: `services/central-api/src/central_api/api/bot_compat.py:101`.
- Esquema: `services/bot-worker/src/bot_worker/bots/facturometro/schema.py:1-103`; ejecución/manifest/captura: `services/bot-worker/src/bot_worker/bots/facturometro/plugin.py:70-96,116-232`.
- Portal: `services/bot-worker/src/bot_worker/runtime/portals/facturometro.py:1-25,36-135`.
- Request model: `services/central-api/src/central_api/api/bot_payloads.py:71-84,172,486,528,567`; la clase pública base permite extras en `:21-34`. Credenciales centralizadas: `services/central-api/src/central_api/security/credentials.py:303-370`.
- Pruebas leídas: `services/bot-worker/tests/test_portal_facturometro.py`, `services/central-api/tests/test_v2_request_alias_parity.py`, `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. El encabezado del schema dice que no produce archivos (`schema.py:5-7`) mientras el manifest declara un PNG (`plugin.py:78-85`) y el default `incluir_captura=true` lo intenta producir. El comentario del portal también dice “Sin ... screenshot” (`runtime/portals/facturometro.py:13-14`), aunque el plugin captura el panel fuera de ese método. Actualizar los comentarios/contrato, no quitar el comportamiento sin decisión de producto.
2. La familia pública central solo incluye `representado_cuit` requerido (`bot_payloads.py:172`), mientras no publica `incluir_captura`; el request model acepta extras (`CompatBodyBase.extra=allow`), así que la bandera puede llegar al proceso pese a faltar en OpenAPI. Exponer captura/default/alias formalmente.
3. En sentido opuesto, el esquema de entrada permite omitir `representado_cuit` y usar CUIT de sesión, pero el request model central lo exige. Hacer pública la alternativa o alinear requiredness.
4. El campo de resultado `screenshot_url` recibe un `object_key` (`plugin.py:198-220`), no URL. Renombrar en un modelo de resultado futuro o exponer el nombre inequívoco sin ruptura.
5. Manifest declara `requiere_credenciales_fiscales=True`, aunque el encabezado del schema llama al bot de solo lectura y el modelo central no enumera credenciales bajo la familia. Asegurar que OpenAPI explique credenciales como bloque separado y no como propiedades del payload.

## Propuestas sin implementar

- Alinear comentario del schema, artefacto manifest, bandera de captura y JSON Schema central.
- Resolver de manera explícita el desacuerdo entre `representado_cuit` requerido en API y opcional en validador.
- Mantener verificación de CUIT visible y prueba de captura ausente, sin volver a presentar una URL pública inexistente.
