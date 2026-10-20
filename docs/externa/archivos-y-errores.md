# Archivos, campos y errores

**Revisión:** 20 de octubre de 2026. Los identificadores, CUIT, nombres de archivo y resultados de los ejemplos son ficticios.

## Cómo leer campos de una operación

Consulte `GET /api/v3/bots` y `GET /api/v3/bots/{bot}` para ver los campos publicados. Los requisitos y valores predeterminados son propios de cada operación. Un campo opcional no significa que todas las formas de ausencia sean equivalentes:

| Forma | Qué enviar | Cómo interpretarla |
|---|---|---|
| Omitir | No incluir la propiedad | Se aplica el valor predeterminado si la operación publica uno. Si no, la propiedad queda ausente. |
| `null` | Incluir la propiedad con valor JSON nulo | Puede aceptarse solo en algunos campos. No equivale automáticamente a omitir. |
| Texto vacío | `""` | Es un valor, no una omisión. Puede rechazarse o tener significado propio. |
| Espacios | Por ejemplo `"   "` | No los use para representar ausencia. La normalización depende del campo. |
| Booleano | `true` o `false` | Envíe un booleano JSON, no el texto `"true"` o `"false"`. |
| Lista vacía | `[]` | No equivale a una lista omitida. Revise si la operación exige uno o más elementos. |

Use los nombres exactos y los tipos publicados. No incluya propiedades con nombres parecidos esperando que se traduzcan. Mantenga separados el cuerpo canónico (`payload` y `credentials`) y los cuerpos planos de compatibilidad cuando el catálogo ofrezca ambos. Para opciones que afecten descargas, presentaciones u otros efectos, envíe explícitamente el valor deseado y no deduzca el comportamiento de la omisión, `null` o `false` por analogía con otro campo.

## Enviar un archivo de entrada

El contenido se envía en dos pasos. No coloque una ruta de su computadora, contenido base64 ni bytes del archivo en el pedido de creación. Primero solicite un ticket:

```http
POST /api/v3/uploads
Content-Type: application/json
```

Ejemplo de ticket ficticio para un TXT de VEP:

```json
{
  "bot": "vep_archivo",
  "operacion": "generar",
  "campo": "archivo",
  "filename": "vep-demo.txt",
  "content_type": "text/plain",
  "size_bytes": 120
}
```

`filename` y `size_bytes` son obligatorios; el tamaño debe ser positivo y se debe declarar el tamaño real del archivo que enviará. `campo` se predetermina a `archivos` y `content_type` a `application/octet-stream` si se omiten. Si la operación no admite `archivos`, indique `campo` explícitamente. `cuit` y `sha256` son opcionales. La API observada no compara el tamaño declarado ni el hash opcional con los bytes cargados, por lo que estos metadatos no confirman la integridad del archivo. Envíe el campo correcto para el par bot/operación: por ejemplo, VEP acepta `archivo` o `archivo_excel`; la carga de IVA usa campos como `liv_cbte`, `liv_alicuota`, `lic_cbte`, `lic_alicuota` y los campos `csv_*`; controladores fiscales usa `archivos`. La operación puede requerir campos relacionados además del archivo.

La respuesta incluye `upload_url`, `upload_url_expires_in`, `required_headers`, `object_key` y `expires_at`, además de identificadores del ticket. Ejemplo con valores ficticios y marcadores para los valores que debe copiar de su respuesta:

```json
{
  "upload_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abe",
  "object_key": "<object_key devuelto por el ticket>",
  "bucket": "<bucket indicado>",
  "upload_url": "<upload_url devuelta>",
  "upload_url_expires_in": 900,
  "expires_at": "2026-10-20T13:00:00Z",
  "required_headers": {"Content-Type": "text/plain"}
}
```

Use el valor de `upload_url` recibido y envíe los bytes originales con los headers indicados, sin sustituir el `Content-Type`:

```sh
curl -X PUT "$UPLOAD_URL" \
  -H 'Content-Type: text/plain' \
  --data-binary @vep-demo.txt
```

La URL de carga dura 900 segundos. `expires_at` es un dato distinto de expiración del ticket/archivo, no la vigencia de esa URL. Si la URL vence antes de terminar, solicite otro ticket. El límite general de tamaño es 52.428.800 bytes (50 MiB), con límites inferiores según campo. Se revisan el tipo de archivo y el contenido aceptado por la operación. Los campos VEP `archivo` y `archivo_excel` aceptan `.txt` y `.xlsx`, respectivamente; `controladores_fiscales/presentar` acepta `.pem` o `.zip`. Consulte la operación para otros formatos y límites.

Al crear el pedido, use el `object_key` que devolvió el ticket en el campo de referencia indicado por la operación. Por ejemplo, el archivo `archivo` de `vep_archivo/generar` se referencia mediante `archivo_key`:

```json
{
  "payload": {
    "archivo_key": "<object_key devuelto por el ticket>"
  },
  "credentials": {}
}
```

Este fragmento muestra la referencia al archivo, no sustituye otros campos obligatorios de la operación. En carga de IVA los nombres de referencia terminan en `_key`; se requieren ambos archivos del par TXT seleccionado (comprobantes y alícuotas), o al menos un CSV de apertura. No envíe una sola mitad del par.

El ticket es específico de su cuenta, bot, operación y campo. Debe estar vigente y el archivo debe existir antes de crear el pedido. Un archivo omitido, no cargado o asociado a otro campo puede producir `422`.

## Archivos de salida

La respuesta del job incluye `files` como lista, incluso cuando está vacía. Cada elemento puede incluir nombres compatibles (`name`, `url`, `size`) y campos ampliados como `artifact_id`, `filename`, `content_type`, `size_bytes`, `sha256`, `download_url` y `url_expires_in`.

- Use `filename` o `name` para identificar el archivo y `size_bytes` o `size` para su tamaño cuando estén presentes.
- Una dirección `url` puede ser una cadena vacía si no se pudo crear un enlace directo. No la abra si está vacía.
- Si está disponible, `download_url` es una dirección autenticada para solicitar la descarga. La respuesta puede redirigir a una dirección temporal. Siga la redirección y no guarde la dirección temporal como permanente.
- Los enlaces directos tienen vencimiento; cuando `url_expires_in` indica un valor, expresa su vigencia en segundos. Si la descarga responde `404` o `410`, el archivo no está disponible para esa solicitud.

Una publicación completada no garantiza por sí sola que `files` incluya una referencia en toda configuración del servicio. Si `files` está vacío, no concluya que la operación no generó el archivo, especialmente en operaciones con efectos. Verifique el estado en el organismo antes de repetir el pedido.

Ejemplo ficticio:

```json
{
  "files": [
    {
      "artifact_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abd",
      "filename": "consulta-demo.pdf",
      "content_type": "application/pdf",
      "size_bytes": 2048,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "url": "",
      "download_url": "/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download",
      "url_expires_in": null
    }
  ]
}
```

## Errores HTTP y errores de ejecución

Un rechazo de solicitud se identifica por su código HTTP. Un error que aparece dentro de un job se consulta en el estado de ese job y no debe confundirse con un rechazo al crearla.

| HTTP | Casos observados | Qué revisar |
|---|---|---|
| `401` | Autenticación ausente o no aceptada | Usuario y API key, vigencia de la clave y estado de la cuenta. No reintente sin corregir la autenticación. |
| `404` | Bot, operación, ejecución o archivo no disponible | Compruebe identificadores y pertenencia a la cuenta. Por seguridad, la respuesta no siempre distingue entre inexistencia y falta de acceso. |
| `409` | Clave de idempotencia reutilizada con otro contenido | Use una clave nueva para una intención distinta; no cambie el cuerpo conservando la misma clave. |
| `410` | Archivo de salida eliminado o vencido | La descarga no está disponible. No suponga que el enlace es permanente. |
| `422` | JSON, campo, archivo, formato o referencia no admisible | Revise `detail`, el schema de la operación y que el ticket se haya cargado correctamente. |
| `429` | Cupo no disponible | Consulte `Retry-After` y `GET /api/v3/mi/cuenta` antes de volver a enviar. |
| `503` | Servicio no disponible temporalmente | Revise el cuerpo de error y reintente con prudencia. Para creación, conserve la misma clave de idempotencia si repite exactamente el mismo pedido. |

La forma habitual de error es:

```json
{
  "detail": {
    "error_code": "quota_exhausted",
    "message": "No hay cupo disponible para esta solicitud"
  }
}
```

La forma de `detail` puede variar: en ciertos errores es una lista o un texto. Lea el cuerpo como JSON cuando sea posible, pero no asuma que `error_code` y `message` estarán presentes en todos los casos. No comparta mensajes que contengan datos personales o credenciales.

En una ejecución terminada, `result` puede ser `OK`, `PARCIAL` o `ERROR`. `error` es una descripción legible y `error_detail`, cuando está disponible, puede aportar un código, causa o correlación. Los fallos y cancelaciones no garantizan una devolución automática de cuota. Revise el estado final y el resultado antes de repetir una operación que pueda tener efectos.
