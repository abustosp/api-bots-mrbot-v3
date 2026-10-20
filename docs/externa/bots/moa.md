# MOA

## Propósito y operación

Consulta información de despachos aduaneros que la cuenta puede abrir para un representado en el servicio MOA. Procesa los identificadores recibidos individualmente y devuelve sus datos de carátula y estado de presentación. No presenta ni modifica despachos.

Ruta V3: `POST /api/v3/bots/moa/consultar`. Alias compatible: `POST /api/v3/moa/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación se fija en la ruta. El trabajo se acepta con `202` y se sigue en `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; requerido | CUIT del titular del árbol MOA; alias `cuit`. Se quitan espacios, guiones, puntos y `/`. Omitido, `null`, vacío o inválido: rechazo. |
| `despachos` | lista de textos; requerida; 1–100 elementos | Identificadores a consultar. Elementos se recortan y se convierten a texto; cada uno debe quedar no vacío y medir hasta 64 caracteres. Duplicados se eliminan conservando el primer orden. Omitida, `null`, lista vacía, valor vacío o más de 100 elementos: rechazo. |
| `tipo_agente` | texto 1–128; opcional; `"IMEX-IMEX-IMPORTADOR/EXPORT."` | Perfil de agente con el que se navega el árbol. Omitido: usa el valor indicado. `null`, vacío o más de 128 caracteres: rechazo. |
| `rol` | texto 1–128; opcional; `"IMEX-Rol Importador Exportador"` | Rol para abrir el árbol de empresa. Omitido: usa el valor indicado. `null`, vacío o más de 128 caracteres: rechazo. |
| `metodo` | `"url"` o `"form"`; opcional; `"url"` | Forma de abrir cada despacho. `null`, cadena vacía u otro valor: rechazo. |
| `incluir_json` | booleano; opcional; `true` | Incluye registros detallados por despacho en `data.datos`. `false` vacía esa lista; los contadores, errores y resumen CSV se mantienen. `null` se rechaza. |
| `subir_csv` | booleano; opcional; `true` | Adjunta el CSV consolidado en `files[]`. `false` no adjunta el archivo. `null` se rechaza. Alias compatible `minio_upload`; también se normalizan `carga_minio` y `archivo_historico_minio`. |

Debe estar activo `incluir_json` o `subir_csv`; si ambos son `false`, se rechaza. Los campos desconocidos se rechazan.

## Credenciales

Se requieren credenciales fiscales válidas y autorización sobre el CUIT indicado.

| Campo en `credentials` | Tipo; requerido; default | Omisión, `null` o vacío |
|---|---|---|
| `cuit_representante` | texto; requerido operativamente; sin default | CUIT de inicio de sesión; admite `cuit_inicio_sesion` como alias. Si falta o está vacío, no se puede iniciar sesión. |
| `clave` | texto; requerida operativamente; sin default | Clave fiscal; alias compatibles `clave_representante` y `contrasena`. Si todas las formas faltan o están vacías, no hay acceso. |
| `clave_encriptada` | texto cifrado; alternativa opcional | Debe estar cifrada con el mecanismo de la API. `null` o vacío equivale a ausencia; cifrado inválido se rechaza. Si se envía también clave en texto, deben coincidir. |

No envíe credenciales en `payload`; los alias de contraseña no deben discrepar.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "despachos": ["DESPACHO-DEMO-0001", "DESPACHO-DEMO-0002"],
    "tipo_agente": "IMEX-IMEX-IMPORTADOR/EXPORT.",
    "rol": "IMEX-Rol Importador Exportador",
    "metodo": "url",
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

Los identificadores son sintácticamente ilustrativos; para obtener datos, deben existir y ser visibles para la cuenta.

## Resultado y archivos

El resultado incluye `despachos_procesados`, `despachos_exitosos`, `despachos_con_error`, `errores`, `datos` y un resumen `csv` con filas y nombre, además de información de tamaño/huella si se adjuntó. Cada fila CSV aplana datos de carátula y estado de presentación. El CSV se adjunta solo cuando `subir_csv` es `true`.

Los errores de un despacho se registran y no impiden procesar los siguientes. El estado global puede ser `OK` aun cuando `despachos_con_error` sea mayor que cero; compruebe los contadores y `errores`. Con `incluir_json: false`, la lista `datos` se omite aunque el CSV y los contadores sigan reflejando el procesamiento.