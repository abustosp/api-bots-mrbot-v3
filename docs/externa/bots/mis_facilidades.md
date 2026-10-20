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

## Resultado y archivos

El resultado lista los CUIT disponibles, los planes examinados y procesados, y los errores asociados a planes o secciones. Los planes cancelados se excluyen; además, se excluye cualquier plan cuya situación coincida con `situacion_excluyente`. Para cada plan procesado se pueden generar PDFs y un archivo XLSX. Los adjuntos aparecen en `files[]` si `subir_archivos` está activo.

Si algunas exportaciones fallan y otras continúan, el trabajo puede terminar `PARCIAL`, con errores por plan/sección. Una consulta sin errores termina `OK`. El resultado no garantiza que cada plan tenga todas las vistas exportables.