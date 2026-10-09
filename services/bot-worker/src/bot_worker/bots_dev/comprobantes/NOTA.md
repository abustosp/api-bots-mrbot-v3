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
  (emitidos/recibidos) y al menos una salida (json/csv/base64). Acepta los
  campos de V1: `descarga_emitidos`/`descarga_recibidos`, `carga_json`,
  `carga_minio`, `puntos_venta_emitidos`/`recibidos`, `timeout_mc`,
  `carga_b64`, `eliminar_descargas` y `verbose`.
- Puntos de venta y `timeout_mc` (30 s por defecto, como V1) viajan al
  portal; `carga_b64` devuelve el archivo descargado en base64.
- El filtro de la planilla renombra las columnas por posicion (mce_xlsx /
  mcr_xlsx), rellena con 0 los importes que el portal no informa y agrega
  las columnas auxiliares de V1 (MC, CUIT Cliente, MC AUX, Factura AUX) al
  JSON de respuesta.
- `solicitar` devuelve solo ids de consulta (continuacion); las cookies de
  sesion nunca salen del worker.
- Errores por categoria (`ENVELOPE_INVALID`, `CREDENTIALS_REJECTED`,
  `TARGET_UNAVAILABLE`, `CHROMIUM_CRASHED`, ...), diagnostico redactado.
