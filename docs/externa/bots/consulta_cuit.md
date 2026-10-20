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

`result` es el estado del job (`OK`, `PARCIAL` o `ERROR`). `data` es un objeto JSON con la respuesta entregada por el servicio consultado, individual o masivo. No se añade un conjunto fijo de claves fiscales: su forma depende de la respuesta disponible del servicio. No produce `files[]`. Una respuesta no JSON o que no sea un objeto, errores HTTP o de conexión causan error, no una respuesta parcial transformada.

No se envían contraseñas personales. La autenticación técnica del servicio, si está configurada, se administra fuera del payload del cliente. No se debe incluir usuario o clave del servicio en `payload`.

El catálogo en `GET /api/v3/bots` publica `costo_creditos` como metadata informativa. La creación actual reserva una unidad de cuota por trabajo; consultar disponibilidad y límites en la API, no interpretar la metadata como cargo efectivo.
