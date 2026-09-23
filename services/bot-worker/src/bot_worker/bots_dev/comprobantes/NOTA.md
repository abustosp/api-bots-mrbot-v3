# comprobantes (stub dev)

Port de `api-bots-mrbot-v2/app/bot/comprobantes_bot.py` al contrato worker V3
(`bots/comprobantes/`): operaciones `consultar`, `solicitar`, `historial`
sobre Mis Comprobantes de ARCA.

## Stub dev

Este directorio es solo un arnes de desarrollo: NO registra el bot en la
central y NO toca `registry.py`. Para probar el plugin sin navegador:

1. Validar el ejemplo: `ComprobantesPlugin().validate(payload_ejemplo.json)`
   debe devolver la tupla `(operacion, entrada)` sin abrir el navegador.
2. `configure({"servicio": "MIS COMPROBANTES", "muestra_json": 5})` aplica la
   seccion `service` del sobre sellado sobre una copia del plugin.
3. `execute` requiere `runtime` real (sesion ARCA + slots `emitidos_csv` /
   `recibidos_csv`); con `subir_csv=false` no se necesita slot.

## Notas del port

- Entrada: CUIT de 11 digitos, rango `dd/mm/aaaa`, al menos un tipo
  (emitidos/recibidos) y al menos una salida (json/csv).
- `solicitar` devuelve solo ids de consulta (continuacion); las cookies de
  sesion nunca salen del worker.
- Errores por categoria (`ENVELOPE_INVALID`, `CREDENTIALS_REJECTED`,
  `TARGET_UNAVAILABLE`, `CHROMIUM_CRASHED`, ...), diagnostico redactado.
