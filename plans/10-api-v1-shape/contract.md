# Contrato: API pública V3 con forma V1/V2

Documento de coordinación del swarm. Todos los agentes lo respetan; los cambios
de contrato se piden al coordinador (elephant), no se improvisan.

## 1. Autenticación Bearer (agente AUTH)

- Header normativo: `Authorization: Bearer <token>`.
- `token = base64url(usuario) + "." + base64url(api_key)`, UTF-8, se acepta con o
  sin padding `=` y también base64 estándar (`+/`). `usuario` es la identidad
  (email o alias de depuración como `abp`), comparación case-insensitive.
- La dependencia `require_api_principal` (api/dependencies.py) valida que la
  API key pertenezca a ESE usuario, que la clave esté activa y el usuario
  habilitado. Error genérico 401 con `WWW-Authenticate: Bearer`.
- `X-API-Key` sigue aceptado como compatibilidad (deprecated en la doc) para no
  romper clientes actuales.
- OpenAPI: security scheme `HTTPBearer` (bearerFormat `b64(usuario).b64(api_key)`)
  aplicado a todas las rutas protegidas, así el botón Authorize de Swagger sirve.
- Helper puro reutilizable: `central_api/security/bearer.py` con
  `encode_bearer(usuario, api_key) -> str` y `decode_bearer(token) -> (usuario, api_key)`.
- `POST /api/v3/auth/token` body `{usuario, api_key}` -> `{access_token, token_type:"bearer", usuario}`.
  Valida credenciales (401 genérico si no son válidas). Sin auth previa.

## 2. Usuarios estilo V1 (agente AUTH)

Router nuevo `api/users.py`, tag `usuarios`, prefijo `/api/v3/usuarios`:

- `POST /usuarios` `{usuario, nombre?, enviar_email?: bool}`: crea el usuario
  **deshabilitado** con API key aleatoria. Devuelve la clave una sola vez
  (`api_key`, `Cache-Control: no-store`) y la envía por email si `usuario` es un
  email y SMTP está configurado. 409 si existe. Un admin lo habilita después.
- `POST /usuarios/restablecer-api-key` `{usuario}`: genera clave nueva, revoca
  las anteriores y la envía SOLO por email. Respuesta genérica 202 siempre (no
  revela si existe ni devuelve la clave).
- `POST /usuarios/establecer-api-key` `{usuario, api_key_actual, api_key_nueva}`:
  verifica la clave vigente, emite `api_key_nueva` (mín. 3 chars, sin espacios,
  mismas reglas que `emitir_clave`), revoca la anterior. 401 genérico si la
  actual no valida.
- `GET /usuarios/me` (Bearer): identidad, estado, plan, saldo/consultas.
- Persistencia igual que el panel admin (memoria + PostgreSQL si hay base). Nunca
  guardar claves en claro; auditar con `log_event`.

## 3. Endpoints por bot (agente ROUTES)

Por cada bot del catálogo, un router con prefijo V1 (el de `BOT_ROUTE_ALIASES`
de `api/bot_compat.py`, ej. `/mis_comprobantes`, `/aportes-en-linea`) y tag
propio del bot (Swagger agrupa por bot como V1). Por cada operación `{op_path}`
(ej. `/consulta`, `/solicitar_consulta`) la forma V2:

- `POST /api/v3/{bot_prefix}{op_path}` -> 202 `{success, job_id, status, links}`
  (crea el job; body = modelo del bot, ver §4).
- `GET  /api/v3/{bot_prefix}{op_path}/{job_id}` -> estado + resultado.
- `POST /api/v3/{bot_prefix}{op_path}/cancelar/{job_id}` -> cancela.
  Validar que el job pertenezca a ese bot/operación (404 si no).

Genéricos (tag `jobs`):
- `GET  /api/v3/jobs/cola` -> jobs del usuario en PENDIENTE/ASIGNADO/CORRIENDO,
  con posición en cola para PENDIENTE; filtros `bot`, `status`. Lee PostgreSQL
  cuando hay base (no solo memoria).
- `POST /api/v3/jobs/{job_id}/cancelar` (existe) y alias `DELETE /api/v3/jobs/{job_id}`:
  cancelación por job ID, funcionando también con PostgreSQL.
- `GET /api/v3/jobs/{job_id}` se mantiene.
- La ruta genérica `POST /bots/{bot}/{operacion}` se mantiene (compatibilidad).

## 4. Schemas y ejemplos (agente SCHEMAS)

- Módulo central `central_api/api/bot_schemas.py` con un modelo Pydantic por
  (bot, operación): campos con `description`, tipos, validaciones y
  `json_schema_extra={"examples": [...]}` con varios ejemplos realistas estilo V1
  (ver `api-bots-mrbot/app/schemas` y `v1_request_schemas.json`).
- Interfaz fija que usa ROUTES:
  `get_request_model(bot: str, operation: str) -> type[BaseModel]` (siempre
  devuelve un modelo; fallback genérico documentado) y
  `get_openapi_examples(bot, operation) -> dict[str, dict]` (formato
  `openapi_examples` de FastAPI: `{nombre: {summary, description, value}}`).
  Modelos de respuesta por bot: `get_result_model(bot, operation)` opcional.
- Las credenciales van en el mismo body plano como en V1 (`cuit_representante`,
  `clave`, etc.) y el central las separa a `credentials` como hoy.
- Duplicado en el worker: `bot_worker/schemas/` con los mismos modelos de
  payload (copia, sin importar el paquete central) y el worker los expone en su
  OpenAPI (`/internal/v1/bots/{bot}/{operation}/schema` y como `openapi_examples`
  documentales), para que el Swagger del worker se autodocumente.
- Test de paridad: los schemas JSON central y worker coinciden por (bot, op).

## Reglas comunes

- Repo: `/media/abp/HDD/Proyectos Python/Scripts/Mr bot/mrbot/api-bots-mrbot-v3`.
- Tests: `/home/abp/.jcode/scratch/mrbot-v3-venv/bin/python -m pytest services/central-api/tests -q`
  (y `services/bot-worker/tests`). No romper los existentes; actualizar si el
  contrato cambia a propósito.
- NO hacer `git commit`/`push` ni desplegar: lo hace el coordinador.
- Solo editar los archivos de tu propiedad; si necesitás tocar otro, pedirlo por DM.
- Nunca imprimir ni loguear API keys o secretos.
