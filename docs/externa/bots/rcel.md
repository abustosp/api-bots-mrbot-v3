# RCEL

**Propósito.** Buscar comprobantes emitidos al CUIT indicado dentro de un rango de fechas, descargar sus PDF y, opcionalmente, incluir campos leídos de cada comprobante.

## Operación y ruta V3

Catálogo: `rcel`, operación `descargar`.

- Canónica: `POST /api/v3/bots/rcel/descargar`.
- Alias V3: `POST /api/v3/rcel/consulta`.

Cuerpo: `{ "payload": { ... }, "credentials": { ... } }`. Una aceptación devuelve `job_id`; el resultado se consulta en `/api/v3/jobs/{job_id}`.

## Credenciales

Se necesita clave fiscal y CUIT representante en `credentials.clave` y `credentials.cuit_representante`. `clave_representante` y `contrasena` son nombres alternativos; los valores no vacíos deben coincidir si se envían varios. También se admite `clave_encriptada` cifrada en Base64 y `cuit_inicio_sesion` como alias del CUIT de sesión. Omitir, `null` o `""` en todas las claves deja el trabajo sin autenticación y la ejecución no podrá continuar. Los campos de clave en el payload se trasladan a credenciales; no enviar claves distintas. Para iniciar sesión se requiere un CUIT representante válido.

## Campos del payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT de 11 dígitos; se quitan espacios, guiones, puntos y `/`. Alias: `cuit`. Vacío o `null` inválidos. Identifica la cuenta cuyos comprobantes se buscan. | `"20-12345678-6"` |
| `representado_nombre` | texto | Sí | ninguno | Nombre de 1–128 caracteres; alias V3 compatibles `denominacion` y `nombre_rcel`. Se usa para seleccionar y verificar la cuenta activa. Vacío o `null` inválidos. El portal no continúa sin el nombre, aunque el CUIT sea correcto. | `"Comercial Ficticia SA"` |
| `fecha_desde` | texto | Sí | ninguno | Fecha real `DD/MM/AAAA`; se valida el calendario. Alias: `desde`. Vacío, `null` o formato inválido se rechazan. | `"01/01/2026"` |
| `fecha_hasta` | texto | Sí | ninguno | Fecha real `DD/MM/AAAA`; debe ser igual o posterior a `fecha_desde`. Alias: `hasta`. Vacío o `null` inválidos. | `"31/01/2026"` |
| `incluir_json` | booleano | No | `true` | `true` incluye el listado resumido de facturas en `data`; `false` omite la lista sin impedir las descargas. Omitir equivale a `true`; `null` inválido. | `true` |
| `subir_pdf` | booleano | No | `true` | `true` publica cada PDF en los resultados; `false` conserva la descarga solo durante el trabajo y no publica el archivo. Omitir equivale a `true`; `null` inválido. | `true` |
| `extraer_campos` | booleano | No | `true` | `true` intenta extraer campos del PDF; `false` devuelve solo el nombre y tamaño de cada comprobante aceptado. Omitir equivale a `true`; `null` inválido. | `true` |

No se aceptan otros campos del payload. Las fechas invertidas se rechazan. Se exige al menos una salida: `incluir_json=true` o `subir_pdf=true`.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "20-12345678-6",
    "representado_nombre": "Comercial Ficticia SA",
    "fecha_desde": "01/01/2026",
    "fecha_hasta": "31/01/2026",
    "incluir_json": true,
    "subir_pdf": true,
    "extraer_campos": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FISCAL_FICTICIA"
  }
}
```

## Resultado y archivos

En la respuesta de `GET /api/v3/jobs/{job_id}`, `result` indica el estado final, `data` contiene los campos de la operación y `files` enumera los archivos publicados. `files` es una lista vacía cuando no hay archivos publicados.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el trabajo | `OK` si no hubo fallos por comprobante; `PARCIAL` si se registraron fallos procesables; `ERROR` si falla el trabajo completo. | `"PARCIAL"` |
| `data.operacion` | texto | En un resultado de operación disponible | Nombre de la operación. | `"descargar"` |
| `data.representado_cuit` | texto | En un resultado de operación disponible | CUIT consultado, normalizado a 11 dígitos. | `"20123456786"` |
| `data.fecha_desde`, `data.fecha_hasta` | texto | En un resultado de operación disponible | Límites del rango consultado, en `DD/MM/AAAA`. | `{"fecha_desde":"01/01/2026","fecha_hasta":"31/01/2026"}` |
| `data.cantidad` | entero | En un resultado de operación disponible | Cantidad de comprobantes aceptados para la salida. | `2` |
| `data.facturas` | lista de objetos | Con `incluir_json=true` (predeterminado); se omite si es `false` | Lista de comprobantes aceptados. Puede ser `[]` si no se encontraron comprobantes. | `[{"archivo":"factura-demo.pdf"}]` |
| `data.facturas[].archivo` | texto | Por cada elemento de `data.facturas` | Nombre del PDF descargado. El contenido del PDF no se incluye como texto. | `"factura-demo.pdf"` |
| `data.facturas[].campos` | objeto | Cuando `extraer_campos=true` y la extracción de ese PDF termina correctamente | Datos legibles del comprobante, como emisor, receptor, fechas, importes, IVA, CAE y vencimiento. Los valores no encontrados pueden ser cadenas vacías. | `{"Número de Factura":"00000001","Importe Total":"1250"}` |
| `data.facturas[].size_bytes` | entero | Por cada comprobante aceptado cuando `incluir_json=true`, se publique o no el PDF | Tamaño del archivo en bytes. | `18432` |
| `data.facturas[].sha256` | texto | Solo con `incluir_json=true`, `subir_pdf=true` y publicación exitosa de ese PDF | Huella del PDF publicado. | `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"` |
| `files[]` | lista de archivos | Siempre en la respuesta del trabajo; un elemento por PDF publicado | Referencia pública del archivo. Sin publicaciones, el valor es `[]`. | `[{"name":"factura-demo.pdf","url":"","filename":"factura-demo.pdf","size":18432,"size_bytes":18432}]` |

Si no hay comprobantes, `data.cantidad` es `0` y, con `incluir_json=true`, `data.facturas` es `[]`, no `null`. Con `incluir_json=false`, `data.facturas` no aparece, aunque `data.cantidad` se conserva y los PDF aún pueden publicarse. Un fallo de descarga, extracción o publicación por comprobante puede dejar el trabajo `PARCIAL`; se conservan los comprobantes aceptados y los demás no se agregan a `data.facturas`. La operación consulta y descarga, no emite ni modifica comprobantes.
