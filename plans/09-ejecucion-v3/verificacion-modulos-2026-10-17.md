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
| `sifere` | OK (102 artefactos, 403 s) | credencial rechazada (clave vencida en la base) → se reintenta con otra fila | `arca_service_not_visible` = DATOS; sin bug de matcher. |
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

- `central-testing` (api2) corre las imágenes `mrbot-central-api:testing-fixes2`
  y `mrbot-bot-worker:testing-fixes2` (build local con `./build-and-push.sh`
  + `docker compose pull/up`), con migraciones aplicadas y un worker propio
  (`mrbot-central-testing-worker`) registrado en el inventario.
- Cambios locales en `~/central-testing` (no versionados, respaldo en
  `compose.yaml.bak-*`):
  - `compose.yaml`: `image:` de central y migrate pasa a la imagen del registry
    con tag `testing-fixes2`, y se agrega el servicio `bot-worker`
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
