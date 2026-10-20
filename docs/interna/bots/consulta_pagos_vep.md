# Auditoría interna: consulta_pagos_vep

## Flujo esquema -> plugin -> portal real

1. **Esquema** (`bots/consulta_pagos_vep/schema.py`): `extra=forbid`, CUIT representado unifica `representado_cuit/cuit/cuit_representado`, rechaza aliases contradictorios, quita separadores y exige 11 dígitos. Requiere período string de 1–3 dígitos, default `72`, valor entero >=1. Flags `incluir_json=true`, `subir_csv=true`; al menos una debe estar activa. Los campos de credenciales/contexto de sesión y `proxy_request` se retiran antes de validar el payload. Alias upload usa `bool()` sobre los valores, compara su verdad/falsedad y rechaza contradicción.
2. **Plugin** (`bots/consulta_pagos_vep/plugin.py`): requiere credencial fiscal; manifest declara consulta de lectura, costo 1 y salida CSV opcional. Ejecuta sesión ARCA en servicio “PRESENTACIÓN DE DDJJ Y PAGOS”, consulta, exporta CSV, calcula filas y opcionalmente resumen/muestra y metadata. `subir_csv=false` evita publicación, no evita exportar/leer CSV.
3. **Portal real** (`runtime/portals/consulta_pagos_vep.py`): SETI, página de consulta de pagos/VEP (`https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps`); selecciona representado y período, aplica filtro y exporta CSV. No presenta ni paga VEP.

Salida: resumen con CUIT/período/filas/nombre; hash/tamaño ligados al archivo publicado; `muestra` solo con `incluir_json`; `files[]` solo cuando se publica CSV. No se implementa resultado parcial por filas: fallos de portal/exportación son error de operación.

## Catálogo, API, credenciales y tests

- Catálogo: `services/central-api/src/central_api/api/bots.py:345,395,428-439`; costo publicado informativo y clase `CONSULTA`. Ruta antigua `/vep/consulta`: `services/central-api/src/central_api/api/bot_compat.py:55-112`.
- Schema: `services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:16-124`; plugin `services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/plugin.py:68-219`; portal `services/bot-worker/src/bot_worker/runtime/portals/consulta_pagos_vep.py:16-190`.
- OpenAPI de catálogo (descriptivo, no validador de la ruta genérica): `services/central-api/src/central_api/api/bot_payloads.py:71-84,156-160,483,525,603-607`; normalización canónica `services/central-api/src/central_api/security/credentials.py:74-115,227-281,303-370`; envelope y encolado `services/central-api/src/central_api/api/bots.py:466-515,517-635`.
- Credenciales runtime: `services/bot-worker/src/bot_worker/runtime/context.py:17-25`; login ARCA `services/bot-worker/src/bot_worker/runtime/arca_login.py:25`.
- Pruebas leídas: `services/bot-worker/tests/test_consulta_pagos_vep_plugin.py`, `services/bot-worker/tests/test_portal_vep_archivo.py`, `tests/test_paridad_catalogo_registry.py` y `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias concretas y propuestas

1. `schema.esquema_entrada()` documenta `subir_csv` y `minio_upload`, pero omite `subir_a_minio`, aunque el validador lo acepta (`schema.py:19-20,81-93,111-123`). Añadirlo al modelo JSON Schema generado o retirar el alias en un cambio compatible.
2. `subir_csv` y `incluir_json` no pueden ser ambos false (`schema.py:95-100`), aunque el modelo público central los declara como booleans independientes sin validación cruzada (`bot_payloads.py:156-160`). Documentar/modelar `anyOf` o validar antes de encolar para evitar el error tardío.
3. El validador del plugin normaliza formatos de CUIT con separadores (`schema.py:47-58`); el modelo público V3 describe patrón estricto `^\\d{11}$` (`bot_payloads.py:71-73,157`). La ruta pública puede rechazar lo que el plugin acepta. Hacer patrón y normalizador coherentes, o explicar la distinción explícitamente.
4. `bool(valor)` en los aliases de upload considera la cadena no vacía `"false"` como verdadera (`schema.py:84-92`). El contrato debe rechazar coerciones textuales y admitir solamente booleanos JSON.
5. El nombre técnico de portal y el nombre de servicio ARCA deben seguir documentados como servicio de consulta, no como una operación de generación/pago de VEP. No hay efecto irreversible observado.
6. `proxy_request` se elimina del payload; la entrada pública V3 tampoco lo expone. Mantenerlo fuera del contrato de cliente, o deprecarlo formalmente en la compatibilidad antigua.

Propuestas limitadas a schema/modelos/pruebas; no se implementan aquí.
