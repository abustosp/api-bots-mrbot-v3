# Auditoría interna: comprobantes

## Flujo efectivo

1. **Esquema:** normaliza puntos de venta, CUIT y fechas; valida rango, flags y operación. Modelo estricto (`extra=forbid`). El campo de puntos de venta requiere una lista; cada elemento se convierte a texto, se filtran dígitos y se preservan ceros; máximo 200. Un helper admite un escalar aislado, pero no es el tipo aceptado por el esquema. La CUIT se valida por 11 dígitos, no checksum. `carga_b64`, `proxy_request`, `eliminar_descargas` y `verbose` no controlan la salida efectiva; `eliminar_descargas` se retira antes de la validación del bot.
2. **Plugin:** manifiesta `comprobantes`, operaciones `consultar/solicitar/historial`, autenticación y captcha, idempotencia `CONTINUACION`, dos CSV opcionales. `solicitar` llama al portal y crea consultas asíncronas por tipo. `consultar` consume las descargas disponibles. `historial` busca registros existentes. CSV se analiza para filas/muestras y se publica opcionalmente; hash/tamaño acompañan la publicación.
3. **Portal:** para consultar/solicitar, sesión ARCA y servicio “Mis Comprobantes” con tipos emitidos/recibidos, fechas y puntos de venta. La solicitud invoca la generación de consulta y devuelve sus IDs. El historial recorre solicitudes existentes y exporta CSV, sin crear otra consulta.

## Contrato y salidas

`consultar`: resúmenes por emitidos/recibidos con filas, archivo, hash/tamaño y muestra opcional; CSV en `files[]` solo si `subir_csv`. `solicitar`: `ids_consulta`; no exporta CSV. `historial`: `consultas` y `faltantes`, con estado de continuación/resultado parcial según disponibilidad.

Credenciales centrales aceptadas: `clave`, `clave_representante`, `contrasena` y `clave_encriptada`; normalización central entrega `clave` y `cuit_representante`. Los secretos vacíos/null se descartan y la falta de credencial se detecta al ejecutar. `cuit_inicio_sesion` se promueve a contexto de login. La contraseña no forma parte del payload persistido.

## Catálogo, rutas y aliases

- Catálogo fuente: `services/central-api/src/central_api/api/bots.py:340-342,388-392,404-405`. `comprobantes` tiene tres operaciones/costo 2 y se declara `deprecated`, `alias_de=mis_comprobantes`; simultáneamente hay operaciones separadas del bot `mis_comprobantes`.
- `services/central-api/src/central_api/scheduler/dispatcher.py:123-135` normaliza `consulta` a `consultar`. La ruta de compatibilidad está en `services/central-api/src/central_api/api/bot_compat.py:55-112`.
- Worker: `services/bot-worker/src/bot_worker/bots/comprobantes/schema.py:42-90,93-209`; `services/bot-worker/src/bot_worker/bots/comprobantes/plugin.py:135-169,197-209,211-251,257-464`; portal de historial `services/bot-worker/src/bot_worker/runtime/portals/comprobantes.py:76-167`; acceso ARCA y solicitudes `services/bot-worker/src/bot_worker/runtime/arca_login.py:43-45,98-225`.
- Central: `services/central-api/src/central_api/api/bot_payloads.py:140-151,492-495,573-576`; `services/central-api/src/central_api/security/credentials.py:74-115,227-281,303-370`; `services/central-api/src/central_api/api/bot_routes.py:475-542`.
- Pruebas leídas: `services/bot-worker/tests/test_comprobantes_plugin.py`, `services/bot-worker/tests/test_comprobantes_paridad_v1.py`, `services/bot-worker/tests/test_comprobantes_historial_plugin.py`, `services/bot-worker/tests/test_mis_comprobantes_operaciones.py`, `services/bot-worker/tests/test_mis_comprobantes_plugin.py`, `services/bot-worker/tests/test_portal_comprobantes_historial.py`, `services/bot-worker/tests/test_arca_service_puntos_venta.py`; central `tests/test_paridad_catalogo_registry.py` y `services/central-api/tests/test_catalog_seed.py`.

## Discrepancias observadas

1. **Alias catalogado pero plugin independiente.** El catálogo marca `comprobantes` alias/deprecated de `mis_comprobantes`, pero existe plugin registrado con ese nombre y un grupo separado de operaciones/rutas. No equivale a una ruta inexistente. La API canónica recomienda `mis_comprobantes`; la ruta antigua sigue apareciendo en catálogo.
2. **Diferencia de default por entrada.** El DTO del alias plano de `mis_comprobantes` serializa `carga_minio=false,carga_json=false` para `consulta/consultar` (`services/central-api/src/central_api/api/bot_payloads.py:573-576`), mientras el esquema efectivo del bot parte de `subir_csv=true,incluir_json=true` (`services/bot-worker/src/bot_worker/bots/comprobantes/schema.py:178-187`). El catálogo describe defaults V3 en true (`services/central-api/src/central_api/api/bot_payloads.py:140-150`), pero la ruta canónica no valida ese DTO: aplica el esquema efectivo del bot. Por eso el alias plano y la ruta canónica pueden producir salidas distintas al omitir flags.
3. `solicitar` no tiene flags de salida en su modelo; `cookies_header` solo está en `historial`, pero el esquema amplio puede inducir a tratarlos como campos comunes. Asociarlos a la operación correcta.
4. El esquema conserva `carga_b64`, `proxy_request`, `eliminar_descargas`, `verbose` y `cookies_header` sin efecto visible; su presencia sugiere controles que no existen.
5. `historial` no genera la solicitud. Confundir su CSV de consultas existentes con `consultar` puede inducir a esperar una consulta nueva.
6. El validador de la operación exige listas (`services/bot-worker/src/bot_worker/bots/comprobantes/schema.py:146-161`); su helper aislado puede envolver un escalar, pero la validación lo rechaza antes de ejecutarlo. El esquema de catálogo anuncia arrays sin publicar el máximo 200 ni que se normalizan a texto conservando ceros (`services/central-api/src/central_api/api/bot_payloads.py:145-148`). Documentar la lista, no un escalar aceptado.

## Propuestas, sin implementar

- Aclarar si `comprobantes` debe seguir teniendo plugin/rutas o ser una redirección única; mantener explícita la deprecación y paridad de operaciones mientras coexista.
- Definir por operación modelos distintos (solicitud sin flags de salida, historial con `cookies_header` legado) y publicar límites/normalización real de puntos de venta.
- Retirar controles ignorados en una versión mayor o marcarlos formalmente como ignorados/deprecados.
- Unificar defaults V2/V3 o documentar en catálogo el cambio de comportamiento de publicación y muestra.
- Añadir tests de contrato de ambas entradas para evitar divergencia de flags y rutas.
