# Auditoría interna: `mis_retenciones_iva_simple`

## Contrato y ruta

El catálogo declara `consultar` (`services/central-api/src/central_api/api/bots.py:L408`). El alias `/api/v3/mis_retenciones_iva_simple/consulta` mapea a la misma operación (`central-api/api/bot_compat.py:L75-L79`). Ruta genérica y sobre estándar: `central-api/api/bots.py:L466-L513`, `L748-L817`.

## Flujo esquema → plugin → portal

1. **Entrada.** El modelo acepta extras (`services/bot-worker/src/bot_worker/bots/mis_retenciones_iva_simple/schema.py:L27-L28`). Exige CUIT, nombre y fechas (`L78-L100`), limpia CUIT y normaliza fechas (`L31-L51`, `L102-L121`). `fecha_hasta` no puede preceder a `fecha_desde` ni superar el último día del mes siguiente al mes inicial (`L54-L76`). `incluir_json` y `subir_csv` default true, y al menos uno debe permanecer activo. `required_result_marker` default `IVA SIMPLE`, es string no nulo sin límite adicional (`L97-L121`).
2. **Plugin.** Registro único `consultar`; login, selección de representado, activación del modo IVA Simple y recorrido de sus categorías (`services/bot-worker/src/bot_worker/bots/mis_retenciones_iva_simple/plugin.py:L107-L135`, `L158-L211`, `L213-L275`).
3. **Portal.** `runtime/portals/mis_retenciones_iva_simple.py:L38-L42`, `L99-L124` activa el modo, elige categoría/formato y descarga CSV. Si la UI no expone el selector histórico, traduce las categorías a pares actuales de impuesto/tipo (`L126-L179`). La validación del marcador no vacío está en la rutina de exportación heredada (`runtime/portals/mis_retenciones.py:L594-L618`).

## Normalizaciones y outputs

Se consultan tres categorías de operación y dos alternativas de formato (`iva_simple`, `xls`) por categoría (`plugin.py:L221-L275`). Los errores por combinación se registran con conteo cero y mensaje/código, se continúa el loop y el estado exterior permanece `OK` salvo fallo fatal (`plugin.py:L213-L275`). CSV se adjunta únicamente con `subir_csv=true`; `incluir_json=false` omite muestras pero no evita exportar. El portal recorta rango futuro durante la selección de fechas (`runtime/portals/mis_retenciones_iva_simple.py:L181-L209`).

## Discrepancias observadas

- `required_result_marker` es aceptado por el validador y usado por la exportación, pero falta en el modelo central público de la operación (`central-api/api/bot_payloads.py:L229-L235`; worker `schema.py:L97-L100`).
- `extra="allow"` acepta otros campos que no son consumidos (`schema.py:L27-L28`). El modelo público V3 no declara ese marcador ni extras (`central-api/api/bot_payloads.py:L229-L235`).
- La consulta puede devolver estado `OK` con errores particulares, sin un `PARCIAL` derivado de `datos.errores` (`plugin.py:L213-L275`).
- Las credenciales del catálogo son genéricas, sin requiredness (`central-api/api/bots.py:L820-L845`), aunque el plugin requiere acceso fiscal (`plugin.py:L174-L181`).

## Cambios por ARCA (octubre 2026)

- **Modo desactivado al recargar.** Con IVA Simple activo ARCA oculta `#selectImpuestos`; `_asegurar_formulario` lo tomaba como formulario ausente, recargaba y desactivaba el modo. El portal ahora sobrescribe `_asegurar_formulario` y considera válido el formulario si `#selectTipoOperacionIvaSimple` está visible. Sin esto el resultado salía sin el marcador (`iva_simple_result_marker_missing`).
- **Archivo repetido.** El formato `iva_simple` de `PERCEPCIONES IMPOSITIVAS` llega con las filas de retenciones. El plugin valida `Descripcion Operacion` (`_operacion_coincide`) y informa `iva_simple_operation_mismatch` en vez de entregar el archivo; los archivos sin esa columna (formato fijo) no se validan. `bots_dev` (`bot` y `bot_mrbot`) lo descartan con `archivo_coincide_operacion`.
- `_contar_filas` ya no resta encabezado en el formato `iva_simple`, que no lo tiene.

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/mis_retenciones_iva_simple/schema.py:L27-L152`
- `services/bot-worker/src/bot_worker/bots/mis_retenciones_iva_simple/plugin.py:L107-L275`
- `services/bot-worker/src/bot_worker/runtime/portals/mis_retenciones_iva_simple.py:L38-L42`, `L99-L179`, `L181-L209`
- `services/bot-worker/src/bot_worker/runtime/portals/mis_retenciones.py:L594-L618`
- `services/central-api/src/central_api/api/bots.py:L408`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L75-L79`
- `services/central-api/src/central_api/api/bot_payloads.py:L229-L235`
- Revisados: `services/bot-worker/tests/test_portal_mis_retenciones_iva_simple.py` y contrato de entrada. Prueban regla de fechas, modo y fallback de formato. No se abrió el portal real.

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
