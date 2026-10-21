# Auditoría interna: `portal_iva`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/portal_iva/schema.py:34-74` define base `extra="allow"`, strip de whitespace, normalizador CUIT y período. `:89-143` define descargar y defaults; `:146-218` define referencias TXT, pares comprobante/alícuota, importación/gestión y operaciones. `gestionar` hereda campos de descargar y archivos.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/portal_iva/plugin.py:144-158` escoge esquema por `operacion`; `:160-230` exige `runtime.credentials`, inicia sesión ARCA, selecciona representado (o CUIT representante), prepara período y llama descargar/importar/gestionar. `:232-313` produce CSV/muestras y puede descartar borrador; `:315-365` valida/importa TXT y compone gestión como importar y después descargar.
3. **Portal.** `services/bot-worker/src/bot_worker/runtime/portals/portal_iva.py:242-323` enumera períodos, aplica opciones de declaración y guarda con `#btnGuardar`; `:325-369` refresca/importa desde ARCA, descarga CSV y opcionalmente borra registros; `:371-383` borra todo antes de importar TXT; `:385-449` implementa actualización y borrado.

## Catálogo y normalizaciones

Catálogo central `services/central-api/src/central_api/api/bots.py:411` publica `descargar`, `importar`, `gestionar`, todos clasificados `EFECTO` por `:428-438` (clasificación conservadora, no significa que toda variante sea igualmente destructiva). Alias `services/central-api/src/central_api/api/bot_compat.py:66-67`: `/portal_iva/consulta` solo apunta a `descargar`; `/portal_iva/carga` apunta a otro bot (`carga_portal_iva`) en `:67`, no es alias de `portal_iva/importar`.

`services/central-api/src/central_api/security/credentials.py:85-103` canoniza alias CUIT/descargas; `:119-126` renombra `denominacion` a `representado_nombre` para Portal IVA; `:200-210` asigna `desde/hasta` como períodos solo a los bots provinciales (Portal IVA usa `periodo`); `:227-263` unifica banderas históricas de subida (`carga_minio`, `minio_upload`, `archivo_historico_minio`) a `subir_csv`. `services/central-api/src/central_api/input_files.py:30-40` mapea `ventas_txt_key`, `ventas_alicuotas_txt_key`, `compras_txt_key`, `compras_alicuotas_txt_key` a nombres internos `_path`; `:54-59` registra uploads permitidos y `:72-76` el límite de 5 MB por TXT.

## Normalización y resultado

CUIT elimina espacios, `-`, `.`, `/`; período admite MM/AAAA, MM-AAAA, AAAA/MM, AAAA-MM o AAAAMM y se normaliza AAAAMM con mes válido. Cada libro seleccionado requiere su TXT de comprobantes y su TXT de alícuotas. El importador comprueba existencia, `.txt`, no vacío y límite en `plugin.py:328-353`.

Descarga devuelve `data.operacion`, `representado_cuit`, `periodo`; opcionalmente `periodos_seleccionables`; para `ventas`/`compras`: `filas` (entero), `archivo` (nombre), opcional `sha256`, `size_bytes` y `muestra` (hasta cinco filas) (`services/bot-worker/src/bot_worker/bots/portal_iva/plugin.py:239-296`). Artefactos son CSV. Importación devuelve `importaciones: [{libro, ok}]`; gestión devuelve forma de descarga más ese arreglo (`:322-365`). No hay ruta explícita de resultado `PARCIAL` en el plugin.

## Discrepancias observadas

- **Defaults destructivos inesperados:** `schema.py:121-125` activa por omisión `refrescar_desde_arca`, `limpiar_despues` y `descartar_borrador`; el plugin pasa esos valores a `descargar_libro` (`plugin.py:267-272`) y descarta borrador (`:302-312`). Sin embargo, el comentario del portal dice que refresh/limpieza son opt-in (`portal_iva.py:333-338`). La ruta actual trata la omisión como tres efectos habilitados.
- **Preparación también escribe:** `preparar_periodo` siempre intenta marcar opciones y guardar (`portal_iva.py:289-322`) antes de cualquiera de las tres operaciones (`plugin.py:191-208`). No es una consulta inocua, y cada flag de preparación puede cambiar estado.
- **`gestionar` no es atómico:** primero importa y reemplaza (`plugin.py:355-365`, portal `:371-383`) y luego descarga. Si la segunda fase falla, no hay `PARCIAL` deliberado ni reversión del reemplazo. Los CSV anteriores que se hayan subido antes de un fallo de otro libro pueden persistir.
- **Contrato público incompleto:** espejo público de campos por familia en `services/central-api/src/central_api/api/bot_payloads.py:248-258` no enumera flags de preparación/refresh/limpieza/borrador ni `periodos_seleccionables`; los campos históricos por operación en `:501` sí documentan algunos flags y no sincronizan con el modelo vigente. La central declara campos como espejo OpenAPI, no reemplaza validación del plugin (`bot_payloads.py:1-7`).
- **Pruebas leídas:** `services/bot-worker/tests/test_portal_portal_iva.py:137-229` cubre normalización de período, selección de representado, guardado y período inválido; el resto del archivo ejercita descarga, eliminación e importación simuladas. `services/central-api/tests/test_catalog_seed.py:33-41` afirma `EFECTO` para descargar/gestionar/importar.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/portal_iva/schema.py:34-218`
- `services/bot-worker/src/bot_worker/bots/portal_iva/plugin.py:144-365`
- `services/bot-worker/src/bot_worker/runtime/portals/portal_iva.py:242-449`
- `services/central-api/src/central_api/api/bots.py:411-438`
- `services/central-api/src/central_api/api/bot_compat.py:55-70`
- `services/central-api/src/central_api/security/credentials.py:74-126, 200-263, 303-370`
- `services/central-api/src/central_api/input_files.py:20-76`
- `services/central-api/src/central_api/api/bot_payloads.py:1-7, 248-258, 330-350, 501`
- `services/bot-worker/tests/test_portal_portal_iva.py:137-229`; `services/central-api/tests/test_catalog_seed.py:33-41`

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
