# Comprobantes

Consulta comprobantes emitidos/recibidos del contribuyente representado, solicita al portal que genere una consulta asíncrona y descarga consultas ya disponibles en el historial. **Consultar** y **historial** son lecturas. **Solicitar** registra una nueva solicitud en el portal: es un efecto externo y no conviene repetirlo a ciegas.

## Ruta V3 y catálogo

Ruta canónica de este bot: `POST /api/v3/bots/comprobantes/{operacion}`. El catálogo publica `consultar`, `solicitar` e `historial`, y marca `comprobantes` como **deprecated** con `alias_de: mis_comprobantes`. Es una anotación de catálogo, no convierte ambas entradas en una sola operación: `mis_comprobantes` tiene su propio registro y contrato. La operación va en la ruta, no dentro de `payload`.

La ruta canónica valida con las reglas efectivas de la operación: la CUIT representada se normaliza quitando espacios, guiones, puntos y barras, y luego debe quedar en 11 dígitos. No se valida el dígito verificador. Los aliases HTTP planos tienen un modelo de entrada distinto; no aplicar sus restricciones a esta ruta.

Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. La identidad de sesión y la contraseña fiscal pertenecen a `credentials`.

## Campos de `payload`

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT del contribuyente consultado. Alias: `cuit`. Se quitan espacios, guiones, puntos y barras y deben quedar 11 dígitos. No se valida el dígito verificador. Omitido, `null` o vacío: rechazo. | `"20123456786"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre que se selecciona y valida en el portal. Longitud de 1 a 128 caracteres. Vacío o `null` inválidos. | `"Comercial Ejemplo SRL"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha `DD/MM/AAAA`. Alias: `desde`. Rango inclusivo y debe ser menor o igual a `fecha_hasta`. Fecha inválida, vacía o nula se rechaza. | `"01/09/2026"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha `DD/MM/AAAA`. Alias: `hasta`. Rango inclusivo y debe ser mayor o igual a `fecha_desde`. Fecha inválida, vacía o nula se rechaza. | `"30/09/2026"` |
| `emitidos` | booleano | No | `false` | Incluye comprobantes emitidos. Alias: `descarga_emitidos`. `false` omite ese tipo. Debe ser `true` al menos uno de `emitidos` o `recibidos`. Omitido aplica `false`; `null` explícito se rechaza. Debe ser booleano JSON, no texto como `"false"`. | `true` |
| `recibidos` | booleano | No | `false` | Incluye comprobantes recibidos. Alias: `descarga_recibidos`. `false` omite ese tipo. Debe ser `true` al menos uno de `emitidos` o `recibidos`. Omitido aplica `false`; `null` explícito se rechaza. Debe ser booleano JSON, no texto como `"false"`. | `true` |
| `puntos_venta_emitidos` | lista de textos | No | `ninguno` | Puntos de venta emitidos, hasta 200 elementos. Cada elemento puede ser texto o entero, se conservan ceros iniciales y debe contener solo dígitos. Omitido, `null` o lista vacía significa sin filtro. Debe enviarse una lista, no un valor escalar. | `["0001", "0007"]` |
| `puntos_venta_recibidos` | lista de textos | No | `ninguno` | Puntos de venta recibidos, hasta 200 elementos. Cada elemento puede ser texto o entero, se conservan ceros iniciales y debe contener solo dígitos. Omitido, `null` o lista vacía significa sin filtro. Debe enviarse una lista, no un valor escalar. | `["0002"]` |
| `timeout_mc` | entero | No | `30` | Controla la espera del servicio, rango 1 a 86400. Omitido o `null` usa el valor efectivo de 30 segundos. Cero y otros valores fuera del rango se rechazan. | `30` |
| `incluir_json` | booleano | No | `true` | `true` incluye la muestra de filas. Alias: `carga_json`. `false` omite la muestra de filas, no el resumen. No forma parte del modelo de `solicitar`. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `subir_csv` | booleano | No | `true` | `true` publica el CSV en `files[]`. `false` no publica CSV en `files[]`, pero el archivo aún se procesa. No forma parte del modelo de `solicitar`. Omitido aplica `true`; `null` explícito se rechaza. | `true` |
| `carga_b64` | booleano | No | `false` | Obsoleto. Se acepta y se ignora: los archivos se entregan como archivos, nunca como base64. `null` explícito se rechaza. | `false` |
| `eliminar_descargas` | booleano | No | `true` | Se elimina antes de procesar la operación, por lo que no tiene efecto. En la ruta canónica también se ignoran `null` y valores no booleanos. | `true` |
| `proxy_request` | booleano | No | `null` | Aceptado por compatibilidad y sin efecto de selección de conexión. `null` es válido. | `false` |
| `verbose` | booleano | No | `false` | Aceptado sin cambiar el resultado. `null` explícito se rechaza. | `false` |
| `cookies_header` | texto | No | `null` | Solo para `historial`. Campo legado sin efecto de autenticación, máximo 8192 caracteres. Omitido o `null` no modifica la sesión; cadena vacía se acepta sin efecto. | `"sesion=ficticia"` |

Para `solicitar`, `incluir_json` y `subir_csv` no forman parte del modelo. `cookies_header` solo corresponde a `historial`. La validación rechaza campos desconocidos y requiere `emitidos=true` o `recibidos=true`. Los valores booleanos deben ser booleanos, no texto como `"false"`. En la ruta canónica se aplican estas reglas del bot; el alias HTTP plano tiene un DTO separado y puede tratar `null` de forma distinta.

## Credenciales

El objeto `credentials` puede omitirse en el envelope, pero se necesita una sesión fiscal para completar la operación. La identidad de sesión y la contraseña no tienen default; su falta normalmente causa un fallo de autenticación. La normalización central de credenciales no exige un patrón numérico para la identidad.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Condicional: se necesita una identidad para completar la operación, porque `credentials` puede omitirse en el envelope pero sin identidad falla la autenticación. Alias: `cuit_inicio_sesion`. Si se envían ambos, deben coincidir. Omitido, `null` o `""` dejan sin identidad. `false` y otros tipos no textuales se rechazan. La normalización central no exige patrón numérico. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | Condicional | ninguno | Condicional: alias de `cuit_representante` con la misma necesidad de identidad. Si se envían ambos, deben coincidir. Omitido, `null` o `""` dejan sin identidad. `false` y otros tipos no textuales se rechazan. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Condicional: se necesita una contraseña salvo que se aporte `clave_encriptada`. Aliases: `clave_representante`, `contrasena`. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. Si se envían varios aliases, deben coincidir. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_representante` | texto | Condicional | ninguno | Condicional: alias de `clave` con la misma necesidad de contraseña. Si se envían varios aliases, deben coincidir. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `contrasena` | texto | Condicional | ninguno | Condicional: alias de `clave` con la misma necesidad de contraseña. Si se envían varios aliases, deben coincidir. Omitir, `null` o `""` equivale a no aportar esa alternativa. `false` y otros tipos no textuales se rechazan. | `"CLAVE_FISCAL_FICTICIA"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada a la contraseña en texto claro. Omitido, `null` o `""` se ignora. `false` y otros tipos no textuales se rechazan. Si se informa, debe poder descifrarse; si también hay texto claro, ambos deben coincidir. | `"CADENA_CIFRADA_FICTICIA"` |

No incluir credenciales reales en ejemplos.

## Ejemplos ficticios

Consultar emitidos y recibidos, ruta `POST /api/v3/bots/comprobantes/consultar`:

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Comercial Ejemplo SRL",
    "fecha_desde": "01/09/2026",
    "fecha_hasta": "30/09/2026",
    "emitidos": true,
    "recibidos": true,
    "puntos_venta_emitidos": ["0001", "0007"],
    "puntos_venta_recibidos": [],
    "incluir_json": true,
    "subir_csv": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

Para solicitar comprobantes emitidos, use esta ruta y un cuerpo mínimo compatible con `solicitar`:

Ruta: `POST /api/v3/bots/comprobantes/solicitar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "representado_nombre": "Comercial Ejemplo SRL",
    "fecha_desde": "01/09/2026",
    "fecha_hasta": "30/09/2026",
    "emitidos": true,
    "recibidos": false
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

La operación crea la solicitud en el portal, no un CSV. Para consultar o ver el historial se usan, respectivamente, `/api/v3/bots/comprobantes/consultar` y `/api/v3/bots/comprobantes/historial`; sus campos específicos se documentan por separado en este contrato.

## Resultado y archivos

Las tablas describen el resultado público del trabajo. Los ejemplos son JSON ficticio, ilustrativo y no ejecutado. En un resultado `ERROR`, `data` puede ser `null`. Los campos de muestra y los registros históricos contienen datos variables.

### Campos comunes

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Siempre que el trabajo tenga resultado final. | `OK` al completar una consulta o solicitud. En `historial`, es `PARCIAL` si faltan algunos tipos y `ERROR` si faltan todos los tipos pedidos. | `"PARCIAL"` |
| `data.operacion` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | Operación ejecutada. | `"consultar"` |
| `data.representado_cuit` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | CUIT normalizado del representado. | `"20123456786"` |
| `data.fecha_desde`, `data.fecha_hasta` | texto | En resultados públicos `OK` o `PARCIAL` con `data`. | Extremos inclusivos del rango, en formato `DD/MM/AAAA`. | `"01/09/2026"` |
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
| `data.emitidos.muestra`, `data.recibidos.muestra` | lista de objetos | Si `incluir_json=true`; puede ser `[]`. Si `incluir_json=false`, se omite. | Hasta las filas configuradas de muestra. Los campos derivan de la planilla y pueden variar. | `[{"MC":"MCE","CUIT Cliente":"20123456786","MC AUX":"valor-ficticio"}]` |
| `files` | lista de archivos | Siempre. Contiene los CSV publicados; queda `[]` si `subir_csv=false`. | Archivos adjuntos disponibles en el resultado del trabajo. | `[{"name":"comprobantes-ejemplo.csv","url":"","size":120,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"comprobantes-ejemplo.csv","content_type":"text/csv","size_bytes":120,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":null,"url_expires_in":null}]` |

### `historial`

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `data.consultas` | objeto | Siempre en un resultado público `OK` o `PARCIAL` con `data`. | Contiene una lista por cada tipo seleccionado. La lista puede estar vacía. | `{"emitidos":[]}` |
| `data.consultas.emitidos`, `data.consultas.recibidos` | lista de objetos | Por cada tipo seleccionado, aunque no haya registros. | Registros visibles en el historial del tipo. | `[{"indice":0,"estado":"Finalizada","exportable":true,"texto_completo":"Rango y estado ficticios","fecha_desde":"01/09/2026","fecha_hasta":"30/09/2026"}]` |
| `data.consultas.<tipo>[].fecha_desde`, `data.consultas.<tipo>[].fecha_hasta` | texto | Solo si el registro del historial permite reconocer ambas fechas. Si no se reconocen, ambas propiedades se omiten. | Rango que se muestra en el registro histórico. | `"01/09/2026"` |
| `data.faltantes` | lista de textos | Siempre en `historial` cuando `data` está disponible. Puede ser `[]`. | Tipos solicitados cuyo resultado no se pudo descargar del historial. | `["recibidos"]` |
| `data.emitidos`, `data.recibidos` | objeto | Solo para un tipo cuyo CSV se descargó. Si no hay descarga, el resumen se omite, aunque exista `data.consultas.<tipo>`. | Tiene los mismos campos `filas`, `archivo` y los campos opcionales `sha256`, `size_bytes`, `muestra` de `consultar`. | `{"filas":1,"archivo":"comprobantes-ejemplo.csv","muestra":[]}` |
| `files` | lista de archivos | Siempre. Puede incluir el CSV descargado para cada tipo disponible; si no se subió ninguno, es `[]`. | Adjuntos del historial, no solicitudes nuevas. | `[{"name":"comprobantes-ejemplo.csv","url":"","size":120,"artifact_id":"0190f0c0-7f5b-7b2e-9f7e-123456789abd","filename":"comprobantes-ejemplo.csv","content_type":"text/csv","size_bytes":120,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","download_url":null,"url_expires_in":null}]` |

En `historial`, `faltantes=[]` corresponde a los tipos pedidos disponibles; algunos faltantes producen `PARCIAL` y todos los tipos pedidos faltantes producen `ERROR`. En un resultado `ERROR`, `data` puede ser `null`. `consultar` y `historial` omiten `sha256` y `size_bytes` si `subir_csv=false`, y omiten `muestra` si `incluir_json=false`. `solicitar` no devuelve identificadores de portal con un formato estable.
