# hacienda (stub dev)

Port de `api-bots-mrbot-v2/app/bot/hacienda_bot.py` al contrato worker V3
(`bots/hacienda/`): operacion `consultar` sobre Comprobantes en Línea +
Hacienda y Carne - Liquidación, por emisor y por receptor.

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `HaciendaPlugin().validate(payload_ejemplo.json)` falla antes del
   navegador si CUIT, denominacion o rango `dd/mm/aaaa` son invalidos.
2. `hojas_consolidado(filas_por_consulta)` arma las hojas del Excel V1
   (`consolidado`, `por_emisor`, `por_receptor`) y
   `runtime.portals.hacienda.escribir_xlsx` las escribe sin pandas.
3. `configure({...})` aplica la seccion `service` del sobre sellado sobre
   una copia del plugin.
4. `execute` requiere `runtime` real (sesion ARCA + slot
   `hacienda_consolidado`); con `subir_excel=false` no se necesita slot.

## Notas del port

- V1 escribe un unico XLSX (`HACIENDA - TABLA CONSOLIDADA - ... .xlsx`) y
  baja el PDF de cada comprobante en la misma navegacion de la consulta.
  V3 hace lo mismo con el escritor XLSX stdlib portado de `mis_facilidades`.
- Subida por slots prefirmados, nunca MinIO; errores por categoria.
- `descargar_pdf` queda activo por defecto (como V1); apagarlo devuelve solo
  el contrato tabular.
