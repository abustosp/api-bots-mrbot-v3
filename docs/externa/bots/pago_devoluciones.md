# Pago Devoluciones

## Propósito y operación

Consulta los pagos de devoluciones disponibles para un representado y exporta el resultado a un archivo de hoja de cálculo. No inicia ni modifica pagos o devoluciones.

Ruta V3: `POST /api/v3/bots/pago_devoluciones/consultar`. Alias compatible: `POST /api/v3/pago_devoluciones/consulta`. Use `{"payload": {...}, "credentials": {...}}`; la operación va en la ruta. La solicitud se acepta asíncronamente (`202`) y el resultado se consulta mediante `GET /api/v3/jobs/{job_id}`.

## Payload

| Campo | Tipo; requerido; default | Efecto y comportamiento al omitir, enviar `null`, vacío o `false` |
|---|---|---|
| `representado_cuit` | texto de 11 dígitos; opcional; `null` | CUIT cuyos pagos se consultan. Aliases `cuit` y `cuit_representado`. Se quitan espacios, guiones, puntos y `/`. Omitido, `null` o `""`: usa `credentials.cuit_representante`. Valor no vacío inválido: rechazo. |
| `incluir_json` | booleano; opcional; `true` | Incluye el nombre del archivo en `data.archivo`. `false` establece ese campo en `null`, pero conserva `data.detalle_archivo`. `null` se rechaza. |
| `subir_archivo` | booleano; opcional; `true` | Adjunta la hoja de cálculo al trabajo. `false` evita el adjunto, no la consulta ni la exportación. `null` se rechaza. Aliases compatibles: `carga_minio`, `minio_upload`, `archivo_historico_minio`. |

Debe estar activo `incluir_json` o `subir_archivo`; ambos en `false` se rechazan. Los campos desconocidos se rechazan. Los nombres `clave`, `clave_representante`, `clave_encriptada`, `cuit_representante`, `cuit_inicio_sesion`, `proxy_request` y `proxy` no son datos de consulta: no los envíe en `payload`.

## Credenciales

Se necesitan credenciales fiscales funcionales y acceso al servicio de pagos de devoluciones.

| Campo en `credentials` | Tipo; requerido; default | Omisión, `null` o vacío |
|---|---|---|
| `cuit_representante` | texto; requerido operativamente; sin default | Identidad que inicia sesión y, si `representado_cuit` no se aporta, CUIT consultado. Se acepta `cuit_inicio_sesion` como alias. Si falta, no puede iniciarse sesión ni resolverse el representado. |
| `clave` | texto; requerida operativamente; sin default | Clave fiscal; alias compatibles `clave_representante` y `contrasena`. Todas las variantes omitidas, `null` o vacías equivalen a no tener clave y el acceso no se completa. |
| `clave_encriptada` | texto cifrado; alternativa opcional | Solo admite el formato cifrado publicado por la API. `null` o vacío equivale a ausencia; valor inválido se rechaza. Si se acompaña de texto claro, ambos deben coincidir. |

No envíe credenciales en `payload`. Si se repiten aliases, sus valores deben coincidir.

## Ejemplo ficticio válido

```json
{
  "payload": {
    "representado_cuit": "30876543210",
    "incluir_json": true,
    "subir_archivo": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "Clave-Ficticia-No-Usar-2026"
  }
}
```

## Resultado y archivos

El resultado incluye `representado_cuit`, `archivo`, `detalle_archivo` y `errores_por_seccion`. `detalle_archivo` puede incluir nombre y tamaño y, cuando se adjunta el archivo, huella y tamaño reportados. El archivo XLSX se adjunta en `files[]` si `subir_archivo` está activo. Con `incluir_json: false`, `archivo` queda en `null`, pero `detalle_archivo` permanece.

Errores críticos de validación, acceso, servicio, representado o consulta producen estado `ERROR`; errores no críticos pueden producir `PARCIAL`. Una subida fallida genera error de archivo, no un éxito parcial silencioso. Si no hay archivo exportado, revise el resultado y sus errores antes de interpretar el resumen.