# SIFERE

**Propósito.** Consultar retenciones y percepciones en SIFERE Web/COMARB para un período, por jurisdicción y consolidados disponibles.

## Operación y ruta V3

Catálogo: `sifere`, operación `consultar`.

- Canónica: `POST /api/v3/bots/sifere/consultar`.
- Alias V3: `POST /api/v3/sifere/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. La aceptación devuelve `job_id`; el resultado se consulta en `/api/v3/jobs/{job_id}`.

## Credenciales

Se necesita clave fiscal y CUIT representante. Convenciones de envío, nombres alternativos, clave cifrada y tratamiento de ausencias: [API pública](../api.md). Este bot inicia sesión con la clave y usa el CUIT consultado del payload; la clave no se envía como campo de consulta. Una credencial vacía/no informada no permite iniciar la consulta.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `clave` | texto | Sí | ninguno | Texto no vacío para ejecutar. Alias: `clave_representante`, `contrasena`; si hay varias deben coincidir. `clave_encriptada` es alternativa cifrada. Omitido, `null` o vacío deja el job sin credencial ejecutable; `false` no es texto válido. | `"CLAVE_FISCAL_FICTICIA"` |
| `cuit_representante` | texto | Sí | ninguno | CUIT de la sesión, sin default de aplicación. Alias: `cuit_inicio_sesion`. Omitido, `null` o vacío se elimina al normalizar; la autenticación requiere un representante disponible. | `"20123456786"` |

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; quita espacios, guiones, puntos y `/`. Alias: `cuit`, `cuit_representado`. Vacío o `null` inválidos. | `"20-12345678-6"` |
| `periodo` | texto | Sí | ninguno | Seis dígitos `AAAAMM`, mes 01–12. Vacío, `null` o separadores inválidos se rechazan. | `"202601"` |
| `representado_nombre` | texto | No | `null` | Máximo 256 caracteres; alias `representado`. Nombre para rótulos de archivo; vacío o `null` se convierte a ausencia y el nombre usa el CUIT. | `"Comercial Ficticia SA"` |
| `jurisdicciones` | lista de enteros | No | `null` | `null` u omitir selecciona todas las jurisdicciones 901–924. Lista vacía no consulta SIRCAR por jurisdicción. Valores fuera de 901–924 se eliminan silenciosamente; los restantes se deduplican y ordenan. | `[901, 902]` |
| `incluir_json` | booleano | No | `true` | `false` omite `jurisdicciones` y `consolidados` de `data`, no impide la consulta ni las descargas. `null` inválido. | `true` |
| `subir` | booleano | No | `true` | `true` publica reportes; `false` no. Omitir equivale a `true`; `null` inválido. | `true` |
| `consolidados` | booleano | No | `true` | Solicita además SIRCREB y SIRTAC; `false` los desactiva. Omitir equivale a `true`; `null` inválido. | `true` |

No se aceptan campos adicionales excepto los cuatro nombres de sesión/clave/proxy que se descartan por compatibilidad. Se exige al menos una salida (`incluir_json=true` o `subir=true`). Aunque `jurisdicciones=[]`, `consolidados=true` mantiene la consulta consolidada.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "periodo": "202601",
    "representado_nombre": "Comercial Ficticia SA",
    "jurisdicciones": [901, 902],
    "incluir_json": true,
    "subir": true,
    "consolidados": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y archivos

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` contiene los campos de la consulta y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el trabajo | `OK` sin errores no fatales; `PARCIAL` si falla una o más consultas individuales y se completa el trabajo; `ERROR` ante una falla que interrumpe la operación. | `"PARCIAL"` |
| `data.operacion`, `data.representado_cuit`, `data.periodo` | texto | En un resultado de operación disponible | Operación, CUIT y período consultados. | `{"operacion":"consultar","representado_cuit":"20123456786","periodo":"202601"}` |
| `data.jurisdicciones` | lista de objetos | Con `incluir_json=true`; se omite si es `false` | Una entrada por método y jurisdicción consultados. Si la entrada `jurisdicciones` fue `[]`, esta salida es `[]`. | `[{"jurisdiccion":901,"metodo":"retencion","etiqueta":"Retenciones","archivos":[]}]` |
| `data.jurisdicciones[].archivos` | lista de objetos | En cada entrada de `data.jurisdicciones` | Archivos disponibles para esa jurisdicción y método. Puede ser `[]`. | `[{"archivo":"sircar-demo.xlsx","tipo":"xlsx"}]` |
| `data.jurisdicciones[].archivos[].sha256`, `data.jurisdicciones[].archivos[].size_bytes` | texto, entero | Solo si `subir=true` y el archivo se publicó | Huella y tamaño del archivo publicado. Si no se publica, estas propiedades no aparecen. | `{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":2048}` |
| `data.consolidados` | lista de objetos | Si `consolidados=true`, `incluir_json=true` y la consulta de consolidados termina | Archivos adicionales de informes y tablas SIRCREB/SIRTAC. Puede ser `[]`; se omite si los consolidados están desactivados, el JSON está desactivado o falla esa consulta. | `[{"archivo":"sircreb-demo.xlsx"}]` |
| `data.consolidados[].sha256`, `data.consolidados[].size_bytes` | texto, entero | Solo si `subir=true` y el archivo se publicó | Huella y tamaño del archivo consolidado publicado. | `{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":2048}` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; un elemento por archivo publicado | Referencia pública de cada reporte publicado. Sin publicaciones, el valor es `[]`. | `[{"name":"sircar-demo.xlsx","url":"","filename":"sircar-demo.xlsx","size":2048,"size_bytes":2048}]` |

`incluir_json=false` omite las listas estructuradas aunque la consulta y la publicación de archivos continúen. Los archivos SIRCAR disponibles pueden ser XLSX o PDF; las consultas consolidadas pueden producir archivos adicionales. Los resultados obtenidos pueden conservarse con `result="PARCIAL"`; el detalle de esos fallos no se añade a las listas de `data`. Un fallo de publicación puede interrumpir el trabajo después de descargas previas.
