# Liquidación de Granos

## Propósito y operación

Consulta las liquidaciones de granos asociadas a un representado en el portal de ARCA. Recorre las categorías seleccionadas y descarga sus planillas y comprobantes disponibles. No crea, presenta ni modifica liquidaciones.

Ruta V3: `POST /api/v3/bots/liquidacion_granos/consultar`. Ruta compatible: `POST /api/v3/liquidacion_granos/consulta`, equivalente a `consultar`. Use el sobre `{"payload": {...}, "credentials": {...}}`; la operación va en la URL. La solicitud queda aceptada asíncronamente (`202`) y se completa como trabajo consultable mediante `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Identifica el representado. Alias: `cuit`, `cuit_representado`. Se quitan espacios, guiones, puntos y `/`; deben quedar 11 dígitos. Omitido, `null`, vacío o CUIT inválido: rechazo. | `"30876543210"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre con el que se elige el representado en el portal. Alias: `denominacion`. Longitud de 1 a 128 caracteres. Omitido, `null` o vacío: rechazo. Debe corresponder a una relación visible para la cuenta. | `"Productora Ejemplo SA"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha `dd/mm/aaaa` de inicio del rango. Alias: `desde`. Debe ser una fecha real. Omitida, `null`, vacía o inválida: rechazo. | `"01/01/2025"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha `dd/mm/aaaa`, fin inclusive del rango. Alias: `hasta`. Debe ser igual o posterior a `fecha_desde`. Omitida, `null`, vacía o inválida: rechazo. | `"31/01/2025"` |
| `lpg_emitidas` | booleano | No | `true` | Incluye LPG emitidas. `false` excluye esa categoría. `null` se rechaza. | `true` |
| `lpg_recibidas` | booleano | No | `true` | Incluye LPG recibidas. `false` excluye esa categoría. `null` se rechaza. | `false` |
| `lsg_emitidas` | booleano | No | `true` | Incluye LSG emitidas. `false` excluye esa categoría. `null` se rechaza. | `false` |
| `lsg_recibidas` | booleano | No | `true` | Incluye LSG recibidas. `false` excluye esa categoría. `null` se rechaza. | `false` |
| `certificados_deposito` | booleano | No | `true` | Incluye certificados de depósito. `false` excluye esa categoría. `null` se rechaza. | `false` |
| `subir_archivos` | booleano | No | `true` | Si es `true`, adjunta las planillas y comprobantes producidos. `false` evita adjuntarlos al resultado, pero no evita consultar ni generar los archivos durante el trabajo. Omitido o `null` equivale al default `true`; un booleano `false` desactiva la subida. | `true` |

Los campos booleanos de sección en `null` se rechazan. La opción de subida es excepción: `subir_archivos: null` se trata como omitido y recupera el default `true`. Debe quedar al menos una de las cinco categorías en `true`; si todas son `false`, se rechaza. Los campos desconocidos se rechazan.

## Credenciales

Se requieren credenciales fiscales funcionales y autorización de ARCA sobre el CUIT consultado.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `sobre` | `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Identifica la cuenta que inicia sesión. Se requiere junto con una clave, salvo que se use el alias `cuit_inicio_sesion`; si se envía con el otro nombre, los valores deben coincidir. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`. Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Clave fiscal en texto. Se requiere junto con una identidad, salvo que se use un alias o `clave_encriptada`. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`. Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`. Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64. Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkUtRklDVElDSUE="` |

Si se envían alias repetidos con valores distintos, la solicitud se rechaza. Mantenga las credenciales fuera de `payload`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Productora Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "lpg_emitidas": true,
    "lpg_recibidas": false,
    "lsg_emitidas": false,
    "lsg_recibidas": false,
    "certificados_deposito": false,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

El CUIT y la denominación del ejemplo son ficticios. Para obtener resultados, el representado y el período deben existir en la cuenta real.

## Resultado y archivos por operación

`result` es `OK` cuando termina una consulta normal. Si falla el acceso, la consulta o la generación de una sección, el trabajo termina con error; no se emite un resultado `PARCIAL` por sección.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al devolver un resultado | En una consulta completada vale `OK`. Una falla que aborta no garantiza datos de salida. | `"OK"` |
| `data.operacion` | texto | Cuando se devuelve `data` | Operación realizada. | `"consultar"` |
| `data.representado_cuit`, `data.fecha_desde`, `data.fecha_hasta` | textos | Cuando se devuelve `data` | CUIT consultado y fechas normalizadas del rango. | `{"representado_cuit":"30876543210","fecha_desde":"01/01/2025","fecha_hasta":"31/01/2025"}` |
| `data.secciones` | objeto | Cuando se devuelve `data`; contiene solo las categorías activas | Cada clave identifica una categoría, por ejemplo `lpg_emitidas`. El resumen incluye `archivo`, `size_bytes` y `comprobantes`; `sha256` de la planilla solo aparece si `subir_archivos: true`. | `{"lpg_emitidas":{"archivo":"LPG-emitidas.xlsx","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","comprobantes":{"cantidad":1,"archivos":["comprobante-01.pdf"]}}}` |
| `data.secciones.<categoria>.comprobantes` | objeto | Por cada categoría activa | `cantidad` es el número de comprobantes. `archivos` es una lista de nombres, que puede ser `[]`; con `subir_archivos: false` contiene nombres locales y no significa que estén adjuntos. | `{"cantidad":0,"archivos":[]}` |
| `files[]` | lista de archivos | Solo archivos publicados; puede quedar vacía | Con `subir_archivos: true`, puede incluir la planilla XLSX de cada categoría activa y sus comprobantes PDF. Con `false`, no contiene archivos aunque se hayan consultado o generado localmente. | `[{"name":"LPG-emitidas.xlsx","url":"","size":1234,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"LPG-emitidas.xlsx","content_type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

`data.secciones` contiene solo las categorías activas. Una falla de una sección no devuelve como resultado público los resúmenes previos: no se promete una salida parcial con archivos de otras secciones.
