# Validación E2E de bots de descarga, 27/09/2026

## Alcance y procedimiento

Se identificaron 28 operaciones de lectura/descarga comparando V1/V2 con el catálogo V3. Quedaron fuera las cargas de IVA Digital, presentación PEM, generación de VEP, solicitudes que crean trámites y bots sin archivos. Tres subagentes gpt-6-luna con esfuerzo xhigh inspeccionaron en solo lectura las tres bases históricas de `/home/abp/Desktop/databases`. Para cada operación se prefirió la combinación con éxito más reciente. Las credenciales, CUIT completos y respuestas con datos de clientes permanecen fuera de Git en archivos privados con permisos 0600.

Un cliente autenticado de V3 envió cada caso a la API central, consultó `GET /api/v3/jobs/{id}` hasta estado terminal y descargó cada archivo por `/api/v3/jobs/{id}/artifacts/{artifact_id}/download`. Se verificaron tamaño, SHA-256 si se declaró, contenido no vacío y formato por firma. Se corrió desde conciliabot2 para alcanzar la URL firmada del bucket. La central y el worker se probaron contra PostgreSQL y MinIO reales, no mocks.

## Resultados

| Operación | Resultado observado |
|---|---|
| `ccma.consultar` | COMPLETO, PDF verificado. Un segundo trabajo de CCMA se asignó al worker de `worker2.mrbot.com.ar:443` en api2 y produjo un PDF verificable de 105378 bytes. La asociación `jobs.worker_id` se corroboró en PostgreSQL. |
| `mis_comprobantes.consultar`, `mis_comprobantes.historial` | COMPLETO, dos CSV válidos por operación. |
| `compensaciones.consultar` | COMPLETO con datos, sin archivos para el rango histórico elegido. No prueba descarga de artefactos. |
| `aportes_en_linea.descargar`, `arba.descargar` | FALLIDO, `ARTIFACT_UPLOAD_FAILED` desde el plugin, sin presign observado para esos jobs. |
| `certificado_mipyme.descargar` | FALLIDO, timeout del portal. |
| `comprobantes.consultar`, `comprobantes.historial`, `consulta_pagos_vep.consultar`, `liquidacion_granos.consultar` | FALLIDO, credencial histórica rechazada. |
| `declaracion_en_linea.consultar`, `mis_facilidades.consultar`, `mis_retenciones_iva_simple.consultar` | FALLIDO, error interno de navegación (`AttributeError`) categorizado como destino no disponible. El rango IVA Simple fue acotado al máximo permitido por su esquema. |
| `hacienda.consultar`, `sifere.consultar`, `siper.consultar` | FALLIDO, servicio fiscal no visible para la cuenta de prueba. En SIPER se comprobó que la corrección de aliases eliminó el rechazo de esquema anterior. |
| `libros_portal_iva.descargar_libros`, `libros_portal_iva.descargar_ddjj`, `mis_retenciones.consultar`, `pago_devoluciones.consultar`, `portal_iva.descargar`, `rcel.descargar` | FALLIDO, representado no seleccionable. |
| `sct.consultar` | FALLIDO, `ENVELOPE_INVALID`: todavía no traduce los nueve flags V1 a pares sección/formato. |
| `srt.consultar_alicuotas` | FALLIDO, error `TypeError` del destino. |
| `moa.consultar` | No se ejecutó: los historiales disponibles no incluyen los despachos requeridos. |
| `retper_iibb_agip.consultar`, `retper_iibb_misiones.consultar` | No se ejecutaron: sin combinación exitosa atribuible en las tres bases. |

De las 28 operaciones previstas, 25 se ejecutaron al menos una vez. Tres completaron con archivos verificados, una completó con datos pero sin archivos, y 21 finalizaron con fallo. No equivale a afirmar que todos los bots funcionan. Durante el segundo pase hubo un error del runner (`UnboundLocalError`) en 11 casos, corregido antes de repetirlos; esos ensayos defectuosos no se cuentan como cobertura. La suite automatizada V3 tras los ajustes de normalización pasó: 391 pruebas, 9 omitidas, 12 subpruebas.

## Infraestructura y riesgos pendientes

- MinIO requería que el proceso central pudiera leer secretos Docker y firmar URLs reales. Se corrigieron permisos de esos secretos y la dirección efectiva del almacenamiento en el `.env` del servidor de testing. La dirección IP actual funciona desde conciliabot2 y api2. Las URLs firmadas viajan sobre HTTP hacia el puerto 9000: antes de producción, poner el almacenamiento detrás de HTTPS o de una red privada entre hosts.
- `worker2.mrbot.com.ar:443` y `worker-2.mrbot.com.ar:443` están registrados, sanos y declaran cinco plazas cada uno. Se aislaron temporalmente para probar el despacho de CCMA y luego se restableció la flota completa.
- Las credenciales históricas rechazadas y los representados no seleccionables requieren nuevas combinaciones autorizadas. Para SCT hace falta definir la equivalencia exacta entre los flags independientes V1 y los pares sección/formato de V3, sin descargar combinaciones adicionales no solicitadas. Quedan por investigar los errores internos y la carga de artefactos de Aportes/ARBA.
