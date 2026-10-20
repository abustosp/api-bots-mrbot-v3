# Auditoría interna: facturometro

## Flujo esquema -> servicio -> portal

1. **Esquema:** `extra=forbid`; CUIT quita separadores y exige 11 dígitos. `representado_cuit` es nullable internamente; `incluir_captura=true` con aliases `carga_minio/subir_captura`; CUIT de sesión opcional con aliases; `proxy_request/headless` nullable. Los valores vacíos explícitos de CUIT pasan por limpieza y fallan, mientras null se permite.
2. **Plugin:** manifest declara `consultar`, requiere credenciales fiscales, sin captcha, costo 1 e idempotencia `LECTURA`. La ejecución, pese a lo que dice el encabezado del schema, requiere `runtime.credentials`; el CUIT objetivo se toma de `representado_cuit`, luego del CUIT de sesión en payload y por último del CUIT sellado. Lee `monto/tope/categoria`; por defecto captura panel y sube un PNG opcional.
3. **Portal:** abre el servicio ARCA `MONOTRIBUTO`, selecciona/verifica CUIT, lee los nodos visibles del Facturómetro y recurre al método AJAX del mismo sitio si no aparece monto. Si no encuentra monto, falla.

## Resultado

Con el estado superior `result=OK`, `data` devuelve `operacion`, `representado_cuit`, `monto`, `tope`, `categoria`, `screenshot_url` inicialmente null, y si se obtiene PNG incluye metadatos en `captura` y artefacto `files[]`. El campo `data.screenshot_url` se rellena con `object_key`, no con URL firmada. Si el panel no está presente y se solicitó captura, agrega warning sin artefacto. La lectura del servicio puede devolver valor string/null según respuesta del organismo.

## Fuentes, catálogo y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:348,398`; costo 1, clase `CONSULTA` (`:428-439`). Alias `/facturometro/consulta`: `services/central-api/src/central_api/api/bot_compat.py:101`.
- Esquema: `services/bot-worker/src/bot_worker/bots/facturometro/schema.py:1-103`; ejecución/manifest/captura: `services/bot-worker/src/bot_worker/bots/facturometro/plugin.py:70-96,116-232`.
- Portal: `services/bot-worker/src/bot_worker/runtime/portals/facturometro.py:1-25,36-135`.
- Request model: `services/central-api/src/central_api/api/bot_payloads.py:71-84,172,486,528,567`; la clase pública base permite extras en `:21-34`. Credenciales centralizadas: `services/central-api/src/central_api/security/credentials.py:303-370`.
- Pruebas leídas: `services/bot-worker/tests/test_portal_facturometro.py`, `services/central-api/tests/test_v2_request_alias_parity.py`, `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. El encabezado del esquema indica que no produce archivos (`services/bot-worker/src/bot_worker/bots/facturometro/schema.py:5-7`), aunque el manifest declara un PNG y `incluir_captura=true` intenta producirlo (`services/bot-worker/src/bot_worker/bots/facturometro/plugin.py:78-85,198-220`). El comentario del portal señala que el método de lectura no devuelve captura (`services/bot-worker/src/bot_worker/runtime/portals/facturometro.py:13-14`); el servicio obtiene la imagen por otra etapa. Alinear comentarios y contrato.
2. El esquema descriptivo del catálogo central marca `representado_cuit` requerido y no lista `incluir_captura` (`services/central-api/src/central_api/api/bot_payloads.py:172`), mientras el esquema efectivo permite omitir el CUIT y habilita la captura por default (`services/bot-worker/src/bot_worker/bots/facturometro/schema.py:20-47,58-72`). La ruta genérica no valida contra el DTO del catálogo, así que esto es una discrepancia de descripción pública, no un rechazo canónico previo. Publicar la opción de captura y el uso de CUIT de sesión.
3. El campo de resultado `data.screenshot_url` recibe un `object_key` (`services/bot-worker/src/bot_worker/bots/facturometro/plugin.py:198-220`), no URL. Renombrar en un modelo de resultado futuro o exponer el significado inequívoco sin ruptura.
4. El manifest declara que se requieren credenciales fiscales (`services/bot-worker/src/bot_worker/bots/facturometro/plugin.py:70-96`), aunque el encabezado del esquema lo describe como solo lectura y el catálogo no enumera credenciales como propiedades de la familia (`services/central-api/src/central_api/api/bot_payloads.py:172`). Describir credenciales como bloque del envelope, no como campos del payload.

## Propuestas sin implementar

- Alinear comentario del schema, artefacto manifest, bandera de captura y JSON Schema central.
- Resolver de manera explícita el desacuerdo entre `representado_cuit` requerido en API y opcional en validador.
- Mantener verificación de CUIT visible y prueba de captura ausente, sin volver a presentar una URL pública inexistente.
