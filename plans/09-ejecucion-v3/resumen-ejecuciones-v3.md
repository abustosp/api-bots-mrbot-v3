# Resumen de ejecuciones V3 tras los cambios recientes

Verificación del 2026-10-17 (tarde), contra la central de testing
`https://testing-central.mrbot.com.ar` con las imágenes `testing-fixes4`, `5` y
`6` (tag vigente: **`testing-fixes6`**, worker `mrbot-central-testing-worker`,
healthy, `/ready` 200). Cada fila es una ejecución real con credenciales del
corpus V1/V2; no hay datos sintéticos.

## Cómo leer los estados

- **`OK`** — la corrida terminó completa con todos los artefactos esperados.
- **`PARCIAL`** — la corrida entregó artefactos pero alguna sección, formato o
  período falló. **El cliente recibe los archivos**: la central proyecta
  `PARCIAL` como estado público `COMPLETO`.
- **`ERROR`** — la corrida no entregó resultado. Estado público `FALLIDO`.
- **`422` / `401`** — rechazo en la creación del job: nunca llegó al worker.
- **Motivo** — separo siempre **código** (defecto del port/central),
  **transitorio** (el portal no respondió a tiempo; el mismo cuerpo pasa después),
  **datos** (cuenta sin servicio, período inexistente, credenciales vencidas) e
  **infra/config** (proxy, red, clave de prueba).

## Matriz comparativa por endpoint

Cada bloque contrasta el comportamiento **antes** de los cambios del día y
**después**, con los mismos cuerpos y las mismas cuentas.

| Endpoint (3 casos salvo indicación) | Antes del cambio | Después del cambio | Motivo del error residual |
|---|---|---|---|
| `POST /sct/consulta` (3 cuentas) | 2/3 `OK`; 1 falló con `sct_frame_missing` (defecto 4) | **3/3 `OK`**, 1 PDF cada uno (27-33 s) | — |
| `POST /sct/consulta` (3 variantes de campos legados vacíos) | 422 `file_not_uploaded` con `b64`/`carga_s3` vacíos (defecto 6) | **2/3 `OK`** (29,1 s y 27,5 s); el 3.º es 422 **esperado** con `archivo_b64:"QQ=="` | El 422 restante es la protección correcta: hay contenido real sin `object_key` |
| `POST /mis_facilidades/consulta` (3 filas) | `ERROR` / `target_unavailable` (`ConnectTimeout` y excepciones de sección) | **2 `OK` (50 y 50 artefactos) + 1 `PARCIAL` (42 artefactos)** | El caso `PARCIAL` es la tolerancia funcionando: una sección no se pudo capturar y la corrida igual entregó 42 archivos |
| `POST /mis_retenciones/consulta` (3 filas) | 3.º con `represented_cuit_not_selectable` (10 fallos en producción) | **2/3 `OK`** (5 CSV); el 3.º falló con `target_unavailable` y **pasó en el reintento** (5 CSV, 208,6 s) | Transitorio: mismo cuerpo, mismo tag, segundo intento OK |
| `POST /rcel/consulta` (3 filas, `minio_upload=true`) | `represented_cuit_not_selectable` (77 fallos en producción) | **3/3 `OK`** con 148, 15 y 26 PDF | — |
| `POST /portal_iva/consulta` (3 filas/users) | **0/3 `ERROR` `envelope_invalid`** (defecto 12: la ruta alias descartaba los flags canónicos) | **3/3 `OK`** con 2 CSV cada uno (96-118 s) en `testing-fixes6` | — |
| `POST /libros_iva/consulta` (3 filas) | `libros_period_not_found` (defecto 8, representado no confirmado) | **3/3 `OK`** con 2 `.bin` (libros + DDJJ) (86-91 s) | — |
| `POST /sifere/consulta` (3 filas) | 1.º intento `target_unavailable` a los 287 s, 2.º OK (455 s) | 2/3 `OK` (407,2 s y 408,3 s); el 1.º `ERROR` `target_unavailable` a los 266,9 s y **en el reintento `PARCIAL` con datos** (410,3 s) | Transitorio: 3 de 4 corridas terminan con datos, pero el primer intento sigue muriendo por falta de reintento automático |
| `POST /vep/consulta-pagos` (1 caso, local + E2E) | `portal_download_missing` (defecto 9: representado no verificado) | **`OK`** (27 s E2E; local 84 filas, CSV de 6.803 B) | — |
| `POST /aportes-en-linea/consulta` | `ENVELOPE_INVALID` con `archivo_historico_minio=false` | **`OK`** (genera `AEL - <cuit> - <fecha>.xls`) | — |
| `GET /utilidades/apoc/{cuit}` (4 casos) | 503 `Base de apócrifos no configurada` | **3/3 `apoc=true`** con fechas reales de AFIP + 1 `apoc=false` para un CUIT válido fuera de la tabla; sin copia provisionada | — |

## Consolidado numérico

Solo la segunda vuelta, que es la que refleja el código actual:

| Resultado | Casos | Detalle |
|---|---:|---|
| `OK` | 20 | sct 3, sct-vacíos 2, mis_facilidades 2, mis_retenciones 1 (+1 en reintento), rcel 3, portal_iva 3, libros 3, sifere 2, vep 1, apoc 3 |
| `PARCIAL` | 3 | mis_facilidades 1 (42 archivos), sifere 1 (reintento, con datos), mis_facilidades cliente 1 (78 archivos) |
| `ERROR` | 2 | sifere 1 (`target_unavailable`, luego `PARCIAL`), mis_retenciones 1 (`target_unavailable`, luego `OK`) |
| `422` esperado | 2 | sct con `archivo_b64:"QQ=="` (protección correcta) |
| `401` | 2 | **artefacto de la rotación de la clave de prueba**, no del producto: reintentados dieron 202 `OK` |

De los `ERROR`, **ninguno quedó como fallo definitivo**: ambos eran transitorios
y pasaron al reintentar el mismo cuerpo. Cero fallos de código atribuibles al
build actual en las 25 ejecuciones de la segunda vuelta.

## Desde el cliente de escritorio V3 (5 flujos, `testing-fixes5`/`6`)

| Flujo del cliente | Creación | Estado / result | Descargas | Motivo |
|---|---|---:|---|---|
| `sct` (control) | 202 | `COMPLETO` / `OK` | 2/2 directas y 2/2 autenticadas | — |
| `mis_facilidades` fila A | 202 | `COMPLETO` / `OK` | 50/50 | — |
| `mis_facilidades` fila B | 202 | `COMPLETO` / `PARCIAL` | 78/78 directas | Tolerancia por sección: entrega parcial completa |
| `mis_facilidades` fila C | 202 | `COMPLETO` / `OK` | 17/17 | — |
| `liquidacion_granos` (control) | 202 | `COMPLETO` / `OK` | el job no ofreció archivos | — |
| `mis_comprobantes` | 202 | `COMPLETO` / `OK` | 2/2 directas y 2/2 autenticadas | — |
| `siper` | 202 | `ERROR` / `ERROR` | 0 | **Datos**: las 7 filas del corpus comparten credenciales vencidas (`credentials_rejected`) |
| `sct_compensaciones` | 422 | — | — | **Cliente**: el formulario permite enviar sin marcar Excel/CSV/PDF y el worker exige al menos un formato |

Contrato: OpenAPI remoto 179 rutas `/api/v3`, alias 38/38 alineados, QA de
contrato 162/163 y QA offline 1334/1336. El único fallo es un **falso positivo del
verificador** (busca `*_key` en el esquema plano de la ruta alias, mientras el
cliente resuelve a la ruta canónica cuyo `payload` es genérico).

## Motivos de error agrupados (producción, 353 fallos desde 2026-08-18)

Esta es la foto del **antes**, y sirve para saber cuánto de eso ya está cubierto:

| `error_code` | Fallos | Causa dominante | Estado |
|---|---:|---|---|
| `TARGET_UNAVAILABLE` | 239 | mezcla de: pasos tolerantes en V1/V2 que pasaron a fatales (**corregido**), transitorios sin reintento (**pendiente de decisión**), y datos de la cuenta | parcialmente resuelto |
| `CREDENTIALS_REJECTED` | 48 | dato del usuario: CUIT/clave incorrectos o cuenta deshabilitada | fuera del alcance del código |
| `ENVELOPE_INVALID` | 33 | contrato: campos legados vacíos (**corregido**) y flags canónicos descartados en Portal IVA (**corregido**) | resuelto en los casos identificados |
| `INTERNAL` | 18 | defectos de port ya corregidos (p. ej. `FileNotFoundError` de `hacienda`) o cortes de infra | resuelto |
| `ARTIFACT_UPLOAD_FAILED` | 9 | "el artefacto ya producido se pierde por un fallo de subida" (`aportes_en_linea` 4, `arba` 4, `siper` 1) | **deuda priorizada**, 2,5 % de los fallos |
| `CAPTCHA_UNSOLVABLE` | 4 | captcha agotado (`retper_iibb_misiones`) | comportamiento esperado |
| `CANCELLATION_REQUESTED` | 2 | cancelación del usuario | esperado |

## Los dos pendientes que explican el resto

1. **Reintentos automáticos.** `TargetUnavailableError` declara
   `reintentable = True`, pero nadie lo consume: la central pasa el job a
   `FALLIDO` con `attempts` 1 de 3. Medido tres veces (sifere 2,
   mis_retenciones 1): mismo cuerpo, primer intento `target_unavailable`,
   segundo `OK`/`PARCIAL` con datos. Es el motivo de error más costoso que queda.
2. **Campos canónicos en las rutas alias (clase del defecto 12).** El modelo de
   compatibilidad se arma con el vocabulario V1/V2 y descarta en silencio los
   nombres canónicos que no declara. Comprobado contra los modelos publicados:
   `sct/consultar` pierde `secciones` y `formatos` (el worker aplica sus
   defaults), `siper/consultar` pierde `subir` (viaja `false`), y
   `mis_comprobantes/consultar` exige `representado_nombre` que el worker no
   pide. Solo `portal_iva` está corregido; el fix general necesita una tabla de
   campos canónicos por operación del lado de la central.

## Calidad del código tras los cambios

| Suite | Resultado |
|---|---|
| `bot-worker` | **775 passed** |
| `central-api` | **613 passed**, 28 skipped |
| root | **109 passed** |
| `tools/local_run` | 3 passed |

## Documentación de respaldo

- `plans/09-ejecucion-v3/verificacion-modulos-2026-10-17.md` — resultados por
  módulo, matriz de 3 casos, salida del cliente.
- `plans/09-ejecucion-v3/analisis-v1v2-vs-v3.md` — por qué V1/V2 andaban y V3 no.
- `plans/09-ejecucion-v3/auditoria-tolerancia-v1v2-v3.md` — auditoría estática de
  los 34 bots con fix mínimo propuesto por módulo.
- Informes por módulo en `~/.jcode/scratch/report_div_*` y
  `report_3casos_*` (permisos 600, sin CUIT, nombres ni claves).
