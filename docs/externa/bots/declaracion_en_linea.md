# Declaración en línea

Consulta declaraciones juradas (DDJJ), VEP e información de nómina ya generados para un rango de períodos. Es una lectura: no presenta ni modifica declaraciones ni genera pagos.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/declaracion_en_linea/consultar`. El alias plano `/declaracion-en-linea/consulta` apunta a esta operación, pero aplica su propio modelo. Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. La ruta genérica sigue la normalización y validación efectivas de la operación: limpia separadores comunes de CUIT y normaliza períodos `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA` y `AAAA-MM` a `AAAAMM`.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | No | ninguno | Identifica el CUIT consultado; alias `cuit` y `cuit_representado`. Si se omite, es `null` o vacío, se consulta el CUIT de la sesión. Si se informa, se limpian los separadores comunes y debe quedar en 11 dígitos. | `"20123456786"` |
| `representado_nombre` | texto | No | `""` | Nombre de referencia para los nombres de archivos, de hasta 128 caracteres. Omitido queda vacío; cadena vacía es válida; `null` explícito se rechaza. | `"Empleador Ejemplo"` |
| `periodo_desde` | texto | Sí | ninguno | Primer período del rango inclusivo; se normaliza a `AAAAMM` y el mes debe ser `01` a `12`. Se aceptan `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA` y `AAAA-MM`. `null`, vacío o inválido: rechazo. Si el rango está invertido, se intercambia con `periodo_hasta`. Se generan hasta 1200 períodos y el resto se trunca sin error explícito. | `"202601"` |
| `periodo_hasta` | texto | Sí | ninguno | Último período del rango inclusivo; se normaliza a `AAAAMM` y el mes debe ser `01` a `12`. Se aceptan `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA` y `AAAA-MM`. `null`, vacío o inválido: rechazo. Si el rango está invertido, se intercambia con `periodo_desde`. | `"202603"` |
| `incluir_json` | booleano | No | `true` | `false` omite `detalle`, no el resumen ni los archivos. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `subir_archivos` | booleano | No | `true` | `false` evita publicar PDFs, XLS y JSON en `files[]`, aunque se consultan y descargan. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `proxy_request` | booleano | No | ninguno | Campo legado: se descarta y no permite elegir conexión. `null` permitido; no cambia el resultado. | `true` |
| `proxy` | texto | No | ninguno | Campo legado: se descarta y no permite elegir conexión. `null` permitido; no cambia el resultado. | `"proxy-ficticio"` |

Se requiere al menos una salida: `incluir_json=true` o `subir_archivos=true`. Campos desconocidos se rechazan por la validación de esta operación. El schema del catálogo es descriptivo y no debe confundirse con el DTO del alias plano.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero se necesita una sesión fiscal. Si `representado_cuit` no se informa, el CUIT de sesión se usa como representado. La falta de identidad o contraseña normalmente causa fallo de autenticación; no se aplican defaults.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | Identidad para iniciar sesión; alias `cuit_inicio_sesion`. Se necesita una identidad, aportada por cualquiera de los dos nombres; si se omiten ambos, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | Condicional | ninguno | Identidad para iniciar sesión; alias de `cuit_representante`. Se necesita una identidad, aportada por cualquiera de los dos nombres; si se omiten ambos, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Contraseña en texto; alias `clave_representante` y `contrasena`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_representante` | texto | Condicional | ninguno | Contraseña en texto; alias de `clave`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `contrasena` | texto | Condicional | ninguno | Contraseña en texto; alias de `clave`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64. Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"Q0xBVkUtRklDVElDSUEtTk8tVVRJTElaQUJMRQ=="` |

No usar datos reales en ejemplos.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/declaracion_en_linea/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Empleador Ejemplo",
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

## Resultado y archivos

Las tablas describen el resultado público del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. Si no se encuentra ninguna DDJJ, el trabajo termina con `ERROR` y `data` puede ser `null`.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Siempre que el trabajo tenga resultado final. | `OK` si se encontró al menos una DDJJ, incluso si algunos períodos no tienen DDJJ; `ERROR` si no se encontró ninguna. No se emite `PARCIAL` por períodos sin DDJJ. | `"OK"` |
| `data.operacion` | texto | En resultados públicos con `data`. | Operación ejecutada. | `"consultar"` |
| `data.representado_cuit` | texto | En resultados públicos con `data`. | CUIT normalizado consultado. | `"20123456786"` |
| `data.periodo_desde` | texto | En resultados públicos con `data`. | Primer período normalizado del rango procesado. | `"202601"` |
| `data.periodo_hasta` | texto | En resultados públicos con `data`. | Último período normalizado del rango procesado. | `"202603"` |
| `data.periodos_consultados` | entero | En resultados públicos con `data`. | Cantidad de períodos que contienen DDJJ. | `2` |
| `data.periodos_sin_ddjj` | lista de textos | En resultados públicos con `data`; puede ser `[]`. | Períodos del rango sin DDJJ. | `["202602"]` |
| `data.detalle` | lista de objetos | Solo con `incluir_json=true`; si `incluir_json=false`, la propiedad se omite, no se devuelve como `null`. En un resultado `OK` contiene al menos un registro. | Un registro por período consultado con DDJJ. | `[{"periodo":"202601","ddjj":"ddjj-ejemplo.pdf","vep":"vep-ejemplo.pdf","estado_vep":"Pendiente","pagado":false,"vep_datos":{},"cabecera":{},"secciones":{},"advertencias":[]}]` |
| `data.detalle[].periodo` | texto | En cada registro de `detalle`. | Período de la declaración. | `"202601"` |
| `data.detalle[].ddjj` | texto | En cada registro de `detalle`. | Nombre del PDF de DDJJ; es `""` cuando el archivo no existe o está vacío. | `"ddjj-ejemplo.pdf"` |
| `data.detalle[].vep` | texto | En cada registro de `detalle`. | Nombre del PDF de VEP; es `""` cuando el archivo no existe o está vacío. | `""` |
| `data.detalle[].estado_vep` | texto | Cuando la consulta del período devuelve información de VEP. | Estado del VEP informado en los datos del período. | `"Pendiente"` |
| `data.detalle[].pagado` | booleano | Cuando la consulta del período devuelve información de VEP. | Indicador de pago normalizado a booleano. | `false` |
| `data.detalle[].vep_datos` | objeto | Cuando la consulta del período devuelve información de VEP; puede ser `{}`. | Datos de VEP devueltos para ese período. Los nombres de sus propiedades dependen de la respuesta recibida. | `{}` |
| `data.detalle[].cabecera` | objeto | Cuando la consulta del período devuelve un objeto de resultado; puede ser `{}`. | Cabecera de DDJJ recibida para el período. | `{}` |
| `data.detalle[].secciones` | objeto | Cuando la consulta del período devuelve un objeto de resultado; puede ser `{}`. | Secciones de DDJJ recibidas para el período. | `{}` |
| `data.detalle[].advertencias` | lista de textos | Cuando la consulta del período devuelve un objeto de resultado; puede ser `[]`. | Advertencias incluidas en los datos del período. | `[]` |
| `data.detalle[].sha256_ddjj` | texto | Solo si `subir_archivos=true` y el PDF DDJJ existe, no está vacío y se publicó; en caso contrario se omite. | Huella del PDF DDJJ publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.detalle[].sha256_vep` | texto | Solo si `subir_archivos=true` y el PDF VEP existe, no está vacío y se publicó; en caso contrario se omite. | Huella del PDF VEP publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.detalle[].sha256_nomina` | texto | Solo si `subir_archivos=true` y la planilla de nómina existe, no está vacía y se publicó; en caso contrario se omite. | Huella de la planilla de nómina publicada. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.detalle[].sha256_json` | texto | Solo si `subir_archivos=true` y el JSON DDJJ existe, no está vacío y se publicó; en caso contrario se omite. | Huella del JSON DDJJ publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Puede estar vacía o contener los archivos existentes y publicados para cada período. | Archivos adjuntos: PDF de DDJJ, PDF de VEP, planilla de nómina y JSON de DDJJ. | `[{"name":"ddjj-ejemplo.pdf","url":"","size":900,"artifact_id":"id-ficticio","filename":"ddjj-ejemplo.pdf","content_type":"application/pdf","size_bytes":900,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":null,"url_expires_in":null}]` |

`subir_archivos=false` deja `files=[]` y omite las huellas, pero no evita las descargas temporales. `incluir_json=false` omite `data.detalle`, pero no impide publicar en `files` el JSON de DDJJ si `subir_archivos=true` y el archivo existe.
