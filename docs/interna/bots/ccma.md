# CCMA: auditoría interna

## Flujo efectivo

1. Catálogo central publica `ccma/consultar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386`). Ruta anidada canónica V3 recibe payload y credentials genéricos (`services/central-api/src/central_api/api/bots.py:194-208,748-817`).
2. Worker requiere CUIT; soporta aliases, `periodo_desde=04/2000`, flags movimientos/PDF y `subir=true` (`services/bot-worker/src/bot_worker/bots/ccma/schema.py:89-131`). CUIT quita separadores sin checksum; período valida forma y mes. `incluir_pdf=true` fuerza movimientos (`services/bot-worker/src/bot_worker/bots/ccma/schema.py:44-86,113-131`).
3. Plugin abre sesión, selecciona CUIT, calcula deuda, abre cuenta corriente, establece rango 48 y añade resumen (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:174-213`). Adaptador extrae resumen y movimientos (`services/bot-worker/src/bot_worker/runtime/portals/ccma.py:74-103,135-206`).
4. Movimientos recorren hasta 100 páginas y 500 registros con fallback; el corte se informa como dato (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:159-172,214-227`). PDF se crea del resumen/movimientos y sólo se publica si `subir=true` (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:228-250`).

## Envelope de datos

Resumen como texto: `cuit`, `periodo`, `deuda_capital`, `deuda_accesorios`, `total_deuda`, `credito_capital`, `credito_accesorios`, `total_a_favor`, `periodo_desde` (`services/bot-worker/src/bot_worker/runtime/portals/ccma.py:157-188`). El plugin devuelve `data.operacion`, `representado_cuit`, `periodo_desde` y resumen sin repetir ese campo (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:208-213`).

Con movimientos, `data.movimientos` es lista de objetos con valores texto `periodo`, `impuesto`, `concepto`, `subconcepto`, `descripcion`, `fecha_movimiento`, `debe`, `haber` (`services/bot-worker/src/bot_worker/runtime/portals/ccma.py:60-69`). `movimientos_corte` indica corte. `movimientos_completos=false` sólo se añade para motivos seleccionados; completar no genera un `true` explícito (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:214-227`). PDF publicado añade `sha256`, `size_bytes`; si no se sube, `archivo` (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:228-250`).

## Discrepancias concretas

- DTOs por bot en `bot_payloads.py` son espejos para OpenAPI/rutas flat. La ruta anidada canónica tiene cuerpo genérico y no impone el patrón por operación antes de encolar; el worker acepta separadores de CUIT (`services/central-api/src/central_api/api/bot_payloads.py:118-123`; `services/central-api/src/central_api/api/bots.py:194-208,748-817`; worker `services/bot-worker/src/bot_worker/bots/ccma/schema.py:44-55`).
- El espejo no expresa default `subir=true`/flags de modelo de forma idéntica ni la regla PDF→movimientos, que sí ejecuta esquema worker (`services/central-api/src/central_api/api/bot_payloads.py:118-123`; `services/bot-worker/src/bot_worker/bots/ccma/schema.py:97-131`).
- El portal fija rango `48` para cuenta corriente mientras `periodo_desde` controla cálculo de deuda (`services/bot-worker/src/bot_worker/bots/ccma/plugin.py:197-207`).
- `movimientos_completos` sólo aparece falso en cortes concretos. La ausencia no equivale a un `true` explícito.

## Propuestas, sin implementar

- Reproducir restricciones de forma y mes, implicación PDF→movimientos y defaults en el DTO de rutas flat; documentar validación posterior del job en la ruta anidada.
- Tipar salida: ocho saldos, lista typed de movimientos, `movimientos_corte`, campo completitud nullable con semántica clara.
- Publicar límite fijo de recorrido o convertirlo en parámetro; definir si truncamiento esperado sigue siendo `OK` o pasa a `PARCIAL`.

## Evidencia revisada

- Catálogo/ruta: `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- Esquema/plugin: `services/bot-worker/src/bot_worker/bots/ccma/schema.py:89-159`; `services/bot-worker/src/bot_worker/bots/ccma/plugin.py:159-260`.
- Portal: `services/bot-worker/src/bot_worker/runtime/portals/ccma.py:60-69,74-103,135-206`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_ccma_plugin.py:176-185,296-302` y `services/bot-worker/tests/test_portal_ccma.py`. Este análisis no ejecutó portal; pruebas locales coordinadas en [validación](../validacion.md).