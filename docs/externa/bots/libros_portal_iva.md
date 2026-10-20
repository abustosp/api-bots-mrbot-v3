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
| `subir_archivos` | booleano; opcional; `true` | Solicita que los archivos generados queden disponibles como archivos adjuntos al trabajo. `false` evita adjuntarlos; no cancela la consulta ni la descarga temporal necesaria para producir el resumen. Omitido equivale a `true`; `null` se rechaza. |

Se requiere que `incluir_json` o `subir_archivos` sea `true`; si ambos son `false`, la solicitud se rechaza. El rango se genera en orden inclusive y la ejecución procesa como máximo 1.200 períodos; rangos mayores se truncan sin error de entrada. Los campos no listados pueden ser descartados o rechazados; no deben usarse como controles.

## Credenciales

Se requieren credenciales fiscales funcionales aunque el sobre permita omitir el objeto. Deben corresponder a una cuenta con acceso a ARCA y permiso sobre el representado.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

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