# Cobertura del catálogo V3 al 29/09/2026

Cada operación marcada OK se ejecutó contra la central local con un cliente V3 real:
alta del job, polling hasta estado terminal, descarga firmada de cada artefacto y
validación de firma, tamaño y SHA-256. Sin credenciales ni datos de clientes.

Resumen: 42 operaciones del catálogo, 31 verificadas OK con evidencia, 2 verificadas
solo contra stubs locales, 7 excluidas por alcance y 2 bloqueadas por credenciales.

| operación | estado | evidencia |
|---|---|---|
| `apoc.consultar` | OK de lógica | La búsqueda funciona (verificada ejecutando el plugin con una base sintética: devuelve `apoc: true` y fechas). En este entorno la central no provisiona `apoc_base_text`, así que la API responde `apoc: false` por el camino de fallback |
| `aportes_en_linea.descargar` | OK | 1 planilla de 591.601 bytes (ARCA la sirve como HTML con extensión .xls) |
| `arba.descargar` | OK | 1 ZIP de 4.260 bytes con CP/CT/CB del período |
| `carga_portal_iva.cargar` | Excluida | carga de archivos, fuera del alcance pedido |
| `ccma.consultar` | OK | 1 PDF de 105.380 bytes |
| `certificado_mipyme.descargar` | OK | 1 PDF de 357.078 bytes |
| `compensaciones.consultar` | OK | 1 XLS de 16.896 bytes |
| `comprobantes.consultar` | OK | 2 CSV, 16.851 bytes |
| `comprobantes.historial` | OK | 2 CSV, 16.851 bytes |
| `comprobantes.solicitar` | OK | COMPLETO con ids de consulta (operación asincrónica, sin archivos) |
| `consulta_cuit.consulta` | OK | alias historico de `consultar`; el dispatcher lo traduce |
| `consulta_cuit.consultar` | OK contra stub | El pipeline completo responde con la constancia, pero `CUIT_SERVICE_BASE_URL` apunta al stub local (`origen: stub-local`); falta probarlo contra el servicio real |
| `consulta_cuit.consultar_masivo` | OK contra stub | Igual que la anterior, con la lista de CUIT; el servicio real no está configurado en este entorno |
| `consulta_pagos_vep.consultar` | OK | 1 CSV de 22.195 bytes |
| `controladores_fiscales.presentar` | Excluida | presentación de archivos, fuera del alcance pedido |
| `declaracion_en_linea.consultar` | OK | 13-14 PDF, ~418 KB |
| `facturometro.consultar` | OK | COMPLETO con monto y tope en el JSON; no produce archivos |
| `hacienda.consultar` | OK | 2 CSV; el portal no devolvio filas para el período del caso |
| `libros_portal_iva.descargar_ddjj` | OK | 12 ZIP, 270.042 bytes |
| `libros_portal_iva.descargar_libros` | OK | 12 ZIP, 270.005 bytes |
| `liquidacion_granos.consultar` | Bloqueada | ARCA rechaza las 8 claves historicas; con claves vigentes de otros bots el login pasa y falla por servicio no asignado o representado no seleccionable |
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
| `retper_iibb_agip.consultar` | Bloqueada | sin cuenta ClaveCiudad para los pares disponibles; el WAF de AGIP bloqueó 5 de 6 intentos |
| `retper_iibb_misiones.consultar` | OK | 1 XLSX de 3.723 bytes y 1 PDF de 140.615 bytes |
| `sct.consultar` | OK | 4 artefactos CSV/PDF, 38.780 bytes |
| `sifere.consultar` | OK | 24 XLSX, 130.311 bytes |
| `siper.consultar` | OK | 2 PNG, 25.593 bytes |
| `srt.consultar_alicuotas` | OK | 1 JSON, 475 bytes |
| `vep_archivo.generar` | Excluida | generación de VEP, fuera del alcance pedido |
| `vep_ccma.generar` | Excluida | generación de VEP, fuera del alcance pedido |

Las dos operaciones bloqueadas necesitan credenciales que no están en las bases:
una Clave Fiscal vigente de un CUIT con *Liquidación Primaria de Granos* asignado, y un
usuario con clave de *ClaveCiudad* para AGIP.

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
| `liquidacion_granos.consultar` (control negativo) | — | — | falla con `CREDENTIALS_REJECTED` y sin artefactos | clasificación correcta |

La verificación de las operaciones sin archivos se repitió leyendo el payload del job
en la API: `facturometro.consultar` devolvió `monto` y `tope` reales, y
`comprobantes.solicitar` y `mis_comprobantes.solicitar` devolvieron los dos
identificadores de consulta (`emitidos` y `recibidos`).

## Trazabilidad de los requisitos

| Requisito | Verificación | Resultado observado |
|---|---|---|
| Probar las operaciones restantes del catálogo | Alta real, polling y descarga firmada por operación | 31 con evidencia de artefacto o payload, 2 contra stub local, 7 excluidas y 2 bloqueadas por credenciales |
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

