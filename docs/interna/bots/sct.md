# Auditoría interna: `sct`

## Flujo esquema → plugin → portal

1. **Esquema:** `services/bot-worker/src/bot_worker/bots/sct/schema.py:20-35` limpia CUIT; `:38-75` requiere listas `secciones` y `formatos`, valida enums, deduplica/ordena y requiere salida; `:85-105` JSON Schema omite `omitir_vacias`.
2. **Plugin:** `services/bot-worker/src/bot_worker/bots/sct/plugin.py:136-148` valida; `:150-208` login y apertura del servicio Sistema de Cuentas, maneja redirect de login en popup; `:210-220` omite solo insignia explícita cero; `:222-317` itera secciones × formatos, acumula errores no fatales, publica y devuelve `PARCIAL` si hubo fallos con artefactos.
3. **Portal real:** `services/bot-worker/src/bot_worker/runtime/portals/sct.py:1-218` maneja iframe del sitio de cuentas, selección de representado, contadores y descargas. El servicio es el sitio de Cuenta Corriente/Cuentas; no realiza cambios en obligaciones.

## Catálogo y normalizaciones

Central `services/central-api/src/central_api/api/bots.py:415`: `sct/consultar`, clase `CONSULTA`. Alias `/sct/consulta` en `services/central-api/src/central_api/api/bot_compat.py:63-66`. `normalize_v2_payload` se aplica también al endpoint canónico. `services/central-api/src/central_api/security/credentials.py:160-191` recorre 12 banderas (tres secciones × cuatro sufijos `excel`, `xlsx`, `csv`, `pdf`); si ninguna es verdadera o están ausentes, `setdefault` selecciona las tres secciones, XLSX y `subir=false`. Este default se fija incluso si el payload moderno canónico no contiene banderas. Las listas modernas explícitas prevalecen por `setdefault`; una bandera verdadera agrega listas solo si estas faltan. `credentials.py:247-263` unifica aliases genéricos de upload, pero un `subir=false` ya sintetizado prevalece. CUIT elimina separadores en schema.

## Datos de salida

`data.operacion`, `representado_cuit`, `reportes`; cada reporte normal trae `seccion`, `formato`, `archivo`, `sha256`/`size_bytes` si se publica. Si la sección tiene insignia explícita cero, puede aparecer `{seccion, omitido: true, motivo: "sin_registros"}`. `errores` lista sección/formato/mensaje. Resultados por sección/formato se conservan aunque otro falle y resultado `PARCIAL` (`plugin.py:205-208, 229-317`).

## Discrepancias observadas

- **Schema de entrada incompleto:** el modelo incluye `omitir_vacias` (`schema.py:50-51`) pero JSON Schema del manifest no lo publica (`:85-105`); catálogo público de familia tampoco (`central-api/api/bot_payloads.py:278-282`).
- **Forma antigua y moderna difieren:** normalización central traduce banderas con nombres `excel`, `xlsx`, `csv`, `pdf`, también en la ruta canónica; repetición `excel`/`xlsx` puede añadir la misma sección/formato, deduplicada al final. La condición «todas ausentes/falsas» activa tres secciones XLSX y desactiva publicación, aunque el modelo directo exija `secciones`/`formatos` y default de `subir=true` (`schema.py:38-75`).
- **Flag `incluir_json`:** controla la presencia de `reportes`/errores de sección en el resultado, pero no las descargas ni artefactos. Con `false`, una falla puede seguir influyendo en estado `PARCIAL` sin que el detalle de `errores` quede en `data`; errors array del job remains separate.
- **Pruebas leídas:** `services/bot-worker/tests/test_portal_sct.py:105-168` cubre redirect, iframe y contadores cero/desconocidos; `:195-489` prueba omisión y fallos parciales simulados.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/sct/schema.py:20-105`
- `services/bot-worker/src/bot_worker/bots/sct/plugin.py:136-317`
- `services/bot-worker/src/bot_worker/runtime/portals/sct.py:1-218`
- `services/central-api/src/central_api/api/bots.py:415-438`
- `services/central-api/src/central_api/api/bot_compat.py:63-66`
- `services/central-api/src/central_api/security/credentials.py:160-191, 247-263`
- `services/central-api/src/central_api/api/bot_payloads.py:278-282`
- `services/bot-worker/tests/test_portal_sct.py:105-168,195-489`
