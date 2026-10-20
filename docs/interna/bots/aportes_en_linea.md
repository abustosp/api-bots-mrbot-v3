# Aportes en Línea: auditoría interna

## Flujo efectivo

1. Catálogo central: `aportes_en_linea/descargar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386`). La ruta canónica V3 acepta objeto genérico `payload` más `credentials` (`services/central-api/src/central_api/api/bots.py:194-208,748-817`).
2. Esquema operativo admite CUIT representado opcional, alias, `subir`, `incluir_base64`, credenciales legacy y `proxy_request`; restringe campos no reconocidos (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:32-104`). Si el CUIT se omite, el plugin usa el representante de credenciales (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:132-155`).
3. El plugin inicia sesión, abre el servicio, solicita Archivo Histórico y guarda XLS (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:145-195`). La interacción está implementada en el plugin, no en un adaptador aparte.
4. `subir=true` publica artefacto opcional hasta 50 MiB; devuelve huella y tamaño. Con `subir=false` sólo deja el nombre en `data`, no bytes/Base64 (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:197-220`; manifiesto `services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:90-112`).

## Normalización y resultado

CUIT elimina separadores comunes, requiere once dígitos y no calcula checksum (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:36-47,91-104`). `incluir_base64` y su alias se validan y excluyen del modelo validado, sin producir Base64 (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:61-67`). `data` contiene `operacion`, `representado_cuit`, `archivo`, y con publicación suma `sha256` y `size_bytes` (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:197-220`). `proxy_request` se acepta como `bool | None` sin cambiar política de proxy (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:74-90`; plugin `services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:163-168`). La central traslada credenciales legacy enviadas en payload a `credentials` (`services/central-api/src/central_api/security/credentials.py:303-370`).

## Discrepancias concretas

- La ruta canónica anidada V3 acepta `payload: dict` genérico y deja validación de negocio al plugin; los modelos de `bot_payloads.py` son espejos declarativos de rutas flat/compatibilidad. La ruta plana genera un modelo tipado antes de despacho (`services/central-api/src/central_api/api/bots.py:194-208,748-817`; `services/central-api/src/central_api/api/bot_routes.py:418-527,663-690`).
- El comentario del esquema dice que debe haber salida Base64 o subida (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:6-8`), pero no hay regla cruzada y el test acepta ambos flags `false` (`services/bot-worker/tests/test_aportes_en_linea_plugin.py:239-244`). Si se omite CUIT y se desactiva `subir`, sólo queda nombre sin artefacto descargable.
- JSON Schema operativo declara sólo operación, representado y subir, omitiendo Base64, proxy y aliases que el modelo sí acepta (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:110-120`). El test confirma que `incluir_base64=true` no devuelve contenido (`services/bot-worker/tests/test_aportes_en_linea_plugin.py:326-341`).
- El límite de 50 MiB es del artefacto producido y no equivale a validación previa del contenido de descarga (`services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:90-112,197-220`).

## Evidencia revisada

- Catálogo y ruta: `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- Alias flat y credenciales: `services/central-api/src/central_api/api/bot_routes.py:418-527,663-690`; `services/central-api/src/central_api/security/credentials.py:303-370`.
- Schema/plugin: `services/bot-worker/src/bot_worker/bots/aportes_en_linea/schema.py:32-120`; `services/bot-worker/src/bot_worker/bots/aportes_en_linea/plugin.py:90-220`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_aportes_en_linea_plugin.py:223-244,287-311,326-341`. Este análisis no ejecutó el portal; pruebas locales coordinadas en [validación](../validacion.md).