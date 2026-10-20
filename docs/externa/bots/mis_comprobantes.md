# Mis Comprobantes

## Propósito y operaciones

Permite pedir una consulta de comprobantes emitidos y/o recibidos, descargar los CSV de esos comprobantes, o descargar resultados ya existentes en el historial. La operación `solicitar` inicia una consulta asíncrona en ARCA y no descarga los CSV en esa misma ejecución.

Rutas canónicas V3 publicadas por el catálogo: `POST /api/v3/bots/mis_comprobantes/consulta`, `/consultar`, `/solicitar` y `/historial`. **Discrepancia:** el catálogo anuncia `consulta`, pero la operación ejecutable correspondiente del bot es `consultar`; `/bots/mis_comprobantes/consulta` puede ser aceptada al crear el trabajo y luego rechazarse en la validación de ejecución. Use `/consultar`. Rutas compatibles: `POST /api/v3/mis_comprobantes/consulta` corresponde a `consultar`; `/solicitar_consulta` corresponde a `solicitar`; `/historial` corresponde a `historial`. En V3 la operación está en la URL. Envíe `{"payload": {...}, "credentials": {...}}`. La respuesta inicial es `202` con `job_id`; consulte `GET /api/v3/jobs/{job_id}`.


## Payload

Los campos marcados `consultar/historial` no se usan por `solicitar`; los campos marcados `todas` aplican a las tres operaciones.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de quien posee los comprobantes. Alias: `cuit`. Se quitan espacios, guiones, puntos y `/`, y se exigen 11 dígitos. Omitido, `null`, vacío o inválido: rechazo. | `"30876543210"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre del representado, usado al identificarlo y nombrar la salida. Longitud de 1 a 128 caracteres. Omitido, `null` o vacío: rechazo. El catálogo público lo presenta como opcional, pero la validación efectiva de las tres operaciones lo exige. | `"Comercio Ejemplo SA"` |
| `fecha_desde` | texto | Sí | ninguno | Inicio del rango inclusive, con formato `dd/mm/aaaa`. Alias: `desde`. Omitida, `null`, vacía o inválida: rechazo. | `"01/01/2025"` |
| `fecha_hasta` | texto | Sí | ninguno | Fin del rango inclusive, con formato `dd/mm/aaaa`, igual o posterior a `fecha_desde`. Alias: `hasta`. Omitida, `null`, vacía o inválida: rechazo. | `"31/01/2025"` |
| `emitidos` | booleano | No | `false` | Solicita comprobantes emitidos. Alias: `descarga_emitidos`. `false` excluye ese tipo; `null` se rechaza. | `true` |
| `recibidos` | booleano | No | `false` | Solicita comprobantes recibidos. Alias: `descarga_recibidos`. `false` excluye ese tipo; `null` se rechaza. Debe quedar `emitidos` o `recibidos` en `true`. | `false` |
| `puntos_venta_emitidos` | lista de textos | No | `null` | Filtro de puntos de venta para emitidos. Acepta enteros o textos; hasta 200 elementos; cada uno debe contener solo dígitos. Se conservan ceros iniciales de los textos. Omitido o `null`: sin filtro. Lista vacía: ningún filtro adicional. Texto vacío, elemento vacío o no numérico, más de 200 elementos o valor no listado: rechazo. | `["0001", "0002"]` |
| `puntos_venta_recibidos` | lista de textos | No | `null` | Filtro de puntos de venta para recibidos, con las mismas reglas que `puntos_venta_emitidos`. Omitido o `null`: sin filtro. Lista vacía: sin filtro. | `["0003"]` |
| `timeout_mc` | entero | No | `30` | Tiempo de espera de la descarga o consulta, en segundos, de 1 a 86.400. Omitido o `null`: 30 segundos. `0`, vacío, decimal o fuera del rango: rechazo. | `30` |
| `incluir_json` | booleano | No | `true` | En `consultar` y `historial` incluye una muestra de filas en el resultado. Alias: `carga_json`. En `solicitar` no aplica. `false` omite la muestra; `null` se rechaza. | `true` |
| `subir_csv` | booleano | No | `true` | Adjunta los CSV al trabajo en `consultar` y `historial`. `false` evita adjuntar, sin evitar la descarga o lectura. En `solicitar` no aplica. `null` se rechaza. | `true` |
| `cookies_header` | texto | No | `null` | Campo de compatibilidad, de hasta 8.192 caracteres, solo para `historial`. No se utiliza para autenticar ni altera la consulta. Omitido o `null`: sin efecto; `""` no aporta valor. | `""` |
| `carga_b64` | booleano | No | `false` | Campo legado aceptado y sin efecto; los archivos no se devuelven en Base64. Omitido equivale a `false`; `false` no cambia la respuesta; `null` se rechaza. | `false` |
| `proxy_request` | booleano | No | `null` | Se descarta en V3 y no controla el acceso ni la ruta de red; cualquier valor se ignora antes de la validación específica. | `false` |
| `eliminar_descargas` | booleano | No | `true` | Campo legado descartado; la retención temporal no se configura con este parámetro. `false`, `null` o `""` no cambia la retención efectiva. | `true` |
| `verbose` | booleano | No | `false` | Se acepta en la validación pero no cambia el resultado. Omitido equivale a `false`; `null` se rechaza. | `false` |

En `solicitar`, `incluir_json` y `subir_csv` no forman parte de sus opciones; su envío no debe usarse para cambiar la solicitud. Los tipos desconocidos de `payload` se rechazan salvo los campos de compatibilidad expresamente indicados.

## Credenciales

Se requieren credenciales fiscales válidas y acceso a Mis Comprobantes para la cuenta y el representado.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `credentials` | objeto | No | `{}` | Omitirlo usa `{}`. `{}` pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials.cuit_representante` | texto | Condicional | ninguno | Identifica la cuenta que inicia sesión. Se requiere junto con una clave, salvo que se use un alias. Omitido, `null` o `""` no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials.cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o `""` equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials.clave` | texto | Condicional | ninguno | Clave fiscal en texto. Se requiere junto con una identidad, salvo que se use un alias o la alternativa cifrada. Omitida, `null` o `""` no aporta clave; `false` se rechaza por tipo. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials.clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials.contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o `""` equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials.clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64; se descifra mediante el mecanismo admitido por la API. Omitida, `null` o `""` equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkVfRklDVElDSUFfTk9fVVNBUg=="` |

Los alias de credenciales con valores distintos se rechazan. No envíe secretos en `payload`.

## Ejemplos ficticios válidos

Los tres ejemplos son solicitudes completas e independientes. No copie los campos exclusivos de `consultar` o `historial` a `solicitar`.

### `consultar`

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "emitidos": true,
    "recibidos": false,
    "puntos_venta_emitidos": ["0001", "0002"],
    "timeout_mc": 30,
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

### `solicitar`

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "emitidos": true,
    "recibidos": false,
    "puntos_venta_emitidos": ["0001"],
    "timeout_mc": 30
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

### `historial`

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "representado_nombre": "Comercio Ejemplo SA",
    "fecha_desde": "01/01/2025",
    "fecha_hasta": "31/01/2025",
    "emitidos": true,
    "recibidos": false,
    "timeout_mc": 30,
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

Los CUIT son ficticios y cumplen el formato y dígito verificador. Para resultados reales deben tener relación y comprobantes en el portal.

## Resultado y archivos

Las tablas describen el resultado público del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. En un resultado `ERROR`, `data` puede ser `null`. Los campos de muestra y los registros históricos contienen datos variables.

### Campos comunes

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | `OK` al completar una consulta o solicitud. En `historial`, es `PARCIAL` si faltan algunos tipos y `ERROR` si faltan todos los tipos pedidos. | `"PARCIAL"` |
| `data.operacion` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | Operación ejecutada: `consultar`, `solicitar` o `historial`. | `"consultar"` |
| `data.representado_cuit` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | CUIT normalizado del representado. | `"30876543210"` |
| `data.fecha_desde`, `data.fecha_hasta` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | Extremos inclusivos del rango, en formato `DD/MM/AAAA`. | `"01/01/2025"` |
| `files` | lista de archivos | Siempre en la respuesta del trabajo. Vacía para `solicitar`; en `consultar` e `historial` contiene los CSV publicados, si los hubo. | Archivos descargables, separados de `data`. | `[]` |

### `solicitar`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.ids_consulta` | objeto | En una solicitud completada. | Mapea solo los tipos solicitados (`emitidos`, `recibidos`) a los identificadores devueltos. El formato del identificador depende del portal y no se garantiza un patrón estable. | `{"emitidos":"identificador-ficticio-no-estable"}` |
| `files` | lista de archivos | Siempre. | Esta operación registra la solicitud, pero no genera ni adjunta CSV. | `[]` |

### `consultar`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.emitidos`, `data.recibidos` | objeto | Por cada tipo seleccionado que se descargó correctamente. El tipo no seleccionado se omite, no se devuelve como `null`. | Resumen del CSV filtrado al rango solicitado. | `{"filas":1,"archivo":"comprobantes-ejemplo.csv","sha256":"0000000000000000000000000000000000000000000000000000000000000000","size_bytes":120,"muestra":[]}` |
| `data.emitidos.filas`, `data.recibidos.filas` | entero | Dentro del resumen del tipo descargado. | Cantidad de filas del archivo filtrado. | `1` |
| `data.emitidos.archivo`, `data.recibidos.archivo` | texto | Dentro del resumen del tipo descargado. | Nombre del CSV procesado. | `"comprobantes-ejemplo.csv"` |
| `data.emitidos.sha256`, `data.recibidos.sha256` | texto | Si `subir_csv=true` y se publicó el CSV; en caso contrario se omite. | Huella del archivo publicado. | `"0000000000000000000000000000000000000000000000000000000000000000"` |
| `data.emitidos.size_bytes`, `data.recibidos.size_bytes` | entero | Si `subir_csv=true` y se publicó el CSV; en caso contrario se omite. | Tamaño del archivo publicado, en bytes. | `120` |
| `data.emitidos.muestra`, `data.recibidos.muestra` | lista de objetos | Si `incluir_json=true`; puede ser `[]`. Si `incluir_json=false`, se omite. | Filas de muestra. Los campos derivan de la planilla y pueden variar. | `[{"MC":"MCE","CUIT Cliente":"30876543210","MC AUX":"valor-ficticio"}]` |
| `files` | lista de archivos | Siempre. Contiene los CSV publicados; queda `[]` si `subir_csv=false`. | Archivos adjuntos disponibles en el resultado del trabajo. | `[{"name":"comprobantes-ejemplo.csv","url":"","size":120,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"comprobantes-ejemplo.csv","content_type":"text/csv","size_bytes":120,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

### `historial`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.consultas` | objeto | Siempre en un resultado público `OK` o `PARCIAL` con `data`. | Contiene una lista por cada tipo seleccionado. La lista puede estar vacía. | `{"emitidos":[]}` |
| `data.consultas.emitidos`, `data.consultas.recibidos` | lista de objetos | Por cada tipo seleccionado, aunque no haya registros. | Registros visibles en el historial del tipo. Cada registro contiene `indice`, `estado`, `exportable` y `texto_completo`; incluye las fechas reconocidas cuando están presentes en el texto. | `[{"indice":0,"estado":"Finalizada","exportable":true,"texto_completo":"Rango y estado ficticios","fecha_desde":"01/01/2025","fecha_hasta":"31/01/2025"}]` |
| `data.consultas.<tipo>[].fecha_desde`, `data.consultas.<tipo>[].fecha_hasta` | texto | Solo si el registro del historial permite reconocer ambas fechas. Si no se reconocen, ambas propiedades se omiten. | Rango que se muestra en el registro histórico. | `"01/01/2025"` |
| `data.faltantes` | lista de textos | Siempre en `historial` cuando `data` está disponible. Puede ser `[]`. | Tipos solicitados cuyo resultado no se pudo descargar del historial. | `["recibidos"]` |
| `data.emitidos`, `data.recibidos` | objeto | Solo para un tipo cuyo CSV se descargó. Si no hay descarga, el resumen se omite, aunque exista `data.consultas.<tipo>`. | Tiene los mismos campos `filas`, `archivo` y los campos opcionales `sha256`, `size_bytes`, `muestra` de `consultar`. | `{"filas":1,"archivo":"comprobantes-ejemplo.csv","muestra":[]}` |
| `files` | lista de archivos | Siempre. Puede incluir el CSV descargado para cada tipo disponible; si no se subió ninguno, es `[]`. | Adjuntos del historial, no solicitudes nuevas. | `[{"name":"comprobantes-ejemplo.csv","url":"","size":120,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"comprobantes-ejemplo.csv","content_type":"text/csv","size_bytes":120,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":"/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download","url_expires_in":null}]` |

En `historial`, `faltantes=[]` corresponde a los tipos pedidos disponibles; algunos faltantes producen `PARCIAL` y todos los tipos pedidos faltantes producen `ERROR`. En un resultado `ERROR`, `data` puede ser `null`. `consultar` y `historial` omiten `sha256` y `size_bytes` si `subir_csv=false`, y omiten `muestra` si `incluir_json=false`. `solicitar` no devuelve identificadores de portal con un formato estable.
