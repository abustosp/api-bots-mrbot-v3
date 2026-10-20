# Hacienda

Consulta comprobantes en el servicio Hacienda de ARCA por emisor y/o receptor, dentro de un rango de fechas. Consolida los datos en una planilla y puede descargar PDF individuales. Es de solo lectura en el portal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/hacienda/consultar`. Alias plano: `/hacienda/consulta`, con DTO propio. Cuerpo: `{"payload": {...}, "credentials": {...}}`. La ruta genérica aplica la validación de la operación: limpia separadores comunes de CUIT y exige 11 dígitos. El esquema del catálogo es descriptivo, no el validador de esta ruta.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT consultado. Aliases: `cuit`, `cuit_representado`. Se limpian espacios, guiones, puntos y barras y se exigen 11 dígitos. Vacío o `null` inválido. | `"20123456786"` |
| `denominacion` | texto | Sí | ninguno | Denominación que se selecciona en el portal. Longitud de 1 a 256 caracteres. Vacía o `null` inválida. | `"Comercial Ejemplo"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha `DD/MM/AAAA`. Alias: `desde`. Fecha real y rango inclusivo en orden ascendente. Vacío o `null` inválido. | `"01/08/2026"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha `DD/MM/AAAA`. Alias: `hasta`. Fecha real y rango inclusivo en orden ascendente. Vacío o `null` inválido. | `"31/08/2026"` |
| `por_emisor` | booleano | No | `true` | Incluye la consulta por emisor. `false` excluye ese tipo de consulta. Se requiere al menos uno de `por_emisor` o `por_receptor` en `true`. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `por_receptor` | booleano | No | `true` | Incluye la consulta por receptor. `false` excluye ese tipo de consulta. Se requiere al menos uno de `por_emisor` o `por_receptor` en `true`. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `incluir_json` | booleano | No | `true` | `true` incluye muestras de filas. `false` omite muestras de filas, no contadores ni Excel/PDF. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `subir_excel` | booleano | No | `true` | `true` publica la planilla en `files[]`. `false` no publica la planilla en `files[]`, pero sí la genera y conserva su resumen; no evita PDFs individuales. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `descargar_pdf` | booleano | No | `true` | `true` obtiene PDF individuales, que pueden publicarse en `files[]` independientemente de `subir_excel`. `false` consulta las filas sin bajar PDF individuales. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `cuit_representante` | texto | No | `null` | Contexto heredado de sesión. Aliases: `cuit_inicio_sesion`, `cuit_login`. La sesión va en `credentials`. Omitido, `null` o vacío se omite; si se informa se limpia y valida. | `"20123456786"` |
| `eliminar_descargas` | booleano | No | `true` | Se elimina antes de procesar la operación, por lo que no tiene efecto. En la ruta canónica también se ignoran `null` y valores no booleanos. | `true` |
| `proxy_request` | booleano | No | `null` | Legado. No selecciona conexión ni altera el resultado. `null` válido. | `false` |
| `headless` | booleano | No | `null` | Legado. No selecciona conexión ni altera el resultado. `null` válido. | `false` |

Debe activarse al menos un tipo de consulta y al menos una salida (`incluir_json` o `subir_excel`). Los booleanos deben enviarse como booleanos JSON. La validación canónica es la del bot, no la del DTO del alias plano.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la operación requiere sesión fiscal. La identidad y la contraseña no tienen default; si faltan, normalmente falla el inicio de sesión.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Condicional: se necesita una identidad para completar la operación, porque `credentials` puede omitirse en el envelope pero sin identidad falla la autenticación. Alias: `cuit_inicio_sesion`. Si se envían ambos, deben coincidir. Omitido, `null` o `""` dejan sin identidad. `false` y otros tipos no textuales se rechazan. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | Condicional | ninguno | Condicional: alias de `cuit_representante` con la misma necesidad de identidad. Si se envían ambos, deben coincidir. Omitido, `null` o `""` dejan sin identidad. `false` y otros tipos no textuales se rechazan. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Condicional: se necesita una contraseña salvo que se aporte `clave_encriptada`. Aliases: `clave_representante`, `contrasena`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_representante` | texto | Condicional | ninguno | Condicional: alias de `clave` con la misma necesidad de contraseña. Si se envían varios aliases, deben coincidir. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `contrasena` | texto | Condicional | ninguno | Condicional: alias de `clave` con la misma necesidad de contraseña. Si se envían varios aliases, deben coincidir. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada a la contraseña en texto claro. Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"CADENA_CIFRADA_FICTICIA"` |

No incluir secretos reales en ejemplos.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/hacienda/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "denominacion": "Comercial Ejemplo",
    "fecha_desde": "01/08/2026",
    "fecha_hasta": "31/08/2026",
    "por_emisor": true,
    "por_receptor": true,
    "incluir_json": true,
    "subir_excel": true,
    "descargar_pdf": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado y archivos por operación

`result` es `OK` cuando la consulta termina normalmente. Los fallos que interrumpen la consulta terminan en error, no en un resultado parcial. `data` contiene los campos indicados a continuación y el sobre común `schema_version`.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al devolver un resultado | En una consulta completada vale `OK`. Los fallos que abortan la ejecución no garantizan datos de salida. | `"OK"` |
| `data.operacion` | texto | Siempre que se devuelve `data` | Operación realizada. | `"consultar"` |
| `data.representado_cuit`, `data.denominacion`, `data.fecha_desde`, `data.fecha_hasta` | textos | Siempre que se devuelve `data` | Identidad consultada y rango usado. | `{"representado_cuit":"20123456786","denominacion":"Comercial Ejemplo","fecha_desde":"01/08/2026","fecha_hasta":"31/08/2026"}` |
| `data.por_emisor`, `data.por_receptor` | objeto | Solo por cada tipo de consulta activo | Incluye `filas`; `muestra` se agrega únicamente con `incluir_json: true`. Si `incluir_json` es `false`, `muestra` no aparece, no se reemplaza por `null` ni por `[]`. | `{"filas":2,"muestra":[{"comprobante":"0001-00000042","importe":"1250.00"}]}` |
| `data.por_emisor.pdf`, `data.por_receptor.pdf` | objeto | En la consulta correspondiente cuando `descargar_pdf: true` | `total` cuenta los PDF encontrados, `subidos` los publicados y `errores` enumera fallos por comprobante. Con `descargar_pdf: false`, el campo `pdf` no aparece. | `{"total":2,"subidos":1,"errores":[{"archivo":"comprobante-02.pdf","error":"No se pudo descargar"}]}` |
| `data.consolidado` | objeto | En una consulta completada | Resumen de la planilla generada, con `archivo`, `filas` y `hojas`. La planilla se genera aunque no se adjunte. `sha256` y `size_bytes` solo aparecen si `subir_excel: true`; con `false` se omiten. | `{"archivo":"consolidado.xlsx","filas":2,"hojas":["Consolidado","Por emisor"],"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":1234}` |
| `files[]` | lista de archivos | Archivos publicados correctamente; puede estar vacía | Incluye la planilla cuando `subir_excel: true` y cada PDF individual que se descargó y publicó. Un fallo de PDF queda en `pdf.errores` y no detiene las demás descargas. | `[{"name":"comprobante-01.pdf","url":"","size":1234,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"comprobante-01.pdf","content_type":"application/pdf","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":null,"url_expires_in":null}]` |

Los objetos `por_emisor` y `por_receptor` que no se seleccionaron no aparecen en `data`. Una lista `muestra` puede estar vacía si la consulta no produjo filas. La salida no presenta ni modifica información fiscal.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
