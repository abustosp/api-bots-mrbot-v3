# CCMA: propuesta y reafectación de saldos a favor

## Propósito y operación

Consulta la cuenta corriente de Monotributistas y Autónomos, prepara una propuesta para reafectar pagos con saldo a favor y, solo cuando se solicita expresamente, confirma las reafectaciones en ARCA.

- **Operación del catálogo:** `compensar`
- **Ruta V3:** `POST /api/v3/bots/ccma_compensacion/compensar`
- La respuesta inicial crea un job. Consulte `GET /api/v3/jobs/{job_id}` para revisar propuesta, confirmaciones, avisos y archivos.

### Efecto importante

`confirmar: true` puede modificar la cuenta corriente: se confirman en ARCA las reafectaciones que el portal haya aplicado. No lo use como una mera consulta. Con `confirmar: false` (default), el portal cancela cada formulario de reafectación y devuelve el detalle simulado, sin confirmar cambios. `compensar: false` omite por completo el paso de reafectación, pero mantiene la consulta del resumen.

## Payload

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `representado_cuit` | texto | Sí | ninguno | CUIT del representado. Alias: `cuit`, `cuit_representado`. Se eliminan espacios, guiones, puntos y barras; deben quedar exactamente 11 dígitos. Omitir o `null`: rechazo. Vacío o `false`: inválido. | `"20123456786"` |
| `compensar` | booleano | No | `true` | Prepara las reafectaciones de pagos con saldo a favor y mantiene la consulta del resumen. Omitir o `null`: se trata como `true`. Vacío: inválido. `false`: no ejecuta el paso de reafectación y no aparece `data.compensaciones`. | `true` |
| `confirmar` | booleano | No | `false` | Confirma en ARCA las reafectaciones aplicadas y puede modificar la cuenta corriente. Omitir o `null`: `false`. Vacío: inválido. `false`: cancela las propuestas, sin modificación confirmada. `true`: confirma las reafectaciones aplicadas. | `false` |
| `incluir_movimientos` | booleano | No | `false` | Solicita la lista de movimientos. Alias: `movimientos`. Omitir: no solicita movimientos. `null` o vacío: inválido. `false`: omite la lista, salvo que `incluir_pdf` active los movimientos automáticamente. | `false` |
| `incluir_pdf` | booleano | No | `false` | Genera un PDF del resumen. Alias: `pdf`. Omitir: no genera PDF. `null` o vacío: inválido. `false`: no genera PDF. `true`: genera PDF y activa también `incluir_movimientos`. | `false` |
| `subir` | booleano | No | `true` | Publica el PDF si se pidió. Omitir: publica el PDF solicitado. `null` o vacío: inválido. `false`: no publica el PDF; no afecta la reafectación. | `true` |

## Credenciales

`credentials` es un objeto separado. Para completar el acceso fiscal, incluya el CUIT de inicio de sesión y una clave fiscal no vacía. Si faltan, el job podrá ser aceptado, pero el inicio de sesión no podrá completarse.

| Campo | Tipo | Requerido | Default | Descripción | Ejemplo |
|---|---|---|---|---|---|
| `cuit_representante` | texto | Condicional | ninguno | CUIT de la cuenta que inicia sesión; se requiere para completar el acceso fiscal. Sin valor, `null` o `""`: falta el CUIT de acceso. `false` no es texto válido. | `"20123456786"` |
| `clave` | texto | Condicional | ninguno | Clave fiscal; se requiere junto con el CUIT para completar el acceso. Sin valor, `null` o `""`: falta la clave. `false` no es texto válido. | `"CLAVE_FISCAL_FICTICIA"` |

Se reconocen `clave_representante` y `contrasena` como nombres compatibles de `clave`, y `clave_encriptada` como alternativa cifrada. Si se envían varias formas de clave no vacías, sus valores deben coincidir. Credenciales ficticias solo sirven para ilustrar la forma del objeto, no para iniciar sesión.

## Ejemplo completo ficticio, en modo propuesta

`confirmar` se deja explícitamente en `false`. Los valores de credenciales son ficticios y no deben usarse en una cuenta real.

```json
{
  "payload": {
    "representado_cuit": "20123456786",
    "compensar": true,
    "confirmar": false,
    "incluir_movimientos": false,
    "incluir_pdf": false,
    "subir": true
  },
  "credentials": {
    "cuit_representante": "20123456786",
    "clave": "CLAVE_FICTICIA_NO_USAR"
  }
}
```

## Resultado, archivos y resultados parciales

La respuesta final del job expone `result`, `data` y `files`. Los ejemplos son ilustrativos y no proceden de ejecuciones. Los campos del resumen conservan los textos obtenidos en la consulta.

| Ruta | Tipo | Condición de presencia | Descripción | Ejemplo |
|---|---|---|---|---|
| `result` | texto o `null` | Al finalizar el job | `OK` si no se reportan avisos ni errores en el detalle; `PARCIAL` si los hay. | `"OK"` |
| `data.operacion` | texto | En una respuesta correcta o parcial | Operación ejecutada. | `"compensar"` |
| `data.representado_cuit` | texto | En una respuesta correcta o parcial | CUIT consultado. | `"20123456786"` |
| `data.compensar` | booleano | En una respuesta correcta o parcial | Indica si se intentó el paso de reafectación. | `true` |
| `data.confirmar` | booleano | En una respuesta correcta o parcial | Modo solicitado; por sí solo no confirma que se hayan aplicado cambios. | `false` |
| `data.cuit` | texto | Si se obtuvo el resumen | CUIT incluido en el resumen. | `"20123456786"` |
| `data.periodo` | texto | Si se obtuvo el resumen | Período incluido en el resumen. | `"<texto del resumen>"` |
| `data.deuda_capital` | texto | Si se obtuvo el resumen | Deuda de capital. | `"<importe>"` |
| `data.deuda_accesorios` | texto | Si se obtuvo el resumen | Deuda de accesorios. | `"<importe>"` |
| `data.total_deuda` | texto | Si se obtuvo el resumen | Total de deuda. | `"<importe>"` |
| `data.credito_capital` | texto | Si se obtuvo el resumen | Crédito de capital. | `"<importe>"` |
| `data.credito_accesorios` | texto | Si se obtuvo el resumen | Crédito de accesorios. | `"<importe>"` |
| `data.total_a_favor` | texto | Si se obtuvo el resumen | Total a favor. | `"<importe>"` |
| `data.movimientos` | lista de objetos | Si se solicitan movimientos o se activan al pedir PDF | Cada movimiento tiene los campos textuales `periodo`, `impuesto`, `concepto`, `subconcepto`, `descripcion`, `fecha_movimiento`, `debe` y `haber`. La lista puede estar vacía. | `[]` |
| `data.movimientos_corte` | texto | Cuando se recorren movimientos | Indica el motivo de cierre o corte del recorrido. | `"<motivo>"` |
| `data.compensaciones` | objeto | Sólo cuando `compensar=true` | Contiene las listas `saldos`, `reafectaciones`, `pagos_sin_destino` y `errores`. Los objetos de cada lista contienen detalle variable. | `{"saldos": [], "reafectaciones": [], "pagos_sin_destino": [], "errores": []}` |
| `data.avisos` | lista de textos | Cuando la reafectación devuelve uno o más avisos | Avisos asociados al proceso. | `["<aviso>"]` |
| `data.archivo` | texto | Si se genera PDF y `subir=false` | Nombre del PDF generado, no publicado para descarga. | `"<nombre>.pdf"` |
| `data.sha256` | texto hexadecimal | Si se genera PDF y `subir=true` | Huella del PDF publicado. | `"<64 caracteres hexadecimales>"` |
| `data.size_bytes` | entero | Si se genera PDF y `subir=true` | Tamaño del PDF publicado en bytes. | `12345` |
| `files` | lista de archivos | En la respuesta final | Contiene el PDF si se publicó; queda vacía si no se pidió PDF o si `subir=false`. | `[]` |

Los cortes de movimientos se informan en `data.movimientos_corte` y no convierten por sí solos el resultado en `PARCIAL`. `confirmar=true` puede haber confirmado algunas operaciones antes de un error posterior, por lo que el resultado parcial no implica reversión. Si `compensar=false`, no aparece `data.compensaciones`. El envelope implementado no incluye `data.periodo_desde`.
