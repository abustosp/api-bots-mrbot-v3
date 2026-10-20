# Auditoría interna: `retper_iibb_misiones`

## Flujo esquema → plugin → portal

1. **Esquema:** `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/schema.py:20-53` valida CUIT y período exactamente seis dígitos/mes 01–12; `:56-116` define aliases, defaults, rangos, salida y campos sellados eliminados; `:126-145` es JSON Schema resumido con `required` incompleto.
2. **Plugin:** `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/plugin.py:117-170` declara portal, timeout, captcha/configuración; `:172-230` valida, exige credenciales, ingresa y consulta; `:232-284` convierte fechas, descarga XLSX y genera/valida PDF, publica ambos bajo `subir_archivo`.
3. **Portal ATM:** sesión propia en `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/session.py`. Login y CAPTCHA posterior al primer envío en `:92-223`; consulta con fechas `AAAA/MM` y XLSX validado en `:225-299`; solicitud de reporte PDF y comprobación de respuesta desde `:301-797`.

## Catálogo, aliases y normalizaciones

Catálogo central `services/central-api/src/central_api/api/bots.py:414`, operación `consultar`, clase `CONSULTA` por `:428-438`. Alias `/retenciones_percepciones_iibb/misiones/consulta` en `services/central-api/src/central_api/api/bot_compat.py:81-84`. Central `services/central-api/src/central_api/security/credentials.py:120-126` renombra `denominacion` a `representado_nombre` para Misiones; `:130-136` busca `cuit_representante`/`cuit_inicio_sesion` en el mapping de payload para completar el CUIT representado; `:200-210` normaliza `desde/hasta` a períodos; `:247-263` upload a `subir_archivo`.

Ese fallback inspecciona solo `payload`: `normalize_v2_payload` no recibe el objeto `credentials`. La ruta canónica conserva ambos mappings separados (`services/central-api/src/central_api/api/bots.py:574,608-618,793-817`); por tanto `credentials.cuit_representante` por sí solo no completa `payload.representado_cuit`. Un CUIT de sesión dentro del payload sí puede ser usado antes de que la extracción de credenciales lo separe, pero el contrato canónico debe enviar `representado_cuit` explícitamente.

Schema limpia CUIT separadores y conserva período AAAAMM estricto. Plugin adapta AAAAMM a AAAA/MM para el formulario (`plugin.py:239-250`); sesión compacta a AAAAMM al generar PDF (`session.py:314-318`). Login consulta ATM con CUIT representante de credenciales y clave, no login ARCA (`session.py:115-132`).

## Datos de salida

El plugin retorna siempre `data.operacion`, `representado_cuit`, `periodo_desde`, `periodo_hasta`, `archivos` con nombres XLSX/PDF; con subida exitosa agrega `sha256_xlsx`, `sha256_pdf`, y produce dos artefactos (`plugin.py:232-284`). PDF se valida por firma antes de publicar (`:254-258`). No crea detalle por fila.

## Discrepancias concretas y propuestas (sin implementar)

- **`incluir_json` es no-op:** el modelo lo valida y rechaza `incluir_json=false` junto con `subir_archivo=false` (`schema.py:81-116`), pero el plugin no consulta ese flag al construir `data` (`plugin.py:259-284`). **Propuesta:** retirar campo y validación del modelo o hacer que controle una parte de `data` de forma explícita; no conservar una restricción de salida que no puede desactivar.
- **Nombre público vs modelo:** normalización central solo traslada `denominacion` a `representado_nombre` para Misiones, mientras el modelo usa `denominacion` como alias de `representado_nombre` (`credentials.py:119-126`, schema `:66-70`). En payload directo el campo interno canonical `denominacion` sí pasa, pero la forma `representado_nombre` depende de la transformación/ruta. **Propuesta:** una sola propiedad canonical (`representado_nombre` o `denominacion`) y documentar alias consistentemente en OpenAPI/validación.
- **Manifest insuficiente:** `schema.py:126-145` declara requerido solo `operacion`, y no publica máximo 256 de denominación en `:136`; el modelo requiere CUIT, nombre y períodos. El catálogo `bot_payloads.py:272-277` refleja solo campos base y no prueba `required` real. **Propuesta:** derivar JSON Schema y tests de contrato del modelo.
- **Resultado no parcial:** plugin realiza XLSX, luego PDF, luego upload de dos archivos; una falla posterior levanta error, sin `PARCIAL` (`plugin.py:247-284`). Si el primer archivo ya se descargó o publicó antes de fallo del segundo, el resultado no describe la parcialidad.
- **Pruebas leídas:** `services/bot-worker/tests/test_portal_retper_iibb_misiones.py:24-52` verifica acceso separado y credenciales obligatorias; `:53-77` valida mensajes de rechazo; pruebas de descarga/exportación están más adelante en el mismo archivo.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/schema.py:20-145`
- `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/plugin.py:117-284`
- `services/bot-worker/src/bot_worker/bots/retper_iibb_misiones/session.py:19-28,92-223,225-318,370-797`
- `services/central-api/src/central_api/api/bots.py:414-438`
- `services/central-api/src/central_api/api/bot_compat.py:81-84`
- `services/central-api/src/central_api/security/credentials.py:119-136, 200-210, 247-263, 303-370`
- `services/central-api/src/central_api/api/bot_payloads.py:272-277`
- `services/bot-worker/tests/test_portal_retper_iibb_misiones.py:24-77`
