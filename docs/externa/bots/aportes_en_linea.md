# Aportes en Línea: descarga de archivo histórico

## Propósito y operación

Inicia sesión fiscal, abre el servicio Aportes en Línea y descarga el archivo Histórico (`.xls`) del CUIT representado. Es una consulta/descarga: no presenta declaraciones ni cambia datos fiscales.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/aportes_en_linea/descargar`
- La creación del job es asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para el resultado y sus archivos.

En V3, envíe las consultas bajo `payload` y los datos fiscales bajo `credentials`.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto o `null`; opcional, default `null`. Alias aceptados: `cuit`, `cuit_representado`. | Omitir o `null`: usa el CUIT de `credentials.cuit_representante`. Vacío o `false`: se rechaza. Se eliminan espacios, guiones, puntos y barras; si se informa deben quedar exactamente 11 dígitos. |
| `subir` | Booleano, opcional, default `true`. | Omitir: se publica el resultado. `null` o texto vacío: inválido. `false`: no se publica el archivo; no se devuelve en Base64. |
| `incluir_base64` | Booleano, opcional, default `false`. Alias: `archivo_historico_b64`. | Omitir o `false`: no cambia la salida. `null` o texto vacío: inválido. `true` se acepta por compatibilidad, pero no produce contenido Base64. |

El CUIT del representado es opcional solo porque puede heredarse del CUIT de inicio de sesión. Para evitar ambigüedad, inclúyalo explícitamente cuando descargue para un representado distinto. El campo heredado `proxy_request` (booleano o `null`, opcional) se acepta por compatibilidad y se ignora: omitirlo o enviar `false` no cambia la solicitud; `null` también es válido. Los nombres de credenciales heredados en el payload (`cuit_representante`, `cuit_inicio_sesion`, `cuit_login`, `clave_representante`, `contrasena`, `clave` y `clave_encriptada`) son compatibles con solicitudes antiguas y se trasladan al bloque de credenciales; para V3 use `credentials` directamente y no envíe valores contradictorios.

## Credenciales

`credentials` acepta un objeto. Para completar el inicio de sesión, envíe los dos valores siguientes. Si faltan, son nulos o están vacíos, la petición puede ser aceptada para ejecución, pero el job no podrá completar el inicio de sesión.

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `cuit_representante` | Texto; CUIT de la cuenta que inicia sesión. También determina el representado si se omite `payload.representado_cuit`. | Sin valor no hay CUIT de inicio de sesión para este servicio. `null` y `""` se tratan como omitidos. `false` no es texto válido. |
| `clave` | Texto; clave fiscal. | Requerida para completar el acceso. Omitida, `null` o `""`: se trata como ausente. `false` no es texto válido. |

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

`data` informa `operacion`, `representado_cuit` y `archivo`. Cuando `subir` es `true`, también informa `sha256` y `size_bytes`, y `files` contiene el `.xls` descargable. Cuando `subir` es `false`, `data.archivo` solo identifica la descarga de esa ejecución: no se devuelve Base64 ni un archivo descargable. Si falla la descarga o la publicación del archivo, el job termina con error, no con un resultado parcial.