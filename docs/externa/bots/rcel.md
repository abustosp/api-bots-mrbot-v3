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

`data` informa operación, CUIT, rango y cantidad de comprobantes. Si `incluir_json=true`, `facturas` contiene cada `archivo`, campos extraídos cuando se solicitaron, y, si se publicó el PDF, su huella y tamaño. Los campos extraídos pueden incluir emisor/receptor, CUIT, punto de venta, número, fechas, importes, IVA, CAE y vencimiento; los valores no encontrados quedan vacíos. El PDF no se incrusta como texto codificado.

Puede no haber comprobantes: la respuesta normal es `cantidad: 0` y lista vacía, no un error. Un fallo en descarga, extracción o publicación de una factura no detiene necesariamente las demás: el trabajo queda `PARCIAL`, expone errores por comprobante y conserva los éxitos. Un comprobante que no pudo extraerse o publicarse queda fuera de la lista aceptada. La operación consulta y descarga, no emite ni modifica comprobantes.
