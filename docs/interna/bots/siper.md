# Auditoría interna: `siper`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/siper/schema.py:18-95` normaliza CUIT, flags y salida. El JSON Schema de entrada figura en `:104-...`; no publica `extraer_campos`, aunque el modelo sí lo admite.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/siper/plugin.py:148-208` valida, requiere credenciales y abre el servicio; `:210-281` captura vistas solicitadas, sube imágenes opcionalmente y extrae datos de detalle con acumulación de error no fatal.
3. **Portal real.** `services/bot-worker/src/bot_worker/runtime/portals/siper.py:17-32` define páginas/campos; `:90-141` extrae campos estructurados; `:149-206` captura pantalla de las vistas detalle/categorías. La selección del representado sucede antes de consulta en el plugin.

## Catálogo, normalización y secuencia central

`services/central-api/src/central_api/api/bots.py:417` registra `siper/consultar`, clasificada `CONSULTA` por `:428-438`. Alias `/api/v3/siper/consulta`: `services/central-api/src/central_api/api/bot_compat.py`.

La ruta canónica pasa por `normalize_v2_payload` también: `services/central-api/src/central_api/api/bots.py:574,608-618,793-817` y `services/central-api/src/central_api/security/credentials.py:74-103,138-158`. El normalizador recibe únicamente el mapping de `payload`, no `credentials`; el handler mantiene separados ambos objetos. La extracción de credenciales se produce después. Por ello, el fallback a `representado_cuit` desde `cuit_representante`/`cuit_inicio_sesion` solo puede operar si el alias llegó dentro de payload antes de separarse; `credentials.cuit_representante` por sí solo no satisface el campo obligatorio del schema. Para la ruta canónica debe enviarse `payload.representado_cuit` explícito.

## Normalización, datos y parciales

- CUIT pierde separadores comunes. En el modelo de entrada `incluir_detalle`, `incluir_categorias`, `incluir_json`, `subir`, `extraer_campos` tienen default declarado `true`; pero la normalización API establece vistas detalle/categorías a `true` y, cuando no existen `subir` ni banderas `detalle_minio`/`categorias_minio`, establece `subir=false` incluso en ruta canónica (`credentials.py:138-158`). El default efectivo de publicación API, por tanto, difiere del modelo directo.
- Si alguna bandera histórica de captura vale true, se usa `subir=true` solo mediante `setdefault`; un `subir` explícito prevalece. Las banderas antiguas se eliminan antes del schema. `incluir_json=false` elimina `vistas`, pero `campos` puede seguir apareciendo.
- `data` incluye `operacion`, `representado_cuit`, `vistas` si JSON está activo, y `campos` si extracción está activa. Cada vista incluye nombre (`detalle`/`categorias`), archivo, ancho/alto, y metadatos hash/tamaño cuando se publica. `campos` posibles: contribuyente, categoría SIPER, motivo, fecha de última categoría y estado del trámite de disconformidad (`plugin.py:224-281`).
- Error de extracción se conserva como `PARCIAL` junto a las capturas producidas. El fallo de captura aborta; la extracción puede seguir consultando detalle aun con `incluir_detalle=false` si `extraer_campos=true` (`plugin.py:231-281`).

## Discrepancias concretas y propuestas (sin implementar)

1. **Fallback de CUIT falla con credencial anidada.** Central normaliza payload antes de extraer credentials, y no combina los mappings (`api/bots.py:574,608-618,793-817`; `credentials.py:138-158`). El modelo requiere `representado_cuit`, pero catálogo `services/central-api/src/central_api/api/bot_payloads.py:289-293` no expresa bien el requerido. **Propuesta:** hacer CUIT representado requerido explícito en contrato público; no inferirlo de identidad autenticadora, o añadir una regla de composición explícita con tests.
2. **Default de subida diferente según entrada.** Schema/modelo declara `subir=true`, normalizador API establece `false` sin flags legacy (`schema.py:...`; `credentials.py:143-158`). **Propuesta:** alinear defaults documentados y publicados, añadir tests de API canónica con flags ausentes y ambos valores explícitos.
3. **`extraer_campos` omitido del manifest/familia pública.** El plugin lo usa para una operación con consultas de detalle aun si la vista no se incluye (`schema.py:...`, `plugin.py:263-280`), pero el espejo `bot_payloads.py:289-293` no lo ofrece. **Propuesta:** añadirlo al schema del manifest y catálogo o quitarlo de la interfaz, con prueba `incluir_detalle=false` combinada con `extraer_campos`.
4. **`incluir_json` no controla `campos`.** El flag suprime `vistas` y conserva `campos` (`plugin.py:259-268`). **Propuesta:** definir por separado `incluir_vistas` y `extraer_campos` o aclarar un contrato de datos consistente.

## Pruebas consultadas

`services/bot-worker/tests/test_portal_siper.py:69-110,153-199` cubre extracción y fallos/estados de vistas; también se revisaron tests del plugin. Sin ejecuciones contra el portal ni uso de credenciales reales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/siper/schema.py:18-...`
- `services/bot-worker/src/bot_worker/bots/siper/plugin.py:148-281`
- `services/bot-worker/src/bot_worker/runtime/portals/siper.py:17-32,90-141,149-206`
- `services/central-api/src/central_api/api/bots.py:417,574,608-618,793-817`
- `services/central-api/src/central_api/api/bot_compat.py`
- `services/central-api/src/central_api/security/credentials.py:74-103,138-158`
- `services/central-api/src/central_api/api/bot_payloads.py:289-293`
- `services/bot-worker/tests/test_portal_siper.py:69-110,153-199`
