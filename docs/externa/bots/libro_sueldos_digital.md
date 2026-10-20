# Libro de Sueldos Digital

Descarga los PDF del Libro de Sueldos Digital correspondientes a las liquidaciones encontradas en un rango mensual. Es una consulta/descarga de información existente, no presenta liquidaciones ni modifica el portal.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/libro_sueldos_digital/descargar`. El alias plano `/libro-sueldos-digital/consulta` aplica su propio DTO. En la operación canónica los períodos son strings `AAAAMM`, mes `01`–`12`; rango inclusivo y ascendente. La CUIT representada es opcional y acepta separadores para normalizar.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `periodo_desde`, `periodo_hasta` | strings requeridos `AAAAMM` | Seis dígitos, mes 01–12. Nulo/vacío inválido; período inicial posterior al final se rechaza. Se genera mes a mes, con tope de 1200 períodos. |
| `representado_cuit` | string nullable, opcional; aliases `cuit`, `cuit_representado` | Si se omite, es nulo o vacío, se usa el CUIT de sesión. Si se informa, se quitan separadores y se valida con el empleador seleccionado. |
| `incluir_json` | boolean, default `true` | `false` omite el detalle por liquidación, pero deja contadores/listas de períodos. `null` explícito se rechaza. |
| `subir_archivos` | boolean, default `true` | `false` no publica PDF en `files[]`; el portal todavía se consulta y los archivos se descargan durante el trabajo. `null` explícito se rechaza. |
| `cuit_representante`, `cuit_inicio_sesion`, `cuit_login` | CUIT de sesión/contexto heredado, normalmente en `credentials` | No se repite en `payload` si la sesión va en `credentials`. `null`/vacío se omite. |
| `proxy_request`, `headless` | boolean nullable opcional, legado | No cambian selección/resultado; `null` es válido. |

El esquema de ejecución tolera campos adicionales; se recomienda enviar solo los documentados. La operación la fija la ruta. El esquema del catálogo es descriptivo y el alias HTTP plano tiene su propio modelo de entrada.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la descarga requiere una sesión fiscal. La identidad y la contraseña no tienen default; si faltan, normalmente falla la autenticación. El servicio comprueba el empleador efectivo y detiene la descarga si no coincide con el CUIT solicitado.

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `cuit_representante`, `cuit_inicio_sesion` | strings; se necesita una identidad para iniciar sesión; sin default | Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. |
| `clave`, `clave_representante`, `contrasena` | strings; se necesita una contraseña, salvo que se aporte `clave_encriptada`; sin default | Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. |
| `clave_encriptada` | string opcional; sin default | Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. |

No usar credenciales reales en el ejemplo.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/libro_sueldos_digital/descargar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo_desde": "202601",
    "periodo_hasta": "202603",
    "incluir_json": true,
    "subir_archivos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado y parciales

`result` es el estado `OK`, `PARCIAL` o `ERROR`. `data` incluye:

| Clave | Tipo / condición |
|---|---|
| `operacion` | string, `descargar`. |
| `representado_cuit` | string del empleador verificado. |
| `periodo_desde`, `periodo_hasta` | strings. |
| `periodos_solicitados`, `periodos_encontrados`, `periodos_descargados` | listas de strings. |
| `liquidaciones_encontradas`, `liquidaciones_descargadas` | enteros. |
| `empleador` | objeto de identidad devuelto por el servicio. |
| `detalle` | lista opcional con `periodo`, `liquidacion`, `archivo` strings; `sha256` string, `size_bytes` entero y `object_key` string si se publica; `error` string en registro fallido. |

PDF exitosos se publican en `files[]` cuando `subir_archivos=true`.

Sin liquidaciones encontradas, la operación falla. Si todas las descargas encontradas fallan, `result=ERROR`; con éxitos y fallos es `PARCIAL`, conservando archivos exitosos y errores; con éxito completo es `OK`. No reintentar un parcial sin revisar qué liquidaciones ya se obtuvieron.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
