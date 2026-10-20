# Hacienda

Consulta comprobantes en el servicio Hacienda de ARCA por emisor y/o receptor, dentro de un rango de fechas. Consolida los datos en una planilla y puede descargar PDF individuales. Es de solo lectura en el portal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/hacienda/consultar`. Alias plano: `/hacienda/consulta`, con DTO propio. Cuerpo: `{"payload": {...}, "credentials": {...}}`. La ruta genérica aplica la validación de la operación: limpia separadores comunes de CUIT y exige 11 dígitos. El esquema del catálogo es descriptivo, no el validador de esta ruta.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string requerido; aliases `cuit`, `cuit_representado` | CUIT consultado; se limpian espacios, guiones, puntos y barras y se exigen 11 dígitos. Vacío/nulo inválido. |
| `denominacion` | string 1–256, requerida | Denominación que se selecciona en el portal. Vacía/nula inválida. |
| `fecha_desde`, `fecha_hasta` | strings requeridos `DD/MM/AAAA`; aliases `desde`, `hasta` | Fechas reales y rango inclusivo en orden ascendente. Vacío/nulo inválido. |
| `por_emisor`, `por_receptor` | boolean, default ambos `true` | Se requiere al menos uno verdadero. `false` excluye ese tipo de consulta. `null` explícito se rechaza. |
| `incluir_json` | boolean, default `true` | `false` omite muestras de filas, no contadores ni Excel/PDF. `null` explícito se rechaza. |
| `subir_excel` | boolean, default `true` | `false` no publica la planilla en `files[]`, pero sí la genera y conserva su resumen. No evita PDFs individuales. `null` explícito se rechaza. |
| `descargar_pdf` | boolean, default `true` | `false` consulta las filas sin bajar PDF individuales. Si es `true`, los PDFs que se obtienen pueden publicarse en `files[]` independientemente de `subir_excel`. Omitido aplica `true`; `null` explícito se rechaza. |
| `cuit_representante` | CUIT de sesión opcional; aliases `cuit_inicio_sesion`, `cuit_login` | Contexto heredado; la sesión va en `credentials`. `null`/vacío se omite; si se informa se limpia y valida. |
| `eliminar_descargas` | boolean, default `true` | Se elimina antes de procesar la operación, por lo que no tiene efecto. En la ruta canónica también se ignoran `null` y valores no booleanos. |
| `proxy_request`, `headless` | boolean nullable opcional, legado | No seleccionan conexión ni alteran el resultado; `null` válido. |

Debe activarse al menos un tipo de consulta y al menos una salida (`incluir_json` o `subir_excel`). Los booleanos deben enviarse como booleanos JSON. La validación canónica es la del bot, no la del DTO del alias plano.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la operación requiere sesión fiscal. La identidad y la contraseña no tienen default; si faltan, normalmente falla el inicio de sesión.

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `cuit_representante`, `cuit_inicio_sesion` | strings; se necesita una identidad para iniciar sesión; sin default | Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. |
| `clave`, `clave_representante`, `contrasena` | strings; se necesita una contraseña, salvo que se aporte `clave_encriptada`; sin default | Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. |
| `clave_encriptada` | string opcional; sin default | Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. |

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
