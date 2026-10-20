# Hacienda

Consulta comprobantes en el servicio Hacienda de ARCA por emisor y/o receptor, dentro de un rango de fechas. Consolida los datos en una planilla y puede descargar PDF individuales. Es de solo lectura en el portal.

## Ruta V3 y catálogo

`POST /api/v3/bots/hacienda/consultar` (2 créditos). Alias de compatibilidad: `/hacienda/consulta`. Cuerpo canónico: `{"payload": {...}, "credentials": {...}}`. CUIT en V3: exactamente 11 dígitos sin separadores.

## Campos de `payload`

| Campo | Tipo; requerido/default | Omisión, `null`, vacío o `false`; efecto |
|---|---|---|
| `representado_cuit` | string, requerido, 11 dígitos; aliases `cuit`, `cuit_representado` en ejecución | CUIT consultado. En V3 público debe enviarse el nombre canónico. Vacío/nulo inválido. |
| `denominacion` | string 1–256, requerida | Denominación que se selecciona en el portal. Vacía/nula inválida. |
| `fecha_desde`, `fecha_hasta` | strings requeridos `DD/MM/AAAA`; aliases `desde`, `hasta` | Fechas reales y rango inclusivo en orden ascendente. Vacío/nulo inválido. |
| `por_emisor`, `por_receptor` | boolean, default ambos `true` | Se requiere al menos uno verdadero. `false` excluye ese tipo de consulta. Nulo público aplica default. |
| `incluir_json` | boolean, default `true` | `false` omite muestras de filas, no contadores ni Excel/PDF. |
| `subir_excel` | boolean, default `true`; aliases `subir_a_minio`, `carga_minio` | `false` no publica la planilla en `files[]`, pero sí la genera y conserva su resumen. No evita PDFs individuales. |
| `descargar_pdf` | boolean, default `true` | `false` consulta las filas sin bajar PDF individuales. Si es true, los PDFs que se obtienen pueden publicarse en `files[]` independientemente de `subir_excel`. |
| `cuit_representante` | CUIT de sesión opcional en el esquema de ejecución; aliases `cuit_inicio_sesion`, `cuit_login` | La sesión debe enviarse en `credentials`; campo heredado para contexto. |
| `eliminar_descargas` | boolean, default `true` | Aceptado por compatibilidad, no cambia la consulta ni los resultados actuales. |
| `proxy_request`, `headless` | boolean nullable opcional, legado | No seleccionan la conexión ni alteran el comportamiento público actual. |

Debe activarse al menos un tipo de consulta y al menos una salida (`incluir_json` o `subir_excel`). Los booleanos deben enviarse como booleanos JSON.

## Credenciales

Se requieren `credentials.cuit_representante` (11 dígitos) y `credentials.clave`. Se aceptan `clave_representante`, `contrasena` o la alternativa cifrada `clave_encriptada`. Sin ambas credenciales no se inicia sesión. No incluir secretos reales en ejemplos.

## Ejemplo ficticio

Ruta: `POST /api/v3/bots/hacienda/consultar`.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "denominacion": "Comercial Ejemplo",
    "fecha_desde": "01/08/2026",
    "fecha_hasta": "31/08/2026",
    "por_emisor": true,
    "por_receptor": true,
    "incluir_json": true,
    "subir_excel": true,
    "descargar_pdf": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE-FICTICIA-NO-UTILIZABLE"
  }
}
```

## Resultado y faltantes

`result` incluye representado, denominación y fechas; por cada consulta activada, contador de filas y, si se pidió, muestra. `consolidado` resume archivo, número de filas y hojas; la planilla Excel se añade a `files[]` si `subir_excel=true`. Si `descargar_pdf=true`, la sección de PDF informa total, subidos y errores por comprobante; un PDF individual fallido no detiene toda la consulta. Con `incluir_json=false` quedan los contadores y resúmenes, no las filas de muestra. No se modifica ni presenta información fiscal.
