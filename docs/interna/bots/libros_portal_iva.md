# Auditoría interna: `libros_portal_iva`

## Contrato y ruta

El catálogo central declara `descargar`, `descargar_libros`, `descargar_ddjj` (`services/central-api/src/central_api/api/bots.py:L400-L401`). Rutas de compatibilidad: `/api/v3/libros_iva/consulta` mapea a `descargar` y `/api/v3/libros_iva/ddjj` a `descargar_ddjj` (`services/central-api/src/central_api/api/bot_compat.py:L98-L99`). La ruta genérica es `/api/v3/bots/{bot}/{operacion}` y el cuerpo exige `payload`, con `credentials` separadas (`services/central-api/src/central_api/api/bots.py:L466-L513`, `L748-L817`).

## Flujo esquema → plugin → portal

1. **Entrada.** `schema.py:L22-L24` permite extras. Los campos comunes de rango son `representado_cuit` opcional, `periodo_desde`/`periodo_hasta` requeridos, `denominacion` default `""`, y las salidas `incluir_json=true`, `subir_archivos=true` (`schema.py:L71-L105`). CUIT acepta `representado_cuit`, `cuit` o `cuit_representado`; espacios, guiones, puntos y `/` se eliminan, con 11 dígitos obligatorios para valores no vacíos (`schema.py:L26-L37`, `L84-L97`). Periodos se normalizan como `AAAAMM`, mes 01–12, orden inclusivo y ascendiente (`schema.py:L40-L68`, `L99-L105`). Vacío de CUIT se vuelve `None`, que activa fallback a credencial.
2. **Plugin.** `plugin.py:L81-L115` manifiesta las tres operaciones y credenciales fiscales requeridas. Validación toma `operacion` del request interno y recurre a `descargar` si falta (`plugin.py:L136-L148`). Ejecución exige el objeto de credenciales, usa `representado_cuit` o el CUIT de acceso, inicia sesión, abre el servicio configurado, por defecto `Portal IVA`, y selecciona representado (`plugin.py:L117-L134`, `L150-L220`). `descargar` invoca ambas descargas; las otras dos solo una.
3. **Portal.** `runtime/portals/libros_portal_iva.py:L126-L216` gestiona la descarga de libros por período, incluido el paquete de documentos disponibles. `L218-L252` implementa DDJJ individual en PDF. Los faltantes de documentos accesorios pueden omitirse; ausencia de libros/documento descargable o error de período se eleva a la capa de resumen.

## Normalizaciones y resultado

`generar_rango_periodos` trunca silenciosamente el rango a 1.200 periodos (`schema.py:L51-L68`). `_descargar` itera períodos, registra éxitos y errores separados, y solo publica `detalle` cuando `incluir_json` aplica; `subir_archivos=false` deja el archivo fuera de `files[]` (`plugin.py:L222-L288`). En la operación compuesta las ramas quedan bajo `libros` y `ddjj` y se agregan `periodos_descargados`/`periodos_error` (`plugin.py:L184-L220`). Los nombres de archivo de plugin son `descargar_libros_{cuit}_{periodo}.bin` y `descargar_ddjj_{cuit}_{periodo}.bin`; son contenedores opacos de archivos, aunque el primer contenido es ZIP y el segundo PDF. Los errores por periodo no degradan por sí solos el estado global.

## Discrepancias observadas

- El modelo público V3 de central expresa `representado_cuit` con patrón de 11 dígitos sin separadores, mientras el validador efectivo limpia puntos, guiones, espacios y `/` (`central-api/api/bot_payloads.py:L188-L194`, schema `L26-L37`).
- El generador corta en 1.200 meses sin devolver advertencia y sin límite equivalente visible en el modelo (`schema.py:L51-L68`).
- El `credentials_schema` publicado por operación es objeto genérico con `additionalProperties=true`, sin nombres ni requiredness (`central-api/api/bots.py:L820-L845`). En ejecución se requieren cuenta/clave.

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/libros_portal_iva/schema.py:L22-L145`
- `services/bot-worker/src/bot_worker/bots/libros_portal_iva/plugin.py:L81-L220`, `L222-L288`
- `services/bot-worker/src/bot_worker/runtime/portals/libros_portal_iva.py:L126-L216`, `L218-L252`
- `services/central-api/src/central_api/api/bots.py:L400-L401`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L98-L99`
- Revisados: `services/bot-worker/tests/test_portal_libros_portal_iva.py` y tests de schemas. Son pruebas mock/contrato. No se ejecutó el portal.

No se usaron credenciales reales.