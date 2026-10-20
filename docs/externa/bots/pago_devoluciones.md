# Pago Devoluciones

## Propósito y operación

Consulta los pagos de devoluciones disponibles para un representado y exporta el resultado a un archivo de hoja de cálculo. No inicia ni modifica pagos o devoluciones.

Ruta V3: `POST /api/v3/bots/pago_devoluciones/consultar`. Alias compatible: `POST /api/v3/pago_devoluciones/consulta`. Use `{"payload": {...}, "credentials": {...}}`; la operación va en la ruta. La solicitud se acepta asíncronamente (`202`) y el resultado se consulta mediante `GET /api/v3/jobs/{job_id}`.

## Payload

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `payload` | `representado_cuit` | texto | No | `null` | CUIT cuyos pagos se consultan. Alias: `cuit`, `cuit_representado`. Se quitan espacios, guiones, puntos y `/` y se exigen 11 dígitos. Omitido, `null` o `""`: usa `credentials.cuit_representante`. Valor no vacío inválido: rechazo. | `"30876543210"` |
| `payload` | `incluir_json` | booleano | No | `true` | Incluye el nombre del archivo en `data.archivo`. `false` establece ese campo en `null`, pero conserva `data.detalle_archivo`. `null` se rechaza. | `true` |
| `payload` | `subir_archivo` | booleano | No | `true` | Adjunta la hoja de cálculo al trabajo. `false` evita el adjunto, no la consulta ni la exportación. `null` se rechaza. | `true` |

Debe estar activo `incluir_json` o `subir_archivo`; ambos en `false` se rechazan. Los campos desconocidos se rechazan. Los nombres `clave`, `clave_representante`, `clave_encriptada`, `cuit_representante`, `cuit_inicio_sesion`, `proxy_request` y `proxy` no son datos de consulta: no los envíe en `payload`.

## Credenciales

Se necesitan credenciales fiscales funcionales y acceso al servicio de pagos de devoluciones.

| Sección | Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|---|
| `envelope` | `credentials` | objeto | No | `{}` | Objeto de credenciales del sobre. Omitirlo usa `{}`. Un objeto vacío pasa el sobre pero no aporta acceso. `null`, `""` o el booleano `false` se rechazan por tipo. | `{}` |
| `credentials` | `cuit_representante` | texto | Condicional | ninguno | Identifica la cuenta que inicia sesión; requerido junto con una clave, salvo que se use un alias. Omitido, `null` o vacío no aporta identidad; el booleano `false` se rechaza por tipo. | `"20123456786"` |
| `credentials` | `cuit_inicio_sesion` | texto | No | ninguno | Alias opcional de `cuit_representante`; aporta la misma identidad. Omitido, `null` o vacío equivale a no enviarlo; `false` se rechaza. Si se envía con el otro nombre, los valores deben coincidir. | `"20123456786"` |
| `credentials` | `clave` | texto | Condicional | ninguno | Clave fiscal en texto; requerida junto con una identidad, salvo alias o alternativa cifrada. Omitida, `null` o vacío no aporta clave; `false` se rechaza por tipo. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_representante` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o vacío equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `contrasena` | texto | No | ninguno | Alias opcional de `clave`; aporta la clave fiscal. Omitida, `null` o vacío equivale a no enviarla; `false` se rechaza. Si se repite con otro alias de clave, los valores deben coincidir. | `"Clave-Ficticia-No-Usar-2026"` |
| `credentials` | `clave_encriptada` | texto | No | ninguno | Alternativa cifrada en Base64; se descifra mediante el mecanismo admitido por la API. Omitida, `null` o vacío equivale a ausencia; `false` o cifrado inválido se rechaza. Si se envía con una clave en texto, ambas deben coincidir. | `"Q0xBVkVfRklDVElDSUFfTk9fVVNFRw=="` |

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