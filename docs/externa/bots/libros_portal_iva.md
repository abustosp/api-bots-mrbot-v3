# Libros Portal IVA

## Propósito y operación

Consulta el servicio **Portal IVA** de ARCA para descargar libros IVA y/o declaraciones juradas (DDJJ) de un representado, para un rango mensual inclusivo. No presenta ni modifica declaraciones.

La ruta V3 canónica es `POST /api/v3/bots/libros_portal_iva/{operacion}`. El sobre usa `{"payload": {...}, "credentials": {...}}`; la operación está en la URL, no se envía en `payload`.

| Operación V3 | Efecto |
|---|---|
| `descargar` | Descarga libros y DDJJ para cada período. |
| `descargar_libros` | Descarga libros IVA. |
| `descargar_ddjj` | Descarga DDJJ. |

Rutas compatibles: `POST /api/v3/libros_iva/consulta` corresponde a `descargar`; `POST /api/v3/libros_iva/ddjj` corresponde a `descargar_ddjj`. No hay alias histórico separado para `descargar_libros`.

La solicitud se acepta como trabajo asíncrono (`202`); la respuesta inicial contiene `job_id` y estado. Consulte luego `GET /api/v3/jobs/{job_id}` para el resultado. Una respuesta aceptada no significa que el portal haya completado la descarga.

## Payload

En todos los campos, enviar `null` solo donde se indica. Los campos booleanos no admiten `null`.

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto; opcional; sin default explícito | Identifica el representado. Acepta 11 dígitos; se quitan espacios, guiones, puntos y `/`. También se aceptan los nombres `cuit` y `cuit_representado`. Omitido, `null` o `""`: usa el CUIT de `credentials.cuit_representante`. Un valor no vacío mal formado falla la validación. |
| `periodo_desde` | texto `AAAAMM`; requerido | Primer mes, incluido. El mes debe ser `01` a `12`. Omitido, `null`, vacío o formato inválido: rechazo. |
| `periodo_hasta` | texto `AAAAMM`; requerido | Último mes, incluido. Debe ser igual o posterior a `periodo_desde`. Omitido, `null`, vacío o formato inválido: rechazo. |
| `denominacion` | texto; opcional; `""` | Nombre opcional asociado al representado. Vacío es válido y se trata como no provisto. `null` no es válido. No cambia los datos solicitados. |
| `incluir_json` | booleano; opcional; `true` | Incluye en el resultado detalles resumidos por período. `false` los omite. Omitido equivale a `true`; `null` se rechaza. |
| `subir_archivos` | booleano; opcional; `true` | Solicita que los archivos generados queden disponibles como archivos adjuntos al trabajo. `false` evita adjuntarlos; no cancela la consulta ni la descarga temporal necesaria para producir el resumen. Omitido equivale a `true`; `null` se rechaza. En clientes compatibles también se normalizan `carga_minio`, `minio_upload` y `archivo_historico_minio` a este control. |

Se requiere que `incluir_json` o `subir_archivos` sea `true`; si ambos son `false`, la solicitud se rechaza. El rango se genera en orden inclusive y la ejecución procesa como máximo 1.200 períodos; rangos mayores se truncan sin error de entrada. Los campos no listados pueden ser descartados o rechazados; no deben usarse como controles.

## Credenciales

Se requieren credenciales fiscales funcionales aunque el sobre permita omitir el objeto. Deben corresponder a una cuenta con acceso a ARCA y permiso sobre el representado.

| Campo en `credentials` | Tipo; requerido; default | Omisión, `null` o vacío |
|---|---|---|
| `cuit_representante` | texto; requerido operativamente; sin default | CUIT de la cuenta que inicia sesión. `cuit_inicio_sesion` es alias compatible. Si falta o queda vacío, no hay identidad utilizable y el inicio de sesión no podrá completarse. |
| `clave` | texto; requerida operativamente; sin default | Clave de acceso. `clave_representante` y `contrasena` son nombres compatibles. Si todos faltan o están vacíos, no hay credencial para iniciar sesión. `null` o `""` se consideran no enviados. |
| `clave_encriptada` | texto cifrado; alternativa opcional | Úsela solo con el mecanismo de cifrado publicado por la API. Vacía o `null` equivale a ausencia; cifrado inválido se rechaza. Si se envía junto a una clave en texto, ambos valores deben coincidir. |

Los alias de una misma clave no pueden contener valores distintos. No incluya claves dentro de `payload`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "periodo_desde": "202501",
    "periodo_hasta": "202502",
    "denominacion": "Comercio Ejemplo SA",
    "incluir_json": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos

El resultado contiene el representado y el rango. Para `descargar`, `libros` y `ddjj` agrupan cada salida; también se devuelven `periodos_descargados` y `periodos_error`. Para operaciones de un solo tipo se informan esos contadores en la sección correspondiente. Con `incluir_json: true` se agregan detalles de períodos; errores por período se devuelven en la lista de errores de período, y la respuesta puede terminar con estado exitoso aunque haya períodos fallidos. Revise siempre ambos contadores.

Los libros disponibles se agrupan en un archivo comprimido por período. Las DDJJ se descargan como documentos PDF. Los archivos adjuntos del trabajo aparecen en `files[]` solo si se solicitó la subida y esta se completó. Si no hay documentos disponibles, o falla la descarga del período, el período figura como error y puede no haber archivo. No se garantiza un adjunto por cada mes solicitado.