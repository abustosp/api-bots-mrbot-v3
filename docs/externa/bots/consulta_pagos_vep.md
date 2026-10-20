# Consulta de pagos VEP

Consulta en ARCA/SETI pagos y VEP asociados a un CUIT representado para un período en meses. Es una consulta de solo lectura. Puede publicar el CSV resultante como archivo.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/consulta_pagos_vep/consultar`. Cuerpo: `{"payload": {...}, "credentials": {...}}`. La ruta genérica aplica la normalización de alias y luego las reglas reales de esta operación; el esquema mostrado por el catálogo es descriptivo, no sustituye esa validación.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Se quitan espacios, guiones, puntos y barras y se requieren 11 dígitos. Alias: `cuit`, `cuit_representado`. Si se mandan varios aliases, deben representar el mismo CUIT. `null` o vacío inválidos. | `"20-12345678-6"` |
| `periodo` | texto | No | `"72"` | Cantidad de meses en 1 a 3 dígitos. Debe ser numérico y mayor que cero (`"000"` se rechaza; `"001"` vale). Omitido usa `"72"`; `null` explícito se rechaza. | `"72"` |
| `incluir_json` | booleano | No | `true` | `false` elimina la muestra de filas del resultado, no el resumen. Omitido o `null` explícito aplica el default `true`. | `true` |
| `subir_csv` | booleano | No | `true` | `false` evita publicar archivo en `files[]`, pero el CSV se sigue obteniendo para procesar filas. Omitido o `null` explícito aplica `true`. Si también se envía el alias histórico, el campo canónico prevalece y el alias puede descartarse durante la normalización. | `true` |

Campos adicionales son rechazados por la validación de la operación. Los booleanos deben enviarse como booleanos, no como cadenas. No se aplica a esta ruta el DTO de los aliases HTTP planos.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la operación requiere una sesión fiscal. La identidad puede diferir de `representado_cuit`. La falta de identidad o contraseña normalmente causa fallo de autenticación; no se aplican defaults.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | Identidad de la sesión fiscal; requerida si no se envía `cuit_inicio_sesion`. Alias: `cuit_inicio_sesion`. Omitido, `null` o vacío no deja identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | Condicional | ninguno | Alias de `cuit_representante`; requerido si no se envía `cuit_representante` y aporta la misma identidad de sesión. Omitido, `null` o vacío no deja identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Contraseña de la sesión fiscal. Alias: `clave_representante`, `contrasena`. Se necesita una contraseña salvo que se aporte `clave_encriptada`; sin default. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_representante` | texto | Condicional | ninguno | Alias de `clave`; aporta la misma contraseña. Se necesita una contraseña salvo que se aporte `clave_encriptada`. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `contrasena` | texto | Condicional | ninguno | Alias de `clave`; aporta la misma contraseña. Se necesita una contraseña salvo que se aporte `clave_encriptada`. Omitir, `null` o vacío equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_encriptada` | texto | Condicional | ninguno | Alternativa cifrada de la contraseña. Omitido, `null` o vacío se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"Q0xBVkVfRklDVElDSUFfTk9fVVNFRw=="` |

No usar valores reales en ejemplos.

## Ejemplo ficticio

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "periodo": "72",
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el job | `OK` si la consulta y la exportación terminan correctamente; los fallos de sesión, consulta o exportación terminan con error. No se define resultado parcial. | `"OK"` |
| `data.operacion` | texto | En una respuesta correcta | Operación ejecutada. | `"consultar"` |
| `data.representado_cuit` | texto | En una respuesta correcta | CUIT consultado. | `"20123456786"` |
| `data.periodo` | texto | En una respuesta correcta | Período en meses usado en la consulta. | `"72"` |
| `data.filas` | entero | En una respuesta correcta | Cantidad de filas de datos contadas en el CSV. Puede ser `0`. | `0` |
| `data.archivo` | texto | En una respuesta correcta | Nombre del CSV generado, incluso cuando `subir_csv=false`. | `"pagos_vep_<cuit>_<periodo>.csv"` |
| `data.sha256` | texto hexadecimal | Si `subir_csv=true` y la publicación termina correctamente | Huella del CSV publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si `subir_csv=true` y la publicación termina correctamente | Tamaño del CSV publicado en bytes. | `12345` |
| `data.muestra` | lista de objetos | Si `incluir_json=true` | Muestra de filas del CSV; puede ser vacía. Cada valor de fila se representa como texto. | `[]` |
| `files` | lista de archivos | En la respuesta final | Contiene el CSV publicado si `subir_csv=true`; queda vacía si `subir_csv=false`. | `[]` |

Con `subir_csv=true`, el CSV se obtiene en `files`; con `false`, no hay archivo descargable aunque se haya generado para procesar las filas. `incluir_json=false` omite `data.muestra`. La consulta no crea ni paga un VEP.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
