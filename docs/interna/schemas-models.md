# Contratos y modelos: diferencias y propuesta

**Revisión:** 20 de octubre de 2026. Este documento propone cambios de contrato y cobertura. No describe cambios runtime ni afirma que la propuesta ya esté implementada. El comportamiento observado y el inventario de rutas están en [API interna](api.md). La evidencia de pruebas y límites de ejecución está en [validación](validacion.md).

## 1. Hallazgos que condicionan el diseño

### 1.1 El schema publicado no es la validación efectiva de creación

El esquema canónico de OpenAPI exige `payload` y no permite propiedades raíz adicionales. En cambio, `CreateJobBody` da defaults vacíos a `payload` y `credentials`, permite extras y la ruta admite mapear ciertas propiedades raíz al payload o a las credenciales. La creación normaliza esos valores, pero no valida el payload con el modelo público específico de la operación antes de admitir el job (`services/central-api/src/central_api/api/bots.py:194-208,466-514,793-817`).

Hay ejemplos reproducidos en el informe de validación: `ccma/consultar` con `payload: {}` recibió `202/PENDIENTE` y luego falló por faltar `representado_cuit` ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). También hay un test donde `consulta_cuit/consultar` acepta `security` desconocido en el payload (`services/central-api/tests/test_saneamiento_secretos.py:226-234`). Esto difiere del schema documentado y permite que un pedido inválido consuma una reserva antes del fallo tardío.

### 1.2 Contratos de operación, alias, ejecutor y ejemplos no comparten una única fuente efectiva

Los modelos centrales para documentación y aliases se crean con `_fields_for_operation`; el cuerpo canónico y el cuerpo histórico se derivan por separado (`services/central-api/src/central_api/api/bot_payloads.py:1198-1237`). La validación de schemas verifica que los ejemplos sean instancias válidas de esos modelos, y compara campos públicos con el catálogo compartido para la mayoría de pares. El test no demuestra que un ejemplo satisfaga la validación real del plugin con todos los requisitos de archivos (`services/central-api/tests/test_bot_schemas.py:34-67,123-146`).

El informe de validación contrastó los 45 ejemplos generados, con normalización, traducción local de referencias de archivo y `plugin.validate()` sin ejecutar operaciones: 42 fueron válidos y 3 quedaron incompletos. Los casos son `carga_portal_iva/cargar` sin un par TXT o CSV, `controladores_fiscales/presentar` sin `archivos` y `vep_archivo/generar` sin referencia que produzca `archivo_path` ([`Ejemplos generados por el catálogo actual`](validacion.md#ejemplos-generados-por-el-catálogo-actual)). No se debe presentar el resultado como 45 ejemplos válidos ni como prueba de ejecución remota.

Los requisitos de archivo existen en validadores específicos: carga IVA requiere un par de comprobantes/alícuotas o un CSV y rechaza pares incompletos (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:95-166`); presentación de controladores requiere de 1 a 50 nombres de archivo (`services/bot-worker/src/bot_worker/bots/controladores_fiscales/schema.py:43-92`). En la generación VEP, la traducción del campo de archivo usa `archivo_key` como referencia a `archivo_path` (`services/central-api/src/central_api/input_files.py:21-29`). Es posible que algunos nombres de referencia no pertenezcan a los campos históricos de `_fields_for_operation`; hay que verificar la correspondencia sin forzar una falsa igualdad entre DTO histórico y entrada del ejecutor.

### 1.3 Estados, errores de transporte y contabilidad mezclan conceptos distintos

La vista de job conserva `status` compatible, agrega `status_detail` y expresa fallos como `COMPLETO` más `result: ERROR` (`services/central-api/src/central_api/api/jobs.py:325-368`; `services/central-api/src/central_api/api/dependencies.py:465-519,551-575,674-698`). El cliente debe leer ambos campos y `result`, no inferir éxito a partir de `status`.

Las categorías diagnósticas del ejecutor incluyen, entre otras, `CHROMIUM_CRASHED`, `ARTIFACT_UPLOAD_FAILED` e `INTERNAL`; la lista declarada está en `services/bot-worker/src/bot_worker/bots/errors.py:16-28`, y la emisión en `services/bot-worker/src/bot_worker/main.py:1399-1489`. La liquidación central, persistente y en memoria, devuelve cuota solo para el valor exacto `INFRASTRUCTURE_ERROR` y confirma para otros resultados (`services/central-api/src/central_api/internal/job_results.py:91-95,129-141,245-269`). El informe local reprodujo que esos tres errores seleccionan confirmación, mientras `INFRASTRUCTURE_ERROR` selecciona devolución ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). Diagnóstico técnico y decisión contable son dimensiones diferentes; la ausencia de una categoría de error no debe decidir accidentalmente el cargo.

### 1.4 Se acepta como recibido un callback no confirmado

`send_result()` devuelve éxito para cualquier respuesta HTTP menor que 500 (`services/bot-worker/src/bot_worker/reporting/results.py:40-78`); el llamador puede marcar el resultado reportado (`services/bot-worker/src/bot_worker/main.py:1504-1535`). La prueba sintética de la coordinación observó `True` para 302, 401, 403, 409 y 422, además de respuestas 2xx ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). Un rechazo 4xx no es confirmación de que el resultado fue aceptado, y una redirección tampoco debe contar como confirmación salvo que se complete y verifique el destino.

### 1.5 Idempotencia, error envelope y metadatos de archivo son inconsistentes

`Idempotency-Key` es opcional en handlers de creación aunque un comentario lo califica de obligatorio; si no se envía, se omite la deduplicación. Las claves persistidas se limitan a 128 caracteres y vencen a los 30 días, mientras el fallback de memoria no tiene la misma expiración (`services/central-api/src/central_api/api/dependencies.py:12-14,361-436`; `services/central-api/src/central_api/api/bots.py:631-745,793-817`).

`public_error()` define un envoltorio común, pero validaciones automáticas, tickets y descargas pueden devolver `detail` como lista, objeto o texto (`services/central-api/src/central_api/security/secret_redaction.py:23-71`; `services/central-api/src/central_api/api/uploads.py:156-157,325-335`; `services/central-api/src/central_api/api/jobs.py:753-764`).

El ticket de carga acepta tamaño declarado y SHA-256 opcional, pero no se encontró verificación de que la longitud recibida coincida con `size_bytes` ni de que el hash coincida con el contenido (`services/central-api/src/central_api/api/uploads.py:69-126,430-575`; `services/central-api/src/central_api/input_files.py:63-82`). El informe interno de API describe el caso de metadatos discrepantes y su test faltante ([sección de uploads y artifacts](api.md#8-uploads-y-artifacts)).

### 1.6 Defaults y aliases pueden perderse o cambiar antes de validar

La normalización V2 se ejecuta también en la ruta canónica (`services/central-api/src/central_api/api/bots.py:570-575`). Para SIPER fija `subir:false`, `incluir_detalle:true` e `incluir_categorias:true` si se omiten; el modelo del ejecutor declara `subir:true` (`services/central-api/src/central_api/security/credentials.py:138-158`; `services/bot-worker/src/bot_worker/bots/siper/schema.py:36-55`). Para SCT, sin banderas heredadas, añade todas las secciones, `formatos:["xlsx"]` y `subir:false`, mientras el modelo requiere secciones y formatos y declara `subir:true` (`services/central-api/src/central_api/security/credentials.py:160-191`; `services/bot-worker/src/bot_worker/bots/sct/schema.py:38-51`). La coordinación reprodujo los valores efectivos y `plugin.validate()` sin ejecutar operaciones ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). Son defaults de compatibilidad observados, no los defaults que se deben inferir del modelo aislado.

El fallback de `representado_cuit` de SIPER y Misiones busca `cuit_representante` o `cuit_inicio_sesion` solo en el payload normalizado (`services/central-api/src/central_api/security/credentials.py:130-158`). La ruta canónica separa `payload` y `credentials` antes de normalizar, por lo que el fallback no ve un CUIT recibido exclusivamente dentro de `credentials` (`services/central-api/src/central_api/api/bots.py:570-575,608-618`). La coordinación reprodujo para SIPER que esa forma falla por `representado_cuit` faltante ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)).

También hay aliases contradictorios de VEP CCMA: si llegan juntos el nombre canónico y su alias antiguo con valores distintos, el normalizador conserva el canónico y descarta el alias, en vez de rechazar el conflicto. La reproducción usó `representado_cuit`/`cuit_representado` y `subir_pdf`/`minio_upload` (`services/central-api/src/central_api/security/credentials.py:85-102,240-283`; detalle en [`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). La precedencia debe formar parte del contrato y tener tests; para una versión nueva se recomienda rechazar aliases simultáneos contradictorios.

## 2. Propuesta objetivo

### 2.1 Un contrato versionado por operación

Adoptar una definición declarativa versionada por pareja `(bot, operación)` que sea la fuente para generar:

1. Schema canónico de solicitud y respuesta.
2. DTO de alias histórico y su adaptador explícito.
3. Campos requeridos, tipos, límites, nullable, defaults y reglas entre campos.
4. Referencias de archivos, extensiones, tamaño máximo, campos emparejados y listas mínimas/máximas.
5. Ejemplos ficticios validados contra el contrato y contra el validador de operación.
6. Casos de prueba de contrato para cada regla.

No derivar la validez de un ejemplo solo de `model_validate()` del DTO público. La verificación debe seguir la misma normalización de la ruta y las mismas reglas puras de entrada del ejecutor, con almacenamiento y llamadas externas sustituidos por dobles. Los ejemplos deben usar referencias ficticias estructuralmente completas para reglas cruzadas. `carga_portal_iva/cargar` incluirá un par TXT completo o un CSV; `controladores_fiscales/presentar`, al menos un elemento de `archivos`; `vep_archivo/generar`, la referencia pública que se traduce a `archivo_path`. Mantener la nota explícita de que las referencias son ilustrativas y no equivalen a archivos existentes.

El DTO histórico puede seguir teniendo nombres antiguos por compatibilidad. Mantener tablas declaradas de equivalencias de campo y verbo, con detección de colisiones y pruebas de ida a la forma canónica. No asumir que los campos históricos de `V2_OPERATION_FIELDS` incluyen automáticamente las referencias de carga actuales. Las tablas de traducción vigentes están en `services/central-api/src/central_api/input_files.py:15-60,84-90`; las pruebas de campos históricos están en `services/central-api/tests/test_bot_schemas.py:34-50`. El contrato debe describir los valores efectivos después de normalizar el conjunto `payload` + `credentials`, no solo los defaults del modelo del ejecutor.

### 2.2 Reglas explícitas de entrada

Propuesta para el contrato canónico:

```json
{
  "type": "object",
  "required": ["payload"],
  "additionalProperties": false,
  "properties": {
    "payload": {"$ref": "#/operation/payload"},
    "credentials": {"$ref": "#/operation/credentials"}
  }
}
```

- `payload` es objeto requerido y se valida contra la operación antes de crear/reservar el job. No mapear silenciosamente extras raíz al payload canónico.
- `credentials` tiene una política decidida por versión. En el contrato actual se documentará como opcional o nullable solo donde lo admita el modelo. Su contenido no se incluye en resultados ni errores.
- Las rutas planas históricas continúan aisladas en el adaptador de compatibilidad, con DTO por ruta y reglas deprecadas declaradas. No deben cambiar el schema canónico.
- Por campo: la ausencia aplica el default efectivo declarado en el contrato, si existe; no puede depender de una normalización oculta que contradiga el schema. `null` se acepta únicamente en campos nullable; `""` se acepta solo si no contradice minLength/validador; booleanos y listas vacías conservan valor y se validan según campo. Documentar estas decisiones por operación, no como promesa global.
- Rechazar campo desconocido con error de validación para el contrato canónico. Si la compatibilidad histórica requiere conservar extras, limitarlo a rutas/versiones históricas y registrar su normalización. Rechazar dos alias simultáneos con valores contradictorios, salvo que una precedencia explícita por versión sea parte del contrato.
- Validar reglas cruzadas, como el par TXT de IVA, listas no vacías y referencias de archivo, antes de aceptar la ejecución. Separar validación sintáctica local de validación que exige inspeccionar bytes y de resultados que solo conoce el organismo.

La diferencia que motiva esta propuesta está entre schema OpenAPI, `CreateJobBody` permisivo y validación tardía del plugin (`services/central-api/src/central_api/api/bots.py:194-208,466-514,793-817`; evidencia reproducida en [`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)).

### 2.3 Contrato común de referencia de archivo

Mantener compatibilidad V3 con los nombres `*_key` y listas de object keys, pero describir cada campo mediante metadatos de contrato únicos:

| Metadato | Regla objetivo |
|---|---|
| `upload_field` | Nombre exacto solicitado al crear ticket. |
| `payload_field` | Nombre exacto de la referencia que se envía al crear el job. |
| `extensions` y tipos admitidos | Lista explícita, distinta por operación y campo. |
| `max_bytes` | Mismo límite aplicado al ticket y al contenido real. |
| `min_items` / `max_items` | Límites efectivos para referencias múltiples. |
| `constraints` | Pares requeridos, alternativas admitidas y relación con otros campos. |

En toda carga, comprobar tamaño real igual a `size_bytes`; si `sha256` fue enviado, comparar con el contenido real antes de aceptar la referencia. Definir si `sha256` debe ser obligatorio en una futura versión y cómo se calcula. Mantener la asociación de ticket con cuenta, bot, operación y campo. La implementación actual define campos y límites en `services/central-api/src/central_api/input_files.py:15-82`, valida propiedad/operación/objeto en `services/central-api/src/central_api/api/uploads.py:430-575` y no verifica los metadatos declarados según el contraste de código citado arriba.

### 2.4 Separar estado, diagnóstico y liquidación

Para la API actual, aclarar que `status_detail` expresa el estado operativo más específico y `result` expresa éxito, parcialidad o error. Mantener `status` compatible por V3, pero no usarlo como indicador de éxito. En una futura versión, publicar un `status` canónico único con los seis estados y deprecar la vista ambigua, conservando un adaptador V3.

Definir diagnóstico y liquidación como campos independientes:

```json
{
  "result": "ERROR",
  "error_detail": {
    "error_code": "CHROMIUM_CRASHED",
    "cause": "infrastructure",
    "message": "No se pudo completar la ejecución"
  },
  "quota": {
    "disposition": "CONFIRMED",
    "reason_code": "policy_default"
  }
}
```

El ejemplo expresa el modelo propuesto, no un cuerpo runtime garantizado. `error_code` clasifica qué falló. `cause` clasifica de forma estable su dominio. `quota.disposition` registra una decisión explícita (`RELEASED`, `REFUNDED`, `CONFIRMED` o `NOT_APPLICABLE`) y un código de política auditable. No derivar el cargo de una comparación de texto contra una categoría diagnóstica ni anunciar reembolso automático universal. Definir y revisar la tabla de liquidación con producto/finanzas, cubrir tanto el camino persistente como el de memoria y hacer coincidir las categorías emitidas con el enum público.

### 2.5 Confirmación del receptor de resultados

Usar un contrato de recepción que distinga `ACKNOWLEDGED`, `RETRYABLE` y `REJECTED`. Solo una confirmación 2xx definida por contrato puede marcar el resultado como recibido. 5xx y fallas de transporte son reintentables; 4xx son rechazo terminal o requiere intervención, nunca ACK silencioso; 3xx se sigue y valida explícitamente o se trata como respuesta inesperada. Mantener la misma clave de callback en los reintentos para deduplicar. Esta propuesta corrige la semántica actual `<500` (`services/bot-worker/src/bot_worker/reporting/results.py:40-78`; consumidor `services/bot-worker/src/bot_worker/main.py:1504-1535`).

### 2.6 Error de API uniforme sin filtrar datos de entrada

Proponer un objeto común para respuestas no exitosas:

```json
{
  "detail": {
    "error_code": "validation_error",
    "message": "Revise los campos indicados",
    "correlation_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
    "field_errors": [
      {"path": "payload.cuit", "code": "invalid_format", "message": "Formato no válido"}
    ]
  }
}
```

Propuesta de reglas: `error_code` y `message` siempre presentes; `field_errors` opcional y estructurado; `job_id` solo cuando corresponde; correlación opcional hasta que se aplique de forma global. No incluir `input`, credenciales, contenido del archivo ni stack trace. Mantener `detail` durante compatibilidad V3, adaptar excepciones automáticas a este objeto y hacer que errores de ticket, descarga y admisión compartan el mismo modelo. El estado terminal de job seguirá teniendo `error_detail` con esquema documentado, no se debe confundir con un rechazo HTTP (`services/central-api/src/central_api/security/secret_redaction.py:23-71`; `services/central-api/src/central_api/api/uploads.py:156-157,325-335`; `services/central-api/src/central_api/api/jobs.py:254-277,753-764`).

### 2.7 Política de idempotencia verificable

V3 debe documentar `Idempotency-Key` como opcional mientras así sea el runtime, y dejar claro que omitirlo no deduplica. Resolver la contradicción en comentario/OpenAPI. Para una versión nueva, hacer obligatorio el header en creación, limitarlo con 400/422 explícito, documentar retención y garantizar la misma expiración en persistencia y memoria. El fingerprint debe ser estable ante normalización, incluir operación y entradas, y no exponer credenciales en logs. Misma clave/mismo fingerprint retorna el mismo job; misma clave/fingerprint distinto devuelve conflicto.

La implementación actual difiere en obligatoriedad y retención de memoria/almacenamiento (`services/central-api/src/central_api/api/dependencies.py:12-14,361-436`; `services/central-api/src/central_api/api/bots.py:631-745,793-817`).

## 3. Matriz de validación y evidencia requerida

| Área | Diferencia/riesgo observado | Comprobación recomendada | Resultado esperado |
|---|---|---|---|
| Schema canónico | Schema exige `payload`, DTO runtime permite omisión y extras | Crear cada operación con payload ausente, vacío, tipo incorrecto y campo extra | Rechazo 4xx antes de reservar; los campos aceptados coinciden con OpenAPI |
| Reglas de plugin | `ccma/consultar` vacío llegó a 202 y falló luego | Caso negativo para `representado_cuit` omitido y caso positivo mínimo por operación | Los errores deterministas de entrada se devuelven antes de admitir job |
| Alias V1 | Serializa defaults y elimina `None`, pero conserva `""`, `false`, `[]` (`services/central-api/src/central_api/api/bot_routes.py:517-542`) | Tabla por campo de omitido/null/vacío/espacios/false/lista vacía y traducción del alias | Comportamiento descrito por campo, mismo valor canónico cuando corresponda |
| Defaults/identidad normalizados | SCT/SIPER tienen defaults efectivos distintos del modelo aislado; fallback SIPER no consulta CUIT solo en `credentials` (`services/central-api/src/central_api/security/credentials.py:130-191`; [`casos reproducidos`](validacion.md#casos-reproducidos-sin-servicios-externos)) | Probar omitidos, defaults efectivos y CUIT solo en cada ubicación admitida | Schema y documentación describen valores tras normalización; identidad se resuelve como contrato o se rechaza antes de reservar |
| Alias contradictorios | VEP CCMA conserva campo canónico y descarta alias conflictivo ([`casos reproducidos`](validacion.md#casos-reproducidos-sin-servicios-externos)) | Enviar cada pareja de alias con valores iguales, distintos y ambos omitidos | Valores iguales equivalen; los distintos se rechazan o siguen precedencia documentada |
| Ejemplos y archivos | Tres de los 45 ejemplos actuales incompletos ([`Ejemplos generados por el catálogo actual`](validacion.md#ejemplos-generados-por-el-catálogo-actual)) | Ejecutar generador → normalizadores → equivalencias de archivo → validador puro del plugin en CI | 45/45 ejemplos aceptados por reglas de entrada; no se llama a `execute()` ni a servicios |
| Archivos de IVA | Requiere par TXT o CSV y rechaza par roto (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:133-166`) | Sin archivo, medio par, par completo, cada CSV permitido | Negativos rechazados; par completo o CSV admitido |
| Controladores | `archivos` debe tener de 1 a 50 elementos (`services/bot-worker/src/bot_worker/bots/controladores_fiscales/schema.py:43-92`) | Ausente, `[]`, uno, 50, 51, extensión no permitida y path traversal | Rechazar vacío, exceso, extensión y rutas; admitir lista válida |
| VEP | `archivo_key` se traduce a `archivo_path` (`services/central-api/src/central_api/input_files.py:21-29`) | Asegurar que esquema, ejemplo, ticket y payload usan la misma equivalencia | Un archivo cargado produce la referencia esperada; falta de archivo se detecta antes de ejecución |
| Integridad de carga | No se encontró igualdad tamaño/hash comprobada | Declarar un tamaño distinto del real; declarar SHA-256 incorrecto y correcto; contenido sobre límite | Tamaño desigual y hash incorrecto son rechazados; límite se aplica a bytes reales |
| Referencia y autorización de archivo | Ticket asociado a dueño/bot/operación/campo (`services/central-api/src/central_api/api/uploads.py:430-575`) | Reutilizar ticket vencido, de otro usuario, bot, operación o campo | Rechazo estable sin revelar existencia de objetos ajenos |
| Error envelope | `detail` puede ser objeto, lista o texto | Provocar 401/404/409/422/429/503 y errores de archivo/artefacto | Todos cumplen el envelope acordado y no reflejan secretos ni body sensible |
| Estado | `status` compatible puede ocultar `ASIGNADO`/`FALLIDO` | Cubrir seis estados y proyección V3/V2, compare GET/list/batch | `status_detail` y `result` coinciden; cliente no deduce éxito de `COMPLETO` |
| Idempotencia | Header opcional con comentario contradictorio y expiraciones distintas | Omitido, vacío, misma clave/cuerpo, misma clave/cuerpo distinto, longitud límite, expiración en memoria y persistencia | V3 coincide con docs; misma clave/mismo body retorna mismo job; cambio da 409 |
| Liquidación | Solo `INFRASTRUCTURE_ERROR` devuelve; categorías realmente emitidas no incluyen ese valor (`services/central-api/src/central_api/internal/job_results.py:129-141,245-269`; `services/bot-worker/src/bot_worker/bots/errors.py:16-28`) | Para cada categoría diagnóstica, revisar disposición en memoria y persistida | Categoría diagnóstica y disposición contable se registran separadas y conforme a tabla aprobada |
| ACK de resultado | Cualquier `<500` es éxito; se observaron 302/401/403/409/422 como `True` ([`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)) | Respuestas 200/201/204, 3xx, 400/401/403/409/422, 500/503 y timeout | Solo 2xx contractual marca recibido; rechazo no se pierde; retry mantiene deduplicación |
| Correlación | La creación la copia, no todas las respuestas (`services/central-api/src/central_api/api/dependencies.py:98-106`; `services/central-api/src/central_api/api/bots.py:534-539,741-745`) | Requests con/sin UUID, formato inválido, handlers de éxito y error | Header reflejado y respuesta/error acordados de manera global, o documentación explícitamente acotada |

## 4. Alcance de verificación

La coordinación informa 1.540 pruebas aprobadas, 30 omitidas y 15 subtests aprobados adicionales al ejecutar paquetes de prueba por separado. La colección conjunta falló por conflictos/imports de tests, no como una corrida integral aprobada. También ejecutó reproducciones locales/sintéticas que confirman el callback `<500`, la liquidación por categoría y la admisión tardía de `ccma/consultar` ([`Pruebas existentes`](validacion.md#pruebas-existentes) y [`Casos reproducidos sin servicios externos`](validacion.md#casos-reproducidos-sin-servicios-externos)). No se accedió a portales ni se ejecutaron consultas, pagos o trámites reales; las pruebas omitidas, la operación desplegada y los efectos remotos requieren validación aparte ([`Límites`](validacion.md#límites)).
