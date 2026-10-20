# Mis Retenciones

## Propósito y operación

Consulta retenciones y percepciones de impuestos seleccionados para un representado y período; genera archivos de salida. La operación es de consulta, no de modificación.

Ruta canónica: `POST /api/v3/bots/mis_retenciones/consultar`. Alias compatible: `POST /api/v3/mis_retenciones/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación se fija en la ruta. El trabajo se acepta con `202` y puede consultarse en `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT consultado; alias `cuit`. Se quitan espacios, guiones, puntos y `/`; deben quedar 11 dígitos. Omitido, `null`, vacío o inválido: rechazo. | `"30876543210"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre para la consulta y los archivos. Alias: `denominacion`. Longitud de 1 a 128 caracteres. Omitido, `null` o vacío: rechazo. | `"Comercio Ejemplo SA"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha `dd/mm/aaaa` de inicio del período. Alias `desde`. Debe ser una fecha real. Omitida, `null`, vacía o inválida: rechazo. | `"01/01/2025"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha `dd/mm/aaaa`, fin inclusive y no anterior a `fecha_desde`. Alias `hasta`. Omitida, `null`, vacía o inválida: rechazo. Si queda en el futuro, el portal limita la fecha final a hoy. | `"31/01/2025"` |
| `impuestos` | lista de textos | No | `null` | Máximo 6 elementos. Códigos admitidos: `216`, `217`, `219`, `353`, `767`, `787`. Omitido, `null` o `[]`: consulta el conjunto predeterminado (los seis códigos en exportación normal; en modo aplicativo se limita al conjunto disponible para ese formato). Los elementos se convierten a texto y se recortan; código desconocido o más de 6 elementos: rechazo. | `["217"]` |
| `tipos` | lista de textos | No | `["Retencion", "Percepcion"]` | Selecciona tipos; valores admitidos `"Retencion"` y `"Percepcion"`. Debe tener al menos un valor; `null`, `[]` o cualquier otra etiqueta: rechazo. Los códigos `216` y `787` solo admiten `Retencion`; los demás admiten ambos tipos. Una selección incompatible con cualquier impuesto elegido se rechaza. | `["Retencion", "Percepcion"]` |
| `exportar_para_aplicativo` | booleano | No | `false` | `true` produce las variantes de exportación para aplicativo, usando los códigos admitidos en ese modo; el código `787` se rechaza si se solicita expresamente. `false` genera la salida CSV estándar. `null` se rechaza. | `false` |
| `incluir_json` | booleano | No | `true` | Incluye muestra y resumen de filas. `false` omite la muestra, no la consulta. `null` se rechaza. | `true` |
| `subir_csv` | booleano | No | `true` | Adjunta los archivos producidos. `false` evita los adjuntos, no la consulta ni la generación necesaria. `null` se rechaza. | `true` |

Debe quedar activo `incluir_json` o `subir_csv`; si ambos son `false`, se rechaza. Los valores de `tipos` distinguen exactamente `Retencion` y `Percepcion`. Se aceptan claves de payload adicionales, pero no se documenta ni garantiza efecto alguno para ellas.

## Credenciales

Se requieren credenciales fiscales válidas y autorización sobre el representado.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `sobre` | `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Identifica la cuenta que inicia sesión. Se requiere junto con una clave, salvo que se use el alias `cuit_inicio_sesion`; si se envía con el otro nombre, los valores deben coincidir. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`. Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Clave fiscal en texto. Se requiere junto con una identidad, salvo que se use un alias o `clave_encriptada`. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`. Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`. Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64. Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkUtRklDVElDSUE="` |

No envíe credenciales en `payload`. Los nombres alternativos repetidos deben contener el mismo valor.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "impuestos": ["217"],
    "tipos": ["Retencion", "Percepcion"],
    "exportar_para_aplicativo": false,
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos por operación

`result` es `OK` cuando termina el recorrido, incluso si alguna combinación impuesto/tipo/formato falla. Esos fallos se describen dentro de `data`; no convierten por sí solos el resultado en `PARCIAL`. Un error fatal puede interrumpir la consulta.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al devolver un resultado | `OK` cuando finaliza el recorrido de combinaciones. | `"OK"` |
| `data.operacion`, `data.representado_cuit`, `data.fecha_desde`, `data.fecha_hasta` | textos | Cuando se devuelve `data` | Operación, representado y período consultado. | `{"operacion":"consultar","representado_cuit":"30876543210","fecha_desde":"01/01/2025","fecha_hasta":"31/01/2025"}` |
| `data.impuestos` | objeto anidado | Cuando se devuelve `data` | Se organiza como código → tipo → formato. El formato es `csv` en la exportación normal, o `siap` y `xls` en la exportación para aplicativo. Los tipos no admitidos para un código no aparecen. | `{"217":{"Retencion":{"csv":{"filas":2,"archivo":"retenciones.csv","muestra":[{"fecha":"10/01/2025","importe":"1250.00"}]}}}}` |
| `data.impuestos.<código>.<tipo>.<formato>` (exportación correcta) | objeto | Para cada combinación procesada correctamente | Incluye `filas` y `archivo`. `muestra` solo aparece con `incluir_json: true`; `sha256` y `size_bytes` solo con `subir_csv: true`. Con `incluir_json: false` se omite `muestra`; con `subir_csv: false` se omiten los metadatos de publicación. | `{"filas":2,"archivo":"retenciones.csv","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":1234,"muestra":[]}` |
| `data.impuestos.<código>.<tipo>.<formato>` (fallo) | objeto | Solo para una combinación cuya exportación falló | Incluye `filas: 0`, `mensaje` y `diagnostic_code`; no incluye `archivo` ni `muestra` para esa combinación. | `{"filas":0,"mensaje":"Exportación no disponible","diagnostic_code":"export_unavailable"}` |
| `data.errores[]` | lista de textos | Siempre dentro de `data`; puede ser `[]` | Describe las combinaciones con error. La consulta continúa con las demás combinaciones. | `["217 Retencion csv: Exportación no disponible"]` |
| `data.avisos[]` | lista de textos | Solo si no se confirma la selección del representado | Informa que se continuó con la relación activa. Es un campo dentro de `data`, no un aviso superior. | `["No se pudo confirmar el representado en Mis Retenciones; se consultó la relación activa."]` |
| `files[]` | lista de archivos | Solo por exportaciones correctas publicadas; puede quedar vacía | Incluye los CSV exitosos cuando `subir_csv: true`. Las exportaciones fallidas no producen un archivo adjunto. | `[{"name":"retenciones.csv","url":"","size":1234,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"retenciones.csv","content_type":"text/csv","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":null,"url_expires_in":null}]` |

Una `muestra` puede ser `[]` si el archivo no tiene filas de datos. La lista de errores y los resúmenes por combinación están dentro de `data`; no se garantiza que el estado global refleje cada fallo individual.
