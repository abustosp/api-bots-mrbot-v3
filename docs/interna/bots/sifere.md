# Auditoría interna: `sifere`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/sifere/schema.py:30-66` fija patrón CUIT/período y rango de jurisdicciones; `:69-129` define el modelo, aliases, valores por omisión, filtrado de jurisdicciones y regla de al menos una salida; `:139-...` publica JSON Schema con `required` limitado a operación.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/sifere/plugin.py:154-208` valida, requiere credenciales y abre la sesión ARCA/servicio; `:210-304` consulta cada método SIRCAR por jurisdicción y conserva errores individuales; `:306-369` consulta consolidados SIRCREB/SIRTAC y adjunta resúmenes/archivos.
3. **Portal real.** `services/bot-worker/src/bot_worker/runtime/portals/sifere.py:28-70` mapea navegación y selección; `:73-...` conduce consultas SIRCAR y consolidadas. SIFERE depende del servicio del portal y no usa una petición directa por CUIT/período.

## Catálogo, operación y normalización

`services/central-api/src/central_api/api/bots.py:416` publica `sifere/consultar`, clasificada `CONSULTA` por `:428-438`. Alias `/api/v3/sifere/consulta`: `services/central-api/src/central_api/api/bot_compat.py:70`.

`services/central-api/src/central_api/security/credentials.py:85-103` mapea `cuit_representado` a `representado_cuit` y retira la forma antigua; `:247-263` unifica aliases de publicación a `subir`. Si llegan ambos nombres de un alias global y su campo canónico, el antiguo se descarta aunque contradiga al canónico. El CUIT de sesión se extrae por `:303-370`.

## Normalizaciones y resultado

- CUIT elimina espacios, guiones, puntos y `/`; exige once dígitos, no checksum. Período exige seis dígitos `AAAAMM`, mes 01–12 (`schema.py:47-66`).
- `representado_nombre`/`representado` es opcional; vacío se vuelve `None`. `jurisdicciones=null` o ausencia significa 901–924; lista explícita vacía es válida y no consulta SIRCAR. Códigos fuera del rango se filtran silenciosamente y la lista restante se ordena y deduplica (`schema.py:80-126`). `consolidados` default `true` y corre incluso con lista jurisdicciones vacía.
- `data` contiene `operacion`, CUIT, período y `jurisdicciones` cuando `incluir_json=true`. Cada jurisdicción tiene `jurisdiccion`, `metodo`, `etiqueta`, `archivos`; cada archivo, `archivo`, `tipo`, y con publicación, `sha256`/`size_bytes`. Con `incluir_json=true` puede incluir `consolidados`, una lista de archivos de consolidado con nombre y metadatos de publicación (`plugin.py:224-304,306-369`).
- `subir=false` evita referencias y archivos finales, no la descarga interna. `incluir_json=false` quita listas estructuradas de jurisdicciones/consolidados, pero la validación exige que alguna salida permanezca activa. Errores individuales SIRCAR y consolidados se agregan a `errors`; éxitos de otras jurisdicciones se retienen y estado es `PARCIAL` si existe cualquier error (`plugin.py:201-208,282-303,315-369`).

## Discrepancias concretas y propuestas (sin implementar)

1. **Validación efectiva más estricta que OpenAPI.** El JSON Schema manifest requiere solo operación, aunque el modelo exige CUIT y período (`schema.py:69-129,139-...`). El espejo de catálogo `services/central-api/src/central_api/api/bot_payloads.py:283-288` enumera campos básicos, no el rango de jurisdicciones. **Propuesta:** compartir/derivar schema del modelo y publicar límites, defaults y nullable.
2. **Jurisdicciones inválidas se silencian.** Los elementos fuera de 901–924 desaparecen, pudiendo resultar lista vacía sin error (`schema.py:115-126`). **Propuesta:** rechazar fuera de rango o devolver advertencia explícita y códigos ignorados.
3. **Default de consolidado no visible de forma completa en espejo público.** `consolidados=true` genera consultas/archivos adicionales incluso si la lista de jurisdicciones es `[]` (`schema.py:92-94`, `plugin.py:302-303`). **Propuesta:** hacer explícito este efecto y ofrecer modos con propósito claro.
4. **No atomicidad y errores de publicación.** Los reportes anteriores pueden descargarse/publicarse antes de un fallo posterior que eleva excepción; no todo fallo se representa como resultado parcial (`plugin.py:257-303,345-369`). **Propuesta:** manejar los fallos de cada archivo como error acumulado y probar el estado final con artefactos ya publicados.

## Pruebas consultadas

`services/bot-worker/tests/test_portal_sifere.py` cubre normalización, exclusión/rango de jurisdicciones, salida y comportamiento de consultas; no se ejecutaron portales ni pruebas con credenciales reales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/sifere/schema.py:30-149`
- `services/bot-worker/src/bot_worker/bots/sifere/plugin.py:154-369`
- `services/bot-worker/src/bot_worker/runtime/portals/sifere.py:28-70,73-...`
- `services/central-api/src/central_api/api/bots.py:416-438`
- `services/central-api/src/central_api/api/bot_compat.py:70`
- `services/central-api/src/central_api/security/credentials.py:74-103,247-263,303-370`
- `services/central-api/src/central_api/api/bot_payloads.py:283-288`
- `services/bot-worker/tests/test_portal_sifere.py`
