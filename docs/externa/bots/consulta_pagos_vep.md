# Consulta de pagos VEP

Consulta en ARCA/SETI pagos y VEP asociados a un CUIT representado para un período en meses. Es una consulta de solo lectura. Puede publicar el CSV resultante como archivo.

## Ruta V3 y catálogo

Ruta canónica: `POST /api/v3/bots/consulta_pagos_vep/consultar`. Cuerpo: `{"payload": {...}, "credentials": {...}}`. La ruta genérica aplica la normalización de alias y luego las reglas reales de esta operación; el esquema mostrado por el catálogo es descriptivo, no sustituye esa validación.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido; aliases `cuit`, `cuit_representado` | Se quitan espacios, guiones, puntos y barras y se requieren 11 dígitos. Si se mandan varios aliases, deben representar el mismo CUIT. Nulo/vacío inválido. |
| `periodo` | string de 1–3 dígitos; default `"72"` | Cantidad de meses. Debe ser numérico y mayor que cero (`"000"` se rechaza; `"001"` vale). Omitido usa 72; `null` explícito se rechaza. |
| `incluir_json` | boolean; default `true` | `false` elimina la muestra de filas del resultado, no el resumen. Omitido o `null` explícito aplica el default `true`. |
| `subir_csv` | boolean; default `true` | `false` evita publicar archivo en `files[]`, pero el CSV se sigue obteniendo para procesar filas. Omitido o `null` explícito aplica `true`. Si también se envía el alias histórico, el campo canónico prevalece y el alias puede descartarse durante la normalización. |

Campos adicionales son rechazados por la validación de la operación. Los booleanos deben enviarse como booleanos, no como cadenas. No se aplica a esta ruta el DTO de los aliases HTTP planos.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero la operación requiere una sesión fiscal. La identidad puede diferir de `representado_cuit`. La falta de identidad o contraseña normalmente causa fallo de autenticación; no se aplican defaults.

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `cuit_representante`, `cuit_inicio_sesion` | strings; se necesita una identidad para iniciar sesión; sin default | Si se omiten, son `null` o `""`, no queda identidad y falla la autenticación. `false` y otros tipos no textuales se rechazan. Si se envían ambos, deben coincidir. |
| `clave`, `clave_representante`, `contrasena` | strings; se necesita una contraseña, salvo que se aporte `clave_encriptada`; sin default | Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. |
| `clave_encriptada` | string opcional; sin default | Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. |

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
