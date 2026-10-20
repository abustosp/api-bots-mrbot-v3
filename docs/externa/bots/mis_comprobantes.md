# Mis Comprobantes

## Propósito y operaciones

Permite pedir una consulta de comprobantes emitidos y/o recibidos, descargar los CSV de esos comprobantes, o descargar resultados ya existentes en el historial. La operación `solicitar` inicia una consulta asíncrona en ARCA y no descarga los CSV en esa misma ejecución.

Rutas canónicas V3 publicadas por el catálogo: `POST /api/v3/bots/mis_comprobantes/consulta`, `/consultar`, `/solicitar` y `/historial`. **Discrepancia:** el catálogo anuncia `consulta`, pero la operación ejecutable correspondiente del bot es `consultar`; `/bots/mis_comprobantes/consulta` puede ser aceptada al crear el trabajo y luego rechazarse en la validación de ejecución. Use `/consultar`. Rutas compatibles: `POST /api/v3/mis_comprobantes/consulta` corresponde a `consultar`; `/solicitar_consulta` corresponde a `solicitar`; `/historial` corresponde a `historial`. En V3 la operación está en la URL. Envíe `{"payload": {...}, "credentials": {...}}`. La respuesta inicial es `202` con `job_id`; consulte `GET /api/v3/jobs/{job_id}`.


## Payload

Los campos marcados `consultar/historial` no se usan por `solicitar`; los campos marcados `todas` aplican a las tres operaciones.

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto; requerido; sin default | CUIT de quien posee los comprobantes. Alias `cuit`. Se quitan espacios, guiones, puntos y `/`, y se exigen 11 dígitos. Omitido, `null`, vacío o inválido: rechazo. |
| `representado_nombre` | texto 1–128; requerido operativamente | Nombre del representado, usado al identificarlo/nombrar la salida. Omitido, `null` o vacío: rechazo. El catálogo público lo presenta como opcional, pero la validación efectiva de las tres operaciones lo exige. |
| `fecha_desde` | fecha `dd/mm/aaaa`; requerida; sin default | Inicio del rango inclusive. Alias `desde`. Omitida, `null`, vacía o inválida: rechazo. |
| `fecha_hasta` | fecha `dd/mm/aaaa`; requerida; sin default | Fin inclusive, igual o posterior a `fecha_desde`. Alias `hasta`. Omitida, `null`, vacía o inválida: rechazo. |
| `emitidos` | booleano; opcional; `false` | Solicita comprobantes emitidos. Alias `descarga_emitidos`. `false` excluye ese tipo; `null` se rechaza. |
| `recibidos` | booleano; opcional; `false` | Solicita comprobantes recibidos. Alias `descarga_recibidos`. `false` excluye ese tipo; `null` se rechaza. Debe quedar `emitidos` o `recibidos` en `true`. |
| `puntos_venta_emitidos` | lista de enteros o textos; opcional; `null` | Filtro de puntos de venta para emitidos. Hasta 200 elementos; cada uno debe contener solo dígitos. Se conservan ceros iniciales de los textos. Omitido o `null`: sin filtro. Lista vacía: ningún filtro adicional. Texto vacío, elemento vacío/no numérico, más de 200 elementos o valor no listado: rechazo. |
| `puntos_venta_recibidos` | lista de enteros o textos; opcional; `null` | Igual que el campo anterior, aplicado a recibidos. Omitido o `null`: sin filtro. Lista vacía: sin filtro. |
| `timeout_mc` | entero de 1 a 86.400 segundos; opcional; efectivo por defecto 30 | Tiempo de espera de la descarga/consulta. Omitido o `null`: 30 segundos. `0`, vacío, decimal o fuera del rango: rechazo. |
| `incluir_json` | booleano; opcional; `true` en `consultar` y `historial` | En `consultar`/`historial` incluye muestra de filas en el resultado. Alias `carga_json`. En `solicitar` no aplica. `false` omite la muestra; `null` se rechaza. |
| `subir_csv` | booleano; opcional; `true` en `consultar` y `historial` | Adjunta los CSV al trabajo en `consultar`/`historial`. Alias `carga_minio`. `false` evita adjuntar, sin evitar la descarga/lectura. En `solicitar` no aplica. `null` se rechaza. |
| `cookies_header` | texto hasta 8.192 caracteres; solo `historial`; opcional; `null` | Campo de compatibilidad. No se utiliza para autenticar ni altera la consulta. Omitido o `null`: sin efecto; `""` no aporta valor. No lo envíe. |
| `carga_b64` | booleano; opcional; `false` | Campo legado aceptado y sin efecto; los archivos no se devuelven en Base64. Omitido equivale a `false`; `false` no cambia la respuesta; `null` se rechaza. |
| `proxy_request` | booleano o `null`; opcional; `null` | Se descarta en V3 y no controla el acceso ni la ruta de red; cualquier valor se ignora antes de la validación específica. |
| `eliminar_descargas` | booleano; opcional; valor declarado `true` | Campo legado descartado; la retención temporal no se configura con este parámetro. `false`, `null` o `""` no cambia la retención efectiva. |
| `verbose` | booleano; opcional; `false` | Se acepta en la validación pero no cambia el resultado. Omitido equivale a `false`; `null` se rechaza. |

En `solicitar`, `incluir_json` y `subir_csv` no forman parte de sus opciones; su envío no debe usarse para cambiar la solicitud. Los tipos desconocidos de `payload` se rechazan salvo los campos de compatibilidad expresamente indicados.

## Credenciales

Se requieren credenciales fiscales válidas y acceso a Mis Comprobantes para la cuenta y el representado.

| Campo en `credentials` | Tipo; requerido; default | Omisión, `null` o vacío |
|---|---|---|
| `cuit_representante` | texto; requerido operativamente; sin default | Cuenta que inicia sesión; admite el alias `cuit_inicio_sesion`. Si no hay CUIT utilizable, el acceso no podrá completarse. |
| `clave` | texto; requerida operativamente; sin default | Clave de la cuenta; alias compatibles `clave_representante` y `contrasena`. Si faltan o están vacías todas las variantes, no se puede iniciar sesión. |
| `clave_encriptada` | texto cifrado; alternativa opcional | Debe ser un valor generado por el mecanismo de cifrado de la API. Vacío o `null` equivale a ausencia; un cifrado inválido se rechaza. Si se envía además la clave en texto, debe representar el mismo valor. |

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