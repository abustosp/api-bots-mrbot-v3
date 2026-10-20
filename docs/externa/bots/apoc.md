# APOC: consulta por CUIT

## Propósito y operación

Busca un CUIT en la información disponible de contribuyentes con condición apócrifa y devuelve si encuentra una coincidencia, junto con las fechas de condición y publicación disponibles. Es una consulta de solo lectura. No inicia trámites ni cambia datos del organismo.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/apoc/consultar`
- La creación responde con un identificador de ejecución; consulta su estado y resultado en `GET /api/v3/jobs/{job_id}`.

La ruta fija la operación. En el cuerpo V3, los datos del bot van dentro de `payload`; las credenciales van aparte en `credentials`.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `cuit` | Texto, requerido. | Omitir, `null` o vacío: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. `false` no es un CUIT y se rechaza. |

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

En `data` se devuelven `cuit`, `apoc` (booleano), `fecha_condicion` y `fecha_publicacion`. Si no hay coincidencia, `apoc` es `false` y ambas fechas son `null`; no se afirma que no haya coincidencia si la información de consulta no está disponible, en cuyo caso la ejecución falla. Esta operación no produce archivos (`files` queda vacío). No hay resultado parcial definido para una consulta individual.

La aceptación inicial del job no equivale a una respuesta de consulta: revise el resultado final del job.