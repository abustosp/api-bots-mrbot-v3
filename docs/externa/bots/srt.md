# Consulta de alícuotas SRT

**Propósito.** Consultar alícuotas ART por lote de CUIT, hasta 50 elementos, devolviendo el estado y los datos tabulares disponibles para cada CUIT.

## Operación y ruta V3

Catálogo: `srt`, operación `consultar_alicuotas`.

- Canónica: `POST /api/v3/bots/srt/consultar_alicuotas`.
- Alias V3: `POST /api/v3/srt/alicuotas/consulta`.

Enviar `{ "payload": { ... }, "credentials": { ... } }`. Consultar el resultado con `GET /api/v3/jobs/{job_id}`.

## Credenciales

Se requiere clave fiscal y CUIT representante para iniciar la sesión; las convenciones completas están en [API pública](../api.md). `clave` es texto no vacío, aliases `clave_representante` y `contrasena` deben coincidir; se admite clave cifrada `clave_encriptada`. Omitir/null/vacío no permite ejecución. `cuit_representante` (alias `cuit_inicio_sesion`) no tiene default y se requiere para autenticación.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuits_consulta` | lista de textos | Sí | ninguno | Lista de 1–50 CUIT. Cada valor se normaliza quitando espacios, guiones, puntos y `/`, y debe resultar en 11 dígitos. Los duplicados se eliminan conservando el primer orden. Omitir, `null`, lista vacía o elemento inválido rechaza. | `["20-12345678-6","27-11122233-4"]` |
| `incluir_json` | booleano | No | `true` | `false` omite `consultas` del JSON inline, no cambia las consultas. Omitir equivale a `true`; `null` inválido. | `true` |
| `subir_json` | booleano | No | `true` | `true` publica el consolidado JSON; `false` no lo publica. Omitir equivale a `true`; `null` inválido. | `true` |

Se requiere al menos una salida (`incluir_json=true` o `subir_json=true`). No se aceptan otros campos del payload. El catálogo de entrada omite erróneamente `cuits_consulta` de la lista `required`, pero la validación efectiva sí la exige; envíela siempre.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "cuits_consulta": ["20-12345678-6", "27-11122233-4"],
    "incluir_json": true,
    "subir_json": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y resultados parciales

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` contiene los conteos y, cuando se solicita, las consultas; `files` enumera el consolidado publicado. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el trabajo | `OK` si no hubo consultas fallidas; `PARCIAL` si uno o más CUIT fallan y el lote continúa; `ERROR` si falla el trabajo completo. | `"PARCIAL"` |
| `data.operacion` | texto | En un resultado de operación disponible | Nombre de la operación. | `"consultar_alicuotas"` |
| `data.cantidad`, `data.consultas_ok`, `data.consultas_error` | enteros | En un resultado de operación disponible | Cantidad total de CUIT únicos procesados, cantidad exitosa y cantidad con estado `ERROR`. `SIN_AFILIACION_VIGENTE` cuenta como consulta exitosa. | `{"cantidad":2,"consultas_ok":1,"consultas_error":1}` |
| `data.consultas` | lista de objetos | Con `incluir_json=true`; se omite si es `false` | Una entrada por CUIT procesado. | `[{"cuit":"20123456786","status":"SIN_AFILIACION_VIGENTE","message":"No se encontraron datos de alícuotas","data":null}]` |
| `data.consultas[].status` | texto | En cada elemento de `data.consultas` | Estado `OK`, `SIN_AFILIACION_VIGENTE` o `ERROR`. | `"SIN_AFILIACION_VIGENTE"` |
| `data.consultas[].message` | texto o `null` | En cada elemento de `data.consultas` | Mensaje de la consulta; puede ser explícitamente `null` si no hay mensaje. | `null` |
| `data.consultas[].data` | datos tabulares o `null` | En cada elemento de `data.consultas` | Tablas devueltas por la consulta; vale `null` cuando el portal no devuelve tablas, también en una fila de error. | `null` |
| `data.sha256`, `data.size_bytes` | texto, entero | Solo si `subir_json=true` y el archivo consolidado se publicó | Huella y tamaño del archivo JSON publicado. | `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"`, `4096` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; contiene el consolidado si se publicó | Referencia pública del archivo JSON. Sin publicación, el valor es `[]`. | `[{"name":"srt_alicuotas.json","url":"","filename":"srt_alicuotas.json","size":4096,"size_bytes":4096}]` |

El consolidado se prepara durante la ejecución incluso cuando `incluir_json=false`; al publicarlo, su contenido se recorta a 1.048.576 caracteres y puede dejar de ser JSON válido. Las filas fallidas no eliminan las demás consultas del lote. Esta consulta no modifica obligaciones.
