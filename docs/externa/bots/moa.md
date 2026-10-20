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
| `subir_csv` | booleano; opcional; `true` | Adjunta el CSV consolidado en `files[]`. `false` no adjunta el archivo. `null` se rechaza. |

Debe estar activo `incluir_json` o `subir_csv`; si ambos son `false`, se rechaza. Los campos desconocidos se rechazan.

## Credenciales

Se requieren credenciales fiscales válidas y autorización sobre el CUIT indicado.

| Campo en el sobre/`credentials` | Tipo; requisito; default | Efecto al enviarlo; omisión, `null`, vacío o `false` |
|---|---|---|
| `credentials` | objeto JSON; opcional; default `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. |
| `credentials.cuit_representante` | texto; requerido junto con una clave, salvo alias; sin default | Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. |
| `credentials.cuit_inicio_sesion` | texto; alias opcional de `cuit_representante`; sin default | Aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. |
| `credentials.clave` | texto; requerida junto con una identidad, salvo alias o alternativa cifrada; sin default | Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. |
| `credentials.clave_representante` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.contrasena` | texto; alias opcional de `clave`; sin default | Aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. |
| `credentials.clave_encriptada` | texto Base64 cifrado; alternativa opcional; sin default | Se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. |

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