# Consulta de CUIT

Obtiene datos de constancia de inscripción asociados a uno o varios CUIT. No inicia sesión fiscal ni produce archivos. El contenido devuelto depende del servicio consultado y no se garantiza un conjunto fijo de atributos más allá del objeto JSON recibido.

## Ruta V3 y catálogo

`POST /api/v3/bots/consulta_cuit/consultar` consulta un CUIT; `POST /api/v3/bots/consulta_cuit/consultar_masivo` consulta una lista. El catálogo también publica `consulta`, alias operativo de `consultar`. Las tres operaciones cuestan 1 crédito. La operación se selecciona en la ruta.

Cuerpo: `{"payload": {...}}`. No se necesitan `credentials` del usuario.

## Campos de `payload`

| Operación | Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|---|
| `consultar` | `cuit` | string, requerido, exactamente 11 dígitos sin separadores en V3 | Omisión, `null` o vacío se rechazan. El endpoint público exige el patrón estricto; la normalización del servicio también elimina separadores, pero estos no se deben usar en V3. No se valida el dígito verificador. |
| `consultar_masivo` | `cuits` | lista de strings, requerida, 1–100 elementos | Debe ser lista, no un scalar. Cada CUIT debe ser string de 11 dígitos en V3; duplicados se eliminan conservando el primer orden. Nulo, vacío o lista vacía inválidos. La normalización interna elimina separadores, pero el patrón público estricto no los admite. |

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

El resultado exitoso contiene el objeto JSON que devuelve el servicio para el CUIT solicitado o, en masivo, el resultado del endpoint de lotes. Su forma y los campos fiscales presentes son los del servicio vigente, no una estructura enriquecida por esta operación. No produce `files[]`. Una respuesta no JSON o que no sea un objeto, errores HTTP o de conexión causan resultado de error, no una respuesta parcial documentada.

No se envían contraseñas personales. La autenticación técnica del servicio, si está configurada, se administra fuera del payload del cliente. No se debe incluir usuario o clave del servicio en `payload`.
