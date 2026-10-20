# ARBA: auditoría interna

## Flujo efectivo

1. Catálogo central: `arba/descargar` (`services/central-api/src/central_api/api/bots.py:329-339,378-386`). Ruta canónica V3 acepta `payload` y `credentials` como diccionarios genéricos (`services/central-api/src/central_api/api/bots.py:194-208,748-817`).
2. Esquema operativo requiere CUIT, período y nombre; soporta aliases `cuit`, `cuit_representado`, `denominacion`; `subir` default `true` (`services/bot-worker/src/bot_worker/bots/arba/schema.py:91-129`). CUIT quita separadores y no valida checksum; período normaliza a seis dígitos, exige mes 01–12 y prohíbe períodos futuros (`services/bot-worker/src/bot_worker/bots/arba/schema.py:43-72`).
3. El plugin requiere credencial y clave no vacía, pero inicia sesión con CUIT representado del payload y esa clave; selecciona rol contribuyente si aparece, consulta período y descarga ZIP (`services/bot-worker/src/bot_worker/bots/arba/plugin.py:140-230`). El nombre de descarga incorpora CUIT, período y denominación saneada (`services/bot-worker/src/bot_worker/bots/arba/schema.py:75-88`).
4. Sin archivo devuelve `OK`, `archivos: []`, mensaje. Con archivo devuelve envelope y opcionalmente publica artefacto (`services/bot-worker/src/bot_worker/bots/arba/plugin.py:204-261`).

## Normalizaciones, salida y parciales

Período acepta separadores no numéricos al limpiar; no se limita a validar el literal inicial. El nombre se limpia, consolida whitespace, quita caracteres inválidos y tiene fallback `SIN_DENOMINACION`, aunque el input requiere nombre no vacío (`services/bot-worker/src/bot_worker/bots/arba/schema.py:57-88,100-104`). `subir=false` deja nombre y `archivos[].url_minio=null` en `data`, sin artefacto/huella/tamaño (`services/bot-worker/src/bot_worker/bots/arba/plugin.py:232-261`). Sin datos es éxito vacío; fallos de consulta, descarga o subida son errores, no parciales.

## Discrepancias concretas

- El `CreateJobBody` de la ruta canónica anidada contiene payload genérico; la validación de negocio del período/nombre ocurre en el plugin tras aceptación del job (`services/central-api/src/central_api/api/bots.py:194-208,748-817`; `services/bot-worker/src/bot_worker/bots/arba/plugin.py:127-138`). El modelo de `bot_payloads.py` es el espejo de rutas flat/compatibilidad, no validador del endpoint anidado.
- Ese espejo flat hace nombre opcional y máximo 256 (`services/central-api/src/central_api/api/bot_payloads.py:95-99`), mientras esquema de worker lo requiere, min 1/max 128 (`services/bot-worker/src/bot_worker/bots/arba/schema.py:100-104`). La ruta flat puede dejar que el pedido llegue al plugin y fallar por omisión/largo.
- El espejo de período sólo modela patrón de seis dígitos; worker valida mes y no futuro (`services/central-api/src/central_api/api/bot_payloads.py:71-73,95-99`; `services/bot-worker/src/bot_worker/bots/arba/schema.py:57-72`).
- `denominacion` se remapea a `representado_nombre` por normalización de compatibilidad (`services/central-api/src/central_api/security/credentials.py:116-125`). `representado_cuit` y `representado_nombre` canonical son los usados por ejecución.
- La sesión ARBA usa el CUIT del representado como CUIT de login (`services/bot-worker/src/bot_worker/bots/arba/plugin.py:163-169`), aunque el contenedor de credenciales también puede contener otro CUIT representante.

## Propuestas, sin implementar

- Declarar nombre requerido, min 1/max 128 en el modelo flat y mantener validación de negocio equivalente en la ruta anidada.
- Unificar en ambos tipos de ruta el rango de período, no futuro, y documentar si la normalización de separadores de fecha es deliberada.
- Aclarar si ARBA debe iniciar sesión con CUIT representado o credencial representante antes de alterar selección; no inferirlo del resto de bots.
- Mantener aliases con lista única y separar en documentación los contratos anidado V3 y flat de compatibilidad.

## Evidencia revisada

- Registro/catalog/ruta: `services/bot-worker/src/bot_worker/bots/_plugins.py:90-100`; `services/central-api/src/central_api/api/bots.py:194-208,329-339,378-386,748-817`.
- Entrada declarativa y aliases: `services/central-api/src/central_api/api/bot_payloads.py:71-73,95-99`; `services/central-api/src/central_api/security/credentials.py:116-125`.
- Esquema/plugin: `services/bot-worker/src/bot_worker/bots/arba/schema.py:39-149`; `services/bot-worker/src/bot_worker/bots/arba/plugin.py:127-261`.
- Pruebas inspeccionadas: `services/bot-worker/tests/test_port_prov.py:446-495,535-568`. Este análisis no ejecutó el portal; pruebas locales coordinadas en [validación](../validacion.md).