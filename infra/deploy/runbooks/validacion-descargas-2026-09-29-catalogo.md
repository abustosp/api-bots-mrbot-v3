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

