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

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | No | ninguno | Identifica el representado. Acepta 11 dígitos; se quitan espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representado`. Omitido, `null` o `""`: usa el CUIT de `credentials.cuit_representante`. Un valor no vacío mal formado falla la validación. | `"30876543210"` |
| `periodo_desde` | texto | Sí | ninguno | Primer mes, incluido, en formato `AAAAMM`. El mes debe ser `01` a `12`. Omitido, `null`, vacío o formato inválido: rechazo. | `"202501"` |
| `periodo_hasta` | texto | Sí | ninguno | Último mes, incluido, en formato `AAAAMM`. Debe ser igual o posterior a `periodo_desde`. Omitido, `null`, vacío o formato inválido: rechazo. | `"202502"` |
| `denominacion` | texto | No | `""` | Nombre opcional asociado al representado. Vacío es válido y se trata como no provisto. `null` no es válido. No cambia los datos solicitados. | `"Comercio Ejemplo SA"` |
| `incluir_json` | booleano | No | `true` | Incluye en el resultado detalles resumidos por período. `false` los omite. Omitido equivale a `true`; `null` se rechaza. | `true` |
| `subir_archivos` | booleano | No | `true` | Solicita que los archivos generados queden disponibles como archivos adjuntos al trabajo. `false` evita adjuntarlos; no cancela la consulta ni la descarga temporal necesaria para producir el resumen. Omitido equivale a `true`; `null` se rechaza. | `true` |

Se requiere que `incluir_json` o `subir_archivos` sea `true`; si ambos son `false`, la solicitud se rechaza. El rango se genera en orden inclusive y la ejecución procesa como máximo 1.200 períodos; rangos mayores se truncan sin error de entrada. Los campos no listados pueden ser descartados o rechazados; no deben usarse como controles.

## Credenciales

Se requieren credenciales fiscales funcionales aunque el sobre permita omitir el objeto. Deben corresponder a una cuenta con acceso a ARCA y permiso sobre el representado.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `sobre` | `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Requerido junto con una clave, salvo alias. Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Requerida junto con una identidad, salvo alias o alternativa cifrada. Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64 que se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkUtRklDVElDSUEtRkFDVElCSUxFLU5PLVVUSUxJWkFCTEU="` |

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

Las tablas describen la respuesta pública del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. Los errores de descarga se informan por período, pero no cambian por sí solos el `result` global de `OK`.

### `descargar`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | `OK` si la operación terminó, incluso si una o más descargas por período fallaron; los fallos del trabajo se informan como `ERROR`. | `"OK"` |
| `data.operacion` | texto | Siempre que se produzca `data`. | Operación solicitada. | `"descargar"` |
| `data.representado_cuit` | texto | Siempre que se produzca `data`. | CUIT normalizado del representado. | `"20123456786"` |
| `data.periodo_desde` | texto | Siempre que se produzca `data`. | Primer período normalizado del rango. | `"202501"` |
| `data.periodo_hasta` | texto | Siempre que se produzca `data`. | Último período normalizado del rango. | `"202502"` |
| `data.libros` | objeto | Siempre en `descargar`. | Resumen de libros con CUIT, rango, períodos descargados y con error, además de `detalle` si se solicitó JSON. | `{"representado_cuit":"20123456786","periodo_desde":"202501","periodo_hasta":"202502","periodos_descargados":["202501"],"periodos_error":["202502"],"detalle":[{"periodo":"202501","archivo":"archivo-ejemplo.bin","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":1200}]}` |
| `data.ddjj` | objeto | Siempre en `descargar`. | Resumen de DDJJ con la misma estructura que `data.libros`. | `{"representado_cuit":"20123456786","periodo_desde":"202501","periodo_hasta":"202502","periodos_descargados":[],"periodos_error":["202501","202502"]}` |
| `data.libros.periodos_descargados`, `data.ddjj.periodos_descargados` | lista de textos | Siempre dentro de cada resumen. Puede ser `[]`. | Períodos cuya descarga se completó para ese tipo. | `["202501"]` |
| `data.libros.periodos_error`, `data.ddjj.periodos_error` | lista de textos | Siempre dentro de cada resumen. Puede ser `[]`. | Períodos cuya descarga falló para ese tipo. | `["202502"]` |
| `data.libros.detalle`, `data.ddjj.detalle` | lista de objetos | Solo con `incluir_json=true`; puede ser `[]`. Si se omite `incluir_json`, usa su valor predeterminado `true`; no se sustituye por `null`. | Un elemento por período procesado. Los libros se entregan como archivo comprimido y las DDJJ como PDF, aunque el nombre del archivo interno termina en `.bin`. | `[{"periodo":"202501","archivo":"archivo-ejemplo.bin","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":1200}]` |
| `data.libros.detalle[].periodo`, `data.ddjj.detalle[].periodo` | texto | En cada elemento de `detalle`. | Período descargado o fallido. | `"202501"` |
| `data.libros.detalle[].archivo`, `data.ddjj.detalle[].archivo` | texto | En cada elemento de `detalle` de una descarga completada; se omite en el registro de error. | Nombre del archivo descargado. | `"archivo-ejemplo.bin"` |
| `data.libros.detalle[].sha256`, `data.ddjj.detalle[].sha256` | texto | En un período descargado solo si `subir_archivos=true` y se publicó el archivo; en caso contrario se omite. | Huella del archivo publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.libros.detalle[].size_bytes`, `data.ddjj.detalle[].size_bytes` | entero | En un período descargado solo si `subir_archivos=true` y se publicó el archivo; en caso contrario se omite. | Tamaño del archivo publicado, en bytes. | `1200` |
| `data.libros.detalle[].error`, `data.ddjj.detalle[].error` | texto | En el elemento de detalle de un período fallido, solo con `incluir_json=true`. | Tipo de error de descarga. No se incluye en el resumen de períodos descargados. | `"TargetUnavailableError"` |
| `data.periodos_descargados` | lista de textos | Siempre en `descargar`; puede ser `[]`. | Concatenación de los períodos descargados en las dos categorías. Un mismo período puede aparecer una vez por categoría. | `["202501","202501"]` |
| `data.periodos_error` | lista de textos | Siempre en `descargar`; puede ser `[]`. | Concatenación de los períodos con error en las dos categorías. Un mismo período puede aparecer una vez por categoría. | `["202502"]` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Vacía si `subir_archivos=false` o no se publicó ningún archivo. | Adjuntos producidos para períodos descargados. No se garantiza un adjunto por cada mes solicitado. | `[{"name":"archivo-ejemplo.bin","url":"","size":1200,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"archivo-ejemplo.bin","content_type":"application/octet-stream","size_bytes":1200,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":null,"url_expires_in":null}]` |

### `descargar_libros` y `descargar_ddjj`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.operacion` | texto | Siempre que se produzca `data`. | `descargar_libros` o `descargar_ddjj`. | `"descargar_libros"` |
| `result` | texto o `null` | Siempre que la operación tenga resultado final. | `OK` al terminar, incluidos los resultados con fallos limitados a algunos períodos; un fallo de operación produce `ERROR`. | `"OK"` |
| `data.representado_cuit` | texto | Siempre que se produzca `data`. | CUIT normalizado del representado. | `"20123456786"` |
| `data.periodo_desde` | texto | Siempre que se produzca `data`. | Primer período normalizado del rango. | `"202501"` |
| `data.periodo_hasta` | texto | Siempre que se produzca `data`. | Último período normalizado del rango. | `"202502"` |
| `data.periodos_descargados` | lista de textos | Siempre; puede ser `[]`. | Períodos descargados para el tipo solicitado. | `["202501"]` |
| `data.periodos_error` | lista de textos | Siempre; puede ser `[]`. | Períodos fallidos para el tipo solicitado. | `[]` |
| `data.detalle` | lista de objetos | Solo con `incluir_json=true`; puede ser `[]`. | Por período descargado incluye `periodo`, `archivo` y, cuando `subir_archivos=true`, `sha256` y `size_bytes`. Para un período fallido incluye `periodo` y `error`. Si `incluir_json=false`, la propiedad se omite. | `[{"periodo":"202501","archivo":"archivo-ejemplo.bin"}]` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Vacía si `subir_archivos=false` o no se publicó ningún archivo. | Archivos comprimidos de libros o PDF de DDJJ, según la operación. | `[]` |

Con `subir_archivos=false`, los archivos pueden procesarse temporalmente, pero no se adjuntan ni se incluyen `sha256` y `size_bytes` en los detalles. Un período fallido permanece en `periodos_error`; el motivo detallado solo aparece en `detalle` cuando esa lista está habilitada.
