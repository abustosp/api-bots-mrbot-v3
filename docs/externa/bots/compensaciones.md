# Compensaciones: consulta y exportación

## Propósito, operación y efectos

Consulta compensaciones y afectaciones para un CUIT en un rango de fechas y puede exportar Excel, CSV y/o PDF. Es de solo lectura: no aplica ni confirma compensaciones.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/compensaciones/consultar`
- La solicitud se ejecuta de manera asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para resultado y archivos.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT del contribuyente consultado. Aliases: `cuit`, `cuit_representado`. Se eliminan espacios, guiones, puntos y barras y deben quedar exactamente 11 dígitos. Omitir, `null`, vacío o `false`: rechazo. | `"20123456786"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha real `dd/mm/aaaa`. Alias: `desde`. Omitir, `null`, vacío o `false`: rechazo. | `"01/01/2024"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha real `dd/mm/aaaa`. Alias: `hasta`. No puede ser anterior a `fecha_desde`. Omitir, `null`, vacío o `false`: rechazo. | `"31/01/2024"` |
| `excel` | booleano | No | `false` | `true` solicita la exportación Excel. Omitir: no solicita Excel. `null` o vacío: rechazo. `false`: desactivado. | `true` |
| `csv` | booleano | No | `false` | `true` solicita la exportación CSV. Omitir: no solicita CSV. `null` o vacío: rechazo. `false`: desactivado. | `true` |
| `pdf` | booleano | No | `false` | `true` solicita la exportación PDF. Omitir: no solicita PDF. `null` o vacío: rechazo. `false`: desactivado. | `false` |
| `subir` | booleano | No | `true` | `true` publica los archivos generados. Omitir: publica archivos generados. `null` o vacío: rechazo. `false`: no publica archivos descargables ni informa huellas/tamaños. | `true` |
| `cuit_representante` | texto | No | `null` | Compatibilidad antigua, no se usa para iniciar sesión. Aliases: `cuit_inicio_sesion`, `cuit_login`. Se acepta y se ignora. `null` permitido; `false` inválido. Use `credentials` para credenciales. | `"20123456786"` |
| `clave_representante` | texto | No | `null` | Compatibilidad antigua, no se usa para iniciar sesión. Aliases: `contrasena`, `clave`. Se acepta y se ignora. `null` permitido; `false` inválido. Use `credentials` para la clave. | `"CLAVE_FICTICIA_NO_USAR"` |
| `proxy_request` | booleano | No | `null` | Compatibilidad antigua. Se acepta y se ignora; `null` permitido. `false` no cambia el acceso. | `false` |
| `headless` | booleano | No | `null` | Compatibilidad antigua. Se acepta y se ignora; `null` permitido. `false` no cambia la ejecución. | `false` |

Debe activarse al menos uno de `excel`, `csv` o `pdf`. Enviar alias y campo canónico simultáneamente puede producir un pedido inválido; use un solo nombre por dato.

## Credenciales

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Titular que inicia sesión. Condicional: sin valor no puede completarse el acceso. Se aceptan alias `cuit_inicio_sesion` y `cuit_login`. `null` o `""` se consideran ausentes. `false`: tipo inválido. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Clave fiscal. Condicional: necesaria para completar el acceso. Se aceptan alias `clave_representante` y `contrasena`, y una clave cifrada válida para el servicio. `null` o `""` se consideran ausentes. `false`: tipo inválido. | `"CLAVE_FICTICIA_NO_USAR"` |

Se aceptan nombres compatibles como `cuit_inicio_sesion`, `cuit_login`, `clave_representante` o `contrasena`, y una clave cifrada válida para el servicio. El CUIT representado puede ser distinto del de inicio de sesión.

## Ejemplo completo ficticio

El CUIT pasa el control de dígito verificador, pero no se presenta como identidad real. La clave es un marcador inválido.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "fecha_desde": "01/01/2024",
    "fecha_hasta": "31/01/2024",
    "excel": true,
    "csv": true,
    "pdf": false,
    "subir": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y resultados parciales

`data` expone el siguiente envelope estable; los nombres y contenidos de los archivos dependen de los formatos solicitados:

| Ruta | Tipo | Cuándo aparece |
|---|---|---|
| `data.operacion` | Texto (`"consultar"`) | Siempre en una consulta exitosa. |
| `data.representado_cuit` | Texto | Siempre. |
| `data.fecha_desde`, `data.fecha_hasta` | Texto `dd/mm/aaaa` | Siempre. |
| `data.mensaje`, `data.message` | Texto | Cuando no hay resultados; ambos contienen `No se encontraron resultados`. No se crean exportaciones en ese caso. |
| `data.excel`, `data.csv`, `data.pdf` | Objeto por formato pedido y exportado; cada objeto contiene `archivo` (texto). | Sólo si el formato se exportó correctamente. Si `subir=true`, el objeto también contiene `sha256` (texto hexadecimal) y `size_bytes` (entero). |
| `data.warnings` | Lista de textos | Si falla al menos un formato y otro formato sí se exporta. |
| `files` del job | Lista de archivos publicados | Contiene únicamente exportaciones que se publicaron correctamente. |

Si falla un formato, se intentan los demás y el job exitoso informa el fallo en `warnings`. Si fallan todos los formatos pedidos, el job termina con error. Con `subir=false` quedan nombres y referencias nulas en `data`, pero ningún archivo descargable.
