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

## Resultado y archivos

`data.impuestos` organiza resultados por código de impuesto, tipo y formato. Las entradas resumen filas y nombre de archivo; si `incluir_json` está activo, puede incluir muestra, tamaño y huella del archivo. `files[]` contiene archivos adjuntos cuando `subir_csv` está activo.

Los fallos de una combinación impuesto/tipo/formato se registran dentro de `data.errores` y el bot continúa con las demás combinaciones. El estado global puede ser `OK` aunque se informen errores por combinación; compruebe `data.errores` y las cantidades por salida. Si ARCA no confirma el representado, el resultado puede incluir un aviso y consultar la relación activa.