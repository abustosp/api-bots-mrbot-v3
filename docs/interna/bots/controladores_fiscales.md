# Auditoría interna: controladores_fiscales

## Flujo esquema -> plugin -> portal

1. **Esquema** (`bots/controladores_fiscales/schema.py`): máximo 50 archivos, lista no vacía, nombres simples de hasta 255 caracteres, sin separadores de ruta, `.`/`..` ni punto inicial; solo `.pem`/`.zip` (insensible a mayúsculas), deduplicación conservando orden. Se quitan del payload los campos de credencial/contexto sellado. Defaults `subir_constancia=true`, `incluir_json=true`; al menos una salida se exige activa. Upload aliases `subir_constancia/minio_upload/subir_a_minio` se consolidan con `bool(value)`, por lo que un string `"false"` se convierte en verdadero y `null` explícito en falso.
2. **Entrada central de archivos:** el request V3 usa claves de objeto de archivos previamente cargados; `externalizar_archivos` resuelve las referencias y entrega nombres/archivos al proceso. `INPUT_FILE_FIELDS` declara `archivos` para `presentar`. No enviar rutas locales.
3. **Plugin** (`bots/controladores_fiscales/plugin.py`): manifiesta `presentar`, costo 2, clase de efecto, credenciales y captcha. Procesa cada archivo por separado. En errores recuperables continúa con el siguiente y compone `total_exitosos`, `total_errores`, detalle opcional y constancias cargadas opcionalmente. Devuelve `OK` si todo bien, `PARCIAL` si hubo éxitos y errores, `ERROR` si todo falla. La bandera de publicación no controla la acción oficial.
4. **Portal real** (`services/bot-worker/src/bot_worker/runtime/portals/controladores_fiscales.py:1-12,24-29,78-122,127-158,231-272`): servicio de presentación ARCA/SETI. El proceso distingue acciones previas a “Presentar” de la confirmación: solo repite fases anteriores a la presentación; después del click no reenvía automáticamente. La operación envía oficialmente los archivos y puede producir constancias.

## Riesgo y salida

La presentación es efecto externo no reversible por la API. En lotes, cada archivo puede completar antes de que otro falle. Un `PARCIAL` incluye éxitos ya presentados; un timeout/ERROR posterior a la confirmación tampoco demuestra ausencia de efecto. Antes de reintentar, consultar el portal por archivo. `data.archivos_presentados` cuenta todos los archivos procesados con resultado, incluidos fallidos; `data.total_exitosos` cuenta presentaciones exitosas. `subir_constancia=false` elimina la referencia a los PDFs de salida, no evita presentar. `incluir_json=false` elimina detalle, no totales ni efectos.

## Catálogo, rutas, fuentes y pruebas

- Catálogo/metadatos: `services/central-api/src/central_api/api/bots.py:346,396,428-439`. Alias HTTP legado `/controladores-fiscales/carga`: `services/central-api/src/central_api/api/bot_compat.py:55-112`.
- Schema: `services/bot-worker/src/bot_worker/bots/controladores_fiscales/schema.py:20-119`; ejecución `services/bot-worker/src/bot_worker/bots/controladores_fiscales/plugin.py:65-95,111-138,172-281`; portal `services/bot-worker/src/bot_worker/runtime/portals/controladores_fiscales.py:1-12,24-29,78-122,127-158,231-272`.
- Referencias públicas a archivos: `services/central-api/src/central_api/api/bots.py:475-542`; `services/central-api/src/central_api/api/file_inputs.py:15-18,44-60,130-164`; campos descriptivos `services/central-api/src/central_api/api/bot_payloads.py:161-164,484,526,565`.
- Credenciales y secretos: `services/central-api/src/central_api/security/credentials.py:74-115,303-370`; parser de alias plano `services/central-api/src/central_api/api/bot_routes.py:491-529`.
- Pruebas leídas: `services/bot-worker/tests/test_controladores_fiscales_plugin.py` (éxito, parciales, flags, referencias ausentes), `services/bot-worker/tests/test_portal_controladores_fiscales.py`; central `tests/test_paridad_catalogo_registry.py`, `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias y propuestas de schema/model

1. El JSON Schema del bot describe `archivos` como strings con longitud, pero no limita sufijo ni representa la regla de nombre sin ruta; el validador deduplica repetidos conservando el primero (`services/bot-worker/src/bot_worker/bots/controladores_fiscales/schema.py:31-86,77-85,102-119`). Añadir patrón/descripción suficiente al esquema público o validar el conjunto en central antes de encolar.
2. `bool()` en aliases de subida admite strings numéricos/textuales con semántica accidental; en la ruta canónica `subir_constancia:null` también se convierte a false, mientras `incluir_json:null` se rechaza. El alias plano aplica su propio DTO y limpieza de nulls. Restringir a booleanos JSON y unificar ambas rutas.
3. La referencia de carga es un `object_key`, pero el plugin valida la extensión del nombre resuelto. El contrato debe decir explícitamente que el nombre original/cargado debe conservar `.pem` o `.zip`, no basta una clave arbitraria con extensión.
4. `incluir_json` controla detalle, no presentación. El nombre `subir_constancia` puede interpretarse como habilitación de la acción cuando solo gobierna la publicación de resultado; aclarar o renombrar en versión futura.
5. Mantener pruebas de idempotencia/no reenvío después del botón de confirmación, además de una prueba de resultado parcial con dos archivos donde el primero queda presentado.

No se cambió schema ni comportamiento; solo quedan propuestas.
