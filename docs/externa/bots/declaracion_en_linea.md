# Declaración en línea

Consulta declaraciones juradas (DDJJ), VEP e información de nómina ya generados para un rango de períodos. Es una lectura: no presenta ni modifica declaraciones ni genera pagos.

## Ruta V3 y catálogo

`POST /api/v3/bots/declaracion_en_linea/consultar` (2 créditos). El alias de compatibilidad `/declaracion-en-linea/consulta` apunta a esta operación. Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. En la ruta V3, CUIT debe ser exactamente 11 dígitos sin separadores; aunque la normalización interna acepta separadores, el patrón público es estricto. Los períodos públicos son seis dígitos `AAAAMM`.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string 11 dígitos, opcional; aliases de compatibilidad `cuit`, `cuit_representado` | Si se omite, es nulo o vacío, se consulta el CUIT de la sesión. Si se informa, debe corresponder al contribuyente que se desea consultar. |
| `representado_nombre` | string máximo 128; default `""` | Nombre de referencia para los nombres de archivos; vacío/nulo queda vacío y no impide la consulta. |
| `periodo_desde`, `periodo_hasta` | strings requeridos, `AAAAMM`, mes `01`–`12` | Rango inclusivo. Si se envían invertidos, la validación los intercambia. Aunque algunos canales heredados normalizan otros formatos, el contrato público V3 requiere seis dígitos. El rango inclusivo está limitado internamente a 1200 períodos; si se pide uno más largo, se procesa solo el máximo de 1200 sin error explícito. |
| `incluir_json` | boolean, default `true` | `false` omite el arreglo de detalle por período, no el resumen ni los archivos. Nulo público aplica el default. |
| `subir_archivos` | boolean, default `true`; aliases `carga_minio`, `minio_upload`, `archivo_historico_minio` | `false` evita publicar PDFs/XLS/JSON en `files[]`, aunque se consultan y descargan durante la ejecución. Nulo público aplica el default. |
| `proxy_request`, `proxy` | boolean/string legado, opcional | Se descartan/no permiten elegir la conexión. No cambian el resultado. |

Se requiere al menos una salida: `incluir_json=true` o `subir_archivos=true`. Campos desconocidos no reconocidos por compatibilidad se rechazan en la validación del bot.

## Credenciales

Se requiere `credentials.cuit_representante` (exactamente 11 dígitos en la ruta pública) y `credentials.clave` para ingresar al servicio fiscal. Se aceptan `clave_representante` o `clave_encriptada` como credenciales heredadas/alternativa cifrada. `cuit_representante` también sirve como CUIT consultado si se omite `representado_cuit`. Sin credenciales válidas no se abre el servicio. No usar datos reales en ejemplos.

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

`result` contiene CUIT, período inicial/final, cantidad de períodos consultados y `periodos_sin_ddjj`. Si existe al menos una DDJJ en el rango, la operación informa resultados disponibles; si no existe ninguna, falla. La falta de DDJJ en algunos períodos se lista y no descarta resultados de otros períodos.

Con `incluir_json=true`, `detalle[]` puede incluir período, nombres DDJJ/VEP, estado de VEP, indicador pagado, cabecera, secciones y advertencias. Según lo que exista en el portal se publican en `files[]` PDF de DDJJ, PDF de VEP, XLS de nómina y JSON de DDJJ. Un archivo ausente aparece vacío/se omite, no se inventa. `subir_archivos=false` suprime `files[]`; `incluir_json=false` suprime detalle, no los archivos.
