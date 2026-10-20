# Certificado MiPyME: auditoría interna

## Flujo efectivo

1. Catálogo central `certificado_mipyme/descargar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386,748-817`). La ruta anidada canónica toma diccionarios genéricos de payload y credentials (`services/central-api/src/central_api/api/bots.py:194-208`).
2. Esquema worker requiere CUIT representado, aliases `cuit`/`cuit_representado`, y `subir=true`; descarta campos legacy de credencial/proxy (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/schema.py:18-19,59-83`).
3. Plugin inicia sesión, abre servicio, envía CUIT al adaptador y descarga el PDF; publica `certificado.pdf` obligatorio si `subir=true` (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/plugin.py:93-117,153-216`).
4. Adaptador busca match exacto de CUIT en opciones LUFE, prueba lista y hasta cinco búsquedas; si no encuentra el pedido, falla en lugar de elegir primera opción (`services/bot-worker/src/bot_worker/runtime/portals/certificado_mipyme.py:17-77,90-110`). Esto evita descargar certificado de otro representado.

## Normalización y outputs

CUIT elimina separadores comunes y exige once dígitos, sin checksum (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/schema.py:45-56,79-83`). `data` contiene `operacion`, `representado_cuit`, `archivo`; cuando se sube, agrega `sha256`, `size_bytes` y artefacto (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/plugin.py:193-216`). Sin subida queda el nombre en `data`, pero no archivo recuperable. Error de selección, descarga o publicación no genera parcial.

## Discrepancias concretas

- `CreateJobBody` de ruta anidada V3 es diccionario genérico y no valida el DTO por bot al recibir el cuerpo (`services/central-api/src/central_api/api/bots.py:194-208,748-817`). El regex del espejo (`services/central-api/src/central_api/api/bot_payloads.py:129-132`) corresponde a esquema OpenAPI/compatibilidad plana; el worker además permite normalización de separadores (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/schema.py:45-56,62-83`).
- `subir` controla publicación, pero el manifiesto declara certificado PDF como artefacto obligatorio del tipo de operación (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/plugin.py:101-107,193-216`). Hay que distinguir generación local y artefacto recuperable.
- El modelo worker tiene lista de campos históricos que elimina, pero JSON Schema operativo sólo declara operación, subir y CUIT (`services/bot-worker/src/bot_worker/bots/certificado_mipyme/schema.py:18-19,88-98`).
- La salvaguarda exacta de representado es más estricta que flujos anteriores que podían escoger primera opción; no se debe degradar ese invariant.

## Propuestas, sin implementar

- Mantener match exacto de CUIT; conservar tests para selector paginado, múltiple coincidencia, CUIT ausente y error de selector.
- Especificar en DTO plano si los separadores son válidos; dejar la ruta anidada documentada con normalización del plugin.
- Tipar la respuesta distinguiendo PDF generado, publicado y recuperable; describir `subir=false` sin ocultar `data.archivo`.
- Reducir campos legacy descartados y mantener sólo aliases soportados de forma explícita.

## Evidencia revisada

- Catálogo/ruta: `services/central-api/src/central_api/api/bots.py:329-339,378-386,748-817`; `services/central-api/src/central_api/api/bot_payloads.py:129-132`.
- Esquema/plugin: `services/bot-worker/src/bot_worker/bots/certificado_mipyme/schema.py:18-98`; `services/bot-worker/src/bot_worker/bots/certificado_mipyme/plugin.py:93-216`.
- Adaptador: `services/bot-worker/src/bot_worker/runtime/portals/certificado_mipyme.py:17-110`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_certificado_mipyme_plugin.py:194-202` y `services/bot-worker/tests/test_portal_certificado_mipyme.py`. Este análisis no ejecutó portal; pruebas locales coordinadas en [validación](../validacion.md).