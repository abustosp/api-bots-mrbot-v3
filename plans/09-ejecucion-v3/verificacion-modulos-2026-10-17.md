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

## Resultado por módulo

Verificación local con credenciales reales (`OK` = flujo completo, con archivos).

| Módulo | Local (real) | E2E testing-central | Clasificación del fallo reportado |
|---|---|---|---|
| `facturometro` | OK | OK | El diagnóstico `represented_cuit_not_selectable` de la cuenta de prueba no se reprodujo con una credencial válida; sí se corrigió la causa genérica (defecto 3). |
| `certificado_mipyme` | OK | reintento (ver corridas) | `portal_step_seleccionar_representado` (timeout del paso) en testing; local OK. |
| `pago_devoluciones` | OK (clave reciente) | OK | El diagnóstico `arca_service_not_visible` es DATOS cuando la cuenta no tiene el servicio (catálogo). |
| `hacienda` | OK (caso con relación válida); el caso del corpus sin relación falla también en V1 ("No se encontró botón Hacienda") | `represented_name_not_selectable` (DATOS, mismo caso que V1) | Defecto 2 corregido; el resto es DATOS. |
| `liquidacion_granos` | OK (5 XLSX) | OK | `arca_service_not_visible` = DATOS. |
| `compensaciones` | OK ("no se encontraron resultados") | OK | DATOS/build viejo. |
| `srt` | OK (alícuotas; `SIN_AFILIACION_VIGENTE` es dato) | PARCIAL (una consulta sin afiliación vigente) | El código funciona; `srt_cuit_input_failed`/`arca_service_not_visible` dependen de la cuenta. |
| `libros_portal_iva` | OK (periodo 202608, con reintento interno) | `libros_period_not_found` (DC) | Ver observación abajo. |
| `mis_facilidades` | OK (11 planes, 49 artefactos) | OK | 1.951 fallos históricos: no reproducidos con el build actual; `arca_service_not_visible` = DATOS. |
| `sifere` | OK (102 artefactos, 403 s) | credencial rechazada (clave vencida en la base) | `arca_service_not_visible` = DATOS; sin bug de matcher. |
| `declaracion_en_linea` | OK | OK | `plugin_service_api_mismatch` pertenecía a builds viejos del worker. |
| `portal_iva` | OK con periodo vigente (202610); `portal_iva_period_not_available` con 202608 | OK (202610) | El diagnóstico de período es DATOS: el portal sólo ofrece 202610 para la cuenta. |
| `sct` | OK (3 juegos de credenciales) | OK | `arca_service_not_visible` = DATOS (la cuenta de prueba no tiene SCT; el log V1 del 2026-03-15 lo confirma). |
| `vep/consulta-pagos` | `portal_download_missing` | `portal_download_missing` | BUG candidato (selección del período/representado en la SPA): ver más abajo. |
| `aportes_en_linea` | OK con `archivo_historico_minio=false` (genera `AEL - <cuit> - <fecha>.xls`) | reintento | El `ENVELOPE_INVALID` del corpus V1 (`archivo_historico_minio=false`) ya no ocurre: el job llega al worker y devuelve un error tipado. |

## Observaciones abiertas

1. **`vep/consulta-pagos` — `portal_download_missing`.** Reproducido localmente y
   en testing-central: el flujo llega a la exportación y no encuentra el botón de
   descarga. Hipótesis con evidencia estática: V1 usaba el primer combobox
   numérico como fallback y V3 usaba `.last`, de modo que podía quedar
   seleccionado el representado equivocado. El portal ahora verifica que el
   CUIT quede reflejado en el selector antes de dar por elegida la opción
   (`runtime/portals/consulta_pagos_vep.py`).
2. **`libros_portal_iva` en el worker del datacenter.** El mismo caso (misma
   cuenta, mismo periodo) termina OK desde la máquina del operador (incluso por
   proxy) y falla en `central-testing` con `libros_period_not_found` ("no hay
   libros IVA para el periodo solicitado"). La corrida local muestra que el
   primer intento falla y el reintento interno acierta: en el datacenter fallan
   los dos intentos. El portal ahora confirma el representado activo y reintenta
   el switcher; queda por validar en el datacenter.
3. **Clasificación pública de `arca_service_not_visible`.** Cuando la cuenta no
   tiene el servicio, el cliente recibe `target_unavailable`
   ("El portal no respondió como se esperaba…"), que sugiere reintentar. V1
   decía "No se encuentra habilitado el servicio de X". El worker ya tiene un
   código de diagnóstico distinto; un cambio de contrato público (nuevo
   `error_code` no reintentable) es una decisión de producto que quedó fuera de
   esta corrida.
4. **Cuenta de prueba `20370074829`.** No tiene clave válida en las bases, así que
   su catálogo no se pudo verificar en fresco. Los diagnósticos de producción
   (`arca_service_not_visible` en sct/sifere/mis_facilidades/liquidacion_granos/
   pago_devoluciones/compensaciones/certificado_mipyme/declaracion_en_linea,
   `represented_cuit_not_selectable` en facturometro/libros_portal_iva) son
   consistentes con una cuenta sin esos servicios y con representados no
   relacionados; el código actual no reproduce esos diagnósticos con cuentas
   válidas.

## Estado de la infraestructura de prueba

- `central-testing` (api2) corre las imágenes `mrbot-central-api:testing-fixes`
  y `mrbot-bot-worker:testing-fixes` con migraciones aplicadas y un worker
  (`mrbot-central-testing-worker`) registrado en el inventario
  (`WORKER_NODES=bot-worker:8080`).
- Usuario de prueba creado en esa central (`agente-test@mrbot.com.ar`), con
  clave `mbk_...` fuera del repositorio.
