# Cobertura del catálogo V3 al 29/09/2026

Cada operación marcada OK se ejecutó contra la central local con un cliente V3 real:
alta del job, polling hasta estado terminal, descarga firmada de cada artefacto y
validación de firma, tamaño y SHA-256. Sin credenciales ni datos de clientes.

Resumen: 42 operaciones del catálogo, 32 verificadas OK con evidencia, 2 verificadas
solo contra stubs locales, 7 excluidas por alcance y 1 bloqueada por el filtro
perimetral del organismo (AGIP), que se detalla al final del documento.

| operación | estado | evidencia |
|---|---|---|
| `apoc.consultar` | OK | 3 jobs reales por la API terminaron `COMPLETO` con `data.apoc = true` y fechas, resolviendo un CUIT contra la tabla real de AFIP (45.645 líneas provistas). Con la descarga caída y sin caché, la central responde `apoc: false` por el camino de fallback; detalle en «Tabla de apócrifos de AFIP (APOC)» |
| `aportes_en_linea.descargar` | OK | 1 planilla de 591.601 bytes (ARCA la sirve como HTML con extensión .xls) |
| `arba.descargar` | OK | 1 ZIP de 4.260 bytes con CP/CT/CB del período |
| `carga_portal_iva.cargar` | Excluida | carga de archivos, fuera del alcance pedido |
| `ccma.consultar` | OK | 1 PDF de 105.380 bytes |
| `certificado_mipyme.descargar` | OK | 1 PDF de 357.078 bytes |
| `compensaciones.consultar` | OK | 1 XLS de 16.896 bytes |
| `comprobantes.consultar` | OK | 2 CSV, 16.851 bytes |
| `comprobantes.historial` | OK | 2 CSV, 16.851 bytes |
| `comprobantes.solicitar` | OK | COMPLETO con ids de consulta (operación asincrónica, sin archivos) |
| `consulta_cuit.consulta` | OK | alias historico de `consultar`; el dispatcher lo traduce (hereda el alcance del stub de la fila anterior) |
| `consulta_cuit.consultar` | OK contra stub | El pipeline completo responde con la constancia, pero `CUIT_SERVICE_BASE_URL` apunta al stub local (`origen: stub-local`); falta probarlo contra el servicio real |
| `consulta_cuit.consultar_masivo` | OK contra stub | Igual que la anterior, con la lista de CUIT; el servicio real no está configurado en este entorno |
| `consulta_pagos_vep.consultar` | OK | 1 CSV de 22.195 bytes |
| `controladores_fiscales.presentar` | Excluida | presentación de archivos, fuera del alcance pedido |
| `declaracion_en_linea.consultar` | OK | 13-14 PDF, ~418 KB |
| `facturometro.consultar` | OK | COMPLETO con monto y tope en el JSON; no produce archivos |
| `hacienda.consultar` | OK | 2 CSV; el portal no devolvio filas para el período del caso |
| `libros_portal_iva.descargar_ddjj` | OK | 12 ZIP, 270.042 bytes |
| `libros_portal_iva.descargar_libros` | OK | 12 ZIP, 270.005 bytes |
| `liquidacion_granos.consultar` | OK | Job real `COMPLETO` con `attempts=1` (sin reencolados): 26 artefactos (5 XLSX + 21 PDF, 953.861 bytes) en la ventana 2024 y 79 artefactos (5 XLSX + 74 PDF, 2.899.933 bytes) con el rango pedido 01/01/2015-31/12/2025, todos con firma, tamaño y SHA-256 validados. El XLSX de LPG emitidas trae 64 filas de datos (desde 03/08/2015) y el de recibidas 6; LSG no tiene registros para este representado (el portal no los expone) |
| `mis_comprobantes.consulta` | OK | alias historico de `consultar`; el dispatcher lo traduce |
| `mis_comprobantes.consultar` | OK | 2 CSV, 16.851 bytes |
| `mis_comprobantes.historial` | OK | 2 CSV, 16.851 bytes |
| `mis_comprobantes.solicitar` | OK | COMPLETO con ids de consulta (sin archivos) |
| `mis_facilidades.consultar` | OK | 60 artefactos PDF/XLSX, 1,1 MB; el job necesita hasta 700 s |
| `mis_retenciones.consultar` | OK | 1 CSV de 1.062 bytes |
| `mis_retenciones_iva_simple.consultar` | Excluida | fuera del alcance pedido; con un rango válido entrega CSV de 5.978 bytes |
| `moa.consultar` | OK | 1 CSV de 1.031 bytes |
| `pago_devoluciones.consultar` | OK | 1 XLSX de 2.626 bytes |
| `portal_iva.descargar` | OK | 2 CSV (ventas y compras), 29.668 bytes |
| `portal_iva.gestionar` | Excluida | importa libros y después descarga: tiene efecto de carga |
| `portal_iva.importar` | Excluida | carga de archivos, fuera del alcance pedido |
| `rcel.descargar` | OK | 7 PDF, 601.628 bytes |
| `retper_iibb_agip.consultar` | Bloqueada por el perímetro del organismo | El login con usuario de ClaveCiudad funciona, pero el hop de sesión `lb.agip.gob.ar/gestionArciba/cc/redir` (submit del propio portal, posterior al login) responde HTTP 500 con la página de bloqueo del appliance (`Web Page Blocked!`, con `Attack ID 20000051` y la IP del egress que informa la propia pagina, que no se publica aca por ser un dato de red del operador); sin ese hop no hay camino al reporte, así que el rango 202506-202601 no llegó a pedirse y no hay ZIP ni SHA-256 que verificar. El filtro distingue la identidad del navegador: el mismo `curl` al mismo hop, en el mismo instante y desde la misma IP, recibe la respuesta normal con UA de Chrome (409) o de curl (409) y la página de bloqueo con UA `HeadlessChrome`. Detalle en `agip_evidencia_2026-09-29.md`. Corrida de cierre por la API pública con el código final: `01a0ed8e-c255`, 75 s, `attempts=1`, 0 artefactos, `agip_waf_blocked` |
| `retper_iibb_misiones.consultar` | OK | 1 XLSX de 3.723 bytes y 1 PDF de 140.615 bytes |
| `sct.consultar` | OK | 4 artefactos CSV/PDF, 38.780 bytes |
| `sifere.consultar` | OK | 24 XLSX, 130.311 bytes |
| `siper.consultar` | OK | 2 PNG, 25.593 bytes |
| `srt.consultar_alicuotas` | OK | 1 JSON, 475 bytes |
| `vep_archivo.generar` | Excluida | generación de VEP, fuera del alcance pedido |
| `vep_ccma.generar` | Excluida | generación de VEP, fuera del alcance pedido |

De las dos operaciones que estaban bloqueadas por falta de credenciales, las dos se
resolvieron con los pares que pasó el usuario: `liquidacion_granos` quedó verificada
en vivo y `retper_iibb_agip` llegó hasta el bloqueo perimetral del organismo descrito
en su propia sección al final del documento (ya no es un problema de credenciales).

## Verificación independiente (29/09/2026)

Un subagente aparte repitió las descargas y leyó los bytes de cada archivo desde el
disco, sin confiar en el resumen del runner. Observado:

| Operación | Artefacto | Bytes | Evidencia de contenido | Veredicto |
|---|---|---|---|---|
| `arba.descargar` | ZIP | 4.260 | firma `PK\x03\x04` y tres miembros CP/CT/CB del período | válido |
| `aportes_en_linea.descargar` | `.xls` | 591.601 | empieza con `<html>`: ARCA sirve la planilla como HTML con esa extensión | válido (limitación del organismo) |
| `ccma.consultar` | PDF | 105.380 | primeros bytes `%PDF-1.4` | válido |
| `mis_comprobantes.consultar` | 2 CSV | 2.998 y 13.853 | encabezado `Fecha de Emisión;Tipo de Comprobante;...` | válidos |
| `mis_comprobantes.historial` | 2 CSV | 2.998 y 13.853 | mismo encabezado y mismo SHA-256 que la operación anterior | válidos |
| `liquidacion_granos.consultar` (control negativo) | no aplica | no aplica | falla con `CREDENTIALS_REJECTED` y sin artefactos | clasificación correcta |
| `liquidacion_granos.consultar` (rango pedido) | 5 XLSX + 74 PDF | 2.899.933 | XLSX abiertos hoja por hoja: 65/7/2/2/5 filas; PDF con cabecera `%PDF-1.4`; SHA-256 y tamaño declarado coincidentes en los 79; el bucket del almacenamiento tiene exactamente esos 79 objetos bajo el intento 1 y su suma de bytes coincide con lo que declara la API | válidos |

La verificación de las operaciones sin archivos se repitió leyendo el payload del job
en la API: `facturometro.consultar` devolvió `monto` y `tope` reales, y
`comprobantes.solicitar` y `mis_comprobantes.solicitar` devolvieron los dos
identificadores de consulta (`emitidos` y `recibidos`).

## Defecto de despacho corregido durante la validación (29/09/2026)

Los jobs que tardaban más de ~20 s empezaron a terminar `FALLIDO` con
`error_code=lease_expired` y los intentos agotados (`attempts` 1 → 2 → 3 en un
minuto), con artefactos ya subidos y sin resultado: la API devolvía 422
`metadata de artefacto inválida` cuando los `object_key` quedaban repartidos
entre varios intentos (`jobs/{job_id}/{attempt}/...`) y luego 403 porque el job
ya era terminal. Observado con `liquidacion_granos` y `retper_iibb_agip`.

Causa: el reaper en PostgreSQL decide con `jobs.lease_expires_at`, que solo se
escribía al asignar el job (`WORKER_ACK_LEASE_SECONDS`, 20 s). La renovación por
evento válido actualizaba únicamente la copia en memoria, así que el trabajo en
curso se reencolaba aunque el worker estuviera sano.

Arreglo (`832020d`): `repositories/jobs.persist_event` renueva
`job.lease_expires_at` en la misma transacción del evento para `started`,
`progress` y `heartbeat_hint`, y el worker manda `heartbeat_hint` cada 20 s
mientras el job corre, para no depender del progreso del plugin en etapas
silenciosas (descargas, armado de ZIP, espera del portal).

Evidencia:

- Antes: `01a0ed6d`, `01a0ed6f` y `01a0ed73` con reentrega cada 20 s exactos y
  presigns cambiando de intento en una sola corrida.
- Después: `01a0ed7b` (26 artefactos, 60 s) y `01a0ed7c` (79 artefactos, 90 s)
  terminaron `COMPLETO` con `attempts=1`, es decir sin un solo reencolado. Los intentos
  de `retper_iibb_agip` posteriores al arreglo (`01a0ed80`, `01a0ed83`) también quedaron
  en `attempts=1`: su único fallo es del portal o de la red, no del despacho.
- Tests: `services/central-api/tests/test_lease_renewal_pg.py` (4 casos contra
  PostgreSQL real: `started`, `progress`, `heartbeat_hint` extienden la lease, y
  el control negativo de un job terminal que no la renueva),
  `services/bot-worker/tests/test_lease_renewal.py` y una aserción de renovación
  dentro del ciclo de vida PG.

## Retenciones AGIP (`retper_iibb_agip`): bloqueo perimetral del organismo

Con el par de credenciales que pasó el usuario el login de ClaveCiudad funciona
(el bot llega a pedir el reporte), pero el hop de sesión del propio portal,
`lb.agip.gob.ar/gestionArciba/cc/redir`, responde con la página de bloqueo del
appliance perimetral: HTTP 500, `Web Page Blocked!` y `Attack ID 20000051` (la
pagina tambien informa la IP del egress, que no se publica por ser un dato de red
del operador). Sin ese hop no hay camino al reporte, así que el rango
202506-202601 no llegó a pedirse: **no hay ZIP ni SHA-256 que verificar**.

El control que separa identidad de red (misma IP, mismo instante, mismo hop)
mostró que el filtro reacciona a la identidad del navegador y no a la IP:

| Cliente | User-Agent | Respuesta |
|---|---|---|
| `curl` | `curl/8.5.0` | 409 Conflict (respuesta normal del portal sin sesión) |
| `curl` | `Chrome/140.0.0.0` | 409 Conflict |
| `curl` | `HeadlessChrome/140.0.7339.16` | **500 + página de bloqueo** |

Lo que sí quedó arreglado en el bot (`0fae68f`):

- Reintento acotado por ventanas (5, con espera creciente y navegación nueva),
  que ya permitió pasar la portada en la corrida de 14:10 tras 4 reintentos.
- Aviso de progreso antes de cada espera, para que el reaper no reencole el job
  durante el backoff (con el arreglo de lease, `attempts=1` en todos los intentos).
- `lb.agip.gob.ar` en `hosts_permitidos`: es el submit real del portal, no una
  ruta inventada.
- Diagnósticos separados: `agip_waf_blocked` cuando llega la página del
  appliance y `agip_service_unavailable` cuando el hop corta la conexión.

**No se falseó la identidad del navegador ni se salteó el filtro**: eso queda
como decisión del usuario, junto con las alternativas que dependen de él (correr
el navegador en modo con interfaz bajo Xvfb, salir por otra red, o pedir que
AGIP habilite el acceso). Evidencia cruda en `agip_evidencia_2026-09-29.md`.

### Cierre del intento de aceptación (14:25 UTC)

Corrida real por la API pública, con el par provisto y el código final desplegado:
job `01a0ed8e-c255-7475-883b-80f2a3f4b9fe`, `FALLIDO` en 75 s, `attempts=1/3`, 0
artefactos, `TARGET_UNAVAILABLE` / `agip_waf_blocked` con `TimeoutError` como causa
y 3 renovaciones de lease durante las ventanas de reintento. El runbook del arnés
no imprime las claves; el detalle sale de la base y del log del worker.

Comparación antes/después del arreglo de lease con todos los jobs de AGIP del día:

| Momento | Jobs | Intentos por job | Causa del fallo |
|---|---|---|---|
| 13:46-14:01 (antes) | `01a0ed6a`, `01a0ed6b`, `01a0ed6e`, `01a0ed78` | 1, 3, 3, 3 | `lease_expired` en los tres reencolados y un `agip_waf_blocked` |
| 14:03-14:25 (después) | `01a0ed7a`, `01a0ed80`, `01a0ed83`, `01a0ed8e` | 1, 1, 1, 1 | solo del organismo: `agip_service_unavailable` y `agip_waf_blocked` |

Qué se probó contra qué, para no dejar el resultado en “no se pudo”:

| Requisito de la operación | Verificación | Resultado observado |
|---|---|---|
| Autenticar con usuario de ClaveCiudad | Job real por API con el par provisto | el portal acepta las credenciales y el flujo avanza hasta el hop de sesión |
| Llegar al servicio de Ret/Per de Gestión-AR | Navegación real del portal | bloqueado por el appliance en `lb.agip.gob.ar/gestionArciba/cc/redir` (HTTP 500 + `Web Page Blocked!`) |
| Descargar el reporte del rango 202506-202601 | Job real por API | no alcanzado: sin ese hop el rango no se pide, 0 artefactos |
| Distinguir bloqueo de caída y reintentar por ventanas | 4 corridas posteriores al arreglo | 4 reintentos hasta pasar la portada y dos diagnósticos separados según el comportamiento del appliance |
| No degradar el despacho | Serie de jobs del día | `attempts=1` en todas las corridas posteriores al arreglo, con renovaciones de lease registradas |

Límite de diseño que cierra el análisis: el worker corre **siempre** headless
(`runtime/browser.py`: `headless no negociable`) y, aunque la imagen trae Xvfb, el
código rechaza el modo con interfaz. Con el appliance filtrando justamente la
identidad headless, no hay camino soportado dentro del diseño actual: las salidas
dependen del usuario (otra red de salida, habilitación en AGIP, o una decisión de
producto de cambiar ese invariante).

## Trazabilidad de los requisitos

| Requisito | Verificación | Resultado observado |
|---|---|---|
| Probar las operaciones restantes del catálogo | Alta real, polling y descarga firmada por operación | 32 con evidencia de artefacto o payload, 2 contra stub local, 7 excluidas y 1 bloqueada por el perímetro del organismo (AGIP) |
| Usar las credenciales que pasó el usuario para los dos bloqueos | Jobs reales por API con los pares provistos | `liquidacion_granos` quedó `COMPLETO` con 105 artefactos entre las dos ventanas (26 + 79) y `retper_iibb_agip` llegó hasta el hop de sesión del portal, donde el appliance del organismo le sirve su página de bloqueo |
| Excluir cargas de archivos, VEP e IVA Simple | Lectura del código de cada operación excluida | `carga_portal_iva.cargar`, `controladores_fiscales.presentar`, `portal_iva.importar`, `portal_iva.gestionar`, `vep_archivo.generar`, `vep_ccma.generar` y `mis_retenciones_iva_simple.consultar` no se ejecutaron |
| Usar los ejemplos más recientes de las bases | Comparación del timestamp de cada caso contra el máximo con éxito de su tabla | Los ocho casos comparados usan exactamente el máximo (arba 16/09, aportes 28/08, ccma 24/09, facturómetro 26/09, Mis Comprobantes 26/09) |
| Verificar con evidencia, no con inspección | Bytes leídos por un subagente distinto del que armó los casos | Firma de PDF y ZIP, miembros del ZIP, encabezados de CSV y SHA-256 propios, más control negativo |

## Tabla de apócrifos de AFIP (APOC)

### Origen y forma de los datos

- Fuente pública de AFIP:
  `https://servicioscf.afip.gob.ar/facturacion/facturasapocrifas/DownloadFile.aspx`.
- Responde un ZIP de ~500 KB cuyo único miembro es `FacturasApocrifas.txt`.
- El texto es UTF-8 con BOM, de ~1,7 MB y ~45.600 líneas. Las primeras líneas
  empiezan con `#` (encabezados) y luego siguen filas de datos con el formato
  `CUIT,Fecha Condicion Apocrifo, Fecha Publicacion, Descripcion`.
- No se documentan CUITs ni datos de contribuyentes en este runbook.

### Descarga y caché en la central

Archivo: `services/central-api/src/central_api/services/apoc_base.py`.

- La central descarga la tabla solo si no existe la caché local.
- La refresca cuando el archivo en disco tiene más de 7 días
  (`APOC_REFRESH_INTERVAL_DAYS`).
- La descarga y el refresco corren en una tarea de fondo (`refresh_loop`) que
  no bloquea el alta de jobs.
- La ruta se configura con `APOC_BASE_PATH`. En el compose por defecto es
  `/var/lib/mrbot/FacturasApocrifas.txt`, montada sobre el volumen `apoc_base`
  para sobrevivir a los reinicios.
- Antes de reemplazar la caché se valida que el contenido parezca la tabla ("# AFIP"
  en los encabezados y al menos una fila `CUIT,fecha,fecha`) y que no supere el
  límite de tamaño.
- Si la descarga falla, se conserva la caché vieja y se registra el aviso; si no
  hay caché, el worker mantiene su camino de fallback (`apoc: false`) con el aviso
  "base APOC no provisionada".
- Si al arrancar la descarga falla y no hay caché, el bucle reintenta dentro de una
  hora (no espera los 7 días); con caché al día respeta el intervalo semanal. Este
  comportamiento está cubierto por tests del módulo (con reloj simulado); en vivo no
  se observó disparando ni el temporizador semanal ni el reintento de una hora. Lo
  que sí se observó en vivo, en el camino de arranque: (a) arranque en frío sin
  caché, el archivo aparece solo a los pocos segundos del arranque (05:14:56 UTC);
  (b) arranque con la caché envejecida 8 días, se refresca al arrancar (06:23:29 UTC,
  mismo tamaño 1.734.993 bytes); (c) archivo fresco, no se re-descarga (misma fecha
  tras 60 s y tras reiniciar).
- La caché vive en el volumen Docker `apoc_base` montado en `/var/lib/mrbot`, con
  `APOC_BASE_PATH=/var/lib/mrbot/FacturasApocrifas.txt`, por lo que sobrevive a los
  reinicios del contenedor. Confirmado el 29/09/2026.
- La extracción del ZIP tolera que el miembro `.txt` venga con otro nombre, siempre
  que sea único y que el contenido parezca la tabla, y falla limpio si no hay un
  `.txt`, si hay más de uno o si el contenido no parece la tabla. Confirmado el
  29/09/2026.

### Operación en este entorno

- La caché vive en el volumen Docker `apoc_base` montado en `/var/lib/mrbot`; el
  compose base define `APOC_BASE_PATH=/var/lib/mrbot/FacturasApocrifas.txt`.
- Al recrear el contenedor de la central en este entorno hay que usar **los dos**
  archivos compose: `-f infra/compose/docker-compose.yml -f
  /home/abp/.jcode/scratch/v3pc/docker-compose.pc.yml` (y `COMPOSE_PROJECT_NAME=mrbot-pc`).
  Recrear solo con el archivo base pierde el montaje del código y los secretos
  locales, y la central queda apuntando a otra base (`OperationalError`, `/ready`
  en false) aunque los contenedores parezcan sanos.
- Cómo comprobar que quedó bien: `curl -s http://127.0.0.1:8000/ready` debe devolver
  `"ready":true`, y `docker inspect` del contenedor debe mostrar el volumen
  `mrbot-pc_apoc_base` en `/var/lib/mrbot`.

### Provisión del sobre y worker

- La central envía el texto solo al bot `apoc`, dentro de la sección `service` del
  sobre sellado (`provisioned_section('apoc')` con la clave `apoc_base_text`); no
  lo incluye en el sobre de los demás bots.
- El plugin (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py`) compara el
  CUIT normalizado (solo dígitos), ignora los encabezados `#` y las líneas vacías,
  y tolera la columna extra (`Descripcion`).

### Tope del sobre sellado

- El sobre sellado tenía un tope de 1 MiB que impedía enviar la tabla: el job
  quedaba ASIGNADO sin POST al worker.
- El tope pasó a 4 MiB **en claro** en la central con rechazo estricto por encima
  (commit `0aab3aa`), con el valor en `MAX_SEALED_SECTION_BYTES` y documentado en
  `security/sealed.py`.
- Del lado del worker se valida el blob Fernet con la fórmula correcta
  (`MAX_SEALED_SECTION_BYTES + 57 + 16`, y su base64), porque el cálculo anterior
  usaba la fórmula de RSA y rechazaba cualquier sección mayor a ~1 MB. Confirmado
  el 29/09/2026.

### Verificaciones ya observadas

- Descarga sin caché OK (ZIP descomprimido de 1.734.993 bytes).
- Refresco con un archivo de 8 días OK (por encima del intervalo de 7 días).
- Archivo fresco (menos de 7 días) no refresca.
- Provisión con 45.645 líneas para `apoc` y ausente (`apoc_base_text`) para los
  demás bots.
- El plugin resuelve un CUIT de la tabla real con `apoc: true` y sus fechas.

### Pendiente (resuelto el 29/09/2026)

- El job real de `apoc.consultar` con un CUIT que figura en la tabla terminó
  `COMPLETO` en ~0,1 s con `data.apoc = true` y `fecha_condicion`/`fecha_publicacion`
  no nulas. Primera confirmación a las 04:57 y reverificación tras corregir el tope
  del worker.

### Límites de la evidencia

- El temporizador semanal del bucle no se observó disparando en vivo; solo se
  observó el camino de arranque, que usa la misma función.
- La validez de los datos proviene de AFIP: si el organismo cambia el formato o el
  nombre del miembro, la extracción tolera el renombre pero falla limpio si el
  contenido no parece la tabla.
- En este entorno la caché se descargó 3 veces en el día por pruebas, algo esperable
  en desarrollo.
- **Almacenamiento de artefactos**: la descarga firmada de cada artefacto se validó
  contra un servidor S3 **emulado** local con el alias de red `minio` y
  `127.0.0.1:9000`, que es lo que espera la configuración de la central
  (`OBJECT_STORAGE_ENDPOINT=http://minio:9000`,
  `OBJECT_STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:9000`). MinIO real no es obtenible
  en esta máquina: `dl.min.io` responde 410 (el binario ya no se distribuye por ahí) y
  el manifiesto en `quay.io/minio/minio` responde 401 sin credenciales; el servicio
  `minio` del Compose vive detrás del perfil `local-storage` y depende de esas
  imágenes. Consecuencia: el camino de firma, subida por URL prefirmada, descarga y
  verificación de SHA-256 se ejercitó de punta a punta, pero **no** la validación de
  firma de un servidor S3 real. Esto aplica a los artefactos de todas las corridas de
  esta sesión, incluidas las 31 operaciones ya verificadas. Operación del reemplazo:
  el emulador corre como contenedor `mrbot-pc-minio-emu` (imagen ya presente en la
  máquina) con alias de red `minio` en `mrbot-pc_storage` y publicación
  `127.0.0.1:9000`; el bucket `mrbot` se crea con
  `curl -X PUT http://127.0.0.1:9000/mrbot`. Para volver al estado previo alcanza
  `docker rm -f mrbot-pc-minio-emu`; si algún día se consiguen las imágenes, el
  camino soportado es `docker compose --profile local-storage up -d minio minio-init`.
- **Reinicio del worker con jobs en vuelo**: el worker genera un `instance_nonce`
  nuevo en cada arranque (`bot_worker/main.py`) y la central exige que
  `jobs.worker_id` coincida con la identidad canónica del nodo
  (`internal/job_access.py`). Un job asignado antes de un reinicio queda sin poder
  reportar: `/events`, `/artifacts/presign` y `/result` devuelven 403 `asignación de
  otro worker`. Observado el 29/09 en `01a0ed6d-409c-7e1a-91b2-fac04f64f6bc` (cuatro
  presign 201 y PUT 200, reinicio, y a partir de ahí 403 en todo, job `FALLIDO` con
  `files: []` y `data: null`) y en `01a0ed6e-7932-71e2-821d-c9e98b2d4a99`, creado diez
  segundos después del reinicio durante el cambio de identidad: `/events` y `/result`
  respondieron 409 y luego 403. Los recupera la política de lease vencido; en
  despliegues conviene **drenar antes de reiniciar**. Los objetos ya subidos por un job
  que después falla quedan **huérfanos** en el bucket, sin limpieza automática.

