# CCMA: consulta de cuentas corrientes

## Propósito, operación y efectos

Consulta saldos y estados de obligaciones del CUIT representado. Puede pedir movimientos y un PDF como salidas adicionales. Es una operación de lectura: no paga, compensa ni modifica saldos.

- **Operación del catálogo:** `consultar`
- **Ruta V3:** `POST /api/v3/bots/ccma/consultar`
- La solicitud se procesa en forma asíncrona; consulte `GET /api/v3/jobs/{job_id}` para resultado y archivos.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT representado. Alias: `cuit`, `cuit_representado`. Omitir, `null`, vacío o `false`: rechazo. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. Si se proporcionan varios alias, deben coincidir. | `"20123456786"` |
| `periodo_desde` | texto | No | `"04/2000"` | Primer período consultado, con formato `MM/AAAA`. Omitir o `null`: conserva el default. Vacío, formato incorrecto o mes fuera de 01 a 12: rechazo. `false`: rechazo. | `"04/2000"` |
| `incluir_movimientos` | booleano | No | `false` | Solicita los movimientos de la cuenta. Alias: `movimientos`. Omitir: no solicita movimientos. `null` o vacío: rechazo. `false`: no incluir. | `true` |
| `incluir_pdf` | booleano | No | `false` | Solicita un PDF del resumen. Alias: `pdf`. Omitir: no solicita PDF. `null` o vacío: rechazo. `false`: no incluir. `true`: también activa movimientos, aun si `incluir_movimientos` se omitió o es `false`. | `false` |
| `subir` | booleano | No | `true` | Publica los artefactos generados. Omitir: publica artefactos generados. `null` o vacío: rechazo. `false`: no publica archivos descargables ni agrega sus huellas o tamaños. | `true` |

## Credenciales

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `credentials.cuit_representante` | texto | Condicional | ninguno | Cuenta fiscal con la que se inicia sesión; se requiere para completar el acceso. Sin valor, `null` o `""` se consideran ausentes. `false`: tipo inválido. | `"20123456786"` |
| `credentials.clave` | texto | Condicional | ninguno | Clave fiscal; se requiere para completar el acceso. `null` o `""` se consideran ausentes. `false`: tipo inválido. | `"CLAVE_FISCAL_FICTICIA"` |

También se aceptan los nombres compatibles de credenciales como `cuit_inicio_sesion`, `cuit_login`, `clave_representante` o `contrasena`; la forma cifrada requiere un valor válido para el servicio. CUIT y clave de acceso pueden ser distintos del CUIT consultado cuando hay representación.

## Ejemplo completo ficticio

El CUIT es ilustrativo y pasa el control de dígito verificador; no identifica aquí a una entidad real. La clave es ficticia y no permite iniciar sesión.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "periodo_desde": "04/2000",
    "incluir_movimientos": true,
    "incluir_pdf": false,
    "subir": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y parciales

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones. Los valores del resumen y de los movimientos se devuelven como texto.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el job | Una consulta completada devuelve `OK`. Un corte de lectura de movimientos no cambia por sí solo este resultado a parcial. | `"OK"` |
| `data.operacion` | texto | En una consulta correcta | Operación ejecutada. | `"consultar"` |
| `data.representado_cuit` | texto | En una consulta correcta | CUIT consultado. | `"20123456786"` |
| `data.periodo_desde` | texto | En una consulta correcta | Período inicial aplicado, incluido el default cuando se omitió en la solicitud. | `"04/2000"` |
| `data.cuit` | texto | Si se obtuvo el resumen | CUIT incluido en el resumen. | `"20123456786"` |
| `data.periodo` | texto | Si se obtuvo el resumen | Período indicado por el resumen. | `"<texto del resumen>"` |
| `data.deuda_capital` | texto | Si se obtuvo el resumen | Deuda de capital. | `"<importe>"` |
| `data.deuda_accesorios` | texto | Si se obtuvo el resumen | Deuda de accesorios. | `"<importe>"` |
| `data.total_deuda` | texto | Si se obtuvo el resumen | Total de deuda. | `"<importe>"` |
| `data.credito_capital` | texto | Si se obtuvo el resumen | Crédito de capital. | `"<importe>"` |
| `data.credito_accesorios` | texto | Si se obtuvo el resumen | Crédito de accesorios. | `"<importe>"` |
| `data.total_a_favor` | texto | Si se obtuvo el resumen | Total a favor. | `"<importe>"` |
| `data.movimientos` | lista de objetos | Si se solicitan movimientos o se activan al pedir PDF | Cada movimiento tiene los campos textuales `periodo`, `impuesto`, `concepto`, `subconcepto`, `descripcion`, `fecha_movimiento`, `debe` y `haber`. La lista puede estar vacía. | `[]` |
| `data.movimientos_corte` | texto | Cuando se recorren movimientos | Indica el motivo de cierre o corte del recorrido. | `"<motivo>"` |
| `data.movimientos_completos` | booleano | Sólo ante ciertos cortes | Se informa como `false` para esos cortes. Si se omite, no se emitió una marca explícita `true`. | `false` |
| `data.archivo` | texto | Si se genera PDF y `subir=false` | Nombre del PDF generado, no publicado para descarga. | `"<nombre>.pdf"` |
| `data.sha256` | texto hexadecimal | Si se genera PDF y `subir=true` | Huella del PDF publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si se genera PDF y `subir=true` | Tamaño del PDF publicado en bytes. | `12345` |
| `files` | lista de archivos | En la respuesta final | Contiene el PDF publicado; queda vacía si no se pidió PDF o si `subir=false`. | `[]` |

Los movimientos tienen límites de recorrido de 100 páginas y 500 registros. Un corte se comunica en `data`; la operación no declara resultado parcial por ese motivo.
