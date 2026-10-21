# APOC: auditoría interna

## Flujo efectivo

1. Catálogo central publica `apoc/consultar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386`). La ruta canónica acepta `POST /api/v3/bots/{bot}/{operacion}`; su `CreateJobBody.payload` es un `dict` y `credentials` otro `dict` (`services/central-api/src/central_api/api/bots.py:194-208,748-817`).
2. El registro activo declara una operación de lectura sin credenciales fiscales ni instancia de navegador (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:71-85`; registro `services/bot-worker/src/bot_worker/bots/_plugins.py:90-100`).
3. El esquema requiere `cuit`; quita espacio, guion, punto y barra, exige once dígitos y no valida checksum (`services/bot-worker/src/bot_worker/bots/apoc/schema.py:17-43`). El plugin valida operación y cuerpo (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:105-116`).
4. No hay portal: `configure` recibe el texto de base por trabajo y `buscar_en_base` produce la respuesta (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:87-103,118-146`). Si el texto no está provisionado, falla con error de servicio, no devuelve un falso `apoc=false`.

## Normalización, datos y archivos

La consulta devuelve `cuit`, `apoc`, `fecha_condicion` y `fecha_publicacion`; `apoc=false` significa búsqueda válida sin coincidencia y las fechas quedan nulas si no se hallan (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:137-146`). También materializa internamente `resultado.json` limitado a 1 MiB, pero el manifiesto declara cero artefactos. No hay resultado parcial (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:71-85,132-146`).

## Discrepancias concretas

- La ruta canónica anidada V3 no valida el `payload` contra el espejo por bot antes de encolar: recibe diccionario genérico (`services/central-api/src/central_api/api/bots.py:194-208,748-817`). El plugin acepta y normaliza CUIT con separadores. En cambio, la ruta plana generada de compatibilidad valida un modelo tipado antes de dispatch (`services/central-api/src/central_api/api/bot_routes.py:418-527,663-690`), cuyo espejo APOC declara patrón `^\d{11}$` (`services/central-api/src/central_api/api/bot_payloads.py:71-73,89-91`). La diferencia es de ruta, no de una validación DTO común a V3.
- El esquema operativo también incluye `operacion`, aunque en el endpoint anidado la ruta ya fija la operación. El plugin conserva `consultar` como default (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:105-115`).
- La falta de base es un error explícito para evitar interpretar fuente ausente como resultado negativo (`services/bot-worker/src/bot_worker/bots/apoc/plugin.py:118-137`).

## Evidencia revisada

- Catálogo y ruta: `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- Alias plano: `services/central-api/src/central_api/api/bot_routes.py:418-527,663-690`; espejo `services/central-api/src/central_api/api/bot_payloads.py:71-73,89-91`.
- Esquema, plugin y manifiesto: `services/bot-worker/src/bot_worker/bots/apoc/schema.py:17-57`; `services/bot-worker/src/bot_worker/bots/apoc/plugin.py:71-146`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_apoc_plugin.py:100-152` cubre validación, búsqueda y fuente ausente. Este análisis no ejecutó el portal; pruebas locales coordinadas en [validación](../validacion.md).
## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
