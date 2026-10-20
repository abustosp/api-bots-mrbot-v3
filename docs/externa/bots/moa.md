# MOA

## Propósito y operación

Consulta información de despachos aduaneros que la cuenta puede abrir para un representado en el servicio MOA. Procesa los identificadores recibidos individualmente y devuelve sus datos de carátula y estado de presentación. No presenta ni modifica despachos.

Ruta V3: `POST /api/v3/bots/moa/consultar`. Alias compatible: `POST /api/v3/moa/consulta`. Envíe `{"payload": {...}, "credentials": {...}}`; la operación se fija en la ruta. El trabajo se acepta con `202` y se sigue en `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT del titular del árbol MOA; alias `cuit`. Se quitan espacios, guiones, puntos y `/` y debe quedar en 11 dígitos. Omitido, `null`, vacío o inválido: rechazo. | `"30876543210"` |
| `despachos` | lista de textos | Sí | ninguno | Identificadores a consultar, de 1 a 100 elementos. Los elementos se recortan y se convierten a texto; cada uno debe quedar no vacío y medir hasta 64 caracteres. Los duplicados se eliminan conservando el primer orden. Omitida, `null`, lista vacía, valor vacío o más de 100 elementos: rechazo. | `["DESPACHO-DEMO-0001", "DESPACHO-DEMO-0002"]` |
| `tipo_agente` | texto | No | `"IMEX-IMEX-IMPORTADOR/EXPORT."` | Perfil de agente con el que se navega el árbol, de 1 a 128 caracteres. Omitido: usa el valor indicado. `null`, vacío o más de 128 caracteres: rechazo. | `"IMEX-IMEX-IMPORTADOR/EXPORT."` |
| `rol` | texto | No | `"IMEX-Rol Importador Exportador"` | Rol para abrir el árbol de empresa, de 1 a 128 caracteres. Omitido: usa el valor indicado. `null`, vacío o más de 128 caracteres: rechazo. | `"IMEX-Rol Importador Exportador"` |
| `metodo` | texto | No | `"url"` | Forma de abrir cada despacho; admite `url` o `form`. Omitido: usa `url`. `null`, cadena vacía u otro valor: rechazo. | `"url"` |
| `incluir_json` | booleano | No | `true` | Incluye registros detallados por despacho en `data.datos`. `false` vacía esa lista; los contadores, errores y resumen CSV se mantienen. Omitido equivale a `true`; `null` se rechaza. | `true` |
| `subir_csv` | booleano | No | `true` | Adjunta el CSV consolidado en `files[]`. `false` no adjunta el archivo. Omitido equivale a `true`; `null` se rechaza. | `true` |

Debe estar activo `incluir_json` o `subir_csv`; si ambos son `false`, se rechaza. Los campos desconocidos se rechazan.

## Credenciales

Se requieren credenciales fiscales válidas y autorización sobre el CUIT indicado.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `sobre` | `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Requerido junto con una clave, salvo alias. Identifica la cuenta que inicia sesión. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Requerida junto con una identidad, salvo alias o alternativa cifrada. Clave fiscal en texto. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64 que se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkUtRklDVElDSUEtRkFDVElCSUxFLU5PLVVUSUxJWkFCTEU="` |

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

## Resultado y archivos por operación

`result` es `OK` si termina el recorrido de despachos, incluso si alguno falla. Los fallos individuales se cuentan y se incluyen en `data`; no convierten por sí solos el estado global en `PARCIAL`.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al devolver un resultado | `OK` cuando finaliza el recorrido. | `"OK"` |
| `data.operacion`, `data.ejecucion`, `data.metodo`, `data.representado_cuit` | textos | Cuando se devuelve `data` | Operación, fecha y hora de ejecución, método seleccionado y CUIT consultado. | `{"operacion":"consultar","ejecucion":"2026-10-20T16:00:00+00:00","metodo":"url","representado_cuit":"30876543210"}` |
| `data.despachos_procesados`, `data.despachos_exitosos`, `data.despachos_con_error` | enteros | Cuando se devuelve `data` | Cantidades de despachos procesados, correctos y fallidos. | `{"despachos_procesados":2,"despachos_exitosos":1,"despachos_con_error":1}` |
| `data.datos[]` | lista de objetos | Siempre dentro de `data`; con `incluir_json: false` queda como `[]` | Con JSON habilitado, contiene un registro por despacho. Un registro correcto tiene `despacho`, `metodo`, `timestamp`, `datos_caratula` y `estado_presentacion`, objetos de pares texto-texto, y `error: null`. Un registro fallido conserva esos objetos como `{}` y agrega `error` con el diagnóstico. | `[{"despacho":"DESPACHO-DEMO-0001","metodo":"url","timestamp":"2026-10-20T16:00:00+00:00","datos_caratula":{"Aduana":"001"},"estado_presentacion":{"Estado":"Presentado"},"error":null}]` |
| `data.errores[]` | lista de objetos | Siempre dentro de `data`; queda como `[]` sin fallos | Cada error individual incluye `despacho`, `diagnostico` y `diagnostic_code`, que puede ser `null`. Con `incluir_json: false`, esta lista se conserva aunque `data.datos` sea `[]`. | `[{"despacho":"DESPACHO-DEMO-0002","diagnostico":"No disponible","diagnostic_code":"moa_dispatch_not_found"}]` |
| `data.csv` | objeto | Cuando se devuelve `data` | Resumen del CSV consolidado, con `filas` y `archivo`. `sha256` y `size_bytes` solo aparecen si `subir_csv: true`. El CSV aplana los datos de carátula y estado en columnas. | `{"filas":2,"archivo":"MOA-30876543210.csv","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","size_bytes":1234}` |
| `files[]` | lista de archivos | Solo si el CSV se publica; puede quedar vacía | Incluye el CSV consolidado cuando `subir_csv: true`. Con `false`, el resumen `data.csv` permanece, pero no se adjunta. | `[{"name":"MOA-30876543210.csv","url":"","size":1234,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"MOA-30876543210.csv","content_type":"text/csv","size_bytes":1234,"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

Con `incluir_json: false`, `data.datos` no se omite ni se vuelve `null`: queda como lista vacía. Los contadores, `data.errores` y el CSV siguen describiendo el recorrido; pueden incluirse errores aunque `result` sea `OK`.
