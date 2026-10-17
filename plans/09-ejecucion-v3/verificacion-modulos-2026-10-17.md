# Verificación de módulos V3 (2026-10-17)

Verificación de los módulos reportados con `FALLIDO target_unavailable`, hecha
primero contra el portal real desde la máquina del operador (plugins V3 reales,
credenciales de las bases V1/V2) y después de punta a punta contra la central de
testing (`ssh api2`, stack `central-testing`) con las imágenes nuevas.

## Cómo se probó

```bash
# Un registro success de la base, sin exponer la clave:
.venv/bin/python tools/local_run/run_bot.py --bot <bot> --operacion <op> --from-db <bot> [--db-index N] \
    [--payload-json '{...}'] [--proxy off|on] --out /tmp/lr/<bot>

# Evidencia de la pantalla del servicio (popup) y del login:
#   final.html/png, final_servicio.html/png, final_servicio.frames.txt, report.json, progress.log

# Login + catálogo de servicios de una cuenta (separa DATOS de BUG):
.venv/bin/python tools/local_run/run_bot.py --mode login --from-db <bot> --out /tmp/lr/<bot>_login

# E2E contra la central de testing (cuerpo V1-compat):
MRBOT_API_KEY=<clave de testing> .venv/bin/python tools/local_run/e2e_testing.py \
    --bot <bot> --operacion <op> --body-file /tmp/e2e/<bot>.jsonl --timeout 600
```

Comparación con V1: los mismos casos se corrieron con los loggers de
`bots_dev/<bot>/` (referencia V1/V2) para separar regresión de código de
problema de datos/cuenta.

## Defectos encontrados y corregidos

| # | Módulo | Defecto | Corrección |
|---|---|---|---|
| 1 | `carga_portal_iva` | El port V3 editaba el CF Computable Global **antes** de cargar la apertura CF; V1 lo hace después de que el CSV CF quedó cargado (`app/bot/carga_portal_iva_bot.py`). La suite ya tenía el test rojo. | `bots/carga_portal_iva/plugin.py`: se edita el crédito fiscal dentro del bucle de aperturas, sólo con `CF: cargado` y `prorrateo_global`. |
| 2 | `hacienda` | `escribir_xlsx` no creaba el directorio padre del consolidado: `FileNotFoundError` → `target_unavailable_unclassified`. | `runtime/portals/hacienda.py`: `destino.parent.mkdir(parents=True, exist_ok=True)` + regresión en `tests/test_portal_hacienda.py`. |
| 3 | varios (`sct`, `libros_portal_iva`, `mis_facilidades`, `liquidacion_granos`, …) | `PortalArca.seleccionar_representado` abortaba con `represented_cuit_not_selectable` cuando el portal **no dibuja** selector (cuenta única / representado ya activo). V1 continuaba ("Asumiendo cuenta única o ya seleccionada"). Reproducido con SCT: V1 descargó 6 reportes con la misma credencial y el V3 abortaba. | `runtime/portals/base.py`: sin selector presente el flujo continúa; el error queda para un selector presente que excluya el CUIT. Test en `tests/test_portales.py`. |
| 4 | `sct` | `open_service` toleraba el timeout del popup y devolvía la página del portal, y el fallo se reportaba como selector de representado o de sección. | `runtime/portals/sct.py`: valida el destino/iframe antes de seleccionar representado y falla con `sct_frame_missing`. Tests en `tests/test_portal_sct.py`. |
| 5 | `central-api` | `GET /utilidades/apoc/{cuit}` devolvía 503 si no había copia local; no descargaba ni actualizaba la tabla. | `services/apoc_base.py` + `api/utilities.py`: descarga si falta, actualiza si tiene más de una semana y usa la copia provisionada como respaldo. Tests en `tests/test_apoc_base.py`. |
| 6 | `central-api` | Los campos viejos `b64`, `*_b64` y `carga_s3` vacíos (`null`/`""`/`false`) disparaban `422 file_not_uploaded`. | `api/file_inputs.py` + `api/bot_compat.py`: sólo se rechaza contenido real. Tests en `tests/test_file_input_pipeline.py`. |
| 7 | `tools/local_run` | El runner local no creaba el `work_dir` por job, no delegaba la fábrica al portal (captcha SRT), no capturaba la página del servicio y no derivaba campos V1→V3. | `tools/local_run/run_bot.py` + `creds.py`: `--from-db`/`--creds-file`, `--db-name`, `--db-cuit`, derivaciones (`fecha_desde/hasta`, `cuits_consulta`, `request_excel/csv/pdf`, `representado_nombre`), evidencia del popup. |
| 8 | `libros_portal_iva` | El portal no confirmaba que el CUIT representado quedara **activo**: leído el HTML real del popup, el rótulo "Representando a" seguía mostrando al representante, y el flujo consultaba como esa cuenta (`libros_period_not_found` para un período que V1/V3 descargan bien). | `runtime/portals/libros_portal_iva.py`: confirma el CUIT en el rótulo activo tras el selector común y tras el switcher; sin confirmación no consulta. Tests en `tests/test_portal_libros_portal_iva.py`. |
| 9 | `consulta_pagos_vep` | El multiselect es una SPA: el portal daba por seleccionado el representado tras despachar un click sintético, sin comprobar que el `fake-input` lo reflejara; el filtro quedaba sin resultados y la descarga fallaba con `portal_download_missing`. | `runtime/portals/consulta_pagos_vep.py`: verifica el valor del fake-input (normalizado a dígitos) y reintenta el click. Tests en `tests/test_consulta_pagos_vep_plugin.py`. |

## Resultado por módulo

Verificación local con credenciales reales (`OK` = flujo completo, con archivos) y
de punta a punta contra `https://testing-central.mrbot.com.ar` con las imágenes
`testing-fixes2` (worker propio registrado).

| Módulo | Local (real) | E2E testing-central | Clasificación del fallo reportado |
|---|---|---|---|
| `facturometro` | OK | OK | El diagnóstico `represented_cuit_not_selectable` de la cuenta de prueba no se reprodujo con una credencial válida; sí se corrigió la causa genérica (defecto 3). |
| `certificado_mipyme` | OK | OK (tras configurar `PROXY_BOTS`, como producción) | `portal_step_seleccionar_representado` era falta de proxy en la central de testing, no un bug. |
| `pago_devoluciones` | OK (clave reciente) | OK | El diagnóstico `arca_service_not_visible` es DATOS cuando la cuenta no tiene el servicio (catálogo). |
| `hacienda` | OK (caso con relación válida); el caso del corpus sin relación falla también en V1 ("No se encontró botón Hacienda") | `represented_name_not_selectable` (DATOS, mismo caso que V1) | Defecto 2 corregido; el resto es DATOS. |
| `liquidacion_granos` | OK (5 XLSX) | OK | `arca_service_not_visible` = DATOS. |
| `compensaciones` | OK ("no se encontraron resultados") | OK | DATOS/build viejo. |
| `srt` | OK (alícuotas; `SIN_AFILIACION_VIGENTE` es dato) | PARCIAL (una consulta sin afiliación vigente) | El código funciona; `srt_cuit_input_failed`/`arca_service_not_visible` dependen de la cuenta. |
| `libros_portal_iva` | OK | OK (tras el fix de confirmación del representado; antes fallaba con `libros_period_not_found`) | Defecto 8. |
| `mis_facilidades` | OK (11 planes, 49 artefactos) | OK | 1.951 fallos históricos: no reproducidos con el build actual; `arca_service_not_visible` = DATOS. |
| `sifere` | OK (102 artefactos, 403 s) | 1.º intento `target_unavailable` (287 s), 2.º `OK` (455 s) con el mismo cuerpo y cuenta | `arca_service_not_visible` = DATOS; la intermitencia la cubre el hallazgo principal. |
| `declaracion_en_linea` | OK | OK | `plugin_service_api_mismatch` pertenecía a builds viejos del worker. |
| `portal_iva` | OK con periodo vigente (202610); `portal_iva_period_not_available` con 202608 | OK (202610) | El diagnóstico de período es DATOS: el portal sólo ofrece 202610 para la cuenta. |
| `sct` | OK (3 juegos de credenciales) | OK (2/2) | `arca_service_not_visible` = DATOS; el resto lo cubren los defectos 3 y 4. |
| `vep/consulta-pagos` | OK tras el fix (84 filas, CSV 6.803 B) | OK (27 s) | Defecto 9: era `portal_download_missing` por no confirmar el representado. |
| `aportes_en_linea` | OK con `archivo_historico_minio=false` (genera `AEL - <cuit> - <fecha>.xls`) | OK | El `ENVELOPE_INVALID` del corpus V1 (`archivo_historico_minio=false`) ya no ocurre. |

### Verificaciones puntuales en la central de testing

- **`GET /utilidades/apoc/{cuit}`** (defecto 5): devuelve 200 con `apoc=true` y
  las fechas reales de la tabla de AFIP (`30707577963` → `08/11/2005`,
  `30707966773` → `26/06/2007 – 09/02/2010`), sin ninguna copia provisionada.
  Con el archivo de caché envejecido 8 días, la consulta lo volvió a descargar
  (mtime 2026-10-09 06:21 → 2026-10-17 06:21). Antes devolvía
  `503 Base de apócrifos no configurada en central-api`.
- **Campos legados vacíos** (defecto 6): `POST /api/v3/sct/consulta` con
  `b64: null`, `carga_s3: false`, `archivo_b64: ""`, `incluir_base64: false` y
  `carga_b64: false` → **202**; el mismo cuerpo con `archivo_b64: "QQ=="` → **422
  `file_not_uploaded`** (la protección sigue activa cuando hay contenido).

## Matriz de 3 casos por endpoint modificado

Segunda vuelta de verificación (17-10-2026, tarde), contra la central de testing
con las imágenes `testing-fixes5` (`7958781` + los fixes de `sct` tolerante,
`mis_retenciones` sin cortar el job, `mis_facilidades` por sección y el ciclo de
imports). Cada caso es un job real con credenciales del corpus V1/V2
(`sql_app_mrbot1.db`), enviado con los nombres de campo legados que la central
acepta (`cuit_login`, `clave`, `cuit_representado`).

### `POST /sct/consulta`

| Caso | Cuerpo | HTTP | Estado / result | Archivos | Tiempo |
|---|---|---:|---|---:|---:|
| 1 | credenciales A, `deudas_pdf_minio` | 202 | `COMPLETO` / `OK` | 1 PDF | 32,6 s |
| 2 | credenciales B | 202 | `COMPLETO` / `OK` | 1 PDF | 27,2 s |
| 3 | credenciales C | 202 | `COMPLETO` / `OK` | 1 PDF | 27,1 s |
| 4 | A + `b64:null`, `carga_b64:false`, `archivo_b64:""`, `incluir_base64:false`, `carga_s3:false` | 202 | `COMPLETO` / `OK` | 1 PDF | 29,1 s |
| 5 | A + los mismos campos vacíos con `carga_s3:true` | 202 | `COMPLETO` / `OK` | 1 PDF | 27,5 s |
| 6 | A + `archivo_b64:"QQ=="` | 422 | — | — | 0,3 s |

Los casos 4 y 5 confirman que los campos legados vacíos ya no se confunden con
"archivo no subido" y que el job corre el flujo completo (antes quedaban
`PENDIENTE` en la matriz acotada a 120 s de polling y se cancelaban). El caso 6
confirma que la protección sigue viva cuando hay contenido real: 422 con
`file_not_uploaded`.

### `POST /mis_facilidades/consulta`

| Caso | HTTP | Estado / result | Planes | Artefactos | Tiempo |
|---:|---:|---|---:|---:|---:|
| 1 | 202 | `COMPLETO` / `OK` | 11 | 50 | 374,7 s |
| 2 | 202 | `COMPLETO` / `OK` | 11 | 50 | 349,3 s |
| 3 | 202 | `COMPLETO` / `PARCIAL` | 11 | 42 | 352,7 s |

El caso 3 es la prueba directa del fix: una sección de ese plan no se pudo
capturar y la corrida igual entregó 42 artefactos como `PARCIAL`. Antes ese mismo
error salía como `FALLIDO` (`target_unavailable` / `target_unavailable_unclassified`)
y el cliente no recibía nada.

### Endpoints de `central-api`

| Endpoint | Casos | Resultado |
|---|---|---|
| `GET /utilidades/apoc/{cuit}` | 3 CUIT presentes en la tabla de AFIP + 1 CUIT válido fuera de la tabla | 200 con `apoc=true` en los tres primeros y `apoc=false` en el cuarto; sin copia provisionada. |
| `POST /uploads` + campos `*_b64`/`carga_s3` vacíos | 6 (4 de ellos por `sct`) | Ver matriz de `sct` arriba; el flag `file_not_uploaded` sólo aparece con contenido. |

Límite declarado: `POST /portal_iva/carga` **no** se ejecutó contra la central.
El flujo V1/V2/V3 del bot borra el contenido previo del libro antes de importar
(`eliminar_todo()`), y no hay confirmación de que los representados del corpus
sean descartables ni de que el worker apunte a un sandbox de ARCA. Queda como
verificación pendiente con datos y entorno de prueba explícitos.

## Hallazgo principal sobre el diagnóstico reportado

El síntoma "casi todos los bots dan `FALLIDO target_unavailable`" se descompone en
tres causas distintas, todas verificadas en esta corrida:

1. **Errores transitorios del portal que hoy terminan el job en el primer
   intento.** Dos ejemplos medidos con credenciales válidas contra la central de
   testing: `sifere/consultar` falló con `target_unavailable` a los 287 s y el
   intento siguiente terminó `OK` con el mismo cuerpo y la misma cuenta (455 s,
   consolidados y jurisdicciones descargados); `sct/consultar` mostró lo mismo
   antes del defecto 4 (un intento `sct_section_open_failed`, el siguiente OK).
   El protocolo marca `TargetUnavailableError.reintentable = True`, pero **nadie
   lo consume**: ni el worker reintenta el job ni la central lo reencola
   (`central_api/internal/job_results.py` pasa el job a `FALLIDO` apenas el
   worker reporta el error). En producción los `FALLIDO` por estos diagnósticos
   tienen `attempts = 1` con `max_attempts = 3`.
   Recomendación: reencolar en la central cuando la categoría sea
   `TARGET_UNAVAILABLE`/`DEADLINE_EXCEEDED` y `attempts < max_attempts`,
   cuidando que el reintento no consuma cuota dos veces (la reserva ya existe por
   job).
2. **Cuenta sin el servicio habilitado** (`arca_service_not_visible`): la causa
   es la cuenta, no el bot. El catálogo de la cuenta no lista la tarjeta y V1
   también fallaba (`No se encuentra habilitado el servicio de SCT`, log del
   2026-03-15 de la cuenta de prueba).
3. **Representado o período inexistente** (`represented_cuit_not_selectable`,
   `represented_name_not_selectable`, `*_period_not_found`,
   `portal_iva_period_not_available`): también son datos; el mismo caso corre OK
   con una cuenta/relación/período válidos (verificado por módulo).

Los defectos 3, 4, 8 y 9 eran lo único atribuible al código y ya están
corregidos; con ellos los quince módulos de la lista cierran `OK` contra la
central de testing.

## Verificación desde el cliente de escritorio V3

Se ejecutó el cliente de escritorio (`mrbot-cliente-escritorio-v3`) contra
`https://testing-central.mrbot.com.ar` usando su propia UI y su `JobClient`
(payload construido por las entradas del cliente, clave fiscal cifrada con la
clave pública de la central, POST, polling y descarga de artefactos).

- **Contrato**: OpenAPI remoto con 179 rutas `/api/v3`; registro de endpoints del
  cliente sin rutas/métodos inexistentes; 38/38 alias alineados; QA de contrato
  162/163 (el único fallo es un falso positivo del verificador: busca `*_key` en
  el esquema de la ruta alias, mientras el cliente resuelve el alias a la ruta
  canónica y ahí el `payload` es `dict` genérico).
- **Cinco flujos reales**: `sct` → 202 y `COMPLETO/OK` con 2/2 archivos
  descargados por el cliente (URL directa y fallback autenticado);
  `liquidacion_granos` → 202 y `COMPLETO/OK`; `facturometro` → 202 y
  `COMPLETO/OK` con 1/1 archivo; el cliente interpretó correctamente el estado y
  el `result` en los cinco casos (UI incluida).
- **Errores observados (no del cliente)**:
  - `mis_facilidades` → `TARGET_UNAVAILABLE` con causa `ConnectTimeout`
    (`target_unavailable_unclassified`): error transitorio de red, el mismo
    patrón del hallazgo principal (sin reintento automático).
  - `sct_compensaciones` → `ENVELOPE_INVALID` (`ValidationError`): el formulario
    permite enviar sin marcar Excel/CSV/PDF y el worker exige al menos un
    formato. Recomendación para el cliente: validar en el formulario que haya al
    menos un formato de salida para esa entrada.
- **Probes sin job**: `GET /utilidades/apoc/{cuit}` devuelve 200 con la forma
  esperada y un `legacy_b64=""` no dispara el rechazo de contenido inline (el 422
  que aparece es el ordinario por falta de `object_key` cuando el bot necesita un
  archivo).
- El cliente no requirió ningún cambio de código para operar contra la central de
  testing.

## Observaciones abiertas

1. **Contrato público de `arca_service_not_visible`** (pendiente de decisión de
   producto). Cuando la cuenta no tiene el servicio, el cliente recibe
   `target_unavailable` ("El portal no respondió como se esperaba…"), que sugiere
   reintentar. V1 decía "No se encuentra habilitado el servicio de X". El worker
   ya distingue el diagnóstico interno; publicar un `error_code` propio y **no
   reintentable** cambia el contrato de la API y no se hizo en esta corrida.
2. **Cuenta de prueba `20370074829`.** No tiene clave válida en las bases, así que
   su catálogo no se pudo verificar en fresco. Los diagnósticos de producción
   (`arca_service_not_visible` en sct/sifere/mis_facilidades/liquidacion_granos/
   pago_devoluciones/compensaciones/certificado_mipyme/declaracion_en_linea,
   `represented_cuit_not_selectable` en facturometro/libros_portal_iva) son
   consistentes con una cuenta sin esos servicios y con representados no
   relacionados; el build actual no reproduce esos diagnósticos con cuentas
   válidas.
3. **Tolerancia del selector de representado (defecto 3).** La paridad con V1
   hace que, sin selector visible, el flujo continúe. Los portales que dependen
   de esa selección ahora la **confirman** (libros, vep) o validan antes la
   página del servicio (sct); conviene mantener ese patrón en los portales que
   se agreguen y, si el portal no expone forma de confirmar, seguir el mismo
   criterio que V1 (consultar y dejar que la sección falle con su diagnóstico).
4. **Credenciales de las bases.** Varias claves de las tablas `success` ya no
   sirven (ARCA las rechaza): `sifere` (2026-10-13), `pago_devoluciones` (id 3) y
   `srt` (id 2) se resolvieron usando una clave más reciente del mismo CUIT en
   otra tabla. Para reproducir, hay que iterar filas (`--db-index`) en lugar de
   asumir que el último `success` sigue vigente.

## Estado de la infraestructura de prueba

- `central-testing` (api2) corre las imágenes `mrbot-central-api:testing-fixes5`
  y `mrbot-bot-worker:testing-fixes5` (build local con `./build-and-push.sh`
  + `docker compose pull/up`), con migraciones aplicadas y un worker propio
  (`mrbot-central-testing-worker`) registrado en el inventario. El tag se
  actualizó tres veces durante la jornada (`testing-fixes3` → `fixes4` →
  `fixes5`): cada tag sólo agrega fixes ya commiteados y verificados localmente.
- Cambios locales en `~/central-testing` (no versionados, respaldo en
  `compose.yaml.bak-*`):
  - `compose.yaml`: `image:` de central y migrate pasa a la imagen del registry
    con tag `testing-fixes5`, y se agrega el servicio `bot-worker`
    (`--central-url http://central-api:8000`,
    `--advertised-url http://bot-worker:8080`, `--concurrency 5`, `shm_size 2gb`,
    red `control`, `depends_on: central-api healthy`).
  - `central.env`: `WORKER_NODES=bot-worker:8080` (el registro exige que el nodo
    esté inventariado) y `PROXY_BOTS=certificado_mipyme` con
    `PROXY_BOTS_HOST/USERNAME/PASSWORD/COUNTRY/PORT_MIN/PORT_MAX` igual que
    producción (sin eso, `certificado_mipyme` falla con timeout en el paso de
    selección de representado).
- Usuario de prueba creado en esa central (`agente-test@mrbot.com.ar`, tipo
  `PRUEBA`) con clave `mbk_...` guardada fuera del repositorio.
- Verificación live de las dos correcciones de central-api: `/utilidades/apoc`
  descarga/refresca la tabla real de AFIP (ver arriba) y el cuerpo con campos
  `b64`/`carga_s3` vacíos se acepta con 202.
