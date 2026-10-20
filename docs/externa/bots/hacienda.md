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

## Resultado y faltantes

`result` es el estado `OK`, `PARCIAL` o `ERROR`. `data` informa:

| Clave | Tipo / condición |
|---|---|
| `operacion` | string, `consultar`. |
| `representado_cuit`, `denominacion`, `fecha_desde`, `fecha_hasta` | strings. |
| `por_emisor`, `por_receptor` | objeto por consulta activa: `filas` entero; `muestra` lista de objetos con `incluir_json=true`; `pdf` objeto si se intentaron descargas PDF. |
| `por_emisor.pdf`, `por_receptor.pdf` | `total`, `subidos` enteros y `errores` lista de objetos `{archivo: string, error: string}`. |
| `consolidado` | objeto opcional con `archivo` string, `filas` entero, `hojas` lista de strings; `sha256` string y `size_bytes` entero cuando se publica. |

La planilla Excel aparece en `files[]` si `subir_excel=true`. PDF individuales exitosos aparecen en `files[]`; un PDF fallido se registra sin detener el resto de la consulta. `incluir_json=false` conserva contadores y resúmenes, no muestras. No modifica ni presenta información fiscal.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
