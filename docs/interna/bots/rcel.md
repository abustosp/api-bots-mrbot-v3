# Auditoría interna: `rcel`

## Flujo esquema → plugin → portal

1. **Esquema:** `services/bot-worker/src/bot_worker/bots/rcel/schema.py:20-44` usa `extra="forbid"`, strip de strings, CUIT con separadores y fecha estricta DD/MM/AAAA. `:47-98` exige CUIT, nombre, fechas, rango ascendente y al menos una salida; flags por defecto JSON/PDF/extracción verdaderos. `:108-122` contiene esquema de entrada publicado por el manifest.
2. **Plugin:** `services/bot-worker/src/bot_worker/bots/rcel/plugin.py:84-190` declara una operación `descargar`, credenciales fiscales requeridas y errores por factura; `:191-283` consulta, procesa PDF de uno en uno, acumula error por descarga/extracción/subida y devuelve `OK`/`PARCIAL`.
3. **Portal:** `services/bot-worker/src/bot_worker/runtime/portals/rcel.py:21-60` define portal y campos extraíbles; `:63-104` extrae la primera página PDF; `:107-152` normaliza nombre y calcula coincidencia; `:177-218` selecciona/verifica empresa y rechaza ausencia del nombre. La implementación de búsqueda/descargas continúa en `:219-509`.

## Catálogo, aliases y normalizaciones

Catálogo central `services/central-api/src/central_api/api/bots.py:412`: `rcel/descargar`, clasificado `EFECTO` por regla genérica `:428-438`. Alias `/rcel/consulta` está en `services/central-api/src/central_api/api/bot_compat.py:68`. Normalización V3: `credentials.py:85-103` canoniza CUIT y flags CSV; `:120-128` transforma `denominacion` a `representado_nombre` y `nombre_rcel` también. `:247-263` transforma upload histórico a `subir_pdf`. El contrato del worker admite `minio_upload` en schema, no todos los nombres históricos del catálogo directo.

CUIT: se quitan espacios, guiones, puntos y `/`; fechas se parsean y se normalizan DD/MM/AAAA con calendario real. Deben coincidir ambas fechas en orden. La selección de empresa busca denominación y verifica el CUIT/nombre en encabezado. El portal devuelve cero facturas como lista vacía, no error.

## Datos de salida

`data`: `operacion`, `representado_cuit`, `fecha_desde`, `fecha_hasta`, `cantidad`, y opcional `facturas`. Cada elemento contiene `archivo`, opcional `campos`, y (si subida) `sha256`/`size_bytes`; sin subida entrega el tamaño local. `campos` contiene las claves definidas en `CAMPOS_FACTURA`, inicializadas vacías si faltan: AUX, emisor/receptor, domicilio/condición, fechas, importes/IVA, CAE y vencimiento (`runtime/portals/rcel.py:24-60, 63-104`). El PDF nunca se devuelve en base64. Resultados/artifacts se construyen por cada éxito (`plugin.py:224-283`).

## Discrepancias observadas

- **Manifest frente a JSON Schema:** `schema.py:47-74` exige `representado_cuit`, `representado_nombre`, `fecha_desde`, `fecha_hasta`, pero `esquema_entrada()` marca requerido solo `operacion` y omite `extraer_campos` (`:108-122`). La central publica forma propia (`central-api/api/bot_payloads.py:260-265`) y también omite `extraer_campos`.
- **Estado de catálogo:** la acción `descargar` se marca `EFECTO` por nombre, pese a que aquí la acción es lectura/descarga, sin cambios fiscales observados; advertencia de clasificación central `bots.py:428-438`.
- **Error por factura se acumula antes que el procesamiento:** el portal registra `errores_descarga` al descargar (`plugin.py:215-223`), y el plugin continúa con los nombres recibidos; un archivo inválido puede desaparecer del `facturas` final pero aún quedar en directorio temporal. La política de `PARCIAL` no comunica exactamente qué bytes locales quedaron, aunque no se consideran salida.
- **Pruebas leídas:** `services/bot-worker/tests/test_portal_rcel.py:92-125` valida registro, selección por nombre y rechazo CUIT distinto; `:128-152` valida cero resultados; `:154-220` prueba extracción de AUX/importes y que no se entrega base64.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/rcel/schema.py:20-122`
- `services/bot-worker/src/bot_worker/bots/rcel/plugin.py:84-190, 191-283`
- `services/bot-worker/src/bot_worker/runtime/portals/rcel.py:21-60, 63-152, 177-218`
- `services/central-api/src/central_api/api/bots.py:412-438`
- `services/central-api/src/central_api/api/bot_compat.py:68`
- `services/central-api/src/central_api/security/credentials.py:85-128, 247-263, 303-370`
- `services/central-api/src/central_api/api/bot_payloads.py:1-7, 260-265`
- `services/bot-worker/tests/test_portal_rcel.py:92-220`
