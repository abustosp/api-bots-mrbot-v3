# Portal IVA

**Propósito.** Consultar libros de IVA de ventas/compras de un período, importar TXT preparados o hacer ambas cosas. La descarga también puede actualizar datos del portal y borrar registros; no es necesariamente una consulta sin cambios.

## Operación y ruta V3

Catálogo: `portal_iva` con `descargar`, `importar` y `gestionar`.

- Ruta canónica: `POST /api/v3/bots/portal_iva/{operacion}`.
- Alias V3: `POST /api/v3/portal_iva/consulta` equivale solo a `descargar`.
- No hay alias equivalente para `importar` ni `gestionar`; usar la ruta canónica.

La ruta canónica recibe `{ "payload": { ... }, "credentials": { ... } }`; el alias de compatibilidad recibe un cuerpo plano. No mezclar ambas formas. La solicitud aceptada devuelve `job_id`; consultar su estado y resultado en `/api/v3/jobs/{job_id}`. Los nombres de archivo de entrada deben referenciar cargas previas mediante `object_key`. Convenciones compartidas de credenciales: [API pública](../api.md).

## Credenciales fiscales

El acceso requiere clave fiscal y CUIT representante para iniciar sesión. Enviar `credentials.clave` y `credentials.cuit_representante`. El contrato también acepta `clave_representante` o `contrasena` como nombres alternativos para la clave, siempre que los nombres de clave enviados coincidan; `clave_encriptada` es una alternativa cifrada Base64. `cuit_inicio_sesion` equivale a `cuit_representante`. Los secretos enviados en `payload` se trasladan al apartado de credenciales; no deben mezclarse valores distintos. La omisión, `null` o cadena vacía de todas las claves no proporciona acceso: el trabajo no podrá ejecutarse. Un CUIT vacío/null se omite; para la sesión debe informarse uno válido.

## Campos del payload

Tipos y valores siguientes reflejan validación efectiva. Campos de descarga son aplicables a `descargar` y `gestionar`; archivos de entrada a `importar` y `gestionar`; las opciones de preparación se aceptan en las tres operaciones.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `periodo` | texto | Sí | ninguno | Acepta `MM/AAAA`, `MM-AAAA`, `AAAA/MM`, `AAAA-MM` o `AAAAMM`; se normaliza a `AAAAMM`, mes 01–12. `null` o vacío inválidos. | `"05/2026"` |
| `representado_cuit` | texto | No | `null` | CUIT de 11 dígitos; admite espacios, guiones, puntos y `/`, que se quitan. Alias: `cuit`. Omitir o `null` usa el CUIT representante. Vacío inválido. | `"20-12345678-6"` |
| `representado_nombre` | texto | No | `null` | Nombre opcional, máximo 128 caracteres. `null` u omitir no agrega nombre; cadena vacía no cumple mínimo de 1. | `"Comercial Ficticia SA"` |
| `descarga_ventas` | booleano | No | `false` | `true` solicita libro ventas; `false` no lo descarga. Alias: `descarga_CSV_ventas`. Omitir equivale a `false`; `null` inválido. | `true` |
| `descarga_compras` | booleano | No | `false` | `true` solicita libro compras; `false` no lo descarga. Alias: `descarga_CSV_compras`. Omitir equivale a `false`; `null` inválido. Debe activarse ventas y/o compras. | `false` |
| `incluir_json` | booleano | No | `true` | Incluye resúmenes y muestras de filas en el resultado. `false` los omite; no afecta la consulta. Omitir equivale a `true`; `null` inválido. | `true` |
| `subir_csv` | booleano | No | `true` | Solicita que los CSV queden disponibles como archivos de resultado. `false` no los publica; omitir equivale a `true`; `null` inválido. Envíe este nombre canónico para controlar la publicación. | `true` |
| `refrescar_desde_arca` | booleano | No | `true` | Antes de cada descarga solicita importar datos desde el organismo fiscal al libro. `false` evita esa actualización. Omitir activa la actualización; `null` inválido. | `false` |
| `limpiar_despues` | booleano | No | `true` | Después de cada descarga intenta borrar los registros del libro. `false` evita ese borrado. Omitir lo activa; `null` inválido. | `true` |
| `descartar_borrador` | booleano | No | `true` | Tras completar las descargas intenta descartar el borrador de declaración. `false` evita el intento. Omitir lo activa; `null` inválido. | `false` |
| `operaciones_ng_o_e` | booleano | No | `false` | Marca la opción de operaciones no gravadas/exentas al preparar el período. `false` la desmarca; omitir equivale a `false`; `null` inválido. | `false` |
| `prorrateo_global` | booleano | No | `false` | Selecciona opción de prorrateo global cuando corresponde. `false` no la selecciona; `null` inválido. | `false` |
| `prorrateo_asignacion_directa` | booleano | No | `false` | Selecciona prorrateo de asignación directa cuando corresponde. `false` no lo selecciona; `null` inválido. | `false` |
| `prorrateo_ambos` | booleano | No | `false` | Selecciona ambas opciones de prorrateo cuando corresponde. `false` no las selecciona; `null` inválido. Si varias opciones de prorrateo son `true`, el portal recibe la prioridad «ambos», luego global y luego directa. | `false` |
| `importacion_definitiva_bienes` | booleano | No | `false` | Marca la opción declarativa correspondiente; `false` la desmarca; `null` inválido. | `false` |
| `importacion_servicios` | booleano | No | `false` | Marca importación de servicios; `false` la desmarca; `null` inválido. | `false` |
| `regimen_turiva` | booleano | No | `false` | Marca el régimen TURIVA; `false` la desmarca; `null` inválido. | `false` |
| `bienes_usados` | booleano | No | `false` | Marca bienes usados/material reciclable; `false` la desmarca; `null` inválido. | `false` |
| `ninguna_anteriores` | booleano | No | `true` | Marca «ninguna de las anteriores» salvo que se indique `false`; `null` inválido. | `true` |
| `periodos_seleccionables` | lista de objetos | No | `[]` | Objetos `{string:string}`. Se acepta, pero no determina el período consultado. El portal devuelve su propia lista al preparar el período y puede reemplazar este valor. `null` inválido. | `[]` |
| `ventas_txt_key` | texto | No | ninguno | Referencia a TXT ya cargado; solo aplicable a `importar` y `gestionar`. `null` se interpreta como ausencia; vacío inválido. Debe enviarse junto con `ventas_alicuotas_txt_key`. | `"uploads/demo/ventas.txt"` |
| `ventas_alicuotas_txt_key` | texto | No | ninguno | Referencia al TXT de alícuotas de ventas; solo aplicable a `importar` y `gestionar`. `null` equivale a ausencia; vacío inválido. Se exige junto con `ventas_txt_key`. | `"uploads/demo/ventas-alicuotas.txt"` |
| `compras_txt_key` | texto | No | ninguno | Referencia a TXT ya cargado; solo aplicable a `importar` y `gestionar`. `null` equivale a ausencia; vacío inválido. Debe enviarse junto con `compras_alicuotas_txt_key`. | `"uploads/demo/compras.txt"` |
| `compras_alicuotas_txt_key` | texto | No | ninguno | Referencia al TXT de alícuotas de compras; solo aplicable a `importar` y `gestionar`. `null` equivale a ausencia; vacío inválido. Se exige junto con `compras_txt_key`. | `"uploads/demo/compras-alicuotas.txt"` |

Cada referencia de archivo debe provenir de `POST /api/v3/uploads`. El contenido debe ser un TXT existente, no vacío, menor o igual a 5 MB. No enviar el contenido del archivo dentro del JSON ni usar los nombres locales terminados en `_path`.

Ejemplo ficticio mínimo para `POST /api/v3/bots/portal_iva/importar`; las dos claves deben reemplazarse por referencias efectivamente obtenidas al cargar archivos:

```json
{"payload":{"periodo":"05/2026","representado_cuit":"20-12345678-6","ventas_txt_key":"uploads/demo/ventas.txt","ventas_alicuotas_txt_key":"uploads/demo/ventas-alicuotas.txt"},"credentials":{"cuit_representante":"20123456786","clave":"CLAVE_FISCAL_FICTICIA"}}
```

Para `POST /api/v3/bots/portal_iva/gestionar`, envíe por lo menos un par TXT como arriba, active al menos una descarga (`descarga_ventas` o `descarga_compras`) y una salida. Para `POST /api/v3/bots/portal_iva/descargar`, no envíe claves TXT y active uno o ambos libros.

### Restricciones por operación

- `descargar`: activar ventas y/o compras, y dejar al menos una salida (`incluir_json=true` o `subir_csv=true`).
- `importar`: se requiere por lo menos el par TXT de ventas o el par TXT de compras. Importar reemplaza los registros existentes del libro.
- `gestionar`: aplica las reglas de `descargar` y de `importar`; primero importa los TXT proporcionados y después descarga los libros elegidos.
- Los flags de opciones declarativas se envían al preparar y guardar el período antes de importar/descargar.

## Ejemplo ficticio válido: `POST /api/v3/bots/portal_iva/descargar`

```json
{
  "payload": {
    "periodo": "05/2026",
    "representado_cuit": "20-12345678-6",
    "representado_nombre": "Comercial Ficticia SA",
    "descarga_ventas": true,
    "descarga_compras": false,
    "incluir_json": true,
    "subir_csv": true,
    "refrescar_desde_arca": false,
    "limpiar_despues": false,
    "descartar_borrador": false,
    "operaciones_ng_o_e": false,
    "prorrateo_global": false,
    "prorrateo_asignacion_directa": false,
    "prorrateo_ambos": false,
    "importacion_definitiva_bienes": false,
    "importacion_servicios": false,
    "regimen_turiva": false,
    "bienes_usados": false,
    "ninguna_anteriores": true,
    "periodos_seleccionables": []
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado, archivos y efectos

Las tablas describen los campos de la respuesta pública del trabajo. En un resultado `ERROR`, `data` puede ser `null`. Los ejemplos son valores JSON ficticios, ilustrativos y no ejecutados. `result` toma `OK` al completar la operación y `ERROR` ante un fallo; esta operación no devuelve `PARCIAL`.

### Campos comunes

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | Estado global de la operación. | `"OK"` |
| `data.operacion` | texto | Siempre que se produzca `data`. | `descargar`, `importar` o `gestionar`. | `"gestionar"` |
| `data.representado_cuit` | texto | Siempre que se produzca `data`. | CUIT normalizado consultado o gestionado. | `"20123456786"` |
| `data.periodo` | texto | Siempre que se produzca `data`. | Período normalizado `AAAAMM`. | `"202605"` |

### `descargar` y la fase de descarga de `gestionar`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.periodos_seleccionables` | lista de objetos | Si la preparación devuelve una lista no vacía. Si devuelve `[]`, la propiedad se omite, no se serializa como `null`. | Opciones devueltas durante la preparación. Las claves internas de cada objeto dependen de la respuesta del portal y no tienen nombres estables documentados. | `[{"campo-ilustrativo":"valor-ficticio"}]` |
| `data.ventas` | objeto | Si se solicitó descarga de ventas y esta terminó. | Resumen del CSV de ventas. | `{"filas":12,"archivo":"ventas-ejemplo.csv","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":1200,"muestra":[]}` |
| `data.compras` | objeto | Si se solicitó descarga de compras y esta terminó. | Resumen del CSV de compras, con la misma estructura que `data.ventas`. | `{"filas":0,"archivo":"compras-ejemplo.csv","muestra":[]}` |
| `data.ventas.filas`, `data.compras.filas` | entero | Dentro del resumen de cada libro descargado. | Cantidad de filas de datos, sin contar la cabecera. | `12` |
| `data.ventas.archivo`, `data.compras.archivo` | texto | Dentro del resumen de cada libro descargado. | Nombre del CSV procesado. Se informa aunque `subir_csv=false`. | `"ventas-ejemplo.csv"` |
| `data.ventas.sha256`, `data.compras.sha256` | texto | Solo si `subir_csv=true` y se publicó el CSV. Si no se publica, la propiedad se omite. | Huella del archivo publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.ventas.size_bytes`, `data.compras.size_bytes` | entero | Solo si `subir_csv=true` y se publicó el CSV. Si no se publica, la propiedad se omite. | Tamaño del archivo publicado, en bytes. | `1200` |
| `data.ventas.muestra`, `data.compras.muestra` | lista de objetos | Solo con `incluir_json=true`; puede ser `[]` si el CSV no contiene filas. | Hasta cinco filas. Los nombres de columna corresponden a la cabecera recibida y pueden variar. | `[{"columna-ilustrativa":"valor-ficticio"}]` |
| `data.avisos` | lista de textos | Solo si falla el intento de descartar el borrador tras completarse las descargas. | Aviso incluido dentro de `data`; si no hay avisos, la propiedad se omite en lugar de devolver `[]`. | `["aviso-ficticio-sobre-borrador"]` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Incluye los CSV publicados; es `[]` si `subir_csv=false` o no se generó un archivo. | Archivos descargables del trabajo, separados de `data`. | `[{"name":"ventas-ejemplo.csv","url":"","size":1200,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"ventas-ejemplo.csv","content_type":"text/csv","size_bytes":1200,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":"/api/v3/jobs/{job_id}/artifacts/{artifact_id}/download","url_expires_in":null}]` |

Si un resumen se genera correctamente con `incluir_json=false`, `muestra` se omite. Con `subir_csv=false`, el nombre `archivo` permanece en `data`, mientras `sha256`, `size_bytes` y el adjunto `files` se omiten o quedan vacíos. La descarga conserva los efectos configurados en la entrada, incluidos la actualización desde el organismo y la limpieza posterior.

### `importar` y fase de importación de `gestionar`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.importaciones` | lista de objetos | Siempre en `importar` y `gestionar` cuando se produce `data`. Contiene una entrada por cada libro importado. | Registra el libro y el valor booleano informado por el servicio tras la importación. | `[{"libro":"ventas","ok":true}]` |
| `data.importaciones[].libro` | texto | En cada elemento de `data.importaciones`. | `ventas` o `compras`. | `"ventas"` |
| `data.importaciones[].ok` | booleano | En cada elemento de `data.importaciones`. | Resultado booleano devuelto por el servicio para ese libro. | `true` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. En `importar` es `[]`; `gestionar` puede incluir los CSV que publique su fase de descarga. | Archivos descargables. | `[]` |

`gestionar` combina los resúmenes de descarga con `data.importaciones`. Cada importación reemplaza los registros existentes del libro correspondiente. Si una fase falla después de haber cambiado el portal, la respuesta puede ser `ERROR` sin un resultado `PARCIAL` que describa lo completado anteriormente.
