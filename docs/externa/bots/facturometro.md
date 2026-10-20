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

## Resultado y archivos

Las tablas describen la respuesta pública del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. Una lectura completada termina con `result=OK`; un error de lectura no produce valores inferidos.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | Estado global de la consulta. | `"OK"` |
| `data.operacion` | texto | En una consulta completada con `data`. | Operación ejecutada. | `"consultar"` |
| `data.representado_cuit` | texto | En una consulta completada con `data`. | CUIT normalizado consultado. | `"20123456786"` |
| `data.monto` | texto o null | Siempre en `data` de una consulta completada. | Monto publicado por el servicio. Si la lectura no aporta el campo, se representa como `null`. | `"123456.78"` |
| `data.tope` | texto o null | Siempre en `data` de una consulta completada. | Tope publicado por el servicio. Si la lectura no aporta el campo, se representa como `null`. | `null` |
| `data.categoria` | texto o null | Siempre en `data` de una consulta completada. | Categoría publicada por el servicio. Si la lectura no aporta el campo, se representa como `null`. | `"B"` |
| `data.screenshot_url` | texto o null | Siempre en `data` de una consulta completada. | `null` si no se generó y publicó una captura; si existe, contiene la referencia de objeto asignada al archivo, no una URL firmada ni una clave estable del portal. | `"valor-ficticio-no-estable"` |
| `data.captura` | objeto | Solo si se generó y publicó una captura. Se omite si la captura está desactivada o si no se obtuvo una captura publicable. | Metadatos del PNG: `archivo`, `sha256` y `size_bytes`. | `{"archivo":"captura-ejemplo.png","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":800}` |
| `data.captura.archivo` | texto | Dentro de `data.captura`. | Nombre del PNG publicado. | `"captura-ejemplo.png"` |
| `data.captura.sha256` | texto | Dentro de `data.captura`. | Huella del PNG publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.captura.size_bytes` | entero | Dentro de `data.captura`. | Tamaño del PNG publicado, en bytes. | `800` |
| `data.warnings` | lista de textos | Solo si se solicitó captura y no se obtuvo una captura publicable, por panel ausente, fallo de captura o archivo vacío. Si no hay esa condición, la propiedad se omite. | Aviso incluido dentro de `data`; no hay una propiedad independiente de avisos en la respuesta. | `["aviso-ficticio-sobre-captura"]` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Incluye el PNG si se generó y publicó; de lo contrario es `[]`. | Archivos adjuntos del trabajo. | `[{"name":"captura-ejemplo.png","url":"","size":800,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"captura-ejemplo.png","content_type":"image/png","size_bytes":800,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

`incluir_captura=false` deja `screenshot_url=null`, omite `captura` y no adjunta un PNG. Si la captura fue solicitada pero no se obtuvo una imagen publicable, la consulta de datos puede terminar correctamente, `screenshot_url` permanece `null`, `captura` se omite y `data.warnings` informa la ausencia.
