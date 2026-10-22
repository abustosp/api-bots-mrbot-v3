# Mis Facilidades

## Propósito y operación

Consulta planes de facilidades de pago, sus cuotas y obligaciones, exportando la información disponible. No adhiere, modifica, cancela ni paga planes.

Ruta V3: `POST /api/v3/bots/mis_facilidades/consultar`. Alias compatible: `POST /api/v3/mis_facilidades/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación forma parte de la ruta. El trabajo se acepta de forma asíncrona (`202`) y se sigue con `GET /api/v3/jobs/{job_id}`.

## Payload

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `payload` | `representado_cuit` | texto | No | `null` | Selecciona el representado. Alias: `cuit`; elimina espacios, guiones, puntos y `/` y exige 11 dígitos. Si se omite o es `null`, se usa el CUIT de `credentials.cuit_representante`. La cadena vacía no equivale a omisión: falla el formato. | `"30876543210"` |
| `payload` | `denominacion` | texto | No | `null` | Nombre alternativo usado al identificar y nombrar el representado; longitud de 1 a 128 caracteres. Omitido o `null`: sin sobreescritura. Cadena vacía: rechazo. | `"Comercio Ejemplo SA"` |
| `payload` | `situacion_excluyente` | lista de textos | No | `[]` | Excluye planes cuya situación contenga alguno de los textos, sin distinguir mayúsculas de minúsculas; máximo 20 elementos. Alias: `lista_exclusion_situacion`. Omitido o `[]`: no excluir por situación. `null`, elemento vacío o más de 20 elementos: rechazo. | `["Plan Cancelado"]` |
| `payload` | `fecha_consolidacion_desde` | texto `DD/MM/AAAA` | No | `null` | Procesa solo los planes cuya fecha de consolidación (la que muestra el detalle del plan) es igual o posterior a esta fecha. Se combina con los demás filtros. Omitido o `null`: sin límite inferior. Formato inválido: rechazo. | `"01/01/2026"` |
| `payload` | `fecha_consolidacion_hasta` | texto `DD/MM/AAAA` | No | `null` | Procesa solo los planes cuya fecha de consolidación es igual o anterior a esta fecha (inclusive). Omitido o `null`: sin límite superior. Si ambas están presentes, `desde` no puede ser posterior a `hasta`. | `"30/06/2026"` |
| `payload` | `incluir_pdf` | booleano | No | `true` | Produce PDF para las vistas de plan disponibles. `false` omite los PDF. `null` se rechaza. | `true` |
| `payload` | `incluir_xlsx` | booleano | No | `true` | Produce libro de cálculo con las tablas disponibles. `false` omite el libro. `null` se rechaza. | `true` |
| `payload` | `subir_archivos` | booleano | No | `true` | Adjunta los archivos producidos. `false` evita los adjuntos, pero no desactiva la consulta ni la generación de las salidas solicitadas. `null` se rechaza. | `true` |

Debe estar activo `incluir_pdf` o `incluir_xlsx`; ambas opciones en `false` se rechazan. Los campos no reconocidos se rechazan.

## Credenciales

Se necesitan credenciales fiscales funcionales y permiso de consulta sobre el representado. La cuenta de acceso debe poder abrir Mis Facilidades.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `envelope` | `credentials` | objeto | No | `{}` | Objeto de credenciales del sobre. Omitirlo usa `{}`. Un objeto vacío pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Identifica la cuenta que inicia sesión; requerido junto con una clave, salvo que se use un alias. Omitido, `null` o vacío no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o vacío equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Clave fiscal en texto; requerida junto con una identidad, salvo alias o alternativa cifrada. Omitida, `null` o vacío no aporta clave; `false` se rechaza por tipo. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o vacío equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o vacío equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64; se descifra mediante el mecanismo admitido por la API. Omitida, `null` o vacío equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkVfRklDVElDSUFfTk9fVVNFRw=="` |

Los valores de alias duplicados deben coincidir. Envíe estos datos dentro de `credentials`, no en `payload`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "denominacion": "Comercio Ejemplo SA",
    "situacion_excluyente": ["Plan Cancelado"],
    "fecha_consolidacion_desde": "01/01/2026",
    "fecha_consolidacion_hasta": "30/06/2026",
    "incluir_pdf": true,
    "incluir_xlsx": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos por operación

`result` es `OK` si no hubo errores registrados y `PARCIAL` si una o más exportaciones de planes o secciones fallaron, o si el límite de tiempo impidió continuar con el siguiente plan. El trabajo conserva los resúmenes y archivos que sí alcanzó a producir. El detalle de esos errores no está incluido en `data`, por lo que el estado `PARCIAL` no garantiza una lista pública de causas ni del plan afectado.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al devolver un resultado | Estado global de las exportaciones completadas. | `"PARCIAL"` |
| `data.operacion`, `data.representado_cuit` | texto | Cuando se devuelve `data` | Operación y CUIT seleccionado para la consulta. | `{"operacion":"consultar","representado_cuit":"30876543210"}` |
| `data.cuits_disponibles` | lista de textos | Cuando se devuelve `data` | CUIT visibles para la cuenta. Puede ser `[]` si no se pudieron leer del servicio. | `["30876543210"]` |
| `data.planes` | lista de objetos | Cuando se devuelve `data` | Planes procesados, salvo los cancelados, los que coinciden con `situacion_excluyente` y los que quedan fuera del rango de consolidación. Con rango, cada plan incluye `fecha_consolidacion`. Puede ser `[]` si no queda ningún plan para procesar. | `[{"numero":"00042","denominacion":"Comercio Ejemplo SA","situacion":"Vigente"}]` |
| `data.planes_procesados` | entero | Cuando se devuelve `data` | Cantidad de elementos de `data.planes`. | `1` |
| `data.planes[].pdf_vistas[]` | lista de objetos | En cada plan solo con `incluir_pdf: true` | Una entrada por vista PDF producida, con `vista`, `archivo` y `size_bytes`. Con `subir_archivos: true` se agregan `sha256` y el tamaño publicado; sin subida se omite `sha256`. Puede ser `[]` si no hubo vistas disponibles. | `[{"vista":"pagos","archivo":"plan-00042-pagos.pdf","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}]` |
| `data.planes[].xlsx` | objeto | En cada plan solo con `incluir_xlsx: true` | Resumen de la planilla del plan: `archivo` y `size_bytes`; `sha256` solo cuando `subir_archivos: true`. Con `incluir_xlsx: false`, la clave se omite. | `{"archivo":"plan-00042-tablas.xlsx","size_bytes":2345,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}` |
| `files[]` | lista de archivos | Solo archivos publicados correctamente; puede quedar vacía | Contiene PDF por vista y planillas XLSX únicamente si se solicitaron y `subir_archivos: true`. En `PARCIAL` conserva los adjuntos exitosos. Con `subir_archivos: false`, los resúmenes pueden conservar nombres y tamaños, pero esta lista queda vacía. | `[{"name":"plan-00042-pagos.pdf","url":"","size":1234,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"plan-00042-pagos.pdf","content_type":"application/pdf","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |
