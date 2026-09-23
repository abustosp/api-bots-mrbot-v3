# hacienda (stub dev)

Port de `api-bots-mrbot-v2/app/bot/hacienda_bot.py` al contrato worker V3
(`bots/hacienda/`): operacion `consultar` sobre Comprobantes en Línea +
Hacienda y Carne - Liquidación, por emisor y por receptor.

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `HaciendaPlugin().validate(payload_ejemplo.json)` falla antes del
   navegador si CUIT, denominacion o rango `dd/mm/aaaa` son invalidos.
2. `escribir_consolidado(destino, filas)` es pura y se prueba sin runtime.
3. `configure({...})` aplica la seccion `service` del sobre sellado sobre
   una copia del plugin.
4. `execute` requiere `runtime` real (sesion ARCA + slot
   `hacienda_consolidado`); con `subir_excel=false` no se necesita slot.

## Notas del port

- V2 consolida con pandas; V3 escribe CSV con la biblioteca estandar
  (misma granularidad por consulta).
- Subida por slots prefirmados, nunca MinIO; errores por categoria.
