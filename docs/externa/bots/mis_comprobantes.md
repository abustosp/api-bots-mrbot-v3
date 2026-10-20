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
| `proxy_request` | booleano | No | `null` | Se descarta en V3 y no controla el acceso ni la ruta de red; cualquier valor se ignora antes de la validación específica. | `null` |
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

| Operación | Claves de `data` y significado |
|---|---|
| `consultar` | `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta`; por cada tipo seleccionado (`emitidos` y/o `recibidos`), un objeto con `filas` y `archivo`, más `sha256` y `size_bytes` si se adjuntó y `muestra` si `incluir_json` está activo. |
| `solicitar` | `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta`, `ids_consulta` (objeto con el identificador de la solicitud por tipo pedido). No adjunta archivos. |
| `historial` | `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta`, `consultas` (resultados históricos por tipo), `faltantes` (lista de tipos sin resultado descargable) y, para cada tipo recuperado, el mismo resumen de filas/archivo opcional que en `consultar`. |

Los CSV se adjuntan en `files[]` cuando `subir_csv` está activo. `historial` no inicia solicitudes nuevas y puede finalizar `PARCIAL` si faltan algunos resultados o `ERROR` si faltan todos los tipos pedidos. Un fallo de acceso o descarga también puede hacer fallar el trabajo.