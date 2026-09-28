# Batería integrada de descargas, 28/09/2026

Cada caso se envió a la central local con un cliente V3 real; por cada artefacto se
siguió el redirect firmado, se descargó el archivo y se validaron tamaño, SHA-256 y
formato por firma. Sin credenciales ni datos de clientes en este informe.

| caso | resultado | estado | seg | archivos ok/total | tipos | bytes |
|---|---|---|---|---|---|---|
| comprobantes.consultar | OK | COMPLETO | 20 | 2/2 | csv | 16851 |
| comprobantes.historial | OK | COMPLETO | 20 | 2/2 | csv | 16851 |
| consulta_pagos_vep.consultar | OK | COMPLETO | 30 | 1/1 | csv | 22195 |
| declaracion_en_linea.consultar | OK | COMPLETO | 90 | 13/13 | pdf | 417915 |
| hacienda.consultar | OK | COMPLETO | 40 | 2/2 | csv,texto | 78 |
| libros_portal_iva.descargar_ddjj | OK | COMPLETO | 190 | 12/12 | zip | 270042 |
| libros_portal_iva.descargar_libros | OK | COMPLETO | 200 | 12/12 | zip | 270005 |
| liquidacion_granos.consultar | FALLIDO | FALLIDO | 10 | 0/0 | - | 0 |
| mis_facilidades.consultar | OK | COMPLETO | 700 | 60/60 | pdf,xlsx | 1109704 |
| mis_retenciones.consultar | OK | COMPLETO | 160 | 1/1 | csv | 1062 |
| mis_retenciones_iva_simple.consultar | OK_SIN_ARCHIVOS | COMPLETO | 30 | 0/0 | - | 0 |
| moa.consultar | OK | COMPLETO | 40 | 1/1 | csv | 1031 |
| pago_devoluciones.consultar | OK | COMPLETO | 20 | 1/1 | xlsx | 2626 |
| portal_iva.descargar | OK | COMPLETO | 40 | 2/2 | csv | 29668 |
| rcel.descargar | OK | COMPLETO | 20 | 7/7 | pdf | 601628 |
| sct.consultar | OK | COMPLETO | 50 | 4/4 | csv,pdf | 38780 |
| sifere.consultar | OK | COMPLETO | 110 | 24/24 | xlsx | 130311 |
| siper.consultar | OK | COMPLETO | 80 | 2/2 | png | 25593 |
| srt.consultar_alicuotas | OK | COMPLETO | 40 | 1/1 | json | 475 |

Total: 19 operaciones; 17 completas con archivos verificados, 1 completas sin archivos (el portal no tenía datos para ese rango) y 1 fallida por credenciales rechazadas por el organismo.
