# Compensaciones: consulta y exportación

## Propósito, operación y efectos

Consulta compensaciones y afectaciones para un CUIT en un rango de fechas y puede exportar Excel, CSV y/o PDF. Es de solo lectura: no aplica ni confirma compensaciones.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/compensaciones/consultar`
- La solicitud se ejecuta de manera asíncrona. Consulte `GET /api/v3/jobs/{job_id}` para resultado y archivos.

## Payload

| Campo | Tipo, requerido y default | Omitir / `null` / vacío / `false` |
|---|---|---|
| `representado_cuit` | Texto, requerido. Alias: `cuit`, `cuit_representado`. | Omitir, `null`, vacío o `false`: rechazo. V3 exige 11 dígitos sin separadores. |
| `fecha_desde` | Texto, requerido, fecha real `dd/mm/aaaa`. Alias: `desde`. | Omitir, `null`, vacío o `false`: rechazo. |
| `fecha_hasta` | Texto, requerido, fecha real `dd/mm/aaaa`. Alias: `hasta`. | Omitir, `null`, vacío o `false`: rechazo. No puede ser anterior a `fecha_desde`. |
| `excel` | Booleano, default `false`. | Omitir: no solicita Excel. `null` o vacío: rechazo. `false`: desactivado. |
| `csv` | Booleano, default `false`. | Omitir: no solicita CSV. `null` o vacío: rechazo. `false`: desactivado. |
| `pdf` | Booleano, default `false`. | Omitir: no solicita PDF. `null` o vacío: rechazo. `false`: desactivado. |
| `subir` | Booleano, default `true`. Alias: `carga_minio`, `subir_archivo`. | Omitir: publica archivos generados. `null` o vacío: rechazo. `false`: no publica archivos descargables ni informa huellas/tamaños. |
| `cuit_representante` | Texto o `null`, opcional, default `null`; compatibilidad antigua, no se usa para iniciar sesión. Alias: `cuit_inicio_sesion`, `cuit_login`. | Se acepta y se ignora. `null` permitido; `false` inválido. Use `credentials` para credenciales. |
| `clave_representante` | Texto o `null`, opcional, default `null`; compatibilidad antigua, no se usa para iniciar sesión. Alias: `contrasena`, `clave`. | Se acepta y se ignora. `null` permitido; `false` inválido. Use `credentials` para la clave. |
| `proxy_request` | Booleano o `null`, opcional, default `null`; compatibilidad antigua. | Se acepta y se ignora; `null` permitido. `false` no cambia el acceso. |
| `headless` | Booleano o `null`, opcional, default `null`; compatibilidad antigua. | Se acepta y se ignora; `null` permitido. `false` no cambia la ejecución. |

Debe activarse al menos uno de `excel`, `csv` o `pdf`. Enviar alias y campo canónico simultáneamente puede producir un pedido inválido; use un solo nombre por dato.

## Credenciales

| Campo | Tipo y necesidad efectiva | Omitir / `null` / vacío / `false` |
|---|---|---|
| `credentials.cuit_representante` | Texto; titular que inicia sesión. | Sin valor no puede completarse el acceso. `null` o `""` se consideran ausentes. `false`: tipo inválido. |
| `credentials.clave` | Texto; clave fiscal. | Necesaria para completar el acceso. `null` o `""` se consideran ausentes. `false`: tipo inválido. |

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

Sin filas, `data` incluye el CUIT y rango consultados, además del mensaje `No se encontraron resultados`, y el resultado es correcto sin exportaciones. Si hay datos, cada formato solicitado que se exporta agrega su resumen `archivo`; cuando `subir=true`, agrega huella y tamaño y queda en `files`. Si falla uno de varios formatos, los restantes continúan y el resultado incluye `warnings`; si fallan todos los formatos pedidos, el job termina con error. Con `subir=false` no quedan archivos descargables.