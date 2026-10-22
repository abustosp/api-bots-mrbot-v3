# Auditoría interna: `liquidacion_granos`

## Contrato y ruta

El catálogo publica solo `consultar` (`services/central-api/src/central_api/api/bots.py:L403`). El alias compatible `/api/v3/liquidacion_granos/consulta` también mapea a `consultar` (`central-api/api/bot_compat.py:L109`). La ruta canónica genérica recibe `payload` y opcionalmente `credentials` en sobre separado (`central-api/api/bots.py:L466-L513`, `L748-L817`).

## Flujo esquema → plugin → portal

1. **Entrada.** El modelo es estricto frente a extras (`services/bot-worker/src/bot_worker/bots/liquidacion_granos/schema.py:L50-L52`). Exige CUIT, nombre representado y fechas `dd/mm/aaaa`; admite alias `cuit`, `cuit_representado`, `denominacion`, `desde`, `hasta` (`L77-L99`). Alias CUIT múltiples se normalizan y se rechazan si difieren (`L31-L48`). CUIT elimina espacios, guiones, puntos y `/` (`L54-L65`). Cinco banderas de secciones y `subir_archivos` son `true` por default (`L100-L105`). Se exige rango cronológico y al menos una sección activa (`L129-L148`).
2. **Plugin.** Configuración puede presetear las cinco secciones y `subir_archivos`; el payload prevalece por `setdefault` (`services/bot-worker/src/bot_worker/bots/liquidacion_granos/plugin.py:L143-L177`). El plugin exige credenciales, inicia sesión, abre Liquidación de Granos y pasa CUIT más denominación al selector (`L179-L221`).
3. **Portal.** El portal selecciona el representado usando denominación, y verifica CUIT visible cuando está disponible (`services/bot-worker/src/bot_worker/runtime/portals/liquidacion_granos.py:L289-L367`). Cada sección activa pide planilla, inspecciona comprobantes y produce XLSX/PDF (`services/bot-worker/src/bot_worker/runtime/portals/liquidacion_granos.py:L484-L543`).

## Normalizaciones y resultado

Las fechas se parsean/serializan a formato uniforme. `subir_archivos: null` y cualquier alias de subida en `null` se eliminan antes de validar, por lo que se aplica el default `true` (`schema.py:L115-L127`). El alias de subida se normaliza con `bool(valor)` y los aliases presentes deben coincidir después de esa conversión (`schema.py:L107-L127`). Consecuencia: la cadena no vacía `"false"` se vuelve `True`, no un booleano falso. El flujo emite `data.secciones`, con nombre/tamaño de planilla y cantidades/nombres de comprobantes; si se adjuntó, añade hash/tamaño. `subir_archivos=false` conserva la consulta y los resúmenes, pero no retorna referencias de archivo. El resultado normal es `OK`; una excepción no capturada aborta el trabajo, no existe degradación por sección a `PARCIAL` (`plugin.py:L179-L221`, `L223-L288`, `L290-L312`).

## Discrepancias observadas

- El V3 canónico acepta normalización de `minio_upload`, `carga_minio` y `archivo_historico_minio`, pero no traduce `subir_a_minio`; `schema.py` sí lo reconoce si llega directamente. Como el modelo prohíbe extras, el alias `subir_a_minio` recibido por la API central queda sin quitar y falla (`central-api/security/credentials.py:L44-L45`, `L227-L275`; `schema.py:L27-L28`, `L115-L127`).
- `bool("false")` activa subida; también afecta a aliases múltiples (`schema.py:L118-L126`).
- En el modelo legado V2, `minio_upload` default `false`, mientras el contrato de ejecución V3 tiene `subir_archivos=true` (`central-api/api/bot_payloads.py:L556-L573`; `schema.py:L100-L105`).
- El schema público lista la ruta canónica y defaults de las secciones, pero el patrón CUIT no describe separadores admitidos por validación real (`central-api/api/bot_payloads.py:L195-L203`).
- Contrato de credenciales se publica genérico, aunque el plugin necesita ambas credenciales (`central-api/api/bots.py:L820-L845`, plugin `L179-L185`).

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/liquidacion_granos/schema.py:L31-L148`
- `services/bot-worker/src/bot_worker/bots/liquidacion_granos/plugin.py:L143-L221`, `L223-L288`, `L290-L312`
- `services/bot-worker/src/bot_worker/runtime/portals/liquidacion_granos.py:L289-L367`, `L484-L543`, `L620-L778`, `L736-L778`
- `services/central-api/src/central_api/api/bots.py:L403`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/security/credentials.py:L44-L45`, `L227-L275`
- `services/central-api/src/central_api/api/bot_payloads.py:L195-L203`, `L556-L573`
- Revisados: `services/bot-worker/tests/test_liquidacion_granos_plugin.py` y contrato público. No se ejecutó el portal.

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública. Con `logger_script: true` (booleano explícito, también oculto) corre en cambio la copia de `liquidacion_granos_bot_logger.py` de `bot_worker/bots_dev/liquidacion_granos/` en lugar del plugin.
