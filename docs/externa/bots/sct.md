# Sistema de Cuentas (SCT)

**Propósito.** Consultar reportes de vencimientos, deudas y declaraciones juradas pendientes para un CUIT; seleccionar uno o más formatos disponibles.

## Operación y ruta V3

Catálogo: `sct`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sct/consultar`.
- Alias V3: `POST /api/v3/sct/consulta`.

Cuerpo canónico: `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe los campos en un cuerpo plano. No mezclar ambas formas. La aceptación devuelve `job_id`; consultar estado/resultado en `/api/v3/jobs/{job_id}`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales

Se requiere clave fiscal y CUIT representante en `credentials.clave` y `credentials.cuit_representante`. Se aceptan `clave_representante` o `contrasena` como nombres alternativos coincidentes y `clave_encriptada` como alternativa cifrada Base64. `cuit_inicio_sesion` equivale al CUIT representante. Si todas las claves faltan, son `null` o `""`, no hay acceso y el trabajo no puede ejecutarse. Para login se requiere un CUIT válido. No se envían claves como parámetros de consulta.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; normaliza espacios, guiones, puntos y `/`. Alias: `cuit`. Vacío o `null` inválidos. | `"20-12345678-6"` |
| `secciones` | lista de textos | No | `["vencimientos","deudas","ddjj_pendientes"]` | Valores: `vencimientos`, `deudas`, `ddjj_pendientes`. Se recortan espacios, se ignora mayúsculas y se deduplica y ordena. Lista vacía, `null` o valor desconocido rechaza. En la API canónica, si se omite esta clave, la normalización agrega las tres secciones. | `["vencimientos","deudas"]` |
| `formatos` | lista de textos | No | `["xlsx"]` | Valores: `xlsx`, `csv`, `pdf`; se recortan espacios, se normalizan a minúsculas, se deduplican y ordenan. Lista vacía, `null` o valor desconocido rechaza. En la API canónica, si se omite esta clave, se agrega `["xlsx"]`. | `["xlsx","csv"]` |
| `incluir_json` | booleano | No | `true` | `true` incluye el listado `reportes`; `false` lo omite. `null` inválido. | `true` |
| `subir` | booleano | No | `false` | `true` publica archivos; `false` no publica. `null` inválido. En la API V3, si se omite `subir`, la normalización lo establece en `false`; el default efectivo de la API es `false`. Debe enviarse explícitamente para controlar la publicación. | `true` |
| `omitir_vacias` | booleano | No | `true` | Omite una sección solo si el portal informa explícitamente cero elementos. `false` intenta descargarla de todos modos. Omitir equivale a `true`; `null` inválido. | `true` |

La normalización de payload se aplica también a la ruta canónica. Si no se envían opciones de sección/formato, el comportamiento efectivo es consultar las tres secciones en formato XLSX; la publicación queda desactivada (`subir=false`). Para controlar selección y publicación, enviar explícitamente `secciones`, `formatos` y `subir`. Use los nombres canónicos y no mezcle grafías alternativas, pues una forma antigua puede eliminarse antes de validar.

Se exige al menos una salida: `incluir_json=true` o `subir=true`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "secciones": ["vencimientos", "deudas"],
    "formatos": ["xlsx", "csv"],
    "incluir_json": true,
    "subir": true,
    "omitir_vacias": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado, archivos y resultados parciales

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` describe los reportes y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el trabajo | `OK` sin fallos por sección/formato; `PARCIAL` cuando solo hay fallos individuales de descarga y se conserva al menos un reporte, marca de sección vacía o archivo; `ERROR` si no queda ningún resultado tras fallos individuales o si una falla interrumpe la operación, incluida la publicación de un archivo. Sin fallos, se puede completar `OK` aunque las secciones estén vacías. | `"PARCIAL"` |
| `data.operacion`, `data.representado_cuit` | texto | En un resultado de operación disponible | Operación y CUIT consultado. | `{"operacion":"consultar","representado_cuit":"20123456786"}` |
| `data.reportes` | lista de objetos | Con `incluir_json=true`; se omite si es `false` | Resultados por sección y formato. Puede contener un reporte descargado o una marca de sección omitida por cero explícito. | `[{"seccion":"deudas","formato":"xlsx","archivo":"sct-demo.xlsx"}]` |
| `data.reportes[].omitido`, `data.reportes[].motivo` | booleano, texto | Solo cuando el portal informa explícitamente cero elementos y `omitir_vacias=true` | Marca una sección omitida por estar vacía; no representa un archivo. | `{"omitido":true,"motivo":"sin_registros"}` |
| `data.reportes[].sha256`, `data.reportes[].size_bytes` | texto, entero | Solo si el reporte se publicó | Huella y tamaño del archivo publicado. Si `subir=false`, estos campos no aparecen en el elemento. | `{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":2048}` |
| `data.errores` | lista de objetos | Cuando falla una descarga individual y se conserva un resultado de operación | Identifica la sección y el formato fallidos; `error` contiene una etiqueta textual del fallo. No aparece como lista vacía si no hubo errores. | `[{"seccion":"deudas","formato":"csv","error":"fallo_de_descarga"}]` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; un elemento por reporte publicado | Referencia pública de cada archivo publicado. Sin publicaciones, el valor es `[]`. | `[{"name":"sct-demo.xlsx","url":"","filename":"sct-demo.xlsx","size":2048,"size_bytes":2048}]` |

`data.reportes` puede incluir una marca `{ "omitido": true, "motivo": "sin_registros" }`; esta se agrega solo ante un contador explícito de cero. Si el portal no permite leer el contador, la sección se intenta descargar. Los errores de sección/formato durante la descarga no detienen las demás consultas; cuando hay otra salida conservada, el resultado es `PARCIAL`. Una falla al publicar interrumpe el trabajo como `ERROR`, incluso si ya hubo descargas previas. Si las secciones están vacías y `incluir_json=false`, puede terminar `OK` sin reportes ni archivos. La consulta no modifica declaraciones ni efectúa pagos.
