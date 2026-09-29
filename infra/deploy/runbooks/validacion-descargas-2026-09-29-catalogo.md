# Cobertura del catálogo V3 al 29/09/2026

Cada operación marcada OK se ejecutó contra la central local con un cliente V3 real:
alta del job, polling hasta estado terminal, descarga firmada de cada artefacto y
validación de firma, tamaño y SHA-256. Sin credenciales ni datos de clientes.

Resumen: 42 operaciones del catálogo, 33 verificadas OK, 7 excluidas por alcance y 2 bloqueadas por credenciales.

| operación | estado | evidencia |
|---|---|---|
| `apoc.consultar` | OK | COMPLETO con datos de la base APOC; no produce archivos |
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
| `consulta_cuit.consultar` | OK | COMPLETO con la constancia del servicio de CUIT |
| `consulta_cuit.consultar_masivo` | OK | COMPLETO con la lista de CUIT consultada |
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
