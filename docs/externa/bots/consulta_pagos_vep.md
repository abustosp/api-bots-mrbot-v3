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

El estado del job incluye `result` (`OK`, `PARCIAL` o `ERROR`); la carga del bot está en `data`:

| Clave de `data` | Tipo / condición |
|---|---|
| `operacion` | string, valor `consultar`. |
| `representado_cuit`, `periodo`, `archivo` | string. |
| `filas` | entero. |
| `sha256` | string, presente si el CSV se publicó. |
| `size_bytes` | entero, presente si el CSV se publicó. |
| `muestra` | lista de objetos, presente si `incluir_json=true`. |

Con `subir_csv=true`, el CSV se referencia en `files[]`; con `false`, no hay archivo publicado aunque se haya generado para procesar las filas. Los errores de sesión, consulta o exportación hacen fallar la operación. No crea ni paga un VEP.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
