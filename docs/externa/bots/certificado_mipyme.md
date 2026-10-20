# Certificado MiPyME

## Propósito, operación y efectos

Inicia sesión, selecciona el CUIT representado y descarga su certificado MiPyME en PDF. Es una descarga de consulta: no solicita ni renueva la certificación y no modifica información fiscal. Si no aparece el CUIT indicado, el proceso falla en vez de seleccionar automáticamente otro.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/certificado_mipyme/descargar`
- La ejecución es asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para el estado y el archivo.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto, requerido. Alias: `cuit`, `cuit_representado`. | Omitir, `null`, vacío o `false`: rechazo. V3 exige 11 dígitos sin separadores. Si se informan varios alias, deben coincidir. |
| `subir` | Booleano, opcional, default `true`. | Omitir: publica el PDF recuperable en el resultado. `null`, vacío o valor no booleano: rechazo. `false`: no lo publica ni informa huella/tamaño. |

## Credenciales

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `credentials.cuit_representante` | Texto; cuenta fiscal que inicia sesión. | Necesario para el acceso; sin él no se completa el inicio de sesión. `null` o `""` se tratan como ausentes. `false`: tipo inválido. |
| `credentials.clave` | Texto; clave fiscal. | Necesaria para el acceso. `null` o `""` se tratan como ausentes. `false`: tipo inválido. |

Se reconocen nombres de credencial compatibles como `cuit_inicio_sesion`, `cuit_login`, `clave_representante` o `contrasena`, y una clave cifrada válida. El CUIT consultado puede diferir del titular que inicia sesión cuando existe representación autorizada.

## Ejemplo completo ficticio

El CUIT pasa el control de dígito verificador, pero se usa solo como ejemplo y no identifica aquí a una entidad real. La clave no es válida.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "subir": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y parciales

`data` informa `operacion`, `representado_cuit` y `archivo` (`certificado.pdf`). Cuando `subir=true`, también agrega `sha256` y `size_bytes`, y el PDF queda en `files`. El archivo es obligatorio para que la operación termine correctamente. Si la publicación está desactivada, `data.archivo` identifica lo producido en esa ejecución, pero no proporciona un archivo descargable. No se define una salida parcial: un CUIT no encontrado, una descarga fallida o una publicación fallida termina con error.