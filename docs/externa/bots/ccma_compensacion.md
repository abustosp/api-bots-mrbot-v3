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

`data` tiene los siguientes campos estables. Los datos extraídos directamente del resumen y el detalle de reafectaciones conservan formatos textuales devueltos por el portal.

| Ruta | Tipo | Cuándo aparece |
|---|---|---|
| `data.operacion` | Texto (`"compensar"`) | Siempre. |
| `data.representado_cuit` | Texto | Siempre. |
| `data.compensar` | Booleano | Siempre; indica si se intentó el paso de reafectación. |
| `data.confirmar` | Booleano | Siempre; refleja el modo solicitado, no prueba por sí solo que hubo cambios. |
| `data.cuit`, `data.periodo`, `data.periodo_desde` | Texto | Siempre, si el resumen de cuenta se obtuvo. |
| `data.deuda_capital`, `data.deuda_accesorios`, `data.total_deuda`, `data.credito_capital`, `data.credito_accesorios`, `data.total_a_favor` | Texto | Siempre, si el resumen de cuenta se obtuvo. |
| `data.movimientos` | Lista de objetos, cada campo texto: `periodo`, `impuesto`, `concepto`, `subconcepto`, `descripcion`, `fecha_movimiento`, `debe`, `haber`. | Si se solicita movimientos o el PDF activa ese requisito. |
| `data.movimientos_corte` | Texto | Cuando se recorren movimientos. |
| `data.compensaciones` | Objeto | Sólo cuando `compensar=true`. Contiene listas `saldos`, `reafectaciones`, `pagos_sin_destino` y `errores`. Las filas son detalle variable del portal, no un esquema de filas uniforme garantizado. |
| `data.avisos` | Lista de textos | Sólo cuando el proceso devuelve avisos. Los avisos también se reportan en `warnings` del resultado del job. |
| `data.archivo` | Texto, nombre PDF | Si se genera PDF y `subir=false`. |
| `data.sha256` | Texto hexadecimal | Si se genera PDF y `subir=true`. |
| `data.size_bytes` | Entero | Si se genera PDF y `subir=true`. |

El archivo publicado aparece en `files` del job. Los cortes de movimientos se informan en datos. Errores o avisos en reafectaciones producen `PARCIAL` y conservan el detalle disponible. `confirmar=true` puede haber confirmado algunas operaciones antes de un error posterior, por lo que el resultado parcial no implica reversión. Si `compensar=false`, no aparece `data.compensaciones`.