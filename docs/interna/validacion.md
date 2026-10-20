# Evidencia de validación

Fecha: **20 de octubre de 2026**. Revisión local de contratos y documentación. No se ingresó a portales de organismos ni se ejecutaron trámites, pagos o consultas con credenciales reales.

## Inventario confirmado

Se importaron `CATALOGUE`, `OPERATIONS` y el registro real de plugins:

- 34 bots públicos y 45 operaciones públicas.
- 34 plugins de catálogo. Diferencia de nombres entre catálogo y registro: vacía.
- Una variante de AGIP no publicada como bot independiente.
- Las operaciones `consulta` de `consulta_cuit` y `mis_comprobantes` requieren traducción del verbo al ejecutar. No se descartaron del inventario.

## Pruebas existentes

Se utilizó el entorno virtual existente del repositorio, sin instalar dependencias ni cambiar su configuración.

| Comando | Resultado observado |
|---|---|
| `.venv/bin/python -m pytest -q` | Falló la colección por módulos de test homónimos, incluido `test_worker_capacity.py`. No representa pruebas funcionales fallidas. |
| `.venv/bin/python -m pytest -q --import-mode=importlib` | Falló la colección por imports de utilidades locales como `fakes_playwright` y `runner`. |
| `.venv/bin/python -m pytest -q packages/mrbot-contracts/tests` | 11 aprobadas. |
| `.venv/bin/python -m pytest -q services/bot-worker/tests` | 784 aprobadas. |
| `.venv/bin/python -m pytest -q services/central-api/tests` | 615 aprobadas, 30 omitidas y 5 subtests aprobados. |
| `.venv/bin/python -m pytest -q tests` | 119 aprobadas y 10 subtests aprobados. |
| `.venv/bin/python -m pytest -q tools/compat_runner` | 11 aprobadas. |

**Total por invocaciones aisladas:** 1.540 pruebas aprobadas, 30 omitidas y 15 subtests aprobados adicionales. Las omitidas no se consideran verificadas. Se observaron advertencias de deprecación y una opción de configuración de pruebas no reconocida. No se alteraron tests para ocultarlas.

## Casos reproducidos sin servicios externos

| Caso | Método | Observación |
|---|---|---|
| Confirmación de callback | `send_result()` con cliente falso y una respuesta por código. | 200, 302, 401, 403, 409 y 422 devolvieron `True`; 503 devolvió `False`. |
| Crecimiento de cachés | Admitir y liberar 1.000 `LocalJob`; registrar 1.000 resultados en `ResultStore`. | 0 activos, 1.000 admisiones cacheadas y 1.000 resultados cacheados. |
| Colisión en ZIP | ZIP temporal con `carpeta-a/reporte.txt` y `carpeta-b/reporte.txt`, usando `expandir_zip()`. | Se devolvió el mismo nombre dos veces. El archivo final contenía solo el segundo contenido. |
| Categorías y cuota | Handler real `report_result()` con persistencia desactivada, job local y operaciones de liquidación interceptadas. | `CHROMIUM_CRASHED`, `ARTIFACT_UPLOAD_FAILED` e `INTERNAL` eligieron confirmación. Solo `INFRASTRUCTURE_ERROR` eligió reembolso. La condición persistente equivalente se revisó en código, no con una cuenta real. |
| Falta de campo requerido en ruta canónica | Handler real `create_job()` de `ccma/consultar`, `payload: {}`, credenciales ficticias y reserva interceptada. | Respondió 202/PENDIENTE. El payload conservado produjo `InvalidInputError` en el plugin por faltar `representado_cuit`. |
| Defaults efectivos de SCT/SIPER | Normalizadores reales, separación de credenciales y `plugin.validate()`, enviando solo `representado_cuit`. | SCT agregó las tres secciones, `formatos: ["xlsx"]` y `subir: false`. SIPER agregó `subir: false`, detalle y categorías activos. El default del schema aislado no describe el pedido canónico completo. |
| CUIT de sesión no sustituye al representado en SIPER canónico | `payload: {}` con CUIT únicamente en `credentials`, aplicando el mismo pipeline local. | La validación falló por faltar `representado_cuit`; el fallback del normalizador solo ve campos del payload. |
| Precedencia de aliases de VEP CCMA | Payload con `representado_cuit` y `cuit_representado` distintos, `subir_pdf: true` y `minio_upload: false`. | El normalizador conservó los nombres canónicos y descartó los aliases; el plugin validó el resultado. No todas las contradicciones se rechazan: algunas desaparecen antes del schema. |

Las reproducciones con transporte falso, reserva interceptada o estado en memoria son **pruebas sintéticas**, no una validación del despliegue completo. La limpieza tras callback agotado, la pérdida de advertencias y el alcance del timeout se trazaron en el runner y el receptor terminal.

## Ejemplos generados por el catálogo actual

Se verificaron los 45 ejemplos producidos por `_operation_documentation()` aplicando:

1. `normalize_v2_payload()`.
2. `normalize_payload_and_credentials(..., require_encryption=False)` para separar datos ficticios, sin exigir una clave de custodia del despliegue.
3. Traducción local de referencias `*_key` a nombres de entrada según `KEY_FIELDS`, y nombres locales para `archivos`. No se descargó contenido ni se comprobó existencia o pertenencia de objetos.
4. Traducción del verbo con `worker_operation()`.
5. `plugin.validate()`, **sin llamar a `execute()`**.

Resultado: **42 válidos y 3 incompletos**:

| Bot/operación | Motivo del rechazo |
|---|---|
| `carga_portal_iva/cargar` | El ejemplo no incluye un par de TXT o un CSV de apertura requerido. |
| `controladores_fiscales/presentar` | El ejemplo no incluye `archivos`. |
| `vep_archivo/generar` | El ejemplo no permite obtener el `archivo_path` requerido. |

Este resultado pertenece a los ejemplos **generados por el código previo**, no a una promesa de que todas las solicitudes admitidas por la API pasan validación. Las fichas nuevas deben explicar la carga previa y proporcionar referencias ficticias completas donde corresponda.

## Verificadores históricos

- `infra/validate-code-blocks.py`: los 189 bloques de planes que revisa parsean correctamente. Ese resultado no equivale a validar todos los bloques de la nueva carpeta `docs/`.
- `infra/check-consistency.py`: se ejecutó durante la creación de las fichas y reportó errores, entre ellos enlaces todavía no creados, enlaces históricos y estilo en archivos de terceros bajo entornos locales. No se considera un pase de aceptación ni se atribuye todo el resultado al estado anterior. La nueva documentación requiere su comprobación focalizada de enlaces y contenido una vez terminada.

## Aceptación de la documentación nueva

La carpeta contiene 78 páginas Markdown: 34 fichas externas, 34 internas y las guías generales. El inventario de ambas carpetas de bots coincide con el catálogo activo y las fichas nombran las 45 operaciones públicas.

La revisión focalizada comprueba:

- Parseo de los bloques JSON y sintaxis de los tres bloques de terminal con `bash -n`, sin ejecutar sus comandos.
- Resolución de enlaces relativos y anchors. Ningún enlace del paquete externo requiere salir de `docs/externa/`.
- Columnas de las tablas, ausencia de em dashes y ausencia de nombres de tecnologías, rutas fuente o terminología de ejecución interna en el paquete externo. Los aliases tecnológicos heredados quedan solo en la documentación interna.
- Existencia de las fuentes citadas y límites de los rangos completos de líneas. Esta comprobación no demuestra por sí sola que cada frase esté contenida en ese rango, por lo que también se contrastó el flujo por bot.
- Ejemplos de solicitud mediante normalizadores reales, traducción local de referencias y `plugin.validate()`. Se excluyen fragmentos señalados como incompletos y respuestas ilustrativas. No se llama a `execute()`, no se cargan archivos ni se comprueba la pertenencia o existencia de referencias ficticias.
- Sondeos de campos con omisión, `null`, cadena vacía, `false` y lista vacía, sobre cuerpos ilustrativos válidos. Sirvieron para corregir, entre otras diferencias, `null` de subida en Liquidación de Granos, `incluir_json` en ambos VEP y `subir_constancia` en Controladores. Se distingue la ruta canónica del alias plano que materializa defaults y excluye valores `None`.

Los sondeos son comprobaciones locales del contrato de entrada, no una prueba exhaustiva de todas las combinaciones de campos ni del comportamiento del organismo.

Resultado del cierre local del 20 de octubre de 2026:

- 78 páginas, 122 enlaces relativos y 16 anchors comprobados, sin errores de destino o escapes del paquete público.
- 49 bloques JSON parseables y 39 cuerpos completos de solicitud que pasan la normalización y validación local correspondientes.
- Las 45 operaciones tienen al menos un cuerpo compatible entre los ejemplos de su ficha. Algunos cuerpos sirven para más de una operación, por lo que no son 45 ejemplos distintos ni 45 ejecuciones reales.
- 92 tablas con columnas consistentes y tres bloques de terminal con sintaxis válida.
- Las referencias completas a fuentes se comprobaron por existencia y límites de líneas, sin rangos abiertos pendientes. No se encontraron nombres de tecnologías internas en el paquete externo.
- `git diff --check` sin errores. Los cambios de esta entrega se limitan a `README.md` y documentación Markdown bajo `docs/`.

Estos resultados corresponden a la documentación nueva. No sustituyen el hallazgo separado del catálogo generado existente, cuyos ejemplos normalizados válidos fueron 42 de 45.

## Aceptación por HTTP de la aplicación real

El 20 de octubre de 2026, entre las 11:52 y las 11:54 UTC, se arrancó la fábrica real `central_api.main:create_app` con Uvicorn en `127.0.0.1:18765`, incluyendo su ciclo de arranque. Se usó un entorno de proceso limpio, sin `DATABASE_URL`, con `SCHEDULER_ENABLED=false`, `AUTOSCALE_ENABLED=false`, inventario de workers vacío y sin servicios de correo o almacenamiento configurados. Se configuraron secretos efímeros locales para exigir autenticación real. No se sustituyeron handlers, dependencias ni transportes de la aplicación. El cliente de comprobación solo envió HTTP a loopback y leyó los ejemplos Markdown.

Se creó una cuenta efímera mediante `POST /admin/users`, con `account_type=PRUEBA`, clave ilustrativa y `enviar_credenciales=false`. La respuesta confirmó `enviadas=false`. Los usuarios, reservas y jobs existieron únicamente en la memoria de ese proceso. El refresco APOC tenía URL vacía y registró `UnsupportedProtocol`, sin consultar el organismo. El proceso fue detenido al terminar.

La pasada final completó **57 solicitudes HTTP con las respuestas esperadas**, además del alta local y la comprobación inicial de vivacidad:

| Recorrido documentado | Observación de la aplicación real |
|---|---|
| Acceso sin credenciales, con Bearer o con clave incorrecta | Los tres casos devolvieron `401`. |
| Autenticación Basic y `email` + `X-API-Key` | Ambas formas devolvieron `200` al consultar el catálogo. |
| Catálogo y detalle | El catálogo devolvió 34 bots y 45 operaciones, todos presentes en las fichas. Los 34 `GET /api/v3/bots/{bot}` devolvieron `200`. |
| Documentación interactiva y contrato | `/docs`, `/redoc` y `/openapi.json` devolvieron `200`. OpenAPI publica la ruta genérica `/api/v3/bots/{bot}/{operacion}`, no 45 rutas expandidas. |
| Ejemplo de creación de la guía externa | El cuerpo se extrajo literalmente del `curl` de `docs/externa/api.md`. El POST canónico devolvió `202`, `job_id` y `PENDIENTE`. |
| Repetición con la misma intención | Mismo cuerpo y misma `Idempotency-Key`: `202` con el mismo `job_id`. Cuerpo cambiado y misma clave: `409`. |
| Reserva de uso | Entre el resumen anterior a la creación y el posterior al replay, `reservadas` aumentó exactamente una unidad y `disponibles` disminuyó exactamente una. No se produjo una segunda reserva por el replay. |
| Consulta individual, listado y lote | Las tres consultas devolvieron `200` e incluyeron el identificador creado. La consulta pendiente devolvió `result=null`, `data=null` y `files=[]`. |
| Identidad de cuenta | `GET /api/v3/usuarios/me` devolvió `200`. |
| Cancelación pendiente | El POST de cancelación devolvió `200`; una consulta posterior confirmó `CANCELADO`, sin ejecutar el bot. |
| Alias plano | `POST /api/v3/consulta_cuit/individual` con el payload plano documentado devolvió `202`. |
| Cuerpo raíz inválido y bot inexistente | `payload` como lista devolvió `422`; un bot inexistente devolvió `404`. |
| Disponibilidad completa | `/ready` devolvió HTTP `200` con `ready=false` y razón `DATABASE_URL no configurada`. No se confunde vivacidad con disponibilidad de producción. |

Esto añade evidencia de uso a la comprobación de textos: un consumidor puede seguir el ejemplo publicado para autenticarse, descubrir operaciones, crear una ejecución, reintentar sin duplicarla, consultar y cancelar una ejecución pendiente. No demuestra que los 45 bots produzcan salidas reales correctas, que las credenciales fiscales funcionen o que la ejecución persistente sea equivalente al modo local. La primera comprobación del cliente asumía rutas OpenAPI expandidas y se corrigió para usar el catálogo real, sin cambiar la aplicación ni atribuir esa suposición a un fallo del producto.

## Límites

No se levantó infraestructura externa, no se ejecutaron migraciones y no se cambió código operativo. La aplicación local descrita arriba no tuvo workers ejecutores. Las pruebas con base de datos que la suite omitió, el almacenamiento real, la entrega tras caída de procesos, los portales, la disponibilidad por cuenta, la concurrencia de producción y los efectos fiscales requieren otra validación en un entorno autorizado.
