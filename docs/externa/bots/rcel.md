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

| Campo | Tipo, requerido y default | Efecto y tratamiento de omisión/null/vacío/false |
|---|---|---|
| `representado_cuit` | texto, requerido; alias `cuit` | CUIT de 11 dígitos; se quitan espacios, guiones, puntos y `/`. Vacío/null inválidos. Identifica la cuenta cuyos comprobantes se buscan. |
| `representado_nombre` | texto, requerido, 1–128 caracteres; aliases V3 compatibles `denominacion` y `nombre_rcel` | Se usa para seleccionar y verificar la cuenta activa. Vacío/null inválidos. El portal no continúa sin el nombre, aunque el CUIT sea correcto. |
| `fecha_desde` | texto, requerido; alias `desde` | Fecha real `DD/MM/AAAA`; se valida el calendario. Vacío/null/formato inválidos. |
| `fecha_hasta` | texto, requerido; alias `hasta` | Fecha real `DD/MM/AAAA`; debe ser igual o posterior a `fecha_desde`. Vacío/null inválidos. |
| `incluir_json` | booleano, default `true` | `true` incluye listado resumido de facturas en `data`; `false` omite la lista, sin impedir descargas. Omitir = `true`; `null` inválido. |
| `subir_pdf` | booleano, default `true`; alias `minio_upload` | `true` publica cada PDF en los resultados; `false` conserva la descarga solo durante el trabajo y no publica el archivo. Omitir = `true`; `null` inválido. Los nombres de compatibilidad V3 para subida se normalizan al campo equivalente. |
| `extraer_campos` | booleano, default `true` | `true` intenta extraer campos del PDF; `false` devuelve solo el nombre/tamaño de cada comprobante aceptado. Omitir = `true`; `null` inválido. |

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

`data` informa operación, CUIT, rango y cantidad de comprobantes. Si `incluir_json=true`, `facturas` contiene cada `archivo`, campos extraídos cuando se solicitaron, y, si se publicó el PDF, su huella y tamaño. Los campos extraídos pueden incluir emisor/receptor, CUIT, punto de venta, número, fechas, importes, IVA, CAE y vencimiento; los valores no encontrados quedan vacíos. El PDF no se incrusta como texto codificado.

Puede no haber comprobantes: la respuesta normal es `cantidad: 0` y lista vacía, no un error. Un fallo en descarga, extracción o publicación de una factura no detiene necesariamente las demás: el trabajo queda `PARCIAL`, expone errores por comprobante y conserva los éxitos. Un comprobante que no pudo extraerse o publicarse queda fuera de la lista aceptada. La operación consulta y descarga, no emite ni modifica comprobantes.