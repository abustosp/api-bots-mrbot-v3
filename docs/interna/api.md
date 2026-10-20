# API pública de la central: contrato observado

**Revisión:** 20 de octubre de 2026. **Alcance:** lectura de implementación y tests. Este documento describe lo que está montado hoy, no lo que sería deseable. La suite se ejecutó por paquetes separados, con totales y límites descritos en `docs/interna/validacion.md`. No se ejecutaron portales ni servicios remotos.

## 1. Superficie y prefijo

La superficie de cliente se monta bajo `/api/v3`. El agregador público incluye usuarios, bots y aliases, jobs, cuenta, uploads, utilidades, seguridad, APOC, extracción de documentos y VEP. Las rutas internas y de administración se montan aparte y no se exponen en el OpenAPI público. El esquema de cliente está disponible en `/openapi.json`, la interfaz en `/docs` y la referencia de lectura en `/redoc` (`services/central-api/src/central_api/main.py:389-423,473-478`; `services/central-api/src/central_api/api/router.py:1-30`).

“Pública” significa superficie de cliente, no anónima: la mayoría de sus rutas exige principal autenticado. La clave pública de protección de credenciales está expresamente exenta de autenticación (`services/central-api/src/central_api/api/security.py:47-80`).

### Rutas principales

| Uso | Método y ruta | Auth | Respuesta/observación comprobada |
|---|---|---|---|
| Descubrir operaciones | `GET /api/v3/bots` | Sí | `success`, `bots` compacto y `items` enriquecido con schema y ejemplo. |
| Ficha de bot | `GET /api/v3/bots/{bot}` | Sí | Operaciones, schemas, ejemplos y tipos de cuenta. No incluye `success`. |
| Crear ejecución canónica | `POST /api/v3/bots/{bot}/{operacion}` | Sí | `202`, `success`, `job_id`, `status`, `Location`, `X-Correlation-ID`. El body publicado tiene `payload` y `credentials`. |
| Ver ejecución | `GET /api/v3/jobs/{job_id}` | Sí | Proyección V2/V3 con datos, estado, error y archivos. |
| Listar ejecuciones propias | `GET /api/v3/jobs` | Sí | `success`, `jobs`, `items`, cursor y `has_more`. `limit` se limita a 1..100. |
| Cola de ejecuciones activas propias | `GET /api/v3/jobs/cola` | Sí | `success`, `jobs`, `items`, `count`; posición de cola solo para pendientes. Estado desconocido da lista vacía. |
| Consultar en lote | `POST /api/v3/jobs/estado:lote` | Sí | Hasta 200 IDs. Respuesta `items` en orden de entrada; un job ajeno/inexistente se informa como error del elemento, con HTTP 200. |
| Cancelación canónica | `POST /api/v3/jobs/{job_id}/cancelar` | Sí | Devuelve el estado proyectado. El body `CancelBody` tiene `motivo`, máximo 500 caracteres, pero el endpoint canónico exige body JSON. |
| Alias REST de cancelación | `DELETE /api/v3/jobs/{job_id}` | Sí | Devuelve estado. Body JSON opcional en runtime. |
| Crear ticket de archivo | `POST /api/v3/uploads` | Sí | `201`, clave, URL temporal de carga, headers y fechas. No crea job ni consume cuota. |
| Descargar resultado | `GET /api/v3/jobs/{job_id}/artifacts/{artifact_id}/download` | Sí | `307` y `Location` temporal, no entrega el archivo dentro de JSON. |
| Ver cupo | `GET /api/v3/mi/cuenta` | Sí | Resumen de límite, reservas, consumo, disponibilidad y período. |
| Identidad/cupo de usuario | `GET /api/v3/usuarios/me` | Sí | Identidad, estado y resumen de consultas. |
| Obtener clave pública | `GET /api/v3/seguridad/clave-publica` | No | Datos públicos requeridos para credenciales protegidas. Puede responder `503`. |
| Compatibilidad APOC | `GET /api/v3/apoc/consulta/{cuit}` | Sí | Alias histórico que crea o recupera una ejecución y contesta `202`. |

El path de cancelación por alias generado es `POST /api/v3/{alias}/cancelar/{job_id}`. La tabla de aliases de abajo contiene el path de creación. Por cada entrada, también se registran `GET {path}/{job_id}` y `POST {path}/cancelar/{job_id}` (`services/central-api/src/central_api/api/bot_routes.py:633-824`).

Los módulos auxiliares de utilidades, extracción PDF, VEP y logs también están incluidos por el agregador. Para su inventario vivo consultar el OpenAPI público, no confundirlo con la superficie privada (`services/central-api/src/central_api/api/router.py:5-30`).

## 2. Catálogo, operaciones y aliases

`CATALOGUE` declara 34 claves de bot y 45 pares bot/operación. `OPERATIONS` contiene las reglas de operación y costo publicado. En el catálogo detallado el código asigna `estado: HABILITADO` al construir cada item. `GET /bots/{bot}` entrega `planes`, pero no una comprobación de cupo disponible de la cuenta. `costo_creditos` es informativo y no garantiza un cálculo proporcional del consumo por bot. Cada creación aceptada reserva una unidad de uso (`services/central-api/src/central_api/api/bots.py:239-319,312-421,820-952`; reserva en `services/central-api/src/central_api/billing/reservation.py:60-99`).

`comprobantes` está marcado en `bots[]` como `deprecated: true`, `alias_de: mis_comprobantes`. Las operaciones canónicas de ambos aparecen en la lista histórica de operaciones, pero los paths V1 duplicados `/comprobantes/{consultar,solicitar,historial}` están ocultos de OpenAPI. El endpoint genérico todavía resuelve las claves que estén en `OPERATIONS`. Los tests fuerzan que cada capacidad canónica aparezca una sola vez en OpenAPI (`services/central-api/src/central_api/api/bots.py:378-421`; `services/central-api/src/central_api/api/bot_routes.py:638-649`; `services/central-api/tests/test_bot_routes.py:109-147`).

### Paths V1 mantenidos

Cada path de la primera columna es una ruta de creación POST con cuerpo plano histórico. Runtime añade consulta GET y cancelación POST. La tabla asigna el nombre canónico despachado, no el nombre textual de la ruta (`services/central-api/src/central_api/api/bot_compat.py:55-112`).

| Path de creación | Bot / operación canónica |
|---|---|
| `/mis_comprobantes/consulta` | `mis_comprobantes/consultar` |
| `/mis_comprobantes/solicitar_consulta` | `mis_comprobantes/solicitar` |
| `/mis_comprobantes/historial` | `mis_comprobantes/historial` |
| `/ccma/consulta` | `ccma/consultar` |
| `/ccma/compensacion` | `ccma_compensacion/compensar` |
| `/siper/consulta` | `siper/consultar` |
| `/sct/consulta` | `sct/consultar` |
| `/sct/compensaciones/consulta` | `compensaciones/consultar` |
| `/portal_iva/consulta` | `portal_iva/descargar` |
| `/portal_iva/carga` | `carga_portal_iva/cargar` |
| `/rcel/consulta` | `rcel/descargar` |
| `/hacienda/consulta` | `hacienda/consultar` |
| `/sifere/consulta` | `sifere/consultar` |
| `/aportes-en-linea/consulta` | `aportes_en_linea/descargar` |
| `/declaracion-en-linea/consulta` | `declaracion_en_linea/consultar` |
| `/mis_facilidades/consulta` | `mis_facilidades/consultar` |
| `/mis_retenciones/consulta` | `mis_retenciones/consultar` |
| `/mis_retenciones_iva_simple/consulta` | `mis_retenciones_iva_simple/consultar` |
| `/retenciones_percepciones_iibb/misiones/consulta` | `retper_iibb_misiones/consultar` |
| `/retenciones_percepciones_iibb/agip/consulta` | `retper_iibb_agip/consultar` |
| `/retenciones_percepciones_iibb/arba/consulta` | `arba/descargar` |
| `/arba/consulta` | `arba/descargar`, alias duplicado oculto de OpenAPI |
| `/pago_devoluciones/consulta` | `pago_devoluciones/consultar` |
| `/moa/consulta` | `moa/consultar` |
| `/libros_iva/consulta` | `libros_portal_iva/descargar` |
| `/libros_iva/ddjj` | `libros_portal_iva/descargar_ddjj` |
| `/libro-sueldos-digital/consulta` | `libro_sueldos_digital/descargar` |
| `/facturometro/consulta` | `facturometro/consultar` |
| `/controladores-fiscales/carga` | `controladores_fiscales/presentar` |
| `/certificado-mipyme/consulta` | `certificado_mipyme/descargar` |
| `/srt/alicuotas/consulta` | `srt/consultar_alicuotas` |
| `/vep/carga` | `vep_archivo/generar` |
| `/vep_archivo/carga` | `vep_archivo/generar`, alias duplicado oculto de OpenAPI |
| `/vep-ccma/generar` | `vep_ccma/generar` |
| `/vep/consulta-pagos` | `consulta_pagos_vep/consultar` |
| `/liquidacion_granos/consulta` | `liquidacion_granos/consultar` |
| `/consulta_cuit/individual` | `consulta_cuit/consultar` |
| `/consulta_cuit/masivo` | `consulta_cuit/consultar_masivo` |

Además de los 38 aliases registrados, el constructor agrega rutas `/{bot}/{operacion}` para capacidades sin alias. Mantiene ejecutables pero oculta de OpenAPI los tres paths de creación bajo `/comprobantes`. Por tanto, runtime y OpenAPI no tienen conjunto idéntico de rutas. `test_bot_routes.py` confirma las rutas y excepciones del esquema (`services/central-api/src/central_api/api/bot_routes.py:633-749`; `services/central-api/tests/test_bot_routes.py:34-108`).

## 3. Autenticación y correlación

Los métodos publicados como alternativas son HTTP Basic y el esquema `V1`. Basic usa identidad de usuario (correo o alias) como username y API key como password. El método V1 usa `X-API-Key` y admite `email` como identidad adicional. En runtime, `X-API-Key` sin `email` también funciona por compatibilidad. Un `Authorization: Bearer ...` de cliente se rechaza con 401. Clave ausente/inválida/caducada o cuenta deshabilitada se presenta con el mismo error genérico. Con secreto de autenticación configurado y base de datos caída, la verificación puede responder 503. En modo sin secreto configurado existe principal de desarrollo, excepto rutas como `/usuarios/me` que rechazan ese principal (`services/central-api/src/central_api/api/dependencies.py:45-96,122-128,267-350`; `services/central-api/tests/test_auth_metodos.py:51-102`; `services/central-api/src/central_api/api/users.py:624-630`).

`correlation_id(request)` acepta únicamente un UUID válido en `X-Correlation-ID`, si no genera otro UUID. La creación de job lo copia a respuesta. La afirmación de módulo “toda respuesta incluye” no está respaldada por un middleware global localizado: varios handlers devuelven JSON sin añadirlo. El job fallido también puede incluir `error_detail.correlation_id` si el resultado lo trae (`services/central-api/src/central_api/api/dependencies.py:15-16,98-106`; `services/central-api/src/central_api/api/bots.py:534-539,741-745`; `services/central-api/src/central_api/api/jobs.py:647-651`).

## 4. Cuerpos, normalización y validación efectiva

### Dos formas de solicitud

- **Canónica:** `POST /bots/{bot}/{operacion}` espera el body publicado `{ "payload": {...}, "credentials": {...} }`. La ruta permite además mapear algunos campos de nivel raíz a `payload` o `credentials` en el modelo runtime (`services/central-api/src/central_api/api/bots.py:194-208,466-514,793-817`).
- **Compatibilidad:** las rutas V1 reciben JSON plano con vocabulario histórico. El adaptador mapea credenciales, valida con modelo por operación, serializa defaults, elimina `None` y normaliza aliases antes de someter el job (`services/central-api/src/central_api/api/bot_routes.py:418-563`).
- **Archivos:** tanto canónica como alias requieren `object_key` de ticket, no multipart ni contenido base64. El adaptador compat rechaza multipart y campos base64 con `422 file_not_uploaded` (`services/central-api/src/central_api/api/bot_routes.py:429-474`; `services/central-api/src/central_api/api/bot_compat.py:127-217`; `services/central-api/tests/test_file_input_pipeline.py:127-178`).

### Capas de modelos

1. `bot_payloads.py` construye los cuerpos que documentan las operaciones canónicas, con defaults, restricciones y ejemplos.
2. `bot_schemas.py` crea los DTOs por operación para aliases y documentación V1 a partir de esos campos. Para operación desconocida genera un fallback permisivo.
3. El schema del worker es otra definición independiente. Tests comparan operaciones, campos y validez de ejemplos, pero esa comprobación no equivale a que la ruta canónica valide cada payload en runtime (`services/central-api/src/central_api/api/bot_payloads.py:21-71,87-160,1219-1392`; `services/central-api/src/central_api/api/bot_schemas.py:98-144`; `services/central-api/tests/test_bot_schemas.py:34-147`).

### Diferencia importante de validación

El schema canónico publica `payload` como requerido y `additionalProperties: false` en el nivel raíz. Runtime define `payload` y `credentials` con defaults vacíos, y `extra="allow"`. La ruta canónica toma `body.payload`, mezcla ciertas extras en payload/credenciales y llama al normalizador, pero no hace `model_validate` del schema público de esa operación. La suite contiene una prueba que envía `{"payload":{"security":"alta"},"credentials":{}}` a `consulta_cuit/consultar` y espera 202, aunque `security` no es campo de ese modelo (`services/central-api/src/central_api/api/bots.py:194-208,466-514,793-817`; `services/central-api/tests/test_saneamiento_secretos.py:226-234`). Un payload incompleto o con campo desconocido puede por ello pasar admisión y fallar durante la ejecución, mientras que el alias plano puede devolver 422 antes de aceptar el job.

Omitir, enviar `null`, `""`, espacios, `false` o `[]` no tienen una regla universal. En alias, `model_dump()` materializa defaults y luego elimina campos con valor `None`; strings y colecciones vacías no se eliminan por esa regla. En canónica, la dict de payload se usa como viene salvo normalizaciones (`services/central-api/src/central_api/api/bot_routes.py:517-542`; `services/central-api/src/central_api/api/bots.py:570-607`). Cada campo se debe consultar en schema de operación y validar en ambas entradas.

Hay defaults efectivos de normalización que contradicen los defaults/requisitos del modelo del ejecutor. Para `siper`, al omitir opciones el normalizador fija `subir:false`, `incluir_detalle:true` e `incluir_categorias:true`; el modelo del ejecutor define `subir:true`. Para `sct`, si no llegan banderas, se fijan todas las secciones, `formatos:["xlsx"]` y `subir:false`, mientras el modelo requiere `secciones`/`formatos` y define `subir:true` (`services/central-api/src/central_api/security/credentials.py:138-191`; `services/bot-worker/src/bot_worker/bots/siper/schema.py:36-55`; `services/bot-worker/src/bot_worker/bots/sct/schema.py:38-51`). El default efectivo en el límite cliente es el que resulta de la normalización, no el del modelo del ejecutor.

Además, los fallbacks de `representado_cuit` para SIPER y Misiones leen `cuit_representante`/`cuit_inicio_sesion` del payload normalizado (`services/central-api/src/central_api/security/credentials.py:130-158`). La ruta canónica normaliza payload antes y por separado de credenciales (`services/central-api/src/central_api/api/bots.py:570-575,608-618`). Si el CUIT de inicio de sesión llega exclusivamente en `credentials`, esa regla no lo copia al payload. Ese caso no está cubierto por la regla actual.

## 5. Idempotencia, cuota y cancelación

`Idempotency-Key` es un header opcional en la firma runtime de la ruta genérica y en el handler de alias. Si se omite, el código no consulta ni guarda registro de idempotencia, así que un retry puede crear otro job. Con clave, calcula fingerprint sobre bot, operación y payload normalizado, incluyendo huella de credencial, y reutiliza el job para fingerprint igual; contenido distinto produce 409. En la persistencia el key se limita a 128 caracteres y la fila vence a los 30 días; el fallback de memoria no expira con esa misma política. Una cabecera vacía tampoco activa idempotencia (`services/central-api/src/central_api/api/bots.py:631-745,793-817`; `services/central-api/src/central_api/api/dependencies.py:108-115,361-436`).

Hay una contradicción entre el comentario de `dependencies.py` que califica la clave como obligatoria y la firma/descripción pública que la hace opcional (`services/central-api/src/central_api/api/dependencies.py:12-14`; `services/central-api/src/central_api/api/bots.py:758-799`; `services/central-api/src/central_api/api/bot_routes.py:424-428`). El cliente debe enviarla siempre en creación, sin tratar eso como requisito runtime ya impuesto.

Al aceptar un job, la cuenta reserva una unidad. `costo_creditos` es informativo y no garantiza un cálculo proporcional del consumo por bot. La cuenta personal informa uso actual en `/mi/cuenta`. La operación puede responder 429 `quota_exhausted` con `Retry-After: 60` al agotar cupo (`services/central-api/src/central_api/billing/reservation.py:60-99`; `services/central-api/src/central_api/api/bots.py:683-728`; `services/central-api/src/central_api/api/account.py:64-123`).

Cancelación: un job pendiente pasa a cancelado y libera su reserva. Si ya está asignado/corriendo, se registra solicitud cooperativa y no hay devolución automática general. Los terminales se devuelven sin cambio. El motivo acepta 500 caracteres en `CancelBody` (`services/central-api/src/central_api/api/jobs.py:184-192,654-736,739-801`).

**Hallazgo contable corroborado con la coordinación:** el path de resultados confirma cuota salvo que el código exacto sea `INFRASTRUCTURE_ERROR`. El ejecutor emite `CHROMIUM_CRASHED`, `ARTIFACT_UPLOAD_FAILED` e `INTERNAL`, entre otros, no `INFRASTRUCTURE_ERROR`; las categorías de error declaradas tampoco incluyen ese nombre. Consecuencia observada: esos fallos diagnósticos pueden consumirse como cuota y no entrar en reembolso, aun siendo fallos de infraestructura. No documentar al cliente un reembolso general por error (`services/central-api/src/central_api/internal/job_results.py:91-95,129-141,245-269`; `services/bot-worker/src/bot_worker/main.py:1399-1489`; `services/bot-worker/src/bot_worker/bots/errors.py:16-28`).

## 6. Estados y resultado

La proyección preserva `status` compatible con clientes anteriores y agrega `status_detail`:

| Estado real | `status` compatible | `status_detail` | `result` |
|---|---|---|---|
| Pendiente | `PENDIENTE` | `PENDIENTE` | `null` |
| Asignado | `PENDIENTE` | `ASIGNADO` | `null` |
| Corriendo | `CORRIENDO` | `CORRIENDO` | `null` |
| Éxito | `COMPLETO` | `COMPLETO` | `OK` |
| Parcial | `COMPLETO` | `COMPLETO` | `PARCIAL` |
| Fallido | `COMPLETO` | `FALLIDO` | `ERROR` |
| Cancelado | `CANCELADO` | `CANCELADO` | `null` normalmente |

`files` siempre se proyecta como lista. `data` es objeto, lista o null según el resultado. Cuando `data` es un objeto, la proyección añade `schema_version: 1` antes de las claves del resultado; si el resultado ya trae `schema_version`, ese valor prevalece (`services/central-api/src/central_api/internal/job_results.py:261`; `services/central-api/src/central_api/api/jobs.py:157-160`). `finished_at`, `started_at`, `cancel_reason`, `cancelled_by`, `error` y `error_detail` pueden ser null. `operation_legacy` se omite cuando no hay nombre histórico distinto. `error` público es mensaje legible, no el error interno. `operation_legacy` cambia nombres como `consulta` a operación canónica `consultar` (`services/central-api/src/central_api/api/jobs.py:254-366`; `services/central-api/src/central_api/api/dependencies.py:465-519,551-575,674-698`; `services/central-api/tests/test_job_status_v2_compat.py:44-73`).

No todas las respuestas de éxito comparten un sobre: alta usa `success/job_id/status`, consulta de job el objeto de estado, batch `items`, lista `success/jobs/items/next_cursor/has_more`, cola `success/jobs/items/count`. `GET /jobs/{id}` puede convertir una excepción de lectura persistida en 404 porque el lector devuelve `None` ante fallo y el handler mapea `None` a not-found. Comparar con lista/cola, que sí tienen rutas 503 (`services/central-api/src/central_api/api/jobs.py:96-134,637-651,999-1083`).

## 7. Errores: envolturas y status HTTP

La forma común generada por `public_error` es `{"detail":{"error_code":"...","message":"..."}}`, con `correlation_id` y/o `job_id` opcionales. No es uniforme en el runtime: validación automática del framework puede emitir `detail` como lista, validación custom usa objeto, ticket de upload no persistible usa `detail` string, y descargas de artifact usan errores string para 404/410/503. `error` de un job terminal es otra estructura distinta (`services/central-api/src/central_api/security/secret_redaction.py:23-71`; `services/central-api/src/central_api/api/uploads.py:156-157,325-335`; `services/central-api/src/central_api/api/bots.py:771-786`; `services/central-api/src/central_api/api/jobs.py:254-260,753-764`).

Errores de admisión observados: 401 autenticación, 404 bot/operación o job no visible, 409 conflicto de idempotencia, 422 forma/datos/archivo no admisible, 429 cuota, 503 indisponibilidad. El código/forma exacta varía por handler. Errores de resultado del job se convierten en `result=ERROR` o `PARCIAL` con `error_detail.error_code`, `cause`, `message` y correlación opcional. Un desconocido se proyecta como `unexpected` (`services/central-api/src/central_api/api/dependencies.py:578-698`; `services/central-api/src/central_api/api/bots.py:768-789`).

## 8. Uploads y artifacts

El ticket de entrada requiere bot, operación, nombre y tamaño positivo. `campo` default `archivos`, `content_type` default `application/octet-stream`, CUIT y SHA-256 opcionales. Límite general 52,428,800 bytes y límites menores por campo. Campo se comprueba contra lista permitida. Ticket contesta `upload_id`, `object_key`, bucket, URL, vida de la URL de 900 segundos, `expires_at` de retención y `required_headers` (`services/central-api/src/central_api/api/uploads.py:37-39,69-126,160-247,248-303`; `services/central-api/src/central_api/input_files.py:15-82`).

Al resolver entrada se comprueba cuenta, bot, operación, campo, ticket vigente y objeto presente; se lee con límite por campo y valida formato. Las claves se exigen asociadas al mismo dueño y operación (`services/central-api/src/central_api/api/uploads.py:430-575`).

Defecto de metadatos: no se encuentra comparación entre longitud real recibida y `size_bytes` declarado ni verificación del SHA-256 opcional. La lectura está acotada al límite del campo, pero ticket con 100 bytes declarados y objeto válido de 4 bytes no se rechaza por discrepancia de longitud. La API debe decidir si verificar ambas propiedades o documentarlas explícitamente como no verificadas. Añadir caso de test para hash incorrecto y tamaño declarado distinto.

Los resultados conservan campos V2 `name`, `url`, `size` y añaden `artifact_id`, `filename`, `content_type`, `size_bytes`, `sha256`, `download_url`, `url_expires_in`. URL directa vale 3600 segundos cuando se puede firmar; si no hay URL se expone `url: ""`, expiración null y puede persistir `download_url`. La ruta de redirección entrega URL de 300 segundos. Se excluyen artifacts vencidos al hidratar estado. `404` oculta inexistencia y falta de acceso, `410` comunica objeto eliminado (`services/central-api/src/central_api/api/jobs.py:66-93,201-253`; `services/central-api/src/central_api/api/dependencies.py:701-732`; `services/central-api/src/central_api/api/uploads.py:306-412`; `services/central-api/tests/test_job_status_v2_compat.py:151-169`; `services/central-api/tests/test_artifact_local_flow.py:96-239`).

## 9. Pruebas contractuales existentes

- Auth: Basic, cabeceras V1, rechazo Bearer, 401 sin credencial y publicación OpenAPI (`services/central-api/tests/test_auth_metodos.py:51-102`).
- Aliases: tripletes create/status/cancel, ocultamiento de duplicados, un solo path canónico por operación, mapping de `comprobantes` (`services/central-api/tests/test_bot_routes.py:34-147`).
- Schemas: 45 pares, modelos operation-specific, ejemplos válidos como instancias de DTO y comparación de campos contra schemas del ejecutor; no valida todas las reglas del plugin/archivos (`services/central-api/tests/test_bot_schemas.py:34-147`).
- OpenAPI de alias/canónico, archivos, estados y ejemplos (`services/central-api/tests/test_swagger_contracts.py:35-233`).
- Estados compatibles, errores sanitizados, archivos y consistencia get/batch/list (`services/central-api/tests/test_job_status_v2_compat.py:44-281`).
- Upload ticket, object keys, formatos y archivo faltante (`services/central-api/tests/test_artifact_local_flow.py:27-70`; `services/central-api/tests/test_file_input_pipeline.py:98-277`).
- Cola/cancelación y categorías públicas de error (`services/central-api/tests/test_jobs_queue_cancel.py:47-185`; `services/central-api/tests/test_job_error_categories.py:6-22`).

La existencia de un test de OpenAPI no demuestra que el runtime aplique el schema documentado.
