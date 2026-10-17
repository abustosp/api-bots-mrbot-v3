# Verificación independiente de correcciones V3, 17 de octubre de 2026

## Dictamen y alcance

**No corresponde afirmar que todos los casos están resueltos en producción.**
Las correcciones compartidas de navegación están presentes en el código y en
`testing-fixes8`, y varias consultas reales vuelven a entregar archivos. El
worker de producción en `api2` conserva otro código. También se encontraron
fallos de la herramienta de diagnóstico y un defecto real de subida compartido.

Esta ronda ejecutó operaciones de consulta/descarga, sin pagos, presentaciones,
compensaciones nuevas, cambios de contraseña, cambios de cuenta ni despliegues.
Se consultaron las bases SQLite en modo de solo lectura. Las evidencias fiscales
quedaron en directorios privados del scratch, fuera del repositorio.

## Despliegue observado por SSH

| Contenedor en api2 | Imagen observada |
|---|---|
| `mrbot-central-testing-worker` | `mrbot-bot-worker:testing-fixes8` |
| `mrbot-central-testing` | `mrbot-central-api:testing-fixes8` |
| `mrbot-v3-bot-worker-api2` | `mrbot-bot-worker:20261015_233423` |

Se compararon SHA-256 de los archivos instalados, no solamente los tags:

| Archivo | Local y testing | Producción |
|---|---|---|
| `runtime/arca_login.py` | `dc3a3f90ca2256dc` | `547e324024cd0cd2` |
| `runtime/portals/base.py` | `b0a34d36ad28d204` | `5d6deacc7a17e438` |
| `runtime/portals/consulta_pagos_vep.py` | `1b3bb0e9f0a399b8` | `b095f67d11745901` |
| `bots/aportes_en_linea/plugin.py` | `c06c27a40800aaf9` | mismo hash |
| `bots/mis_facilidades/plugin.py` | `58e8ecea8041fdb5` | `02c0c79368b079b7` |

Son prefijos de hash para lectura. **Testing valida esos fixes, no el worker
viejo de producción.** Los cambios nuevos de `runtime/context.py` y del diagnóstico
`portal_iva_period_save_failed` de esta ronda tampoco están desplegados en
testing ni producción.

## Matriz de nuevas ejecuciones locales

Cada resultado corresponde a la muestra indicada, no a una garantía sobre todas
las cuentas, períodos, relaciones y formatos. Los números cuentan archivos
físicos locales. Los fallos de entrada del runner se separaron de la navegación.

| Bot | Resultado de esta ronda | Archivos | Observación |
|---|---|---:|---|
| `facturometro` | OK | 1 | También pasó una segunda cuenta de otro corpus |
| `certificado_mipyme` | No validado de punta a punta | 0 | `arca_service_not_visible` con la cuenta disponible |
| `pago_devoluciones` | Bloqueado por credenciales | 0 | Dos combinaciones distintas rechazadas por ARCA |
| `hacienda` | Bloqueado por relación/nombre | 0 | Tras corregir el runner: `represented_name_not_selectable` |
| `liquidacion_granos` | OK | 5 | 206,8 s |
| `compensaciones` | Sesión no válida durante consulta | 0 | Modal `#sessionError`, `portal_step_consultar` |
| `srt` | No validado de punta a punta | 0 | Una clave rechazada y otra cuenta sin servicio visible |
| `libros_portal_iva` | OK | 2 | 184,9 s |
| `mis_facilidades` | PARCIAL | 4 | Entregó archivos sin perder el resultado por una sección fallida |
| `sifere` | OK | 102 | 375,6 s con otra credencial tras rechazo |
| `declaracion_en_linea` | OK | 14 | Rango 202601 a 202607, 445,9 s |
| `portal_iva` | Pendiente: libro no accesible | 0 | `portal_iva_book_not_visible` en 202609, pantalla Datos Iniciales |
| `sct` | OK | 1 | 79,6 s, formatos/secciones de la fila fuente |
| `vep/consulta-pagos` | OK | 1 | CSV de 84 filas y 6.803 bytes |
| `aportes_en_linea` | OK | 1 | XLS de 169.694 bytes tras rechazos de credenciales anteriores |

Resultado de la muestra: **9 de 15 bots entregaron archivos** (8 `OK` y
`mis_facilidades` `PARCIAL`). Los otros 6 no se consideran verificados de punta
a punta. No se concluye que los seis tengan un bug de código.

No se interpreta automáticamente `arca_service_not_visible` como prueba de que
el bot está bien: la consulta de esa cuenta queda sin validar. Tampoco se
clasifica una credencial rechazada como regresión de descarga.

## E2E nuevo contra testing en api2

Se usó la clave de testing ya disponible, sin crear usuarios ni modificar cuotas.

| Endpoint | Job | Resultado | Descarga comprobada |
|---|---|---|---|
| `/vep/consulta-pagos` | `01a14bf5-62de-73df-b43e-846d37c3bf31` | COMPLETO / OK | HTTP 200, `text/csv`, 6.803 bytes |
| `/aportes-en-linea/consulta` | `01a14bf5-ed31-776e-9550-c159165623d8` | COMPLETO / OK | HTTP 200, `application/vnd.ms-excel`, 169.694 bytes |

En ambos casos el SHA-256 publicado por la central coincide con el archivo
obtenido localmente. No se dio por válido solamente un estado `COMPLETO`.
Se consultaron además jobs y artefactos previos de la base de testing, pero no
se presentan como ejecuciones nuevas de esta ronda.

### Contraste VEP ejecutado también en V1 y V2

Se ejecutaron los loggers de V1 y V2 con la misma credencial, representado y
período `72` que en V3, secuencialmente y sin MinIO. Ambos terminaron
`success=True`, sin errores, con un CSV de 6.803 bytes. Los SHA-256 de ambos
CSV coinciden con V3 local y con la central de testing: **V1, V2 y V3 entregaron
el mismo archivo**, no solamente la misma cantidad de bytes.

## Por qué V1/V2 parecían funcionar y V3 fallaba

1. **Regresiones reales de navegación.** El port había perdido esperas y
   alternativas de apertura del servicio. Se verificó que el código actual
   recupera DOM + `networkidle` acotados y los fallbacks del popup. V3 también
   abortaba cuando no existía selector de representado. El fix actual tolera
   su ausencia, como V1/V2, y mantiene el rechazo cuando un selector existente
   excluye al representado. Esto no sustituye una comprobación de identidad en
   cada portal.
2. **VEP tenía una selección no confirmada.** Despachar un clic no garantiza que
   la SPA haya seleccionado el CUIT. El código actual comprueba el fake-input
   antes de filtrar y exportar. La prueba real local y el E2E entregaron el CSV.
3. **Semántica de éxito distinta.** `success` legacy no demuestra un flujo sin
   incidencias. Comprobación agregada de `sql_app_mrbot1.db`: SCT tiene 609 filas
   `success`, 278 con `error_message` no vacío. En `sql_app_api2.db` son 938 y 510.
   Esos mensajes pueden incluir advertencias o errores parciales. No se
   contabilizan todas esas filas como fallos, pero tampoco como éxito íntegro.
   V3 expone errores y `PARCIAL` de forma más estricta.
4. **Sesión vencida de Compensaciones, deuda heredada.** V1/V2 recuperan un popup
   inicial redirigido al login, pero no la modal `#sessionError` aparecida a mitad
   de consulta. Ese caso tampoco se recupera en V3. No es evidencia de una nueva
   regresión exclusiva del port. No se eliminó la modal para fingir una consulta
   válida ni se añadió un reintento global que pueda duplicar operaciones.
5. **Infraestructura y datos siguen importando.** Claves antiguas, relaciones no
   disponibles, servicios no visibles y períodos no ofrecidos deben separarse
   de las regresiones. La política actual tampoco consume automáticamente toda
   marca `reintentable=True` de los errores del bot.

6. **Portal IVA sigue pendiente en una rama concreta.** Con la entrada original
   ya bien traducida, el período 202609 supera la selección pero queda en
   `verDatosInicialesPresentacion.do`, con formulario de Datos Iniciales y
   `#btnGuardar` deshabilitado. No existen todavía enlaces al libro de ventas.
   Antes del ajuste de esta auditoría, V3 capturaba el fallo de guardar en
   `preparar_periodo` y continuaba, por lo que acababa mostrando
   `portal_iva_book_not_visible`. El V2, en cambio, espera que
   `#btnGuardar` se habilite antes de avanzar (`app/bot/portal_iva_bot.py`,
   `prepare_period`). Se reprodujo el error silenciado con una prueba aislada:
   un timeout del guardado no interrumpía el flujo. El cambio mínimo ahora
   devuelve `portal_iva_period_save_failed`, sin exponer detalles de la excepción
   ni continuar hacia descargas/importaciones. Se conserva la rama sin botón
   de guardado para libros existentes. Afecta también a `carga_portal_iva`, que
   delega en este método compartido. Pasaron los tres casos nuevos (guardado
   correcto, guardado fallido y libro sin botón) y las 41 pruebas de ambos flujos.
   Esto corrige el diagnóstico, no habilita el formulario fiscal ni demuestra
   una descarga real de ese período. No se forzó Registrar ni se eligieron
   opciones fiscales nuevas para obtener una prueba verde. Este caso no puede
   darse por resuelto por los éxitos anteriores de otras cuentas/períodos.

La revisión independiente contrastó el código común, VEP/Aportes y los flujos
legacy de subida de Facturómetro, Pago de Devoluciones, Compensaciones,
Liquidación de Granos y SRT. No se reejecutó cada rama de cada bot V1/V2.

## Defecto adicional real: subida de archivos

En `runtime/context.py::ArtifactStore.upload`, el PUT no comprobaba la respuesta
HTTP. Una respuesta 403/500 podía devolver una referencia de archivo como si se
hubiera subido. Un `httpx.ConnectError` escapaba como excepción genérica, que en
Aportes podía terminar como `INTERNAL` porque su bloque de subida solo convertía
`ValueError`.

Se reprodujo antes de corregir: **3 casos rojos** para HTTP 403, HTTP 500 y error
de transporte. El caso HTTP 200 ya pasaba. El arreglo mínimo está en el único
punto compartido: `response.raise_for_status()` y conversión de `httpx.HTTPError`
a `ArtifactUploadError`, con diagnóstico fijo y sin URL firmada.
Después pasaron los 4 casos, los tests de Aportes y el E2E de worker/central.
**Es un mecanismo probado, no la atribución demostrada del `internal` histórico:**
para eso faltan el job y su traza originales. El E2E fiscal nuevo en testing se
hizo sobre `fixes8`, no sobre este cambio aún no desplegado.

## Correcciones del diagnóstico realizadas en esta ronda

- `creds.py`: seleccionar por timestamp antes del `LIMIT` y ordenar entre bases.
  Antes el primer registro dependía del nombre de la base y del ID. Se mantuvo
  el soporte de timestamp ISO de SQLite y fallback por ID, sin nuevos proveedores.
- `e2e_testing.py`: `COMPLETO/ERROR` ya no produce exit code 0.
- `run_bot.py`: traducir `nombre_representado` a `denominacion` para Hacienda.
- `run_bot.py`: conservar `descarga_csv_ventas/compras` al armar las banderas
  canónicas de Portal IVA, respetando explícitamente los valores falsos.

Las dos últimas fallas pertenecían al runner local, no al endpoint de producción.
Las credenciales se eligieron por recencia de registros disponibles. Para VEP y
MiPyME faltaban filas de esa operación en `/home/abp/Desktop/api/DBs`, por lo que
se usó también el corpus legacy ya configurado en `creds.py`, bajo
`/home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/BD-api-v1`.

## Trazabilidad de requisitos y salidas modificadas

La raíz privada de las rutas abreviadas siguientes es
`/home/abp/.jcode/scratch/verificacion-20261017-`. Un bloqueo observado cuenta
como evidencia del límite, no como requisito funcional satisfecho.

| Requisito de verificación | Evidencia concreta | Aceptación observada o restricción |
|---|---|---|
| Facturómetro | `2221/facturometro/summary.json`, `2218/facturometro/summary.json`, `cli-acceptance/facturometro-acceptance.json` | Dos cuentas locales y una descarga API con SHA-256 idéntico |
| Certificado MiPyME | `2221/certificado_mipyme/summary.json` | Bloqueado: servicio no visible, sin descarga aceptada |
| Pago Devoluciones | `2221/pago_devoluciones/summary.json` | Bloqueado: dos credenciales rechazadas |
| Hacienda | `2232/hacienda/summary.json` | Payload ya válido, relación/nombre no seleccionable |
| Liquidación Granos | `2221/liquidacion_granos/summary.json` | Cinco archivos reales locales |
| Compensaciones | `2221/compensaciones/summary.json` y HTML privado | Sesión vencida intercepta consulta, sin aceptación funcional |
| SRT | `2221/srt/summary.json` | Credencial rechazada o servicio ausente, sin aceptación funcional |
| Libros Portal IVA | `2221/libros_portal_iva/summary.json` | Dos archivos reales locales |
| Mis Facilidades | `2221/mis_facilidades/summary.json` | Cuatro archivos reales, aceptación parcial, no todas las secciones |
| SIFERE | `2232/sifere/summary.json` | 102 archivos reales después de descartar credencial rechazada |
| Declaración en Línea | `2232/declaracion_en_linea/summary.json` | Catorce archivos reales en el rango solicitado |
| Portal IVA | `2242/portal_iva/summary.json` y `1/final_servicio.html` en esa carpeta | Bloqueado en Datos Iniciales, Registrar deshabilitado. No se eligieron opciones fiscales ni se forzó Registrar |
| SCT | `2232/sct/summary.json`, `cli-acceptance/sct-acceptance.json`, `cli-acceptance/sct-upload-timeout.json` | Archivo local y descarga API tras reintento. Timeout transitorio de storage comprobado en intento anterior, no certifica los nueve formatos/secciones |
| VEP consulta-pagos | `2218/consulta_pagos_vep/summary.json`, `legacy/v1/summary.json`, `legacy/v2/summary.json`, `e2e/consulta_pagos_vep.json` | CSV idéntico por SHA-256 en V1/V2/V3 local y API testing |
| Aportes en Línea | `2218/aportes_en_linea/summary.json`, `e2e/aportes_en_linea.json` | XLS real local y API testing, SHA-256 validado. No demuestra causa del INTERNAL histórico |
| Comparar V1/V2 y revisar api2 | Secciones de hashes y diferencias de este informe, más loggers VEP | Contraste exhaustivo solo VEP, análisis de código parcial para otros flujos. Producción tiene código distinto |
| Usar credenciales recientes sin divulgarlas | Tests de ordenación y metadatos de fuente/fecha en cada summary | Se priorizó timestamp antes del LIMIT. Recencia de un registro no garantiza clave vigente |

Cada cambio tiene además una comprobación de su salida observable. En
particular, el exit code E2E se observó en SCT real: 1 para COMPLETO/ERROR
(`cli-acceptance/sct-upload-timeout.json`) y 0 para su reintento con descarga
(`cli-acceptance/sct-acceptance.json`).

| Cambio | Comprobación exacta | Mejora observada y límite |
|---|---|---|
| Selección de credenciales | `tests/test_local_run_creds.py::test_filas_ordena_por_timestamp_antes_del_limite_y_entre_bases` y `test_filas_usa_id_como_fallback_si_no_hay_timestamp` | Registro nuevo gana aunque ID/base induzcan otro orden, fallback sin timestamp. Casos SQLite temporales, además usados por las consultas reales |
| Exit code E2E | `tests/test_local_run_e2e.py::test_main_exige_estado_y_resultado_exitosos` | COMPLETO/ERROR, FALLIDO, CORRIENDO y timeout devuelven 1. OK/PARCIAL terminales devuelven 0. Estados anómalos son pruebas aisladas, no se fabricaron jobs fiscales para producirlos |
| Denominación Hacienda | `tests/test_local_run_payload.py::test_payload_hacienda_deriva_denominacion_sin_pisar_el_canonico` y rerun `2232/hacienda` | Dejó de fallar ENVELOPE_INVALID y llegó a selección de relación real. La consulta sigue bloqueada allí |
| Flags Portal IVA | `tests/test_local_run_payload.py::test_payload_portal_iva_conserva_flags_y_prioriza_canonicos` y rerun `2242/portal_iva` | Dejó de rechazar la entrada y llegó al período/formulario real. Respeta false y valores canónicos |
| ArtifactStore | `services/bot-worker/tests/test_artifact_manifest_slots.py::test_upload_confirma_http_y_normaliza_error_sin_url_firmada` y `storage-acceptance/result.json` | 403/500 ya no retornan referencia ficticia, transporte ya no escapa genérico. 403 y conexión rechazada se observaron también sin mocks, contra storage real y socket TCP respectivamente. 500 permanece prueba aislada |
| Guardado Portal IVA | `services/bot-worker/tests/test_portal_portal_iva.py::test_preparar_periodo_no_oculta_fallo_al_guardar` y `test_preparar_periodo_sin_guardar_permite_libro_existente` | Timeout ahora interrumpe con diagnóstico de guardado, éxito y libro existente conservan contrato. Tres casos aislados, no equivalen a descarga fiscal real posterior al fix |

### Aceptación adicional mediante la CLI pública del proyecto

Se ejecutó `tools/local_run/e2e_testing.py` como proceso real, con `--body-file`,
clave por entorno y API de testing, sin sustituir HTTP ni el worker:

- **Facturómetro:** job `01a14c15-9c44-741a-80ac-13d665508977`, POST 202,
  `COMPLETO/OK`, exit code 0, 38,4 s. Descarga HTTP 200 de 28.243 bytes,
  SHA-256 publicado idéntico al descargado. Evidencia:
  `cli-acceptance/facturometro-acceptance.json`.
- **SCT, fallo de integración real:** job
  `01a14c17-2e83-784e-b9e8-55ec101ecc2d`, POST 202, `COMPLETO/ERROR`,
  `target_unavailable`, exit code **1**, 64,4 s, sin archivo publicado.
  Los logs del worker prueban `httpx.ConnectTimeout` en
  `sct/plugin.py::_descargar -> ArtifactStore.upload -> http.put`,
  no un fallo de navegación ARCA. En `fixes8` ese timeout de almacenamiento
  se transforma en `TargetUnavailableError`. Este caso confirma que también
  un problema secundario de storage puede aparentar un fallo del organismo.
  El fix local compartido ahora lo tipa como `ARTIFACT_UPLOAD_FAILED`, pero
  no soluciona por sí mismo la conectividad del servidor. Evidencia:
  `cli-acceptance/sct-upload-timeout.json` y log privado
  `/home/abp/.jcode/scratch/sct-testing-acceptance-private.log`.
- **SCT, reintento controlado de solo consulta:** job
  `01a14c19-7be8-728d-b023-b26de2580c74`, POST 202, `COMPLETO/OK`, exit code 0,
  32,3 s. Descarga HTTP 200 de 5.120 bytes con SHA-256 publicado idéntico.
  No se cambió el servidor entre intentos. Este resultado demuestra que el
  timeout anterior fue transitorio en esta muestra, no que la conectividad
  esté resuelta ni que el worker tenga reintento automático. Evidencia:
  `cli-acceptance/sct-acceptance.json`.
- **Borde público observado:** un cuerpo con nombres de credenciales de otra
  familia fue rechazado HTTP 422 y la CLI devolvió 1. Se corrigió la invocación
  al contrato V2 de estas rutas: `cuit_login` y `clave`. En SCT deben enviarse
  las banderas públicas, por ejemplo `deudas_excel_minio=true`; los campos
  canónicos `secciones/formatos` por sí solos no solicitaron archivos en esta
  ruta de compatibilidad. El job `01a14c15-9d0e-7b62-8d38-012672b4ebd0`
  dio `COMPLETO/OK` sin archivos, por lo que **no se aceptó como descarga**.
  Se conserva `cli-acceptance/sct-canonical-no-artifact.json`.

Estas observaciones cierran el recorrido CLI -> API -> worker -> portal ->
archivo para Facturómetro y SCT, y prueban el rechazo de errores de verdad en
la CLI: el SCT `COMPLETO/ERROR` devolvió 1, mientras su reintento con descarga
real devolvió 0. Junto con VEP/Aportes son cuatro bots verificados mediante API.
No convierten los nueve éxitos locales en nueve éxitos de API. El estado
terminal y el exit code no sustituyen la comprobación de archivos solicitados.

### Límite real de aceptación de los cambios nuevos

El almacenamiento real respondió **HTTP 403** a un PUT con firma deliberadamente
inválida y clave aleatoria de verificación. El `ArtifactStore` local corregido
emitió `ARTIFACT_UPLOAD_FAILED / artifact_upload_http_failed`, sin referencia
exitosa ni URL firmada en el error. Se volvió a descargar el archivo autorizado
original y su SHA-256 permaneció idéntico. No se escribió ni reemplazó un objeto
fiscal. Una conexión TCP real rechazada produjo también el error tipado. La
prueba usa el código de producción del componente y el storage real, pero no
es un job completo del worker desplegado con este cambio.

El cambio de guardado de Portal IVA sigue **bloqueado para aceptación fiscal**:
la pantalla observada exige resolver Datos Iniciales y tiene Registrar
deshabilitado. Cambiar elecciones fiscales para forzar una prueba verde queda
fuera de esta verificación. El fix local y el de ArtifactStore no están en la
imagen testing observada. No se los certifica como desplegados ni se confunde
una suite verde con paridad fiscal completa de los 15 bots.

## Pruebas y límites

- Suite completa del worker después del fix de subida: **781 passed**.
- Suite completa del worker repetida tras el diagnóstico de guardado Portal IVA:
  **784 passed**, 6 warnings. Esta última corrección solo se verificó en pruebas,
  sin repetir el flujo fiscal real ni desplegarla.
- Central + raíz + compat runner, al inicio: **733 passed, 28 skipped**.
- Raíz + compat runner, repetida después de todos los cambios: **130 passed**.
- Pruebas específicas de los tres archivos de tooling tras el último cambio:
  **13 passed**.
- Comprobación focalizada de todos los cambios y sus callers: **65 passed**
  (creds, payload, CLI E2E, ArtifactStore, Portal IVA y Carga Portal IVA).
- Empaquetado real: `uv build --offline --wheel --out-dir <scratch> services/bot-worker`
  produjo `mrbot_bot_worker-3.0.0-py3-none-any.whl`. Se importaron `ArtifactStore`
  y `PortalIvaPortal` desde el wheel con Python aislado (`-I`), se comprobó
  `bot-worker = bot_worker.main:run` y se compararon los bytes empaquetados con
  ambos archivos fuente modificados. No requirió dependencias nuevas ni red.
  El intento previo con `python -m pip wheel` no pudo ejecutarse porque este
  venv no tiene pip, por eso se usó el backend declarado mediante uv.
- La matriz local tuvo una interrupción externa por el límite de 600 segundos
  del lanzador. Se retomaron las consultas pendientes. No se atribuye esa
  interrupción a los bots ni se cuentan archivos incompletos como éxito.

Evidencia privada: prefijo `/home/abp/.jcode/scratch/verificacion-20261017-`,
con tandas `2218`, `2221`, `2232`, `2242`, `e2e` y `legacy`, más logs de suites. Los archivos
`report.json` contienen la información de ejecución necesaria para el operador,
pero no se incorporaron al repositorio por sus datos fiscales.

Pendiente de operación: publicar una imagen que incluya los cambios aprobados y
verificarla en testing antes de promoverla. No se desplegó ni reinició producción.
No se considera demostrada la causa exacta del `internal` original, ni se
promete paridad de todas las opciones legacy: por ejemplo, Aportes acepta el
flag base64 pero V3 no lo devuelve, y la selección de representado sigue siendo
una limitación heredada que requiere revisión separada si se necesitan terceros.
