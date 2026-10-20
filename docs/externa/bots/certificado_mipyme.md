# Certificado MiPyME

## Propósito, operación y efectos

Inicia sesión, selecciona el CUIT representado y descarga su certificado MiPyME en PDF. Es una descarga de consulta: no solicita ni renueva la certificación y no modifica información fiscal. Si no aparece el CUIT indicado, el proceso falla en vez de seleccionar automáticamente otro.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/certificado_mipyme/descargar`
- La ejecución es asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para el estado y el archivo.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Alias: `cuit`, `cuit_representado`. Omitir, `null`, vacío o `false` se rechazan. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. Si se informan varios alias, deben coincidir. | `"20123456786"` |
| `subir` | booleano | No | `true` | Omitir publica el PDF recuperable en el resultado. `null`, vacío o valor no booleano se rechazan. `false` no lo publica ni informa huella o tamaño. | `true` |

## Credenciales

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `credentials.cuit_representante` | texto | Sí | ninguno | Cuenta fiscal que inicia sesión. Necesaria para el acceso; sin ella no se completa el inicio de sesión. `null` o `""` se tratan como ausentes. `false` es tipo inválido. | `"20123456786"` |
| `credentials.clave` | texto | Sí | ninguno | Clave fiscal necesaria para el acceso. `null` o `""` se tratan como ausentes. `false` es tipo inválido. | `"CLAVE_FICTICIA_NO_USAR"` |

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

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el job | `OK` si se descarga y procesa el certificado; los fallos terminan con error. No se define resultado parcial. | `"OK"` |
| `data.operacion` | texto | En una descarga correcta | Operación ejecutada. | `"descargar"` |
| `data.representado_cuit` | texto | En una descarga correcta | CUIT seleccionado para la descarga. | `"20123456786"` |
| `data.archivo` | texto | En una descarga correcta | Nombre del PDF descargado. | `"certificado.pdf"` |
| `data.sha256` | texto hexadecimal | Si `subir=true` y la publicación termina correctamente | Huella del PDF publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si `subir=true` y la publicación termina correctamente | Tamaño del PDF publicado en bytes. | `12345` |
| `files` | lista de archivos | En la respuesta final | Contiene el PDF si se publicó; queda vacía si `subir=false`. | `[]` |

El archivo es necesario para completar la operación. Si `subir=false`, `data.archivo` identifica el archivo producido, pero no se entrega un archivo recuperable. Un CUIT no encontrado, una descarga fallida o una publicación fallida termina con error, no con un resultado parcial.
