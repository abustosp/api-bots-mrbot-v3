# Compatibilidad de solicitudes V2

Los alias de solicitudes mantienen el cuerpo plano que usaban los clientes V2. En la documentación de cada ruta se muestran los nombres y tipos aceptados por esa operación. Envíe los campos que correspondan a la ruta, no un sobre con `payload` y `credentials`.

## Credenciales

Se aceptan los nombres históricos `clave`, `clave_representante` y `contrasena` para la clave fiscal, además de `clave_encriptada` cuando el cliente ya la cifró con RSA-OAEP y la codificó en Base64. El CUIT de acceso puede llamarse `cuit_representante`, `cuit_login` o `cuit_inicio_sesion`, según la operación. El CUIT consultado puede recibirse como `cuit_representado`, `representado_cuit` o `cuit`.

Si se envían varios nombres de clave en una misma solicitud, deben contener el mismo valor. No incluya claves en registros, capturas ni mensajes de soporte.

## CUIT/CUIL

Los campos de CUIT/CUIL admiten separadores habituales, como guiones. Se quitan los caracteres que no son dígitos y se comprueban los 11 dígitos y el dígito verificador. El valor normalizado se utiliza en la solicitud aceptada. Por ejemplo, `20-12345678-6` se normaliza a `20123456786`.

Una solicitud de libros `/libros_iva/consulta` ejecuta conjuntamente la descarga de libros y de declaraciones juradas, como hacía la consulta V2. `/libros_iva/ddjj` conserva la descarga individual de declaraciones juradas.

## Fechas y períodos

- Los rangos `periodo_desde` y `periodo_hasta` de libros usan `AAAAMM`, deben tener meses entre `01` y `12`, no pueden superar el mes actual y el inicio no puede ser posterior al fin.
- En `mis_retenciones_iva_simple/consulta`, `desde` y `hasta` usan `DD/MM/AAAA`; el fin no puede preceder al inicio ni superar el último día del mes siguiente al inicio.
- En las consultas de retenciones IIBB de AGIP y Misiones, `desde` y `hasta` usan `AAAAMM`, con mes válido y rango ascendente.
- En la consulta ARBA, `periodo` usa `AAAAMM`, con mes válido.
- Los demás tipos, campos requeridos, valores predeterminados y opciones de cada alias se detallan en la referencia de esa ruta.

En Portal IVA, si no se informa el CUIT representado se usa el CUIT del representante. `descartar_borrador` conserva su valor predeterminado histórico y solo se aplica después de que hayan finalizado correctamente las descargas solicitadas.

## Errores de entrada

### HTTP 400: CUIT/CUIL no válido

La respuesta indica los campos con CUIT/CUIL incorrecto en `detail.invalid_fields`. Ocurre si, luego de quitar separadores, el valor no tiene 11 dígitos o no supera la comprobación del dígito verificador.

**Cómo resolverlo:** revise que el CUIT esté completo y escrito correctamente. Puede enviarlo con o sin guiones; no modifique el dígito verificador para forzar la aceptación. Vuelva a enviar la solicitud con el valor correcto.

### HTTP 422: solicitud no válida

Se devuelve cuando falta un campo obligatorio, un valor no tiene el tipo esperado, las credenciales enviadas con distintos nombres no coinciden, o una fecha/período no respeta el formato o rango de la operación. El mensaje no reproduce los valores enviados para evitar revelar información sensible.

**Cómo resolverlo:** consulte la lista de campos de la ruta, agregue los obligatorios, corrija tipos (por ejemplo, listas y booleanos), use los formatos indicados arriba y ordene los rangos desde/hasta. Si envía más de un campo de clave, use el mismo valor en todos.

### Errores de archivos

Para operaciones con archivos, HTTP 413 significa que se superó el límite de tamaño; reduzca o divida el contenido. HTTP 415 indica una extensión no permitida; use el formato admitido para ese campo. HTTP 503 indica una interrupción temporal al guardar archivos; reintente la solicitud. Los archivos también pueden enviarse como partes binarias, como Base64 cuando la operación lo permite, o mediante las referencias de carga aceptadas por la ruta.
