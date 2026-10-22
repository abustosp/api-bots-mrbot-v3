# MrBot: guía de bots e integración

Revisión: **20 de octubre de 2026**.

MrBot permite consultar información, obtener archivos y realizar operaciones autorizadas en distintos organismos. Cada pedido devuelve un identificador de ejecución (`job_id`): consulte su estado para conocer el resultado y los archivos disponibles. Una aceptación `202` confirma la recepción, no que todos los datos sean válidos ni que la operación haya terminado: ciertos errores se informan en el resultado posterior.

## Por dónde empezar

1. Lea [cómo usar la API](api.md): autenticación, envío del pedido y consulta del resultado.
2. Busque su bot en el catálogo de esta página y revise **todos los campos de su operación**.
3. Lea [archivos y errores](archivos-y-errores.md) si necesita enviar archivos o interpretar un resultado parcial.
4. Use valores reales propios únicamente en su integración. Los ejemplos de este manual contienen datos ficticios y no autorizan trámites.

## Cómo leer las variables

| Término | Significado |
|---|---|
| Requerido | El pedido necesita ese dato. La ficha puede añadir una condición, por ejemplo obligatorio solo para importar. |
| Predeterminado | Valor utilizado al no enviar la propiedad, si la operación define uno. No implica que `null` o `""` produzcan el mismo efecto. |
| Omitir | No incluir la propiedad en el objeto JSON. |
| `null` | Enviar explícitamente ausencia de valor. Puede ser rechazado aunque el campo sea opcional. |
| `""` / espacios | Texto vacío o compuesto por espacios. No equivale a omitir el campo. |
| `false` | Desactivar una opción booleana. No utilizar el texto `"false"` como sustituto. |
| `[]` | Lista sin elementos. Puede significar ningún filtro, ninguna selección o un error, según el campo. |
| Input / entrada | Datos que debe enviar para la operación. |
| Output / salida | Datos devueltos por esa ejecución. Los resultados específicos del bot se consultan dentro de `data`. |
| Archivo de entrada | Archivo previamente enviado y referenciado con el identificador indicado en la guía. Una ruta de su computadora no sube el archivo. |

**Regla práctica:** omita las opciones que no necesita, pero consulte primero sus valores predeterminados. Algunas opciones activan descargas, envíos o cambios reales por defecto. No suponga que un campo desconocido o escrito con un error será rechazado: podría no tener efecto.

Los nombres de propiedades son exactos y distinguen su vocabulario por operación. No mezcle el cuerpo de una ruta de compatibilidad con el de una ruta que utiliza `payload` y `credentials`. Cada ficha indica la forma que corresponde.

**Dos accesos distintos:** la API key identifica su cuenta en MrBot y se envía en la autenticación HTTP. La clave fiscal, cuando el bot la requiere, se envía en `credentials.clave` junto con el CUIT de sesión. No intercambie ambas claves. `representado_cuit` identifica al contribuyente consultado y puede ser distinto del CUIT que inicia sesión.

## Catálogo de bots

La disponibilidad de una operación en su cuenta debe confirmarse con el catálogo de la API. Los bots con nombres similares no tienen necesariamente los mismos campos ni el mismo resultado.

| Bot | Ficha y finalidad |
|---|---|
| `apoc` | [Consultar información de apócrifos](bots/apoc.md) |
| `aportes_en_linea` | [Consultar y descargar aportes](bots/aportes_en_linea.md) |
| `arba` | [Obtener información de ARBA](bots/arba.md) |
| `carga_portal_iva` | [Cargar archivos y opciones de IVA](bots/carga_portal_iva.md) |
| `ccma` | [Consultar cuenta corriente y movimientos](bots/ccma.md) |
| `ccma_compensacion` | [Analizar y realizar compensaciones de cuenta corriente](bots/ccma_compensacion.md) |
| `certificado_mipyme` | [Obtener el certificado MiPyME](bots/certificado_mipyme.md) |
| `compensaciones` | [Consultar compensaciones](bots/compensaciones.md) |
| `comprobantes` | [Consultar, solicitar y obtener historial de comprobantes](bots/comprobantes.md) |
| `consulta_cuit` | [Consultar uno o varios CUIT](bots/consulta_cuit.md) |
| `consulta_pagos_vep` | [Consultar pagos de VEP](bots/consulta_pagos_vep.md) |
| `controladores_fiscales` | [Presentar archivos de controladores fiscales](bots/controladores_fiscales.md) |
| `declaracion_en_linea` | [Consultar declaraciones y archivos por período](bots/declaracion_en_linea.md) |
| `facturometro` | [Consultar información del Facturómetro](bots/facturometro.md) |
| `hacienda` | [Consultar operaciones de hacienda](bots/hacienda.md) |
| `libro_sueldos_digital` | [Descargar documentación del Libro de Sueldos Digital](bots/libro_sueldos_digital.md) |
| `libros_portal_iva` | [Descargar libros y declaraciones de IVA](bots/libros_portal_iva.md) |
| `liquidacion_granos` | [Consultar liquidaciones y certificados de granos](bots/liquidacion_granos.md) |
| `certificacion_primaria_granos` | [Descargar certificados de depósito como depositante](bots/certificacion_primaria_granos.md) |
| `mis_comprobantes` | [Consultar, solicitar y obtener historial de Mis Comprobantes](bots/mis_comprobantes.md) |
| `mis_facilidades` | [Consultar planes y documentación](bots/mis_facilidades.md) |
| `mis_retenciones` | [Consultar retenciones y percepciones](bots/mis_retenciones.md) |
| `mis_retenciones_iva_simple` | [Consultar retenciones de IVA Simple](bots/mis_retenciones_iva_simple.md) |
| `moa` | [Consultar despachos aduaneros](bots/moa.md) |
| `pago_devoluciones` | [Consultar pagos de devoluciones](bots/pago_devoluciones.md) |
| `portal_iva` | [Descargar, importar y gestionar información de IVA](bots/portal_iva.md) |
| `rcel` | [Descargar comprobantes en línea](bots/rcel.md) |
| `retper_iibb_agip` | [Consultar retenciones y percepciones de AGIP](bots/retper_iibb_agip.md) |
| `retper_iibb_misiones` | [Consultar retenciones y percepciones de Misiones](bots/retper_iibb_misiones.md) |
| `sct` | [Consultar el Sistema de Cuentas Tributarias](bots/sct.md) |
| `sifere` | [Consultar información de SIFERE](bots/sifere.md) |
| `siper` | [Consultar el perfil de riesgo](bots/siper.md) |
| `srt` | [Consultar alícuotas de riesgos del trabajo](bots/srt.md) |
| `vep_archivo` | [Generar VEP desde archivos](bots/vep_archivo.md) |
| `vep_ccma` | [Generar VEP desde cuenta corriente](bots/vep_ccma.md) |

## Antes de una operación con efectos

Presentar archivos, importar información, confirmar compensaciones o generar VEP puede modificar datos del organismo. Revise el CUIT, el período, los archivos y las opciones de confirmación en la ficha correspondiente. Generar un VEP no significa que esté pagado.

Si envía ZIP, utilice nombres de archivo únicos en todo el lote, también entre carpetas y entre ZIP distintos. No suponga que dos archivos llamados igual se conservan por estar en carpetas diferentes: uno puede reemplazar al otro al preparar la entrada.

Cancelar una ejecución **no deshace** lo que ya haya sido realizado. Si una ejecución con efectos termina sin confirmación suficiente, consulte el estado en el organismo antes de crear otro pedido. Repetir un pedido con otra clave de idempotencia puede crear otra ejecución.

## Cómo interpretar el resultado

- `status` indica el avance de la ejecución. Revise también `status_detail` y `result`: una ejecución fallida puede aparecer como `COMPLETO` con `result: "ERROR"` en la vista de compatibilidad.
- `COMPLETO` con `PARCIAL` no significa que todas las secciones o descargas hayan terminado correctamente.
- No tener registros no equivale siempre a un error. Revise el resultado y los contadores que describe la ficha.
- Solo los archivos publicados en `files` tienen una referencia de descarga del resultado. Desactivar su publicación puede dejar una respuesta sin archivos descargables aunque se haya consultado información. Además, una lista `files` vacía no prueba por sí sola que la operación no haya generado un archivo.
- Las credenciales no forman parte de los resultados. No las incluya en nombres de archivos, consultas de soporte o mensajes de error que comparta.
