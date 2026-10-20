# ARBA: descarga de retenciones y percepciones

## Propósito, operación y efectos

Descarga el archivo ZIP de retenciones/percepciones de Ingresos Brutos para un CUIT y período. Es una consulta y descarga, no presenta ni modifica declaraciones. Si el portal no tiene archivos para el período, termina correctamente con una lista vacía.

- **Operación del catálogo:** `descargar`
- **Ruta V3:** `POST /api/v3/bots/arba/descargar`
- La aceptación es asíncrona: consulte `GET /api/v3/jobs/{job_id}` para conocer el resultado final y recuperar archivos.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto, requerido. Alias: `cuit`, `cuit_representado`. | Si se omite o es `null`, se rechaza. Vacío o `false`: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. |
| `periodo` | Texto, requerido, formato `AAAAMM`. | Omitir, `null`, vacío o `false`: rechazo. Mes 01–12 y el período no puede ser futuro al validar. |
| `representado_nombre` | Texto, requerido, 1–128 caracteres. Alias: `denominacion`. | Omitir, `null`, vacío o `false`: rechazo. Se limpia para formar el nombre del archivo, no cambia datos del portal. |
| `subir` | Booleano, opcional, default `true`. | Omitir: publica el ZIP como archivo descargable. `null`, vacío o texto no booleano: rechazo. `false`: no publica archivo descargable ni agrega huella/tamaño. |

La fecha máxima permitida depende del mes en que se valide la solicitud. Los alias de un mismo dato no deben tener valores contradictorios.

## Credenciales

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `credentials.clave` | Texto, requerida para completar el acceso. | Omitida, `null` o `""`: la solicitud puede encolarse pero falla al ejecutar por falta de clave. `false`: tipo inválido. |

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

La salida de éxito con ZIP tiene este envelope estable en `data`:

| Ruta | Tipo | Cuándo aparece |
|---|---|---|
| `data.operacion` | Texto (`"descargar"`) | Siempre. |
| `data.representado_cuit` | Texto | Siempre. |
| `data.periodo` | Texto `AAAAMM` | Siempre. |
| `data.tipo` | Texto (`"RETPER ARBA"`) | Cuando hay archivo. |
| `data.archivo` | Texto, nombre ZIP | Cuando hay archivo. |
| `data.archivos` | Lista de objetos | Siempre en respuesta exitosa; vacía si no hay archivos. |
| `data.archivos[].tipo` | Texto (`"RETPER ARBA"`) | Por cada archivo descargado. |
| `data.archivos[].archivo` | Texto, nombre ZIP | Por cada archivo descargado. |
| `data.sha256` | Texto hexadecimal | Cuando hay ZIP y `subir=true`. |
| `data.size_bytes` | Entero | Cuando hay ZIP y `subir=true`. |
| `data.mensaje` | Texto | Sólo cuando el portal informa que no hay archivos; en ese caso `data.archivos=[]` y no aparecen tipo/nombre/huella. |

Para recuperar el ZIP publicado, consúmalo desde `files` del job. Si `subir=false`, el nombre permanece en `data` pero no hay archivo recuperable. La ausencia de datos es resultado correcto, no parcial; un fallo de descarga o publicación da error.