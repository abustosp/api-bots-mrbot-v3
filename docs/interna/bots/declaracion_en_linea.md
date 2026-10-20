# Auditoría interna: declaracion_en_linea

## Flujo esquema -> plugin -> portal

1. **Esquema:** `extra=forbid`; CUIT limpia separadores y exige 11 dígitos. Normaliza períodos a `AAAAMM`, admite `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA`, `AAAA-MM`; mes 01–12. Si el rango llega invertido, lo ordena; genera hasta 1200 meses y corta silenciosamente en ese límite. `incluir_json=true`, `subir_archivos=true`; ambos false no válido. Se eliminan del payload campos de sesión/secretos/proxy para que viajen por el contexto central.
2. **Plugin:** una operación `consultar`, exige credenciales fiscales y captcha, idempotencia `LECTURA`. Si falta `representado_cuit`, consulta el CUIT de la sesión. Inicia sesión y recorre período por período. Una DDJJ ausente se agrega a `periodos_sin_ddjj` y se continúa; no haber encontrado ninguna DDJJ hace fallar la operación.
3. **Portal real:** `DeclaracionEnLineaPortal` consulta DDJJ ya generadas y VEP asociados, además de nómina. URL fallback `https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/`; listado `dj_generadas.aspx`. No presenta ni genera DDJJ, VEP o nómina.

## Salidas reales y parciales

Retorna rango, CUIT, número de períodos con DDJJ y `periodos_sin_ddjj`. `detalle[]` opcional: período, nombres de archivos DDJJ/VEP, estado y pagado del VEP, cabecera/secciones/advertencias. Puede producir DDJJ PDF, VEP PDF, nómina XLS y JSON por período; solo archivos realmente existentes se publican. `subir_archivos=false` evita slots de salida, pero se descargan para procesar. Aunque haya períodos sin DDJJ, el resultado global sigue `OK` si existe al menos uno, con la lista de faltantes; rango entero sin DDJJ produce error.

## Catálogo, central y pruebas

- Catálogo: `services/central-api/src/central_api/api/bots.py:347,397,428-439`; costo informativo y clase `CONSULTA`. Alias `/declaracion-en-linea/consulta`: `services/central-api/src/central_api/api/bot_compat.py:72`.
- Schema: `services/bot-worker/src/bot_worker/bots/declaracion_en_linea/schema.py:24-169`; plugin: `services/bot-worker/src/bot_worker/bots/declaracion_en_linea/plugin.py:107-233,235-347`; portal: `services/bot-worker/src/bot_worker/runtime/portals/declaracion_en_linea.py:1-55`.
- Modelo público y compatibilidad: `services/central-api/src/central_api/api/bot_payloads.py:71-84,165-171,485,527,566`; transformación: `services/central-api/src/central_api/security/credentials.py:74-115,200-281,303-370`; payload nulo opcional se omite en `services/central-api/src/central_api/api/bot_routes.py:521-529`.
- Pruebas leídas: `services/bot-worker/tests/test_declaracion_schema.py:1-111`, `services/bot-worker/tests/test_declaracion_plugin_detalle.py:155-214`, pruebas de portal correspondientes; central `tests/test_paridad_catalogo_registry.py` y `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas

1. El esquema canónico acepta/normaliza varios formatos de período; el esquema OpenAPI del catálogo anuncia solo `AAAAMM` (`services/central-api/src/central_api/api/bot_payloads.py:71-73,168-169`). La ruta genérica no aplica ese DTO antes de ejecutar, de modo que la publicación describe una restricción más estrecha que el comportamiento real. Alinear la documentación con los formatos normalizados.
2. El rango se trunca a 1200 meses (`services/bot-worker/src/bot_worker/bots/declaracion_en_linea/schema.py:72-93`) sin error ni aviso. La respuesta devuelve rango de entrada normalizado, no indica que se descartó la cola del rango. Propuesta: rechazar rangos largos o exponer `periodos_procesados`/warning con límite explícito.
3. El descriptor del catálogo no representa que el esquema efectivo acepta períodos invertidos y los intercambia (`services/bot-worker/src/bot_worker/bots/declaracion_en_linea/schema.py:72-93`). La ruta genérica no aplica ese DTO descriptivo antes de validar; documentar la normalización canónica o decidir rechazar rangos invertidos.
4. La relación entre `incluir_json` y `subir_archivos` (al menos uno true) no está expresada por el esquema OpenAPI del catálogo, que lista ambos booleanos independientes (`services/central-api/src/central_api/api/bot_payloads.py:165-171`). Añadir restricción cruzada a la descripción/esquema publicado.
5. Algunos alias upload existen en schema, pero OpenAPI central solo describe `subir_archivos`; los aliases son útiles en compatibilidad, no como campos canónicos V3.

## Propuestas sin implementar

- Añadir límites/rango cruzado al request model central, unificar lista de formatos de período y hacer explícita la política ante rangos >1200.
- Mantener pruebas que aseguren la lectura únicamente, ausencia de solicitudes de presentación y salida parcial de períodos faltantes.
