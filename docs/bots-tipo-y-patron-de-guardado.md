# Bots de api-bots-mrbot-v3: tipo y patrón de guardado

Análisis de los 35 bots, uno por agente `claude-haiku-5-5`, leyendo el código. Revisado y corregido por el coordinador.

**Patrón general:** `{bucket}/jobs/{job_id}/{archivo}`. El nombre lo arma cada bot. La clave sanea todo carácter fuera de `A-Za-z0-9._() -` a `_` (por ejemplo `Pérez` queda `P_rez`), pero la descarga usa el nombre original guardado en la base.

**Tipos:** scrap = lee datos de la pantalla o de documentos y los devuelve en el JSON. descarga = baja archivos del sitio. carga = sube archivos o datos al sitio. genera = el bot arma el archivo. lee_pdf = extrae contenido de un PDF.

| Bot | Tipo | Patrón de bucket |
|---|---|---|
| apoc | ninguno (consulta una base local) | no guarda archivos |
| consulta_cuit | ninguno (consulta una API propia) | no guarda archivos |
| carga_portal_iva | carga (+ scrap de períodos) | no guarda archivos. Los TXT y CSV de entrada están en el bucket `uploads`: `{usuario}/carga_portal_iva/{cuit}/{archivo}` |
| aportes_en_linea | descarga | `aportesenlinea/jobs/{job_id}/AEL - {cuit} - {AAAAMMDD}.xls` |
| arba | descarga | `retper-iibb-arba/jobs/{job_id}/{ultimo_digito} - {cuit} - RETPER ARBA - {periodo} - {denominacion}.zip` |
| ccma | scrap, genera | `ccma/jobs/{job_id}/ccma_saldos_{cuit}.csv`<br>`ccma/jobs/{job_id}/ccma_movimientos_{cuit}.csv`<br>`ccma/jobs/{job_id}/ccma_resumen_{cuit}.pdf` |
| ccma_compensacion | scrap, genera, carga | `ccma/jobs/{job_id}/ccma_compensacion_{cuit}.pdf` |
| certificacion_primaria_granos | descarga, genera | `general/jobs/{job_id}/CPG - {cuit} - {desde} - {hasta}.xlsx`<br>`general/jobs/{job_id}/CD - {tipo} - deposito - {cuit} - SIN_CUIT_VENDEDOR - {coe}.pdf` |
| certificado_mipyme | descarga | `certificadomipyme/jobs/{job_id}/sepyme_{cuit}_{AAAAMMDD-HHMMSS}.pdf` |
| compensaciones | descarga | `general/jobs/{job_id}/sct_compensaciones_{cuit}_{AAAAMMDD_HHMMSS}.xls` (y `.csv`, `.pdf`) |
| comprobantes (alias de mis_comprobantes) | scrap, descarga, genera | `miscomprobantes/jobs/{job_id}/{ultimo_digito} - MCE\|MCR - {desde} - {hasta} - {cuit} - {nombre}.csv` |
| mis_comprobantes | scrap, descarga, genera | `miscomprobantes/jobs/{job_id}/{ultimo_digito} - MCE\|MCR - {desde} - {hasta} - {cuit} - {nombre}.csv`<br>y el mismo nombre con `.zip` (el ZIP original de ARCA, se sube siempre) |
| consulta_pagos_vep | scrap, descarga | `vep/jobs/{job_id}/pagos_vep_{cuit}_{periodo}.csv` |
| controladores_fiscales | scrap, descarga, carga | `controladoresfiscales/jobs/{job_id}/constancia_{nombre_sin_extension}.pdf` |
| declaracion_en_linea | scrap, descarga, genera | `declaracionenlinea/jobs/{job_id}/{ultimo_digito} - {cuit} - Declaracion en linea - DDJJ\|VEP\|DDJJ Cuiles - {AAAAMM} - {nombre}.pdf` (también `.json` de la DDJJ y la nómina en `.xls`) |
| facturometro | scrap, genera | `facturometro/jobs/{job_id}/facturometro_seccion.png` |
| hacienda | scrap, descarga, genera | `hacienda/jobs/{job_id}/HACIENDA - TABLA CONSOLIDADA - {cuit} - {denominacion} - {ddmmaaaa} - {ddmmaaaa} - {AAAAMMDD_HHMMSS}.xlsx`<br>`hacienda/jobs/{job_id}/HACIENDA - {POR_EMISOR\|POR_RECEPTOR} - {cuit} - {cuit_ref} - {codigo}.pdf` |
| libros_portal_iva | descarga, genera | `librosiva/jobs/{job_id}/descargar_libros_{cuit}_{periodo}.bin`<br>`librosiva/jobs/{job_id}/descargar_ddjj_{cuit}_{periodo}.bin` |
| libro_sueldos_digital | scrap, descarga | `lsd/jobs/{job_id}/{cuit}_{periodo}_{nro:05d}.pdf` |
| liquidacion_granos | descarga, genera | `liquidaciongranos/jobs/{job_id}/{cuit} - {LPG\|LSG EMITIDAS\|RECIBIDAS \| CERTIFICADOS DEPOSITO} - {ddmmaaaa} - {ddmmaaaa}.xlsx`<br>`liquidaciongranos/jobs/{job_id}/{LPG\|LSG} - {Emitida\|Recibida} - {cuit} - {cuit_vendedor} - {coe}.pdf` |
| mis_facilidades | scrap, genera | `misfacilidades/jobs/{job_id}/{ultimo_digito} - {cuit} - {plan} - {denominacion} - {vista}.pdf`<br>`misfacilidades/jobs/{job_id}/{ultimo_digito} - {cuit} - {plan} - {denominacion} - tablas.xlsx` |
| mis_retenciones | scrap, descarga, genera | `misretenciones/jobs/{job_id}/{ultimo_digito} - {cuit} - {desde} - {hasta} - {codigo_impuesto} - {Retencion\|Percepcion} - {denominacion}.csv` (y variantes `- siap.csv`, `- xls.csv`). Ejemplo real: `2 - 20137366852 - 01092026 - 30092026 - 216 - Retencion - Ayerza Fabian Cirio.csv` |
| mis_retenciones_iva_simple | scrap, descarga | `misretenciones/jobs/{job_id}/{ultimo_digito} - {cuit} - {desde} - {hasta} - IVA-SIMPLE - {operacion} - {formato} - {denominacion}.csv` |
| moa | scrap, genera | `moa/jobs/{job_id}/MOA - {cuit}.csv` |
| pago_devoluciones | descarga | `pagodevoluciones/jobs/{job_id}/pago_devoluciones_{cuit}_{AAAAMMDD_HHMMSS}.xlsx` |
| portal_iva | scrap, descarga, carga | `portaliva/jobs/{job_id}/PORTAL-IVA - ventas - {periodo} - {cuit}.csv`<br>`portaliva/jobs/{job_id}/PORTAL-IVA - compras - {periodo} - {cuit}.csv` |
| rcel | scrap, descarga, genera, lee_pdf (pdfplumber) | `rcel/jobs/{job_id}/{nombre de PDF que sugiere ARCA}` (uno por comprobante)<br>`rcel/jobs/{job_id}/rcel_facturas_{cuit}.csv` |
| retper_iibb_agip | descarga, genera | `retper-iibb-agip/jobs/{job_id}/{ultimo_digito} - {cuit} - RETPER IIBB AGIP - {desde} - {hasta} - {denominacion}.zip` |
| retper_iibb_misiones | descarga | `retper-iibb-misiones/jobs/{job_id}/{ultimo_digito} - {cuit} - {desde} - {hasta} - RETPER IIBB MISIONES - {denominacion}.xlsx` (y `.pdf`) |
| sct | descarga | `sct/jobs/{job_id}/sct_{seccion}_{cuit}_{sello}.{ext}` |
| sifere | descarga, genera | `sifere/jobs/{job_id}/{ultimo_digito} - {cuit} - SIFERE - SIRCAR\|SIRCREB\|SIRTAC - ... - {AAAAMM} - {representado}.{ext}` (xlsx, pdf, txt y consolidado) |
| siper | scrap, genera | `siper/jobs/{job_id}/{ultimo_digito} - SIPER - detalle - {cuit}.png`<br>`siper/jobs/{job_id}/{ultimo_digito} - SIPER - categorias - {cuit}.png` |
| srt | scrap, genera | `srt/jobs/{job_id}/srt_alicuotas.json` |
| vep_archivo | carga, descarga | `vep/jobs/{job_id}/{cuit} - VEP - {medio_pago}.pdf`<br>`vep/jobs/{job_id}/{cuit} - VEP - qr - QR.png` (solo con medio de pago QR) |
| vep_ccma | scrap, descarga | `vep/jobs/{job_id}/{cuit} - VEP CCMA.pdf`<br>`vep/jobs/{job_id}/{cuit} - VEP CCMA - QR.png` |

## Cosas que conviene saber

- **Colisión por limpieza de nombre (medida, sin corregir).** Dos nombres *distintos* pueden dar la misma clave porque todo carácter fuera de `A-Za-z0-9._() -` pasa a `_`: `Pérez.pdf` y `Pàrez.pdf` quedan ambos como `P_rez.pdf`, y `ñ.pdf` y `ó.pdf` como `_.pdf`. En producción no pasó nunca: de 24.169 artefactos, 0 jobs tienen dos nombres distintos con la misma clave. La corrección sería agregar un hash corto cuando la limpieza altera el nombre. No se aplicó porque cambiaría los nombres de los archivos nuevos respecto de los 24.000 ya guardados. Decisión pendiente.

- **CCMA, comprobado línea por línea y con producción (113 jobs reales del 29/10):** los 113 completaron con `datos_en_archivo` y con CSV en S4. `subir=false` solo evita subir el PDF: los CSV de saldos y movimientos se suben siempre (`plugin.py:279` está fuera del `if subir`). Un test lo fija a propósito. `ccma_compensacion` arma su nombre con `nombre_pdf_compensacion`, que ahora falla con un error claro si cambia el nombre base de CCMA (antes un `replace` fallaba en silencio).

- **Qué está comprobado con producción y qué no.** Los patrones de estos bots los comparé con archivos reales subidos después del despliegue (29/10): `arba`, `ccma`, `certificado_mipyme`, `declaracion_en_linea`, `facturometro`, `hacienda`, `libros_portal_iva`, `libro_sueldos_digital`, `liquidacion_granos`, `mis_comprobantes`, `mis_facilidades`, `mis_retenciones`, `pago_devoluciones`, `portal_iva`, `rcel`, `sct`, `sifere`, `siper`, `srt` y `vep_ccma`. El resto (`aportes_en_linea`, `compensaciones`, `controladores_fiscales`, `moa`, `consulta_pagos_vep`, `retper_iibb_agip`, `retper_iibb_misiones`, `certificacion_primaria_granos`, `mis_retenciones_iva_simple`, `vep_archivo`, `ccma_compensacion`, `comprobantes`) sale solo de leer el código, sin un archivo real que lo confirme.

- **Solo `rcel` lee el contenido de PDF** (con pdfplumber, para armar `rcel_facturas_{cuit}.csv`). Los demás bots que guardan PDF lo descargan o lo imprimen desde HTML, sin leerlo. `declaracion_en_linea` lee HTML, no PDF.
- **Bucket `general`:** `certificacion_primaria_granos` y `compensaciones` siguen sin bucket propio. Si `OBJECT_STORAGE_BUCKET` está vacío en algún entorno, la firma de subida cae a un ticket de desarrollo, así que hay que confirmar el bucket real.
- **Buckets inferidos por nombre, comprobados en S4 (30/10):** `comprobantes` → `miscomprobantes` (2 objetos), `libro_sueldos_digital` → `lsd` (51), `mis_retenciones_iva_simple` → `misretenciones` (61), `vep_ccma` (3) y `consulta_pagos_vep` (7) → `vep`. Los 201 objetos existen en el bucket asignado, y el listado de S4 incluye `lsd`, `misretenciones`, `vep` y `miscomprobantes`. Queda una discrepancia de nombre: el bot legacy de `mis_retenciones_iva_simple` apuntaba a `misretencionesivasimple`, que no existe en S4.
- **Colisiones posibles dentro de un job** (mismo nombre, uno pisa al otro):
  - `controladores_fiscales` (RIESGO_REAL en el código): `F8010.001.pem`, `F8010_001.pem` y `F8010.001.zip` dan la misma clave `constancia_F8010_001.pdf`. El nombre se corta a 100 caracteres.
  - `hacienda` (RIESGO_REAL en el código, nunca visto en producción: 0 jobs con nombre repetido en 37): dos PDF con igual `cuit_ref` y código, o ambos `SIN_CUIT` o `SIN_CODIGO`. El nombre no incluye punto de venta. La central rechaza el resultado entero con 422 por firma duplicada (`repositories/jobs.py:479`) y el worker lo da por entregado (`reporting/results.py:68`).
  - `libro_sueldos_digital` (RIESGO_BAJO, depende de que el portal entregue `nroliq` como `1` y `01` en el mismo período): `nro` conserva solo dígitos, así que `1` y `01` coinciden.
  - `carga_portal_iva`: en `uploads`, dos campos con el mismo nombre de archivo apuntan al mismo objeto.
- **Colisiones reproducidas ejecutando las funciones reales (30/10):** `controladores_fiscales` (`F8010.001.pem` y `F8010_001.pem` dan `constancia_F8010_001.pdf`, y el schema acepta ambos), `libro_sueldos_digital` (`1`, `01` y `1-A` dan `..._00001.pdf`) y `hacienda` (mismo `cuit_ref` y código dan el mismo nombre). `hacienda` distingue bien `POR_EMISOR` de `POR_RECEPTOR`. En producción no se vio ninguna: `controladores_fiscales` tiene 1 job con 27 archivos, `hacienda` 18 jobs con más de un archivo (máximo 7) y `libro_sueldos_digital` 6 (máximo 19), ninguno con nombres repetidos (155 filas). Ojo: es un sesgo de supervivencia, porque una colisión haría que la central rechazara el resultado con 422 y ese job no dejaría filas, así que esto no descarta que haya pasado. Los nombres reales de `controladores_fiscales` traen fecha y secuencia, por eso no chocan.
- **Reintentos:** la clave no incluye el intento. Lo verifiqué en producción: 10 jobs reintentados completaron con archivos y no hay ninguna clave duplicada en `job_artifacts`.
- **Hallazgos sin resolver:**
  - `ccma_compensacion` arma su nombre cambiando un texto de `ccma_resumen_{cuit}.pdf`. Si cambia ese prefijo no da error y el nombre queda mal.
  - `carga_portal_iva` tiene código muerto (`decodificar_archivo`) que usa `base64` sin importarlo.
  - En `consulta_cuit` la documentación dice carpeta `logger/{job_id}/` y el código sube `{job_id}.tar.gz`.
  - `aportes_en_linea`: resuelto leyendo S4. Los 6 archivos más recientes empiezan con `0d0a3c68746d6c3e` (`<html>`), o sea que el contenido es HTML aunque el nombre sea `.xls` y el tipo declarado sea `application/vnd.ms-excel`. Es una pregunta de producto si debería llamarse `.html`.
