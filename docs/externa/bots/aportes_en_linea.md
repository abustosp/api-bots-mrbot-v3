# Aportes en Línea: descarga de archivo histórico

## Propósito y operación

Inicia sesión fiscal, abre el servicio Aportes en Línea y descarga el archivo Histórico (`.xls`) del CUIT representado. Es una consulta/descarga: no presenta declaraciones ni cambia datos fiscales.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/aportes_en_linea/descargar`
- La creación del job es asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para el resultado y sus archivos.

En V3, envíe las consultas bajo `payload` y los datos fiscales bajo `credentials`.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | No | `null` | Alias: `cuit`, `cuit_representado`. Omitir o `null` usa el CUIT de `credentials.cuit_representante`. Vacío o `false` se rechaza. Se eliminan espacios, guiones, puntos y barras; si se informa deben quedar exactamente 11 dígitos. | `"20123456786"` |
| `subir` | booleano | No | `true` | Omitir publica el resultado. `null` o texto vacío inválidos. `false` no publica el archivo y no lo devuelve en Base64. | `true` |
| `incluir_base64` | booleano | No | `false` | Alias: `archivo_historico_b64`. Omitir o `false` no cambia la salida. `null` o texto vacío inválidos. `true` se acepta por compatibilidad, pero no produce contenido Base64. | `false` |

El CUIT del representado es opcional solo porque puede heredarse del CUIT de inicio de sesión. Para evitar ambigüedad, inclúyalo explícitamente cuando descargue para un representado distinto. El campo heredado `proxy_request` (booleano o `null`, opcional) se acepta por compatibilidad y se ignora: omitirlo o enviar `false` no cambia la solicitud; `null` también es válido. Los nombres de credenciales heredados en el payload (`cuit_representante`, `cuit_inicio_sesion`, `cuit_login`, `clave_representante`, `contrasena`, `clave` y `clave_encriptada`) son compatibles con solicitudes antiguas y se trasladan al bloque de credenciales; para V3 use `credentials` directamente y no envíe valores contradictorios.

## Credenciales

`credentials` acepta un objeto. Para completar el inicio de sesión, envíe los dos valores siguientes. Si faltan, son nulos o están vacíos, la petición puede ser aceptada para ejecución, pero el job no podrá completar el inicio de sesión.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Sí | ninguno | CUIT de la cuenta que inicia sesión; también determina el representado si se omite `payload.representado_cuit`. Sin valor no hay CUIT de inicio de sesión para este servicio. `null` y `""` se tratan como omitidos. `false` no es texto válido. | `"20123456786"` |
| `clave` | texto | Sí | ninguno | Clave fiscal requerida para completar el acceso. Omitida, `null` o `""` se trata como ausente. `false` no es texto válido. | `"CLAVE_FICTICIA_NO_USAR"` |

También se reconocen credenciales compatibles como `clave_representante` o `contrasena` en lugar de `clave`. Se puede enviar `clave_encriptada` en vez de una clave en claro si es un valor cifrado válido para el servicio; si se envían ambas formas, deben corresponder a la misma clave. No envíe valores ficticios a un entorno real.

## Ejemplo completo ficticio

Los valores CUIT son ilustrativos, elegidos para satisfacer el formato y el dígito verificador; no identifican aquí a una persona o entidad real. Los valores de acceso son marcadores ficticios. Sustitúyalos por credenciales autorizadas únicamente al usar el servicio real.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "subir": true,
    "incluir_base64": false
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y resultados parciales

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto | Al finalizar el job | `OK` si la descarga solicitada termina correctamente; los fallos terminan con error. No se define resultado parcial. | `"OK"` |
| `data.operacion` | texto | En una descarga correcta | Operación ejecutada. | `"descargar"` |
| `data.representado_cuit` | texto | En una descarga correcta | CUIT del representado usado en la consulta. | `"20123456786"` |
| `data.archivo` | texto | En una descarga correcta | Nombre del archivo Histórico descargado. | `"<nombre>.xls"` |
| `data.sha256` | texto hexadecimal | Si `subir=true` y la publicación termina correctamente | Huella del archivo publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si `subir=true` y la publicación termina correctamente | Tamaño del archivo publicado en bytes. | `12345` |
| `files` | lista de archivos | En la respuesta final | Contiene el archivo descargable si se publicó. Queda vacía si `subir=false`. | `[]` |

`data.archivo` identifica lo descargado aun con `subir=false`, pero en ese caso no se entrega un archivo recuperable. `incluir_base64=true` no agrega contenido Base64 a la salida. Si falla la descarga o la publicación, el job termina con error, no con un resultado parcial.
