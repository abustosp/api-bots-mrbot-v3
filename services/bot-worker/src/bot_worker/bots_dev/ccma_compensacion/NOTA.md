# ccma_compensacion

Port V3 de `bots_dev/ccma_compenscion/ccma_compensacion_bot.py` (V1/V2
`app/bot/ccma_compensacion_bot.py`).

- Flujo `ccma` (Elegir CUIT, CALCULO DE DEUDA desde `04/2000`, resumen) y
  luego reafectación de los pagos con botón *Reafectar* contra obligaciones
  con saldo deudor (portal `runtime/portals/ccma_compensacion.py`).
- `compensar` (default true), `confirmar` (default false: sólo devuelve la
  propuesta y cancela cada formulario sin modificar ARCA), `incluir_movimientos`.
- `idempotency_class="EFECTO"`: con `confirmar=true` modifica la cuenta
  corriente. Resultado `PARCIAL` si hubo errores de imputación.
