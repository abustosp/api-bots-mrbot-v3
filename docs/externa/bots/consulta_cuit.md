# Consulta de CUIT

Obtiene datos de constancia de inscripción asociados a uno o varios CUIT. No inicia sesión fiscal ni produce archivos. El contenido devuelto depende del servicio consultado y no se garantiza un conjunto fijo de atributos más allá del objeto JSON recibido.

## Ruta V3 y catálogo

`POST /api/v3/bots/consulta_cuit/consultar` consulta un CUIT; `POST /api/v3/bots/consulta_cuit/consultar_masivo` consulta una lista. El catálogo también publica `consulta`, alias operativo de `consultar`. La operación se selecciona en la ruta. La ruta genérica aplica la validación efectiva de cada operación, no el DTO del alias plano.

Cuerpo: `{"payload": {...}}`. No se necesitan `credentials` del usuario.

## Campos de `payload`

| Operación | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `consultar` | `cuit` | texto | Sí | ninguno | Se quitan espacios, guiones, puntos y barras; deben quedar 11 dígitos. No se valida el dígito verificador. Omitido, `null`, vacío o valor que no normaliza a 11 dígitos se rechaza. | `"20123456786"` |
| `consultar_masivo` | `cuits` | lista de textos | Sí | ninguno | Lista de 1 a 100 valores, normalmente textos; debe ser lista y no un escalar. Cada elemento se convierte a texto, se normaliza igual que el CUIT individual y debe quedar en 11 dígitos. Duplicados se eliminan conservando el primer orden. `null`, vacío o lista vacía inválidos. | `["20123456786", "30876543210"]` |

No hay campos con default ni filtros. Los campos desconocidos se rechazan. Para la consulta masiva el máximo se aplica a la lista recibida antes de deduplicar.

## Ejemplos ficticios

Consulta individual, ruta `POST /api/v3/bots/consulta_cuit/consultar`:

```json
{"payload": {"cuit": "20123456786"}}
```

Consulta masiva, ruta `POST /api/v3/bots/consulta_cuit/consultar_masivo`:

```json
{"payload": {"cuits": ["20123456786", "30876543210"]}}
```

## Resultado

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones. La forma de `data` depende del objeto JSON entregado por el servicio consultado, tanto para la consulta individual como para la masiva.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el job | `OK` cuando el servicio devuelve un objeto JSON válido; los errores de conexión, HTTP o formato terminan con error. La operación no produce resultado parcial. | `"OK"` |
| `data` | objeto JSON | Cuando la consulta termina correctamente | Copia la respuesta objeto del servicio. No se garantiza un conjunto fijo de campos fiscales. | `{"<clave del servicio>": "<valor>"}` |
| `files` | lista de archivos | En la respuesta final | Siempre vacía. Esta operación no produce archivos. | `[]` |

No se envían contraseñas personales. La autenticación técnica del servicio, si está configurada, se administra fuera del payload del cliente. No se debe incluir usuario o clave del servicio en `payload`.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
