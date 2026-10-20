# Auditoría interna: `vep_ccma`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/vep_ccma/schema.py:17-38` enumera medios y unifica aliases de CUIT; `:41-56` limpia CUIT; `:59-113` normaliza filtros; `:116-132` unifica subida; `:135-185` define modelo/defaults/validaciones; `:195-224` publica JSON Schema.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/vep_ccma/plugin.py:142-181` autentica, selecciona representado y ejecuta; `:183-267` consulta obligaciones, filtra filas, genera VEP opcional, descarga PDF/QR y compone resultado.
3. **Portal real.** `services/bot-worker/src/bot_worker/runtime/portals/vep_ccma.py:1-16` tiene nota heredada que contradice campos soportados; `:38-50` mapea filtros; `:170-219` aplica criterios; `:270-288` selecciona obligaciones; `:317-364` realiza el click final; `:403-442` descarga PDF/QR. No se efectúa pago.

## Catálogo, alias y normalización

`services/central-api/src/central_api/api/bots.py:420` publica `vep_ccma/generar`, clasificado `EFECTO` por `:428-438`; alias `/api/v3/vep-ccma/generar`: `services/central-api/src/central_api/api/bot_compat.py:107`.

La familia pública `services/central-api/src/central_api/api/bot_payloads.py:304-310` no incluye filtros, aunque el modelo histórico los enumera en `:434-438,510`; `normalize_v2_payload` los preserva para este bot mediante `services/central-api/src/central_api/security/credentials.py:108-114`. El normalizador global utiliza `setdefault` y elimina la grafía antigua en `credentials.py:85-103`; los aliases de subida se convierten y retiran en `:227-263`. Algunas discrepancias de alias, por tanto, desaparecen antes de que el modelo del bot compare valores.

## Normalización, salida y efectos

- El validador directo compara `representado_cuit`, `cuit` y `cuit_representado` tras limpiar separadores y rechaza valores distintos (`schema.py:22-38`). Sin embargo, `cuit_representado` es alias global: si también está `representado_cuit`, central conserva el canónico y elimina el antiguo (`credentials.py:85-103`). El rechazo del modelo no aplica a esa forma ya normalizada.
- El validador directo compara alias de subida por `bool(valor)` (`schema.py:116-132`). En API, `minio_upload`/`subir_a_minio` se consumen antes y se asignan con `setdefault` al campo objetivo, por lo que un `subir_pdf` canónico presente prevalece incluso si el alias discrepa. Además, toda cadena no vacía, incluso `"false"`, resulta verdadera si llega al helper.
- Filtros de capital admiten `periodo`, `impuesto`, `concepto`, `subconcepto`, `categoria`; intereses también `subcpto`, pero no `categoria`. Se descartan claves desconocidas y valores null; objetos sin valores se descartan. El plugin aplica filtros al seleccionar filas (`schema.py:59-113`, `plugin.py:197-211`).
- `data` siempre incluye operación, CUIT y medio; contiene totales y `volante` cuando se genera; con `incluir_json=true`, listas `impuestos` e `intereses`. PDF publicado agrega `sha256`/`size_bytes`; QR disponible agrega `nombre_qr` y hash (`plugin.py:213-267`).
- `generar_volante=true` por default genera VEP con efecto fiscal, no paga. El click final no se reintenta ante confirmación ambigua (`runtime/portals/vep_ccma.py:317-364`). Una falla posterior de descarga/publicación puede coexistir con un volante ya creado.

## Discrepancias observadas

1. **Rechazo de aliases prometido demasiado ampliamente.** El modelo directo sí comprueba contradicciones, pero central elimina `cuit_representado` y convierte aliases de subida con precedencia (`credentials.py:85-103,227-263`).
2. **Filtros omitidos del catálogo de familia.** Schema/plugin/runtime los aceptan y aplican, pero `bot_payloads.py:304-310` no los declara; la superficie histórica sí (`:434-438,510`). El runtime contiene comentario obsoleto sobre ausencia de filtros (`runtime/portals/vep_ccma.py:1-16`).
3. **Coerción booleana ambigua.** `_unificar_subida` usa `bool` antes de comparar (`schema.py:116-132`), así que `"false"` se toma como verdadero.
4. **Schema público no sincronizado.** La familia central omite filtros que sí afectan obligaciones, con riesgo de que el catálogo sugiera que son ignorados.
5. **Ambigüedad del efecto final.** El click no tiene reintento seguro; error posterior no demuestra que el VEP no se creó (`runtime/portals/vep_ccma.py:317-364`).

## Pruebas consultadas

`services/bot-worker/tests/test_portal_vep_ccma.py:58-76,123-204,207-216` cubre filtros, selección, límite al click final y PDF/QR; también se revisaron pruebas del plugin. No se ejecutaron portales ni se usaron credenciales reales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/vep_ccma/schema.py:17-224`
- `services/bot-worker/src/bot_worker/bots/vep_ccma/plugin.py:142-267`
- `services/bot-worker/src/bot_worker/runtime/portals/vep_ccma.py:1-16,38-50,170-219,270-288,317-364,403-442`
- `services/central-api/src/central_api/api/bots.py:420-438`
- `services/central-api/src/central_api/api/bot_compat.py:107`
- `services/central-api/src/central_api/security/credentials.py:74-114,227-263`
- `services/central-api/src/central_api/api/bot_payloads.py:304-310,434-438,510`
- `services/bot-worker/tests/test_portal_vep_ccma.py:58-76,123-204,207-216`
- `services/bot-worker/tests/test_vep_ccma_plugin.py`
