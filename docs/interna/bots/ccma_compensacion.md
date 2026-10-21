# Auditoría interna: `ccma_compensacion`

## Identidad y V3

- Catálogo central: `ccma_compensacion`, operación `compensar`; `services/central-api/src/central_api/api/bots.py:329-339`, `378-386`.
- Ruta de creación canónica: `POST /api/v3/bots/{bot}/{operacion}`; el cuerpo separa `payload` y `credentials` y despacha `submit_job`; `services/central-api/src/central_api/api/bots.py:194-208`, `748-817`.
- Plugin activo y manifiesto `operaciones=("compensar",)`, clase de idempotencia `EFECTO`: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:80-108`.
- Directorio activo coincide con catálogo: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/`. Discrepancia de nombre solo en árbol heredado de desarrollo: `bots_dev/ccma_compenscion/` (falta la segunda `a` de `compensacion`); el registro activo usa el nombre correcto, `services/bot-worker/src/bot_worker/bots/_plugins.py:90-100`.

## Flujo esquema → plugin → portal

1. `schema.py` exige CUIT representado, admite aliases, elimina campos del sobre, normaliza flags nulos de `compensar`/`confirmar` a sus defaults y fuerza movimientos si se solicita PDF: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/schema.py:33-75`.
2. El plugin valida operación y modelo, requiere credenciales, abre sesión fiscal y la cuenta CCMA; calcula deuda desde `04/2000` (no es parámetro de este contrato), obtiene resumen, opcionalmente movimientos/PDF y luego llama a `compensar_saldos_a_favor` si `compensar` es verdadero: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:123-227`.
3. La acción del portal usa la misma base CCMA y construye destinos de reafectación. `confirmar=True` invoca confirmación; si no se confirma, cancela el formulario. Con `False`, simula saldos y cancela sin confirmar: `services/bot-worker/src/bot_worker/runtime/portals/ccma_compensacion.py:770-820`, `900-943`, `946-974`.
4. El plugin marca `PARCIAL` si hay avisos o errores dentro del detalle; entrega esos datos y warnings: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:228-247`.

## Normalizaciones y validación efectiva

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Alias: `cuit`, `cuit_representado`. Longitud validada de 11 a 14 antes de limpiar espacios, `-`, `.` y `/`; debe quedar en 11 dígitos. Omitirlo o `null` falla. Alias con valores distintos falla. | `"20123456786"` |
| `compensar` | booleano | No | `true` | En el pre-validador, `null` se borra y reaparece el default `true`. Vacío inválido. `false` omite `compensar_saldos_a_favor`. | `true` |
| `confirmar` | booleano | No | `false` | `null` se borra y reaparece `false`. Vacío inválido. `true` es el control de confirmación remota. | `false` |
| `incluir_movimientos` | booleano | No | `false` | Alias: `movimientos`. `null` o vacío inválidos. `false` omite movimientos salvo que `incluir_pdf` lo cambie. | `false` |
| `incluir_pdf` | booleano | No | `false` | Alias: `pdf`. `null` o vacío inválidos. `true` fuerza `incluir_movimientos=true`. | `true` |
| `subir` | booleano | No | `true` | Alias: `carga_minio`, `minio_upload`. `null` o vacío inválidos. `false` crea el PDF local si se pidió, sin subida ni artefacto. | `true` |

El wrapper central permite `credentials` como objeto genérico, acepta varios nombres de clave, ignora `null`/`""`, requiere que duplicados no vacíos coincidan y puede descifrar `clave_encriptada`; no valida por operación la presencia de clave antes de aceptar el job: `services/central-api/src/central_api/api/bots.py:194-208`, `793-817`; `services/central-api/src/central_api/security/credentials.py:288-370`. En ejecución, el plugin exige objeto de credenciales; la sesión CCMA consume CUIT representante y clave: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:141-158`; `services/bot-worker/src/bot_worker/runtime/context.py:17-25`; `services/bot-worker/src/bot_worker/runtime/arca_login.py:233-250`.

## Salida real

`data`: `operacion`, `representado_cuit`, `compensar`, `confirmar`, campos del resumen CCMA, opcionales `movimientos`/`movimientos_corte`, opcional PDF y `compensaciones`. `PARCIAL` depende de avisos o `compensaciones.errores`. PDF subido se registra como artefacto; sin subida solo queda `archivo` en data: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:174-227`, `228-247`. El manifiesto declara `ccma_compensacion.pdf` opcional, PDF hasta 10 MiB: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:83-108`.

## Discrepancias observadas

1. **Contrato público incompleto.** `services/central-api/src/central_api/api/bot_payloads.py:124-128` modela `representado_cuit`, `compensar`, `confirmar`, `incluir_movimientos`, pero omite `incluir_pdf` y `subir`. El esquema operativo los acepta; las pruebas de plugin usan `pdf` y comprueban `subir`: `services/bot-worker/tests/test_ccma_compensacion_plugin.py:124-147`, `278-294`.
2. **Alto riesgo con defaults implícitos.** `compensar` default true y `confirmar` default false es seguro respecto de confirmación, pero solicita igualmente una propuesta y acciones de formulario.
3. **Efecto e idempotencia.** El manifiesto marca `EFECTO` y el catálogo central default de operación es `EFECTO`, coherente con la confirmación remota; confirmar exige no reintentar ciegamente (`services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:83-108`; `services/central-api/src/central_api/models/catalog.py:72-90`).
4. **Nombre heredado divergente.** `bots_dev/ccma_compenscion/` vs catálogo y registro `ccma_compensacion`.

## Tests leídos

- `services/bot-worker/tests/test_ccma_compensacion_plugin.py:116-180`: registro, alias/defaults, extra prohibido, paso de `confirmar`, parcial y `compensar=False`.
- `services/bot-worker/tests/test_ccma_compensacion_plugin.py:195-278`: prueba que `confirmar=False` no llama confirmar y prueba de PDF.
- Esta auditoría no ejecutó el portal; las pruebas locales coordinadas están en [validación](../validacion.md).

## Referencias fuente

- `services/bot-worker/src/bot_worker/bots/ccma_compensacion/schema.py:33-103`
- `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:80-108`, `123-247`
- `services/bot-worker/src/bot_worker/runtime/portals/ccma_compensacion.py:675-700`, `770-943`, `946-974`
- `services/central-api/src/central_api/api/bots.py:329-339`, `748-817`, `820-845`
- `services/central-api/src/central_api/api/bot_payloads.py:124-128`
- `services/central-api/src/central_api/security/credentials.py:288-370`
- `services/bot-worker/src/bot_worker/bots/_plugins.py:90-100`
- `services/bot-worker/tests/test_ccma_compensacion_plugin.py:116-278`
## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
