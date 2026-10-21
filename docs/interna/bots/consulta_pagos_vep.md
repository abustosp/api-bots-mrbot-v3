# Auditoría interna: consulta_pagos_vep

## Flujo esquema -> plugin -> portal real

1. **Esquema** (`services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py`): `extra=forbid`, CUIT representado unifica `representado_cuit/cuit/cuit_representado`, quita separadores y exige 11 dígitos. Si varios aliases llegan al esquema, contrasta sus valores; antes de ello, la normalización canónica puede conservar el campo canónico y retirar aliases redundantes. Requiere período string de 1–3 dígitos, default `72`, valor entero >=1. Flags `incluir_json=true`, `subir_csv=true`; al menos una debe estar activa. Los campos de credenciales/contexto de sesión y `proxy_request` se retiran antes de validar el payload. Alias upload usa `bool()` sobre los valores y compara su verdad/falsedad.
2. **Plugin** (`bots/consulta_pagos_vep/plugin.py`): requiere credencial fiscal; manifest declara consulta de lectura, costo 1 y salida CSV opcional. Ejecuta sesión ARCA en servicio “PRESENTACIÓN DE DDJJ Y PAGOS”, consulta, exporta CSV, calcula filas y opcionalmente resumen/muestra y metadata. `subir_csv=false` evita publicación, no evita exportar/leer CSV.
3. **Portal real** (`services/bot-worker/src/bot_worker/runtime/portals/consulta_pagos_vep.py:16-190`): SETI, página de consulta de pagos/VEP (`https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps`); selecciona representado y período, aplica filtro y exporta CSV. No presenta ni paga VEP.

Salida: resumen con CUIT/período/filas/nombre; hash/tamaño ligados al archivo publicado; `muestra` solo con `incluir_json`; `files[]` solo cuando se publica CSV. No se implementa resultado parcial por filas: fallos de portal/exportación son error de operación.

## Catálogo, API, credenciales y tests

- Catálogo: `services/central-api/src/central_api/api/bots.py:345,395,428-439`; costo publicado informativo y clase `CONSULTA`. Ruta antigua `/vep/consulta`: `services/central-api/src/central_api/api/bot_compat.py:55-112`.
- Schema: `services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:16-124`; plugin `services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/plugin.py:68-219`; portal `services/bot-worker/src/bot_worker/runtime/portals/consulta_pagos_vep.py:16-190`.
- OpenAPI de catálogo (descriptivo, no validador de la ruta genérica): `services/central-api/src/central_api/api/bot_payloads.py:71-84,156-160,483,525,603-607`; normalización canónica `services/central-api/src/central_api/security/credentials.py:74-115,227-281,303-370`; envelope y encolado `services/central-api/src/central_api/api/bots.py:466-515,517-635`.
- Credenciales runtime: `services/bot-worker/src/bot_worker/runtime/context.py:17-25`; login ARCA `services/bot-worker/src/bot_worker/runtime/arca_login.py:25`.
- Pruebas leídas: `services/bot-worker/tests/test_consulta_pagos_vep_plugin.py`, `services/bot-worker/tests/test_portal_vep_archivo.py`, `tests/test_paridad_catalogo_registry.py` y `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias observadas

1. `esquema_entrada()` documenta `subir_csv` y `minio_upload`, pero omite `subir_a_minio`, aunque el validador lo acepta (`services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:19-20,81-93,111-123`).
2. `subir_csv` y `incluir_json` no pueden ser ambos false (`services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:95-100`), aunque el esquema del catálogo central los describe como booleanos independientes (`services/central-api/src/central_api/api/bot_payloads.py:156-160`). La ruta genérica no aplica ese DTO antes del esquema real, por lo que la combinación puede encolarse y fallar durante la validación del bot.
3. El esquema del catálogo describe el CUIT con patrón estricto `^\\d{11}$` (`services/central-api/src/central_api/api/bot_payloads.py:71-73,157`), más estrecho que la normalización efectiva que acepta separadores (`services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:47-58`). La ruta canónica genérica no aplica el DTO del catálogo; la diferencia corresponde a la descripción publicada y potencialmente al alias plano, no a un rechazo canónico previo.
4. `bool(valor)` en aliases de upload considera la cadena no vacía `"false"` como verdadera (`services/bot-worker/src/bot_worker/bots/consulta_pagos_vep/schema.py:84-92`).
5. El nombre técnico de portal y el nombre de servicio ARCA corresponden a un servicio de consulta, no a una operación de generación/pago de VEP. No hay efecto irreversible observado.
6. `proxy_request` se elimina del payload; la entrada pública V3 tampoco lo expone.

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
