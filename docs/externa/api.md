# API pública

**Revisión:** 20 de octubre de 2026. Esta guía describe la superficie de cliente observada. Los valores de los ejemplos son ficticios.

## Base, autenticación y catálogo

Las solicitudes de cliente usan el prefijo `/api/v3`. En los ejemplos, `API_BASE` es la dirección base del servicio sin añadir `/api/v3`, `API_USER` es el correo o alias de la cuenta y `API_KEY` es la API key de la cuenta, no una clave fiscal de un organismo. La descripción interactiva está disponible en `/docs`, su versión de lectura en `/redoc` y el catálogo de contratos en `/openapi.json`.

La mayoría de las operaciones requiere autenticación. Puede usar HTTP Basic con el correo o alias de su cuenta como usuario y su API key como contraseña:

```sh
curl --user "${API_USER}:${API_KEY}" \
  "${API_BASE}/api/v3/bots"
```

También se admite la credencial V1 `X-API-Key`; `email` puede enviarse como identidad adicional. No use un token Bearer. La clave pública para proteger credenciales de ciertas operaciones se consulta sin autenticación en `GET /api/v3/seguridad/clave-publica`.

Descubra bots y operaciones con `GET /api/v3/bots`; consulte el detalle de una clave con `GET /api/v3/bots/{bot}`. La disponibilidad efectiva y los campos dependen de la operación. Revise sus campos, valores predeterminados, tipos, requisitos de archivo y opciones antes de enviar un pedido. Cada ejecución nueva aceptada reserva una unidad de uso. `costo_creditos` es informativo y no garantiza un cálculo proporcional del consumo por bot. Consulte el resumen de cuenta para el uso informado. El catálogo señala `comprobantes` como alias deprecado de `mis_comprobantes`. Prefiera el nombre canónico.

## Crear una ejecución

La forma canónica es `POST /api/v3/bots/{bot}/{operacion}`. En el cuerpo publicado, incluya `payload` como objeto. `credentials` lleva solo las credenciales que requiera esa operación; su omisión, valor nulo y propiedades dependen del contrato de la operación. El cuerpo separa los datos de la operación (`payload`) de las credenciales (`credentials`). Por ejemplo, consulta ficticia de un CUIT:

```sh
curl --user "${API_USER}:${API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: consulta-cuit-demo-20261020-001' \
  -d '{"payload":{"cuit":"20123456786"},"credentials":{}}' \
  "${API_BASE}/api/v3/bots/consulta_cuit/consultar"
```

La respuesta aceptada es `202 Accepted`, con un identificador de ejecución, por ejemplo:

```json
{
  "success": true,
  "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
  "status": "PENDIENTE"
}
```

Use el identificador para consultar el resultado. La respuesta de creación también puede incluir `Location` con la dirección de consulta. No todas las respuestas de la API usan el mismo envoltorio: siga el código HTTP y los campos documentados para cada operación, no presuponga que toda respuesta contiene `success` o `data`.

### Reintentos e idempotencia

Envíe `Idempotency-Key` en cada creación, con una clave única por intención de pedido, y reutilice la misma clave solo al reintentar exactamente el mismo contenido. Dentro de la retención de la clave, el mismo contenido reutiliza la ejecución; si el contenido cambia, puede recibir `409 Conflict`. El header es opcional en el servicio observado. Si no lo envía, un reintento puede crear otra ejecución. Las claves persistidas tienen una retención limitada, por lo que no use esta función como registro permanente.

Las rutas de compatibilidad históricas, cuando aparecen en el catálogo, usan cuerpos planos y nombres de campo propios. Por ejemplo, `POST /api/v3/consulta_cuit/individual` recibe `{"cuit":"20123456786"}` en el cuerpo, no `payload`/`credentials`. No mezcle ambos formatos. Para integraciones nuevas, prefiera la operación canónica publicada en el catálogo.

## Consultar ejecuciones y resultados

- `GET /api/v3/jobs/{job_id}` consulta una ejecución propia.
- `GET /api/v3/jobs` lista ejecuciones propias y puede paginar con cursor.
- `GET /api/v3/jobs/cola` consulta la cola propia.
- `GET /api/v3/usuarios/me` consulta la identidad y el estado de la cuenta.
- `POST /api/v3/jobs/estado:lote` consulta hasta 200 identificadores. La respuesta HTTP puede ser `200` aunque algún elemento tenga error; revise cada entrada de `items`.

Estados que puede encontrar: `PENDIENTE`, `ASIGNADO`, `CORRIENDO`, `COMPLETO`, `FALLIDO` y `CANCELADO`. En la vista compatible, `ASIGNADO` puede aparecer como `PENDIENTE` y un fallo puede aparecer como `COMPLETO` con `result: "ERROR"`. No infiera éxito a partir de `COMPLETO`: revise siempre `result` y `status_detail`. El resultado final puede ser `OK`, `PARCIAL` o `ERROR`; no está disponible mientras la ejecución sigue activa.

Ejemplo estrictamente ilustrativo del envoltorio genérico de estado, no de la salida de una operación concreta:

```json
{
  "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
  "status": "COMPLETO",
  "status_detail": "COMPLETO",
  "result": "OK",
  "data": null,
  "files": [],
  "error_detail": null
}
```

`data` contiene el resultado específico de la operación y puede ser `null`. `files` es una lista, incluso cuando no hay archivos. `COMPLETO` junto con `PARCIAL` indica que el resultado no fue completo en todos sus aspectos. La estructura de `data` depende del bot.

## Cancelar y consultar el cupo

`POST /api/v3/jobs/{job_id}/cancelar` solicita la cancelación. Puede enviar `{"motivo":"Solicitud de ejemplo"}`. También está disponible `DELETE /api/v3/jobs/{job_id}`. Una ejecución pendiente puede cancelarse de inmediato; si ya empezó, la solicitud es cooperativa y el trabajo puede haber producido efectos antes de detenerse. Cancelar no revierte acciones ya realizadas. Una ejecución terminada no cambia por pedir su cancelación.

`GET /api/v3/mi/cuenta` muestra el resumen de uso y disponibilidad. Una creación puede ser rechazada con `429 Too Many Requests` cuando no hay cupo; en ese caso revise `Retry-After` y el estado de cuenta antes de reintentar. No se garantiza una devolución automática general de cuota para todos los errores o cancelaciones.

## Errores y correlación

Considere primero el código HTTP. Muchos errores devuelven `detail` con `error_code` y `message`, pero hay respuestas de validación con otras formas de `detail`. Su cliente debe tolerar que el campo sea un objeto, una lista u otro valor y conservar el cuerpo para diagnóstico sin exponer credenciales.

Puede enviar un `X-Correlation-ID` con UUID para asociar solicitudes relacionadas. La creación de una ejecución devuelve este encabezado; no todas las respuestas lo incluyen. Consulte [archivos y errores](archivos-y-errores.md) para carga, descarga y ejemplos de interpretación.
