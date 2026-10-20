# Carga del Portal IVA: auditoría interna

## Flujo efectivo

1. Catálogo central publica `carga_portal_iva/cargar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386`). Ruta canónica V3 acepta diccionarios genéricos para payload/credentials (`services/central-api/src/central_api/api/bots.py:194-208,748-817`).
2. Contrato de entrada pública canónica usa `*_key`. El despacho resuelve esas referencias de carga y las materializa a campos locales `*_path` antes del worker: `services/central-api/src/central_api/api/bot_payloads.py:100-117`; `services/central-api/src/central_api/input_files.py:21-42,47-82,130-179`. No son alias para que cliente mande `_path`.
3. Esquema worker exige CUIT/período; permite flags y ocho referencias opcionales; requiere par completo de TXT ventas o compras o un CSV; rechaza par incompleto y cualquier clave que indique Base64 (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:95-166`). Materialización comprueba existencia, no vacío, extensión correcta y máximo 20 MiB (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:180-199`).
4. Plugin abre servicio IVA, selecciona representado/período y recorre secciones (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:244-361`). Cada par TXT borra primero todos los registros previos del libro, después importa y reintenta sin repetir borrado (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:201-234`). CSV de apertura se cargan por sección (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:236-242`).
5. Con `prorrateo_global=true`, CSV CF presente y apertura CF exitosa, usa total calculado del CSV para cambiar el crédito fiscal computable (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:263-275,336-350`). Adaptador portal: `services/bot-worker/src/bot_worker/runtime/portals/carga_portal_iva.py:231-313`.

## Normalización, salida y riesgo

CUIT quita separadores y exige once dígitos, sin checksum; período elimina separadores no numéricos, exige mes 01–12 y no futuro (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:53-79,123-131`). `denominacion` default vacío y no se usa en flujo de carga (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:103-104`; plugin `services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:268-275`).

`data` estable: `operacion`, `representado_cuit`, `periodo`, `secciones`; condicionalmente `periodos_seleccionables` y `total_credito_fiscal_computable_cf` (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:268-275,301-302`). El mapa `secciones` contiene estados string para `ventas_txt`, `compras_txt`, `CF`, `CF Restitucion`, `DF`, `DF Restitucion`, y condicionalmente `credito_fiscal_compras`. Sección fallida queda como `error: <tipo>` y las restantes continúan. Resultado `ERROR` si fallan todas, `PARCIAL` si falla alguna pero no todas, `OK` si ninguna falla (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:363-422`). No hay rollback de efectos exitosos al llegar a `PARCIAL`. No se declaran artifacts de salida.

## Discrepancias concretas

- Los DTOs de `bot_payloads.py` documentan nombres y tipos para rutas flat/compatibilidad. La ruta canónica anidada V3 no valida ese DTO por operación, su cuerpo acepta diccionario genérico y el plugin valida tras despacho (`services/central-api/src/central_api/api/bot_payloads.py:100-117`; `services/central-api/src/central_api/api/bots.py:194-208,748-817`). Las reglas por campo del espejo no equivalen a reglas cruzadas de archivos.
- El puente de entrada hace la transformación `*_key` → path temporal y resuelve uploads (`services/central-api/src/central_api/input_files.py:21-42,130-179`); el plugin sólo consume nombres `_path`. Sin prueba contrato que cruce ambos vocabularios, una edición de uno deja referencia no resuelta.
- OpenAPI por campo no expresa grupos TXT, alternativa CSV, rechazo de Base64, límite 20 MiB ni extensión (esquema `services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:133-166`; upload `services/central-api/src/central_api/input_files.py:47-82`; plugin `services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:180-199`).
- `secciones` puede ser parcial aunque operaciones previas hayan mutado el portal. La clasificación de `ERROR` mira todas las secciones fallidas; no hay compensación/rollback al fallar una sección (`services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:363-422`).

## Evidencia revisada

- Catálogo/ruta: `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- DTO plano y puente: `services/central-api/src/central_api/api/bot_payloads.py:100-117`; `services/central-api/src/central_api/input_files.py:21-42,47-82,130-179`.
- Schema/plugin/portal: `services/bot-worker/src/bot_worker/bots/carga_portal_iva/schema.py:95-199`; `services/bot-worker/src/bot_worker/bots/carga_portal_iva/plugin.py:180-422`; `services/bot-worker/src/bot_worker/runtime/portals/carga_portal_iva.py:231-313`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_carga_portal_iva_plugin.py`. Este análisis no ejecutó carga real; pruebas locales coordinadas en [validación](../validacion.md).