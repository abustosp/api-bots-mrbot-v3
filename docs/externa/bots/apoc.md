# APOC: consulta por CUIT

## Propósito y operación

Busca un CUIT en la información disponible de contribuyentes con condición apócrifa y devuelve si encuentra una coincidencia, junto con las fechas de condición y publicación disponibles. Es una consulta de solo lectura. No inicia trámites ni cambia datos del organismo.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/apoc/consultar`
- La creación responde con un identificador de ejecución; consulta su estado y resultado en `GET /api/v3/jobs/{job_id}`.

La ruta fija la operación. En el cuerpo V3, los datos del bot van dentro de `payload`; las credenciales van aparte en `credentials`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit` | texto | Sí | ninguno | Omitir, `null` o vacío: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. `false` no es un CUIT y se rechaza. | `"20123456786"` |

No se requiere objeto de credenciales: use `credentials: {}`. No envíe claves fiscales para esta consulta.

## Ejemplo completo ficticio

El CUIT es ilustrativo, elegido para satisfacer el formato y el dígito verificador; no identifica aquí a una persona o entidad real.

```json
{
  "payload": {
    "cuit": "20123456786"
  },
  "credentials": {}
}
```

## Resultado y límites

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el job | En una consulta válida es `OK`. Si no se puede consultar la base, la ejecución falla. | `"OK"` |
| `data.cuit` | texto | Cuando la consulta termina correctamente | CUIT consultado. | `"20123456786"` |
| `data.apoc` | booleano | Cuando la consulta termina correctamente | `true` indica coincidencia y `false` indica que no se encontró el CUIT en la base consultada. | `false` |
| `data.fecha_condicion` | texto o `null` | Cuando la consulta termina correctamente | Fecha asociada a la condición si hay coincidencia; `null` si no la hay. | `null` |
| `data.fecha_publicacion` | texto o `null` | Cuando la consulta termina correctamente | Fecha de publicación si hay coincidencia; `null` si no la hay. | `null` |
| `files` | lista de archivos | En la respuesta final | Siempre vacía. La operación no publica archivos. | `[]` |

Una consulta válida sin coincidencia es un resultado correcto con `apoc: false` y ambas fechas en `null`. La ausencia o indisponibilidad de la base causa error, no un falso negativo. Esta operación no genera resultados parciales.

La aceptación inicial del job no equivale a una respuesta de consulta: revise el resultado final del job.
