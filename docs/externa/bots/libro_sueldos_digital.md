# Libro de Sueldos Digital

Descarga los PDF del Libro de Sueldos Digital correspondientes a las liquidaciones encontradas en un rango mensual. Es una consulta/descarga de información existente, no presenta liquidaciones ni modifica el portal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/libro_sueldos_digital/descargar`. El alias plano `/libro-sueldos-digital/consulta` aplica su propio DTO. En la operación canónica los períodos son strings `AAAAMM`, mes `01`–`12`; rango inclusivo y ascendente. La CUIT representada es opcional y acepta separadores para normalizar.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `periodo_desde` | texto | Sí | ninguno | Seis dígitos en formato `AAAAMM`, mes `01`–`12`. Nulo o vacío: inválido. Un período inicial posterior al final se rechaza. Se genera mes a mes, con tope de 1200 períodos. | `"202601"` |
| `periodo_hasta` | texto | Sí | ninguno | Seis dígitos en formato `AAAAMM`, mes `01`–`12`. Nulo o vacío: inválido. El rango es inclusivo y ascendente; si es anterior a `periodo_desde` se rechaza. Se genera mes a mes, con tope de 1200 períodos. | `"202603"` |
| `representado_cuit` | texto | No | `null` | CUIT del empleador. Alias: `cuit`, `cuit_representado`. Si se omite, es `null` o vacío, se usa el CUIT de sesión. Si se informa, se quitan los separadores y se valida con el empleador seleccionado. | `"20123456786"` |
| `incluir_json` | booleano | No | `true` | `false` omite el detalle por liquidación, pero deja contadores y listas de períodos. `null` explícito se rechaza. | `true` |
| `subir_archivos` | booleano | No | `true` | `false` no publica PDF en `files[]`; el portal todavía se consulta y los archivos se descargan durante el trabajo. `null` explícito se rechaza. | `true` |
| `cuit_representante` | texto | No | ninguno | CUIT de sesión o contexto heredado, normalmente en `credentials`. No se repite en `payload` si la sesión va en `credentials`. `null` o vacío se omite. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | No | ninguno | Alias de `cuit_representante` para el CUIT de sesión o contexto heredado, normalmente en `credentials`. No se repite en `payload` si la sesión va en `credentials`. `null` o vacío se omite. | `"20123456786"` |
| `cuit_login` | texto | No | ninguno | Alias de `cuit_representante` para el CUIT de sesión o contexto heredado, normalmente en `credentials`. No se repite en `payload` si la sesión va en `credentials`. `null` o vacío se omite. | `"20123456786"` |
| `proxy_request` | booleano | No | `null` | Campo nullable opcional legado. No cambia la selección ni el resultado; `null` es válido. | `false` |
| `headless` | booleano | No | `null` | Campo nullable opcional legado. No cambia la selección ni el resultado; `null` es válido. | `false` |

El esquema de ejecución tolera campos adicionales; se recomienda enviar solo los documentados. La operación la fija la ruta. El esquema del catálogo es descriptivo y el alias HTTP plano tiene su propio modelo de entrada.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la descarga requiere una sesión fiscal. La identidad y la contraseña no tienen default; si faltan, normalmente falla la autenticación. El servicio comprueba el empleador efectivo y detiene la descarga si no coincide con el CUIT solicitado.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | Identidad de sesión. Se necesita una identidad para iniciar sesión, salvo que se use el alias `cuit_inicio_sesion`. Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | No | ninguno | Alias de `cuit_representante`. Aporta la identidad de sesión. Si se omite, es `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Contraseña en texto. Se necesita una contraseña, salvo que se aporte `clave_encriptada`; `clave_representante` y `contrasena` son alias. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_representante` | texto | No | ninguno | Alias de `clave`. Aporta la contraseña en texto. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `contrasena` | texto | No | ninguno | Alias de `clave`. Aporta la contraseña en texto. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_encriptada` | texto | No | ninguno | Alternativa cifrada. Omitida, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"Q0xBVkUtRklDVElDSUE="` |

No usar credenciales reales en el ejemplo.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/libro_sueldos_digital/descargar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo_desde": "202601",
    "periodo_hasta": "202603",
    "incluir_json": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado y archivos por operación

`result` puede ser `OK`, `PARCIAL` o `ERROR`: una mezcla de descargas correctas y fallidas da `PARCIAL`; si fallan todas las liquidaciones encontradas, da `ERROR`. El detalle de cada descarga se conserva en `data` cuando se devuelve un resultado con datos. Si no se encuentra ninguna liquidación, la ejecución falla y no se garantiza ese detalle.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al devolver un resultado | Estado de las descargas. | `"PARCIAL"` |
| `data.operacion` | texto | Cuando se devuelve `data` | Operación realizada. | `"descargar"` |
| `data.representado_cuit`, `data.periodo_desde`, `data.periodo_hasta` | textos | Cuando se devuelve `data` | CUIT del empleador verificado y rango solicitado. | `{"representado_cuit":"20123456786","periodo_desde":"202601","periodo_hasta":"202603"}` |
| `data.empleador` | objeto | Cuando se devuelve `data` | Identidad informada para el empleador, con `cuit` y `denominacion`. | `{"cuit":"20123456786","denominacion":"Empleador Ejemplo SA"}` |
| `data.periodos_solicitados`, `data.periodos_encontrados`, `data.periodos_descargados` | listas de textos | Cuando se devuelve `data` | Períodos del rango, períodos con liquidaciones listadas y períodos con al menos una descarga correcta, respectivamente. Pueden ser `[]` si no hay elementos en esa etapa. | `{"periodos_solicitados":["202601","202602"],"periodos_encontrados":["202601"],"periodos_descargados":["202601"]}` |
| `data.liquidaciones_encontradas`, `data.liquidaciones_descargadas` | enteros | Cuando se devuelve `data` | Cantidad listada y cantidad descargada correctamente. La segunda no depende de que se adjunten los archivos. | `{"liquidaciones_encontradas":2,"liquidaciones_descargadas":1}` |
| `data.detalle[]` | lista de objetos | Solo con `incluir_json: true`; con `false` se omite la clave | Cada fila incluye `periodo`, `liquidacion` y `archivo`. Si falla una descarga, incluye `error` con el código del fallo. En una descarga correcta, `sha256`, `size_bytes` y `object_key` aparecen únicamente cuando `subir_archivos: true`. | `[{"periodo":"202601","liquidacion":"0007","archivo":"libro-202601-0007.pdf","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":1234,"object_key":"jobs/demo/libro/ejemplo"},{"periodo":"202602","liquidacion":"0008","archivo":"libro-202602-0008.pdf","error":"lsd_download_error"}]` |
| `files[]` | lista de archivos | PDF descargados y publicados; puede estar vacía | Con `subir_archivos: true`, contiene solo las liquidaciones descargadas correctamente. Con `false`, queda vacía aunque se consulten y descarguen archivos durante el trabajo. En un resultado `PARCIAL`, conserva los archivos exitosos. | `[{"name":"libro-202601-0007.pdf","url":"","size":1234,"artifact_id":"lsd_pdf","filename":"libro-202601-0007.pdf","content_type":"application/pdf","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":null,"url_expires_in":null}]` |

`incluir_json: false` no cambia los contadores ni las listas de períodos, pero `data.detalle` no aparece, no se devuelve como `null` ni como lista vacía. Si todas las descargas fallan, `data.detalle` puede describir esos fallos cuando está habilitado. No se promete un objeto superior de advertencias o errores.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
