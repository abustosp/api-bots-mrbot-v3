# Auditoría interna: consulta_cuit

## Flujo esquema -> plugin -> servicio

1. `schema.py` usa modelos estrictos. Normaliza CUIT quitando separadores y verifica exactamente 11 dígitos, sin validar checksum. El modo masivo requiere lista de 1–100 CUIT, normaliza/deduplica en orden estable.
2. `plugin.py` declara `consultar` y `consultar_masivo`, sin credencial fiscal, navegador o captcha. Consulta endpoints HTTP configurables; individual envía CUIT como parámetro y masivo un JSON `{"cuits": [...]}`. La respuesta útil es el objeto JSON remoto, no un esquema fiscal local. Respuesta no objeto, error HTTP/red o JSON inválido producen error.
3. No hay navegación de portal ARCA en este bot: el portal/servicio integrado es el endpoint de constancias de inscripción configurable; URLs base y credenciales técnicas se configuran fuera del payload del cliente.

No produce archivos ni una respuesta parcial transformada. La configuración de base URL, usuario y API key es del servicio y no forma parte de `credentials` públicas del solicitante.

## Catálogo/ruta y pruebas

- Operaciones/costos: `services/central-api/src/central_api/api/bots.py:330,343-344,393-395`; catálogo incluye `consulta`, `consultar`, `consultar_masivo`.
- El dispatcher traduce `consulta` a operación de plugin `consultar`: `services/central-api/src/central_api/scheduler/dispatcher.py:123-135`.
- Schema: `services/bot-worker/src/bot_worker/bots/consulta_cuit/schema.py:1-89`; plugin y endpoints/configuración: `services/bot-worker/src/bot_worker/bots/consulta_cuit/plugin.py:43-202`.
- API: `services/central-api/src/central_api/api/bot_payloads.py:152-155,480-482,522-524`; ruta/envelope y normalización canónica: `services/central-api/src/central_api/api/bots.py:466-515,517-635`.
- Pruebas leídas: `services/bot-worker/tests/test_consulta_cuit_plugin.py`, `tests/test_worker_operacion_payload.py:1-65`, `tests/test_paridad_catalogo_registry.py:1-76`, `services/central-api/tests/test_catalog_seed.py:1-46`.

## Discrepancias y propuestas

1. El catálogo expone tres operaciones, pero el manifest del plugin solo declara `consultar` y `consultar_masivo`. La primera es una traducción en dispatcher, no una tercera rama del plugin. Mantener la prueba de alias de catálogo/dispatcher y dejar esa distinción clara en OpenAPI.
2. El schema del plugin incluye `operacion` como literal en las entradas, pero en V3 la operación viene de la ruta/sobre y el dispatcher la inyecta. El JSON Schema del body de central enumera por separado `cuit` y `cuits` sin expresar el requisito ligado a la ruta. Propuesta: esquemas request por combinación ruta-operación, o marcar `operacion` como campo de transporte no suministrado por el cliente.
3. El API central representa CUIT con regex de 11 dígitos, el plugin elimina separadores antes de validar. OpenAPI no comunica las formas con guiones/puntos/barras que realmente se normalizan. Propuesta: patrón/documentación coherente y tests de paridad para ambas formas.
4. El endpoint responde con objeto libre remoto. Mantener el esquema de resultado explícitamente abierto, no inventar campos fiscales ni convertirlo en contrato fijo sin evidencia del proveedor.
