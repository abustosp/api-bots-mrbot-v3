# Declaración en línea

Consulta declaraciones juradas (DDJJ), VEP e información de nómina ya generados para un rango de períodos. Es una lectura: no presenta ni modifica declaraciones ni genera pagos.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/declaracion_en_linea/consultar`. El alias plano `/declaracion-en-linea/consulta` apunta a esta operación, pero aplica su propio modelo. Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. La ruta genérica sigue la normalización y validación efectivas de la operación: limpia separadores comunes de CUIT y normaliza períodos `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA` y `AAAA-MM` a `AAAAMM`.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string nullable, opcional; aliases `cuit`, `cuit_representado` | Si se omite, es `null` o vacío, se consulta el CUIT de la sesión. Si se informa, se limpian separadores y debe quedar en 11 dígitos. |
| `representado_nombre` | string máximo 128; default `""` | Nombre de referencia para los nombres de archivos. Omitido queda vacío; cadena vacía es válida; `null` explícito se rechaza. |
| `periodo_desde`, `periodo_hasta` | strings requeridos; período válido normalizado a `AAAAMM`, mes `01`–`12` | Rango inclusivo. Se aceptan `AAAAMM`, `MM/AAAA`, `AAAA/MM`, `MM-AAAA`, `AAAA-MM`; invertidos se intercambian. `null`/vacío se rechaza. Se generan hasta 1200 períodos y el resto se trunca sin error explícito. |
| `incluir_json` | boolean, default `true` | `false` omite `detalle`, no resumen ni archivos. Omitido aplica `true`; `null` explícito se rechaza. |
| `subir_archivos` | boolean, default `true` | `false` evita publicar PDFs/XLS/JSON en `files[]`, aunque se consultan y descargan. Omitido aplica `true`; `null` explícito se rechaza. |
| `proxy_request`, `proxy` | boolean/string nullable, opcional, legado | Se descartan/no permiten elegir conexión. `null` permitido; no cambian el resultado. |

Se requiere al menos una salida: `incluir_json=true` o `subir_archivos=true`. Campos desconocidos se rechazan por la validación de esta operación. El schema del catálogo es descriptivo y no debe confundirse con el DTO del alias plano.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero se necesita una sesión fiscal. Si `representado_cuit` no se informa, el CUIT de sesión se usa como representado. La falta de identidad o contraseña normalmente causa fallo de autenticación; no se aplican defaults.

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `cuit_representante`, `cuit_inicio_sesion` | strings; se necesita una identidad para iniciar sesión; sin default | Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. |
| `clave`, `clave_representante`, `contrasena` | strings; se necesita una contraseña, salvo que se aporte `clave_encriptada`; sin default | Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. |
| `clave_encriptada` | string opcional; sin default | Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. |

No usar datos reales en ejemplos.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/declaracion_en_linea/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Empleador Ejemplo",
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

## Resultado

`result` es el estado `OK`, `PARCIAL` o `ERROR`; el contenido está en `data`:

| Clave de `data` | Tipo / condición |
|---|---|
| `operacion` | string, `consultar`. |
| `representado_cuit`, `periodo_desde`, `periodo_hasta` | strings normalizados. |
| `periodos_consultados` | entero. |
| `periodos_sin_ddjj` | lista de strings. |
| `detalle` | lista de objetos, presente con `incluir_json=true`; cada registro contiene `periodo`, `ddjj`, `vep` strings; `estado_vep` string, `pagado` boolean, `vep_datos` objeto; `cabecera`/`secciones` objetos y `advertencias` lista de strings cuando existen. |

Si existe al menos una DDJJ en el rango, el estado es `OK` aunque haya faltantes; si no existe ninguna, falla. Los períodos sin DDJJ se listan sin descartar resultados de otros períodos.

Según lo que exista en el portal se publican en `files[]` PDF de DDJJ, PDF de VEP, XLS de nómina y JSON de DDJJ. Un archivo ausente aparece vacío/se omite, no se inventa. `subir_archivos=false` suprime `files[]`; `incluir_json=false` suprime `detalle`, no los archivos.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
