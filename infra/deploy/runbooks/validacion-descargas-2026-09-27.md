# Validación E2E de bots de descarga, 27/09/2026

## Alcance y procedimiento

Se identificaron 28 operaciones de lectura/descarga comparando V1/V2 con el catálogo V3. Quedaron fuera las cargas de IVA Digital, la presentación PEM, la generación de VEP, las solicitudes que crean trámites y los bots sin archivos. Tres subagentes gpt-6-luna con esfuerzo xhigh inspeccionaron en solo lectura las tres bases históricas de `/home/abp/Desktop/databases`. Para cada operación se prefirió la combinación con éxito más reciente. Las credenciales, los CUIT completos y las respuestas con datos de clientes permanecen fuera de Git, en archivos privados con permisos 0600.

Un cliente autenticado de V3 envió cada caso a la API central, consultó `GET /api/v3/jobs/{id}` hasta estado terminal y descargó cada archivo por `/api/v3/jobs/{id}/artifacts/{artifact_id}/download`. Se verificaron tamaño, SHA-256 declarado, contenido no vacío y formato por firma. La corrida se ejecutó desde conciliabot2 contra la central, PostgreSQL y MinIO reales.

Estado del sistema durante la corrida final: central `01c1c04`, los tres workers de la flota (`bot-worker:8080`, `worker2.mrbot.com.ar:443`, `worker-2.mrbot.com.ar:443`) con la imagen que incluye el arreglo de artefactos, 5 plazas cada uno y latido fresco.

## Resultado de la corrida completa

26 casos enviados, 26 con estado terminal, sin errores del runner y sin rechazos por cuota.

| resultado | casos |
|---|---|
| OK con archivos descargados y verificados | 5 (7 artefactos: PDF de CCMA, 4 CSV de mis comprobantes, planilla de Aportes, ZIP de ARBA) |
| COMPLETO con datos y sin archivos | 0 en esta corrida (compensaciones completó sin archivos en el ensayo previo) |
| FALLIDO con causa clasificada | 21 |

| Operación | Resultado observado |
|---|---|
| `ccma.consultar` | COMPLETO, PDF de 105379 bytes verificado. Jobs completos en los tres nodos de la flota. |
| `mis_comprobantes.consultar`, `mis_comprobantes.historial` | COMPLETO, dos CSV válidos por operación; artefactos subidos desde los tres nodos. |
| `aportes_en_linea.descargar`, `arba.descargar` | COMPLETO con archivo verificado tras habilitar el presign bajo demanda para los artefactos que declara el manifiesto del plugin. Antes fallaban con `ARTIFACT_UPLOAD_FAILED` porque el sobre solo enumeraba slots para CCMA y mis comprobantes. |
| `certificado_mipyme.descargar` | FALLIDO, timeout del portal. |
| `comprobantes.consultar`, `comprobantes.historial`, `consulta_pagos_vep.consultar`, `liquidacion_granos.consultar` | FALLIDO, credencial histórica rechazada por el organismo. |
| `declaracion_en_linea.consultar`, `mis_facilidades.consultar` | FALLIDO, error interno de navegación (`AttributeError`) clasificado como destino no disponible. |
| `hacienda.consultar`, `sifere.consultar`, `siper.consultar` | FALLIDO, servicio fiscal no visible para la cuenta de prueba. En SIPER se comprobó que la traducción de flags V1 eliminó el rechazo de esquema previo. |
| `libros_portal_iva.descargar_libros`, `libros_portal_iva.descargar_ddjj`, `mis_retenciones.consultar`, `mis_retenciones_iva_simple.consultar`, `pago_devoluciones.consultar`, `portal_iva.descargar`, `rcel.descargar` | FALLIDO, representado no seleccionable. Los casos atravesaron validación de esquema: los arreglos de campos y de rango de IVA Simple quedaron verificados. |
| `sct.consultar` | FALLIDO, `ENVELOPE_INVALID`: falta definir la equivalencia entre los nueve flags V1 y los pares sección/formato del worker. |
| `moa.consultar` | FALLIDO, `ENVELOPE_INVALID`: la operación exige despachos y los registros históricos disponibles no los incluyen. |
| `srt.consultar_alicuotas` | FALLIDO, `TypeError` del sitio del organismo. |
| `compensaciones.consultar` | En el ensayo previo completó con datos y sin archivos para el rango elegido. En la corrida final cayó por timeout del portal, igual que certificado mipyme. |
| `retper_iibb_agip.consultar`, `retper_iibb_misiones.consultar` | No ejecutados: sin combinación exitosa atribuible en las tres bases. |

Ninguno de los 21 fallos corresponde a un defecto de contrato, cuota o carga de artefactos: son credenciales vencidas, representados no autorizados, servicios no asignados a la cuenta, errores del organismo, o datos de entrada ausentes (moa) y equivalencia pendiente (SCT).

## Trazabilidad requisito a verificación

| requisito | verificación | observado |
|---|---|---|
| Probar cada endpoint de descarga con credenciales que funcionaron antes | 25 de 28 operaciones enviadas a la API real; caso por operación con el último éxito histórico | 26 casos enviados, 26 terminales; 5 con archivos verificados; 21 con causa clasificada |
| Excluir flujos de carga | alcance documentado y lista explícita en el runner | ningún caso de carga, PEM, VEP o solicitud se ejecutó |
| Credenciales y datos de clientes fuera de Git | archivos privados 0600; informe y commits sin secretos; `git status` limpio | verificado en las tres bases y en los resultados del servidor |
| Descarga real de artefactos, no solo estado COMPLETO | `GET .../download` con redirect firmado, descarga y validación de bytes, SHA-256 y firma | 7 artefactos descargados y válidos |
| Persistencia durable del resultado | consulta a `job_artifacts` en PostgreSQL | filas con `object_key` con prefijo `jobs/<job>/<intento>/`, `content_type`, `size_bytes` y `sha256` de 64 caracteres |
| Despacho real hacia el worker de api2 | job forzado a `worker2.mrbot.com.ar` y consulta de `jobs.worker_id` | PDF de CCMA de 105378 bytes verificado y asociación durable al nodo |
| API pública: catálogo, job inexistente y alta idempotente | `GET /api/v3/bots`, `GET /api/v3/jobs/<uuid inexistente>`, doble `POST` con la misma `Idempotency-Key` | 200 con catálogo, 404 con error saneado, 202/202 con el mismo `job_id` y un único job |
| Normalización sin secretos ni efectos secundarios | `normalize_v2_payload` dos veces sobre cuatro bots y separación de credenciales | idempotente, sin `clave` ni `proxy_request` en el payload que se persiste |
| Gate de artefactos | pruebas unitarias: artefacto declarado, familia sin extensión, artefacto ajeno, exceso de `max_bytes`, slot del sobre | 5 pruebas en verde; el artefacto ajeno sigue rechazado |
| Cuota agotada | 14 rechazos por cuota durante la corrida con 5 en paralelo sobre plan gratuito (20 unidades) | respuesta 429 saneada; tras reiniciar la central los 14 casos se ejecutaron y ninguno volvió a rechazarse |
| Empaquetado | build de la imagen, carga en api2 y verificación dentro del contenedor | `ArtifactStore` con el parámetro nuevo presente en la imagen de ambos hosts |
| Regresión automatizada | suite completa en árbol limpio | 396 pruebas, 9 omitidas, 12 subpruebas |

## Infraestructura, límites y hallazgos

- MinIO: el proceso central debía poder leer secretos Docker y firmar URLs reales. Se corrigieron permisos y la dirección efectiva del almacenamiento en el `.env` del servidor de testing. Las URLs firmadas viajan sobre HTTP al puerto 9000: antes de producción conviene HTTPS o una red privada entre hosts.
- Flota: `worker2.mrbot.com.ar:443` y `worker-2.mrbot.com.ar:443` quedaron con la imagen que incluye el arreglo, sanos y con 5 plazas. Se aislaron temporalmente durante las pruebas y se restablecieron después.
- Cuota y saldo: el consumo del período gratuito vive en memoria del proceso, por lo que un reinicio de la central repone las 20 unidades. La acreditación administrativa (`POST /admin/billing/accounts/{id}/credit`) responde 404 para usuarios que existen solo en PostgreSQL, porque busca en el registro del panel; los usuarios creados desde V3 no se pueden acreditar hasta que el panel los hidrate. La corrección de `saldo_creditos` nulo cubre el caso de usuarios hidratados.
- Pendientes funcionales: equivalencia de flags de SCT, datos de entrada de moa, errores internos de navegación en declaracion_en_linea y mis_facilidades, y nuevas combinaciones autorizadas de credencial/CUIT para los representados no seleccionables.

## Segunda pasada: correcciones y credenciales alternativas

Sobre las 19 operaciones que fallaban se aplicaron tres correcciones de código y se
reintentó con credenciales de las tres bases exploradas (hasta cinco combinaciones
credencial/CUIT por operación, sin repetir par representante/representado).

Correcciones de esta pasada:

- SCT: la central traduce las nueve banderas V1 (`vencimientos_*`, `deudas_*`,
  `ddjj_pendientes_*`) a `secciones` y `formatos`, que es lo que valida el worker.
  Antes el job moría con `ENVELOPE_INVALID`.
- Clasificación de errores: cuando la causa real es `AttributeError`, `TypeError`,
  `NameError` o `NotImplementedError` de código propio, el job se reporta como
  `INTERNAL` con `plugin_service_api_mismatch` en lugar de culpar al organismo con
  `TARGET_UNAVAILABLE`. El diagnóstico queda además en el log del worker.
- Comprobantes: la lectura de las planillas tolera codificaciones locales (cp1252) y
  filas con campos de más, y el formato inesperado se reporta como
  `portal_csv_unexpected_format`.

Resultado de los 61 intentos sobre 19 operaciones:

| operación | intentos | causa final |
|---|---|---|
| `consulta_pagos_vep.consultar` | 5 | 2 credenciales rechazadas y 3 representados no seleccionables |
| `liquidacion_granos.consultar` | 5 | las 5 credenciales de mrbot1 rechazadas |
| `libros_portal_iva.descargar_libros`, `libros_portal_iva.descargar_ddjj` | 5 y 5 | representado no seleccionable en las 5 |
| `mis_retenciones.consultar`, `mis_retenciones_iva_simple.consultar` | 5 y 5 | representado no seleccionable en las 5 |
| `portal_iva.descargar`, `rcel.descargar` | 5 y 5 | representado no seleccionable en las 5 |
| `sifere.consultar` | 5 | 4 servicios no visibles y 1 credencial rechazada |
| `hacienda.consultar` | 3 | servicio no visible en las 3 |
| `pago_devoluciones.consultar` | 2 | representado no seleccionable y credencial rechazada |
| `sct.consultar` | 4 | 3 representados no seleccionables y 1 API de servicio ausente |
| `siper.consultar` | 1 | servicio no visible |
| `comprobantes.consultar`, `comprobantes.historial` | 1 y 1 | formato de planilla inesperado del portal |
| `declaracion_en_linea.consultar`, `mis_facilidades.consultar`, `moa.consultar` | 1 cada una | API de servicio ausente en el runtime |
| `srt.consultar_alicuotas` | 1 | API de servicio ausente en el runtime |

Lectura de estos resultados:

- Probar otra credencial sirvió para separar dos causas que antes se confundían:
  `liquidacion_granos` y parte de `sifere`/`vep`/`pago_devoluciones` tienen
  credenciales realmente rechazadas por el organismo, mientras que
  `libros_portal_iva`, `mis_retenciones` (ambas), `portal_iva`, `rcel` y `sct`
  fallan igual con las cinco combinaciones, lo que descarta la credencial como causa.
- En esos casos el bloqueo real es de implementación: `ArcaServicePage` solo
  implementa los flujos de Mis Comprobantes (`seleccionar_representado`,
  `descargar_csv`, `solicitar_consulta`). Los plugins de estos bots invocan métodos
  de portal que no existen en el runtime (`descargar_reporte`, `descargar_periodo`,
  `listar_planes`, `consultar_cuit`, `consultar_periodo`, `navegar_arbol_empresa`),
  por eso la selección de representado falla o el job muere con una API ausente.
  Completar esos bots requiere implementar la automatización de cada portal.
- `moa` quedó sin bloqueo de datos: se recuperaron despachos reales de las bases y el
  caso ya se envía con ellos; lo que falta es el método de scraping en el runtime.

## Reintentos locales del 28/09/2026

La batería posterior se ejecutó contra la central local y el worker con credenciales
históricas en archivos privados `0600`. El usuario de pruebas tuvo cuota suficiente.
Los resultados anteriores de este documento describen corridas anteriores y no
constituyen el estado actual de cada bot.

| Operación | Comprobación nueva |
|---|---|
| `compensaciones.consultar` | Se cambió `subir` de `false` a `true` en los casos privados. Tras un primer intento fallido por selección del representado y timeout del portal, el reintento integrado terminó `COMPLETO` y permitió descargar XLS de 16.896 bytes, con firma y tamaño válidos. El esquema del plugin ya tenía `subir: true` por defecto. |
| `mis_retenciones_iva_simple.consultar` | Se eligió una alternativa histórica distinta de la principal, que devolvía `OK` sin archivo. La API local completó y permitió descargar CSV de 5.978 bytes para 01/08/2026 a 28/09/2026, validado por tamaño y formato. En el runner aislado se comprobaron además períodos de dos meses entre enero y agosto de 2026, todos con al menos un artefacto. El portal limita cada consulta al último día del mes siguiente al inicio, por lo que un rango anual se divide en tramos en vez de enviarse como una sola consulta. |
| `certificado_mipyme.descargar` | La credencial con éxito histórico aún funciona: el runner aislado obtuvo un PDF y la API local completó en 20 s al habilitar `subir: true` en el caso. El artefacto descargado mide 357.078 bytes y pasa verificación de firma, tamaño y SHA-256. El archivo privado de prueba tenía originalmente la ruta sin prefijo `/api/v3` y `subir: false`, por lo que las primeras altas de esta pasada no probaron la carga del archivo. |
| `sifere.consultar` | La credencial histórica permitió entrar a COMARB. Se corrigió el envío del formulario del representado, se abrió `retencionIn` antes de consultar las jurisdicciones y se declararon slots dinámicos. COMARB entrega XLSX, no CSV: ahora se conserva extensión y MIME correctos. Tras recargar el worker, la API local terminó `COMPLETO` con 24 XLSX descargados, todos con firma, tamaño y SHA-256 válidos (129.578 bytes en total). |
| `liquidacion_granos.consultar` | Se probaron otras tres credenciales históricas el 28/09 además de las ocho ensayadas el día anterior. Las tres nuevas fueron rechazadas por ARCA antes de llegar al servicio. No hay evidencia para atribuir este caso a un defecto del portal; requiere una credencial vigente y autorizada. |
| `retper_iibb_agip.consultar`, `retper_iibb_misiones.consultar` | Un reintento aislado por sistema el 28/09 devolvió `CREDENTIALS_REJECTED` antes de obtener archivos. Las cinco pruebas AGIP y seis Misiones del día anterior tampoco tuvieron éxito histórico. Son accesos independientes de Clave Fiscal ARCA; no se sustituyeron sus claves con credenciales ARCA. Las alternativas sin éxitos previos no se agotaron indiscriminadamente para evitar bloqueos de cuenta. |

Las combinaciones de credenciales y el contenido de los archivos descargados siguen
fuera de Git. No se infiere que un `COMPLETO` sin archivos sea una descarga exitosa.
La suite completa tras estas correcciones pasó con 507 pruebas y 12 subpruebas;
9 pruebas quedaron omitidas.
