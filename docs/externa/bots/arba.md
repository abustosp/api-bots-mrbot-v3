# ARBA: descarga de retenciones y percepciones

## Propósito, operación y efectos

Descarga el archivo ZIP de retenciones/percepciones de Ingresos Brutos para un CUIT y período. Es una consulta y descarga, no presenta ni modifica declaraciones. Si el portal no tiene archivos para el período, termina correctamente con una lista vacía.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/arba/descargar`
- La aceptación es asíncrona: consulte `GET /api/v3/jobs/{job_id}` para conocer el resultado final y recuperar archivos.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | Alias: `cuit`, `cuit_representado`. Si se omite o es `null`, se rechaza. Vacío o `false`: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. | `"20123456786"` |
| `periodo` | texto | Sí | ninguno | Formato `AAAAMM`. Omitir, `null`, vacío o `false`: rechazo. Mes `01`–`12` y el período no puede ser futuro al validar. | `"202401"` |
| `representado_nombre` | texto | Sí | ninguno | Longitud de 1 a 128 caracteres. Alias: `denominacion`. Omitir, `null`, vacío o `false`: rechazo. Se limpia para formar el nombre del archivo, no cambia datos del portal. | `"EJEMPLO FICTICIO SA"` |
| `subir` | booleano | No | `true` | Omitir: publica el ZIP como archivo descargable. `null`, vacío o texto no booleano: rechazo. `false`: no publica archivo descargable ni agrega huella ni tamaño. | `true` |

La fecha máxima permitida depende del mes en que se valide la solicitud. Los alias de un mismo dato no deben tener valores contradictorios.

## Credenciales

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `credentials.clave` | texto | Condicional | ninguno | Clave fiscal. Se requiere para completar el acceso; sin ella la solicitud puede encolarse pero falla al ejecutar por falta de clave. Omitida, `null` o `""`: falta de clave. `false`: tipo inválido. | `"CLAVE_FICTICIA_NO_USAR"` |

El CUIT de inicio de sesión se toma del CUIT representado de este pedido. No se requiere otro campo de CUIT en `credentials`. También puede usarse un nombre de campo de clave compatible, por ejemplo `clave_representante` o `contrasena`; una clave cifrada requiere un valor aceptado por el servicio.

## Ejemplo completo ficticio

Los CUIT son valores ilustrativos que pasan el control de dígito verificador; no identifican aquí a una persona o entidad real. La clave es un marcador que no sirve para iniciar sesión.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo": "202401",
    "representado_nombre": "EJEMPLO FICTICIO SA",
    "subir": true
  },
  "credentials": {
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y parciales

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el job | `OK` tanto si hay ZIP como si la consulta informa que no hay archivos. Los fallos terminan con error. | `"OK"` |
| `data.operacion` | texto | En una respuesta correcta | Operación ejecutada. | `"descargar"` |
| `data.representado_cuit` | texto | En una respuesta correcta | CUIT consultado. | `"20123456786"` |
| `data.periodo` | texto | En una respuesta correcta | Período consultado en formato `AAAAMM`. | `"202401"` |
| `data.archivos` | lista de objetos | En toda respuesta correcta | Archivos descargados; puede ser una lista vacía cuando no hay archivos para el período. | `[]` |
| `data.tipo` | texto | Cuando se descargó un ZIP | Tipo de archivo. | `"RETPER ARBA"` |
| `data.archivo` | texto | Cuando se descargó un ZIP | Nombre del ZIP descargado. | `"<nombre>.zip"` |
| `data.archivos[].tipo` | texto | Por cada ZIP descargado | Tipo de archivo. | `"RETPER ARBA"` |
| `data.archivos[].archivo` | texto | Por cada ZIP descargado | Nombre del ZIP. | `"<nombre>.zip"` |
| `data.sha256` | texto hexadecimal | Si hay ZIP y `subir=true` | Huella del ZIP publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si hay ZIP y `subir=true` | Tamaño del ZIP publicado en bytes. | `12345` |
| `data.mensaje` | texto | Cuando la consulta no encuentra archivos | Mensaje informativo; en este caso `data.archivos` es `[]` y no aparecen `data.tipo`, `data.archivo`, `data.sha256` ni `data.size_bytes`. | `"<mensaje informativo>"` |
| `files` | lista de archivos | En la respuesta final | Contiene el ZIP si se publicó; queda vacía sin archivo o con `subir=false`. | `[]` |

Para recuperar el ZIP publicado, consúltelo en `files` del job. Si `subir=false`, el nombre permanece en `data` pero no hay archivo recuperable. La ausencia de archivos es un resultado correcto, no parcial; un fallo de descarga o publicación termina con error.
