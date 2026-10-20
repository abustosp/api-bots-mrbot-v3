# Facturómetro

Lee del servicio de Monotributo el monto facturado, el tope y la categoría informada para un contribuyente. Es una consulta, no modifica la situación fiscal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/facturometro/consultar`. El alias plano `/facturometro/consulta` aplica su propio DTO. La ruta genérica valida con las reglas efectivas del bot: `representado_cuit` es opcional, y si se omite se usa el CUIT de la sesión. La CUIT se normaliza quitando separadores comunes y debe quedar en 11 dígitos.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | No | ninguno | Identifica el CUIT consultado; alias `cuit` y `cuit_representado`. Si se omite o es `null`, se usa el CUIT de sesión. Si se envía vacío o inválido, se rechaza. Se quitan los separadores comunes y se requieren 11 dígitos. | `"20123456786"` |
| `incluir_captura` | booleano | No | `true` | `false` evita la captura PNG y su entrada en `files[]`; no cambia los datos consultados. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `cuit_representante` | texto | No | ninguno | CUIT opcional de sesión o contexto; alias `cuit_inicio_sesion` y `cuit_login`. La sesión fiscal se envía en `credentials`. Si se informa aquí, se normaliza como CUIT; `null` se omite y vacío se rechaza. No enviar secretos en este campo. | `"20123456786"` |
| `proxy_request` | booleano | No | ninguno | Campo legado: no cambia el resultado. `null` es válido; si se omite no tiene efecto. | `false` |
| `headless` | booleano | No | ninguno | Campo legado: no cambia el resultado. `null` es válido; si se omite no tiene efecto. | `false` |

Los campos ajenos al contrato se rechazan al procesar la solicitud. La publicación de la captura puede no producir archivo si el servicio no muestra el panel, aunque los datos se hayan consultado.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la consulta requiere sesión fiscal. La identidad y la contraseña no tienen default; si faltan, normalmente falla la autenticación.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | Identidad para iniciar sesión; alias `cuit_inicio_sesion`. Se necesita una identidad, aportada por cualquiera de los dos nombres; si se omiten ambos, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `cuit_inicio_sesion` | texto | Condicional | ninguno | Identidad para iniciar sesión; alias de `cuit_representante`. Se necesita una identidad, aportada por cualquiera de los dos nombres; si se omiten ambos, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Contraseña en texto; alias `clave_representante` y `contrasena`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_representante` | texto | Condicional | ninguno | Contraseña en texto; alias de `clave`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `contrasena` | texto | Condicional | ninguno | Contraseña en texto; alias de `clave`. Se necesita una contraseña, aportada por cualquiera de estos nombres o por `clave_encriptada`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios alias, deben coincidir. | `"CLAVE-FICTICIA-NO-UTILIZABLE"` |
| `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64. Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"Q0xBVkUtRklDVElDSUEtTk8tVVRJTElaQUJMRQ=="` |

No usar credenciales reales en el ejemplo.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/facturometro/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "incluir_captura": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado

`result` es el estado `OK`, `PARCIAL` o `ERROR`. `data` incluye:

| Clave | Tipo / condición |
|---|---|
| `operacion` | string, `consultar`. |
| `representado_cuit` | string del CUIT seleccionado. |
| `monto`, `tope`, `categoria` | string o null, según lo publicado por el servicio. |
| `screenshot_url` | string o null; cuando hay captura contiene una clave de objeto, no URL firmada. |
| `captura` | objeto opcional con `archivo` y `sha256` strings, `size_bytes` entero. |
| `warnings` | lista de strings si falta el panel para capturar. |

Si se solicita y obtiene captura, el PNG aparece en `files[]`. Un servicio sin lectura disponible produce error, no valores inferidos.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
