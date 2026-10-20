# Auditoría interna: `ccma_compensacion`

## Identidad y V3

- Catálogo central: `ccma_compensacion`, operación `compensar`; `services/central-api/src/central_api/api/bots.py:329-339`, `378-386`.
- Ruta de creación canónica: `POST /api/v3/bots/{bot}/{operacion}`; el cuerpo separa `payload` y `credentials` y despacha `submit_job`; `services/central-api/src/central_api/api/bots.py:194-208`, `748-817`.
- Plugin activo y manifiesto `operaciones=("compensar",)`, clase de idempotencia `EFECTO`: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:80-108`.
- Directorio activo coincide con catálogo: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/`. Discrepancia de nombre solo en árbol heredado de desarrollo: `services/bot-worker/src/bot_worker/bots_dev/ccma_compenscion/` (falta la segunda `a` de `compensacion`); el registro activo usa el nombre correcto, `services/bot-worker/src/bot_worker/bots/_plugins.py:90-100`.

## Flujo esquema → plugin → portal

1. `schema.py` exige CUIT representado, admite aliases, elimina campos del sobre, normaliza flags nulos de `compensar`/`confirmar` a sus defaults y fuerza movimientos si se solicita PDF: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/schema.py:33-75`.
2. El plugin valida operación y modelo, requiere credenciales, abre sesión fiscal y la cuenta CCMA; calcula deuda desde `04/2000` (no es parámetro de este contrato), obtiene resumen, opcionalmente movimientos/PDF y luego llama a `compensar_saldos_a_favor` si `compensar` es verdadero: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:123-227`.
3. La acción del portal usa la misma base CCMA y construye destinos de reafectación. `confirmar=True` invoca confirmación; si no se confirma, cancela el formulario. Con `False`, simula saldos y cancela sin confirmar: `services/bot-worker/src/bot_worker/runtime/portals/ccma_compensacion.py:770-820`, `900-943`, `946-974`.
4. El plugin marca `PARCIAL` si hay avisos o errores dentro del detalle; entrega esos datos y warnings: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:228-247`.

## Normalizaciones y validación efectiva

| Campo | Tipo / requerido / default activo | Omisión, `null`, vacío y `false` |
|---|---|---|
| `representado_cuit` | string, requerido; aliases `cuit`, `cuit_representado`; longitud validada 11–14 antes de limpieza. | Omisión/null fallan; limpia espacio, `-`, `.`, `/`, exige 11 dígitos; aliases con valores distintos fallan. |
| `compensar` | bool, default `true`. | En el pre-validador, `null` se borra y reaparece default `true`; vacío inválido; `false` omite `compensar_saldos_a_favor`. |
| `confirmar` | bool, default `false`. | `null` se borra y reaparece `false`; vacío inválido; `true` es el control de confirmación remota. |
| `incluir_movimientos` | bool, default `false`, alias `movimientos`. | `null`/vacío inválidos; `false` omite movimiento salvo que `incluir_pdf` lo cambie. |
| `incluir_pdf` | bool, default `false`, alias `pdf`. | `null`/vacío inválidos; true fuerza `incluir_movimientos=True`. |
| `subir` | bool, default `true`, aliases `carga_minio`, `minio_upload`. | `null`/vacío inválidos; `false` crea PDF local si se pidió, sin subida ni artefacto. |

El wrapper central permite `credentials` como objeto genérico, acepta varios nombres de clave, ignora `null`/`""`, requiere que duplicados no vacíos coincidan y puede descifrar `clave_encriptada`; no valida por operación la presencia de clave antes de aceptar el job: `services/central-api/src/central_api/api/bots.py:194-208`, `793-817`; `services/central-api/src/central_api/security/credentials.py:288-370`. En ejecución, el plugin exige objeto de credenciales; la sesión CCMA consume CUIT representante y clave: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:141-158`; `services/bot-worker/src/bot_worker/runtime/context.py:17-25`; `services/bot-worker/src/bot_worker/runtime/arca_login.py:233-250`.

## Salida real

`data`: `operacion`, `representado_cuit`, `compensar`, `confirmar`, campos del resumen CCMA, opcionales `movimientos`/`movimientos_corte`, opcional PDF y `compensaciones`. `PARCIAL` depende de avisos o `compensaciones.errores`. PDF subido se registra como artefacto; sin subida solo queda `archivo` en data: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:174-227`, `228-247`. El manifiesto declara `ccma_compensacion.pdf` opcional, PDF hasta 10 MiB: `services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:83-108`.

## Discrepancias concretas y propuestas (no implementadas)

1. **Contrato público incompleto.** `services/central-api/src/central_api/api/bot_payloads.py:124-128` modela `representado_cuit`, `compensar`, `confirmar`, `incluir_movimientos`, pero omite `incluir_pdf` y `subir`. El esquema operativo los acepta; las pruebas de plugin usan `pdf` y comprueban `subir`: `services/bot-worker/tests/test_ccma_compensacion_plugin.py:124-147`, `278-294`. Propuesta: completar el DTO V3 de la operación con ambos campos, alias y sus defaults, y derivar `credentials_schema` por bot/operación en vez de `additionalProperties: true` genérico (`services/central-api/src/central_api/api/bots.py:820-845`).
2. **Alto riesgo con defaults implícitos.** `compensar` default true y `confirmar` default false es seguro respecto de confirmación, pero solicita igualmente una propuesta y acciones de formulario; para una API de efecto conviene requerir `confirmar` explícito o exigir una segunda operación de aprobación/token. No cambiar el comportamiento hasta decisión de producto y revisión de idempotencia.
3. **Efecto e idempotencia.** El manifiesto marca `EFECTO` y el catálogo central default de operación es `EFECTO`, coherente con la confirmación remota; confirmar exige no reintentar ciegamente (`services/bot-worker/src/bot_worker/bots/ccma_compensacion/plugin.py:83-108`; `services/central-api/src/central_api/models/catalog.py:72-90`). Mantener una política de replay que nunca convierta un job confirmado en una nueva confirmación.
4. **Nombre heredado divergente.** `bots_dev/ccma_compenscion/` vs catálogo y registro `ccma_compensacion`. Propuesta: normalizar el nombre de la carpeta de fixtures/documentación heredados o registrar la discrepancia como alias histórico, sin crear un segundo bot.

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