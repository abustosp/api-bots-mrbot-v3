# Auditoría interna: `pago_devoluciones`

## Contrato y ruta

El catálogo declara `consultar` (`services/central-api/src/central_api/api/bots.py:L410`). Alias `/api/v3/pago_devoluciones/consulta` apunta a esa operación (`central-api/api/bot_compat.py:L96`). Ruta genérica y sobre separado `payload`/`credentials`: `central-api/api/bots.py:L466-L513`, `L748-L817`.

## Flujo esquema → plugin → portal

1. **Entrada.** Modelo estricto (`services/bot-worker/src/bot_worker/bots/pago_devoluciones/schema.py:L36-L38`). CUIT representado opcional y acepta tres alias. Se eliminan separadores; `""` se transforma en `None`. Credenciales/proxy que vengan en payload se descartan (`L24-L33`, `L71-L84`). `incluir_json` y `subir_archivo` son `true` por default y al menos uno debe ser `true` (`L54-L90`).
2. **Plugin.** Configuración puede sobreponer defaults solo cuando payload no trae la llave. Ejecución requiere credenciales, usa el CUIT representado indicado o CUIT de acceso, elige representado y consulta/exporta (`services/bot-worker/src/bot_worker/bots/pago_devoluciones/plugin.py:L153-L180`, `L182-L225`).
3. **Portal.** `services/bot-worker/src/bot_worker/runtime/portals/pago_devoluciones.py:L20-L102` elige el representado, abre Consultar, ejecuta exportación Excel y acepta confirmación opcional.

## Normalizaciones y outputs

`data` contiene `operacion`, `representado_cuit`, `archivo` y `errores_por_seccion`; `detalle_archivo` persiste aunque `incluir_json=false`, que solo establece `archivo=null` (`plugin.py:L227-L281`). XLSX se adjunta si `subir_archivo=true`; upload fallido propaga error de artefacto, no un parcial silencioso (`L264-L280`). `_estado_por_secciones` devuelve `ERROR` para fallos críticos, `PARCIAL` para no críticos y `OK` sin errores (`plugin.py:L88-L111`, `L220-L225`).

## Discrepancias observadas

- El catálogo central publica esquema genérico de credenciales, sin campos requeridos (`services/central-api/src/central_api/api/bots.py:L820-L845`), pero el bot exige el objeto y el servicio requiere cuenta/clave (`plugin.py:L182-L189`).
- El esquema V3 público compacto no muestra aliases `cuit`, `cuit_representado`, `carga_minio`, `minio_upload`, `archivo_historico_minio`, aunque la normalización central y modelo del bot los aceptan (`central-api/api/bot_payloads.py:L244-L247`; `central-api/security/credentials.py:L44-L45`, `L227-L275`; worker `schema.py:L54-L84`).
- `incluir_json=false` no suprime el objeto `detalle_archivo`, solo el alias `archivo` (`plugin.py:L260-L281`), lo que hace que la bandera no suprima todo el detalle.

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/pago_devoluciones/schema.py:L24-L113`
- `services/bot-worker/src/bot_worker/bots/pago_devoluciones/plugin.py:L88-L111`, `L114-L145`, `L153-L225`, `L227-L281`
- `services/bot-worker/src/bot_worker/runtime/portals/pago_devoluciones.py:L20-L102`
- `services/central-api/src/central_api/api/bots.py:L410`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L96`
- `services/central-api/src/central_api/api/bot_payloads.py:L244-L247`
- `services/central-api/src/central_api/security/credentials.py:L44-L45`, `L227-L275`
- Revisados: `services/bot-worker/tests/test_pago_devoluciones_errores.py` y `test_portal_pago_devoluciones.py`, incluyendo clasificación de errores y alias CUIT vacío.

No se ejecutó el portal ni se usaron credenciales reales.
