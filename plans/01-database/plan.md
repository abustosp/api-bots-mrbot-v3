# Plan 01: base de datos PostgreSQL para MrBot API V3
> **Estado:** diseño de implementación.
> **Ámbito:** `plans/01-database/`.
> **Fuentes V2:** `app/models/user.py`, `app/models/playwright_job_active.py`, `app/models/playwright_job_history.py`, `app/jobs/manager.py`, `app/api/deps.py` y los inventarios `.research/02-jobs-workers.md`, `.research/03-database-models.md`, `.research/04-auth-admin-config.md`.
## 1. Objetivo y alcance
Este plan define el esquema PostgreSQL 17 que será la única fuente de verdad persistente de MrBot API V3. Define las tablas, restricciones, índices, política de IDs, migraciones Alembic, importación de legado, consultas operativas y retención.
La propiedad de la base corresponde exclusivamente a `services/central-api`. `bot-worker` no recibe DSN, usuario ni credenciales de PostgreSQL. Un worker recibe un sobre autocontenido, sube artefactos mediante URL prefirmada y reporta eventos y resultado por HTTP a la central. Esta es la invariante W-1 del plan maestro.
Este plan **sí** posee:
- Identidad de clientes, claves API y administradores.
- Catálogo declarativo de bots y operaciones.
- Cola, ciclo de vida, resultado, eventos, leases y artefactos de jobs.
- Registro y telemetría persistida de workers.
- Planes, suscripciones, períodos, consumo, créditos y pagos.
- Auditoría inmutable y el esquema de compatibilidad `legacy`.
- DDL base, índices, convenciones Alembic y trabajos de poda.
Este plan **no** posee:
- Rutas HTTP, Pydantic, algoritmo de scheduler ni autenticación mTLS del protocolo. Son responsabilidad de `plans/02-central-api/plan.md` y `plans/03-worker/plan.md`.
- La UX del administrador, aunque esta consulta las tablas aquí definidas. Pertenece a `plans/05-admin-panel/plan.md`.
- Precio comercial, integración de checkout ni firma/validación de MercadoPago. La semántica de facturación se coordina con `plans/04-billing/plan.md`.
- Almacenamiento físico de bytes de archivos. Es MinIO/S3. La base guarda solamente metadatos y claves de objeto, nunca URL prefirmadas.
- Migrar y reinterpretar el contenido histórico de las 28 tablas de consulta durante la fase inicial. Se importa sin modificación a `legacy`.
## 2. Principios de diseño del esquema
1. **Una única fuente de verdad.** PostgreSQL es la autoridad para identidad, admisión, asignación, cobro, estado y auditoría. No existen contadores equivalentes en memoria del worker ni tablas por réplica.
2. **Un único escritor de base.** Solo `central-api` abre conexiones de aplicación. Los workers están aislados de la DB y sus callbacks atraviesan validación, autorización e idempotencia en la central.
3. **Estado de job normalizado.** `jobs` contiene una fila desde creación hasta estado terminal. No se copia ni borra una fila para moverla entre activa e histórica como hacía V2 con `playwright_jobs_active` y `playwright_jobs_history`.
4. **Append-only donde importa.** `usage_ledger`, `credit_ledger`, `payment_events`, `job_events` y `audit_log` no se actualizan ni borran por lógica de negocio. Rectificar equivale a agregar un hecho compensatorio ligado al original.
5. **JSONB solo para variación real.** La identidad, filtros operativos, estados, fechas, claves, ownership y artefactos se tipan en columnas. Los campos que cambian por bot viven en `job_results.payload JSONB`.
6. **Secretos fuera de logs.** Nunca se persisten en claro `clave`, `clave_representante` ni `clave_encriptada`; la tabla `jobs` solo guarda `credential_ciphertext` RSA y el acceso administrativo descifra bajo auditoría. Tampoco se persisten URL prefirmadas ni tokens de proveedor.
7. **UTC y tiempo con zona.** Todos los instantes son `timestamptz`, almacenados y comparados en UTC. Los períodos de facturación son intervalos explícitos, no inferencias desde una fecha del usuario.
8. **Restricciones en el servidor.** Defaults que son invariantes del servidor se expresan en PostgreSQL. La aplicación genera los UUID, pero checks, FK, unicidad y transiciones admisibles se verifican en la base.
9. **Diseñado para reintentos.** Webhooks, callbacks, asignación y resultados poseen claves de idempotencia y restricciones únicas. El comportamiento es al menos una vez en el borde y exactamente una vez para efectos contables.
10. **Sin IDs correlativos.** No se usan `serial`, `bigserial`, `identity`, secuencias ni enteros autoincrementales en el esquema V3.
## 3. Estrategia de identificadores
### 3.1 Regla de tipos
| Dominio | Tipo PostgreSQL | Generador canónico | Motivo |
|---|---|---|---|
| `users.id` | `uuid` UUIDv4 | aplicación | No revela orden ni momento de alta del cliente. |
| Todo lo demás V3 | `uuid` UUIDv7 | aplicación | Orden temporal aproximado, buena localidad de B-tree y orden natural de inserción. |
| Legado | `integer` | preservado | Solo dentro de `legacy`, que es lectura y compatibilidad. |
PostgreSQL almacena ambos formatos como el tipo nativo `uuid`, no como `varchar(36)`. La representación textual se limita a fronteras HTTP, logs de proceso y migración de datos.
### 3.2 `users.id`: UUIDv4
`users.id` se genera como UUIDv4 criptográficamente aleatorio en `central-api`. La razón no es rendimiento sino privacidad de identidad: un UUIDv4 no permite enumerar clientes, estimar el número de altas ni filtrar el orden temporal de registro. Un UUIDv7 sí expondría un prefijo temporal aproximado y por ello no corresponde a la identidad pública primaria del cliente.
La aplicación debe llamar `uuid.uuid4()` al construir la entidad. No se debe depender de que un cliente entregue el ID. El DDL no instala un `DEFAULT` en `users.id`, de forma que la omisión falle y revele un bug de frontera.
`gen_random_uuid()` de `pgcrypto` es una alternativa válida para UUIDv4 generados por la base, especialmente para scripts SQL administrativos. No es la estrategia canónica porque se desea portabilidad de pruebas, consistencia con la creación de objetos del dominio y trazabilidad previa al `INSERT`. Si se habilita como defensa opcional, se hace con `CREATE EXTENSION IF NOT EXISTS pgcrypto` y `DEFAULT gen_random_uuid()`, documentando que el ID puede no estar disponible hasta el insert. La migración inicial elegirá una sola estrategia, no ambas de manera ambigua.
### 3.3 IDs UUIDv7 para bots, jobs, facturación y auditoría
`bots`, `bot_operations`, las tablas de ejecución, flota, facturación y auditoría usan UUIDv7. UUIDv7 ordena de forma aproximada por milisegundo de creación. Frente a UUIDv4 aleatorio reduce dispersión de páginas B-tree en tablas de alta inserción, como `jobs`, `job_events`, `usage_ledger`, `payment_events` y `audit_log`.
El orden de `jobs.id` es además el desempate natural de cola. La reclamación ordena por `created_at, id`, y `id` garantiza estabilidad entre inserciones del mismo instante. No se reemplaza `created_at`, ya que la marca explícita sigue siendo necesaria para SLA, retención y diagnóstico. Sí reemplaza la necesidad de un entero correlativo para FIFO.
V2 ya contiene `app/utils/uuid7.py`, que utiliza el paquete `uuid-utils`, y `app/jobs/manager.py` ya crea `job_id` UUIDv7. V3 reutiliza ese patrón en un módulo compartido de persistencia y genera los valores en la aplicación antes de persistirlos. Esto permite pruebas deterministas mediante un generador inyectable y evita imponer extensiones no estándar en instalaciones PostgreSQL administradas.
La extensión `pg_uuidv7` es una alternativa operativa. Permite algo equivalente a `DEFAULT uuid_generate_v7()` o a la función expuesta por la versión de extensión instalada. Es conveniente para inserciones SQL masivas y para defensas server-side, pero añade dependencia de empaquetado, privilegios de instalación y nombres de función no portables. No forma parte del baseline de PostgreSQL 17 ni debe asumirse disponible. Si se adopta, se encapsula en una migración condicional, se registra la versión exacta y no se mezclan IDs generados por extensión y aplicación sin una prueba de monotonicidad.
### 3.4 Prohibición verificable de secuencias
La regla dura es cero IDs secuenciales en `public`. No basta una convención de ORM. La CI ejecutará esta consulta después de `alembic upgrade head` y deberá devolver cero filas:
```sql
WITH sequence_objects AS (
    SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
), identity_or_serial_columns AS (
    SELECT n.nspname AS schema_name,
           c.relname AS table_name,
           a.attname AS column_name,
           a.attidentity,
           pg_get_expr(ad.adbin, ad.adrelid) AS default_expression
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attrdef ad
      ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND (
          a.attidentity <> ''
          OR pg_get_expr(ad.adbin, ad.adrelid) ~* 'nextval\\('
      )
)
SELECT schema_name, object_name AS object_or_table, NULL::text AS column_name,
       relkind::text AS finding
FROM sequence_objects
UNION ALL
SELECT schema_name, table_name, column_name,
       CASE WHEN attidentity <> '' THEN 'identity' ELSE 'serial/nextval' END
FROM identity_or_serial_columns
ORDER BY 1, 2, 3;
```
La consulta excluye deliberadamente `legacy`: ese esquema preserva enteros V2 por compatibilidad y no forma parte del modelo nuevo. Una prueba adicional verifica que cada PK de `public` sea `uuid` mediante `pg_catalog.format_type`.
## 4. Cambios de `users` y ciclo de cuota
### 4.1 Comparación completa antes y después
| Columna V2 | V2: tipo y semántica | V3 | Justificación y destino |
|---|---|---|---|
| `id` | `INTEGER` PK autoincremental | `id uuid` UUIDv4 PK | Elimina enumeración y la secuencia. |
| `mail` | `String`, nullable, única | `email text NOT NULL`, única case-insensitive | Identidad de contacto no ambigua. Normalizar a minúsculas en la aplicación y restringirla. |
| `api_key` | verificador HMAC en la fila de usuario | eliminado | Se mueve a `api_keys.verifier_hmac`. Soporta varias claves y rotación sin alterar identidad. |
| `maximas_consultas_mensuales` | contador límite mutable | eliminado | Pertenece al snapshot `subscription_periods.included_units`. |
| `consultas_realizadas` | contador mensual mutable | eliminado | Se deriva de hechos `usage_ledger` para el período. |
| `habilitado` | boolean nullable, default `False` | `habilitado boolean NOT NULL DEFAULT false` | Mantiene bloqueo explícito de la cuenta. |
| `fecha_ultimo_reset` | fecha de reset de cuota | eliminado | El límite se reinicia al abrir un nuevo `subscription_periods`, no al autenticar. |
| `created_at` | fecha Python de alta | eliminado | Remoción explícita solicitada. La evidencia temporal administrativa vive en `audit_log`. |
| `updated_at` | fecha Python `onupdate` | eliminado | Remoción explícita solicitada. Los cambios auditables se registran como eventos. |
Evidencia: V2 define las columnas en `app/models/user.py:8-24`. El inventario `.research/04-auth-admin-config.md` §4 confirma que `fecha_ultimo_reset` no resetea contraseña pese a su docstring, sino consumo mensual.
### 4.2 Del reset perezoso a períodos explícitos
En V2 `validate_api_key` de `app/api/deps.py:102-155` autentica email y clave, rechaza `habilitado=False`, lee el reloj UTC y compara el año y mes de `fecha_ultimo_reset`. Si el mes cambió, ejecuta `consultas_realizadas = 0`, escribe `fecha_ultimo_reset = now` y hace `commit`. Luego rechaza con 429 si `consultas_realizadas >= maximas_consultas_mensuales`. El incremento ocurre más tarde y por ruta mediante `incrementar_consultas_realizadas`. Es un efecto lateral de autenticación, no una transacción de admisión.
V3 elimina completamente esa conducta. Autenticar una API key solo identifica a un `users.id`, valida la vigencia de la clave y comprueba `users.habilitado`. Nunca cambia consumo y nunca abre o resetea períodos.
Al crear o renovar una suscripción, el servicio de facturación crea un `subscription_periods` con límites congelados desde el plan: `starts_at`, `ends_at`, `included_units`, `credit_unit_price` y estado `ABIERTO`. El scheduler/admisor localiza el período que satisface `starts_at <= now() < ends_at` y `status='ABIERTO'`. La reserva de uso y la inserción del job se hacen en la misma transacción. Cada reserva crea una entrada `usage_ledger` de `units = 1`, `event_type='RESERVA'`, ligada al `job_id` y al `period_id`.
La cuota consumida es una suma, no un contador. Para un período se suman unidades del ledger, donde `RESERVA` agrega unidades y `REEMBOLSO` agrega unidades negativas. Al completar, una entrada `CONFIRMACION` de cero unidades fija el hecho final sin duplicar consumo. Si se cancela o falla bajo una política reembolsable, `REEMBOLSO` compensa la reserva. La restricción única `(job_id, event_type)` vuelve idempotente cada paso.
El período siguiente existe aunque nadie llame a la API: puede ser creado por webhook de renovación o por un job de facturación. No hay lógica basada en el primer request del mes. La zona y los límites son los de la suscripción, no UTC-calendario implícito.
Un usuario sin suscripción activa no tiene `subscription_periods` abierto. La admisión no crea uno por autenticación y no consume una cuota imaginaria. Debe intentar débito de `credit_ledger` si el producto permite créditos sin suscripción. Si el saldo disponible no cubre la operación, el job se rechaza antes de insertarse con error de entitlement. No se deja un `PENDIENTE` que el scheduler reintente eternamente, defecto posible en V2 según `.research/02-jobs-workers.md` §1.4.
### 4.3 API keys separadas y rotables
`api_keys` separa credencial de identidad. La aplicación entrega el secreto únicamente en la creación o rotación. Guarda `key_prefix` para selección y soporte, y `verifier_hmac` con formato versionado, por ejemplo `hmac-sha256$<hex>`, usando secreto rotado por key-id configurable. La comparación usa `hmac.compare_digest`.
Una cuenta puede tener varias claves activas por automatización o transición. La rotación crea una fila nueva, opcionalmente con `replaces_key_id`, y revoca la previa mediante `revoked_at`. No se sobrescribe el verificador, lo que preserva trazabilidad. `last_used_at` se actualiza como telemetría acotada y es la única excepción operativa documentada a la inmutabilidad de credential metadata. Nunca se almacena el secreto en claro.
## 5. Esquema V3 completo
Convenciones de las tablas siguientes:
- Todos los IDs de `public` son `uuid`. `users.id` es UUIDv4, el resto UUIDv7.
- `now()` significa `CURRENT_TIMESTAMP` con zona.
- `FK` indica clave foránea y su comportamiento `ON DELETE` se detalla en la nota.
- `UQ` significa restricción o índice único.
- Los checks de estado se enumeran en §7 y se materializan en el DDL de §9.
### 5.1 Identidad
#### `users`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv4 | PK `pk_users` | Identidad no enumerable. |
| `email` | `text` | no | ninguno | UQ `uq_users_email_lower` por `lower(email)` | Selector de cuenta normalizado. |
| `habilitado` | `boolean` | no | `false` | `NOT NULL` | Bloqueo de acceso del cliente. |
#### `api_keys`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Identidad de la clave. |
| `user_id` | `uuid` | no | ninguno | FK a `users`, índice | Dueño. `RESTRICT` evita borrar una identidad con claves. |
| `key_prefix` | `varchar(16)` | no | ninguno | UQ | Prefijo público no secreto para soporte. |
| `verifier_hmac` | `text` | no | ninguno | UQ | HMAC versionado, nunca la clave. |
| `label` | `varchar(120)` | sí | ninguno | ninguno | Nombre humano opcional. |
| `scopes` | `jsonb` | no | `'[]'::jsonb` | CHECK array | Alcances permitidos. |
| `expires_at` | `timestamptz` | sí | ninguno | índice parcial activa | Vencimiento opcional. |
| `revoked_at` | `timestamptz` | sí | ninguno | índice parcial activa | Revocación sin borrar evidencia. |
| `replaces_key_id` | `uuid` | sí | ninguno | FK a `api_keys` | Cadena de rotación. |
| `last_used_at` | `timestamptz` | sí | ninguno | ninguno | Telemetría de uso. |
| `created_at` | `timestamptz` | no | `now()` | índice | Momento de emisión, no pertenece a `users`. |
#### `admin_users`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Sustituye admin global de `.env`. |
| `email` | `text` | no | ninguno | UQ por `lower(email)` | Principal administrador. |
| `password_hash` | `text` | no | ninguno | ninguno | Argon2id, no HMAC. |
| `roles` | `jsonb` | no | `'[]'::jsonb` | CHECK array | Roles explícitos, p. ej. `support_read`. |
| `habilitado` | `boolean` | no | `true` | ninguno | Deshabilitación reversible. |
| `mfa_secret_ref` | `text` | sí | ninguno | ninguno | Referencia a vault, no secreto TOTP. |
| `last_login_at` | `timestamptz` | sí | ninguno | ninguno | Telemetría administrativa. |
| `created_at` | `timestamptz` | no | `now()` | ninguno | Alta del principal. |
### 5.2 Catálogo
#### `bots`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | ID interno estable. |
| `code` | `varchar(80)` | no | ninguno | UQ | Clave de registry, p. ej. `libros_iva`. |
| `display_name` | `varchar(160)` | no | ninguno | ninguno | Nombre de UI. |
| `enabled` | `boolean` | no | `true` | índice parcial | No admite nuevos jobs si es falso. |
| `manifest` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Metadatos de contrato y capacidades. |
| `created_at` | `timestamptz` | no | `now()` | ninguno | Alta de catálogo. |
#### `bot_operations`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | ID de operación. |
| `bot_code` | `varchar(80)` | no | ninguno | FK a `bots(code)`, parte UQ | Bot dueño. |
| `code` | `varchar(80)` | no | ninguno | UQ `(bot_code, code)` | Operación como `consulta` o `carga`. |
| `enabled` | `boolean` | no | `true` | ninguno | Interruptor granular. |
| `unit_cost` | `integer` | no | `1` | CHECK `> 0` | Unidades que reserva la operación. |
| `input_schema_version` | `varchar(32)` | no | ninguno | ninguno | Versión de contrato de entrada. |
| `timeout_seconds` | `integer` | no | `600` | CHECK rango | Límite para el worker. |
| `effect_class` | `varchar(16)` | no | `'EFECTO'` | CHECK `IN ('CONSULTA','EFECTO')` | **Gobierna el reintento.** Ver nota abajo. |
| `manifest` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Datos específicos del catálogo. |

> **`effect_class` no es metadato, es una salvaguarda.** Varios bots de la V2
> ejecutan actos irreversibles ante organismos públicos, no solo consultas:
> `controladores_fiscales` hace clic en `Presentar`
> (`app/bot/controladores_fiscales_bot.py:226-228`), `vep_ccma` genera un VEP
> (`app/bot/vep_ccma_bot.py:629-637`), `rcel` emite factura electrónica. El
> inventario completo está en `plans/04-billing/plan.md` §6.6.
>
> El scheduler **no puede** reintentar a ciegas una operación `EFECTO` cuando
> vence un lease: el acto pudo haberse consumado del otro lado y un reintento
> produciría una presentación o una factura duplicada. Eso es un problema del
> contribuyente, no un error técnico recuperable.
>
> Por eso el default es `'EFECTO'` y no `'CONSULTA'`. Es el valor seguro:
> olvidarse de clasificar una consulta cuesta un reintento que no ocurre;
> olvidarse de clasificar un acto irreversible cuesta una presentación
> duplicada ante ARCA. La columna es `NOT NULL` con `CHECK` para que la base
> impida el estado ambiguo, en vez de depender de que la aplicación recuerde.
### 5.3 Ejecución
#### `jobs`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | `job_id` público y orden natural. |
| `user_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Propietario y frontera de autorización. |
| `bot` | `varchar(80)` | no | ninguno | FK compuesta con operación | Código de bot. |
| `operation` | `varchar(80)` | no | ninguno | FK compuesta con operación | Código de operación. |
| `status` | `varchar(16)` | no | `'PENDIENTE'` | CHECK, índices parciales | Estado de ciclo de vida. |
| `result` | `varchar(16)` | sí | ninguno | CHECK | Resultado separado de estado. |
| `request_payload` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Entrada saneada, sin secreto claro. |
| `idempotency_key` | `varchar(128)` | sí | ninguno | UQ parcial por usuario, bot y operación | De-duplica creación del cliente. |
| `priority` | `smallint` | no | `100` | CHECK `0..1000` | Menor valor es más urgente. |
| `created_at` | `timestamptz` | no | `now()` | índice de cola | Creación autorizada. |
| `assigned_at` | `timestamptz` | sí | ninguno | ninguno | Scheduler eligió worker. |
| `started_at` | `timestamptz` | sí | ninguno | ninguno | Worker confirmó ejecución. |
| `finished_at` | `timestamptz` | sí | ninguno | índice de retención | Final terminal. |
| `worker_id` | `uuid` | sí | ninguno | FK `SET NULL`, índice | Worker asignado. |
| `lease_expires_at` | `timestamptz` | sí | ninguno | índice de recuperación | Lease renovable de assignment. |
| `attempts` | `integer` | no | `0` | CHECK `>= 0` | Intentos ya iniciados. |
| `max_attempts` | `integer` | no | `3` | CHECK `1..20` | Límite por job. |
| `app_version` | `varchar(64)` | sí | ninguno | ninguno | Versión de worker que recibió el sobre. |
| `protocol_version` | `varchar(32)` | no | ninguno | ninguno | Versión central-worker exigida. |
| `cancel_reason` | `text` | sí | ninguno | ninguno | Motivo público saneado. |
| `cancelled_by` | `varchar(16)` | sí | ninguno | CHECK | Actor de cancelación. |
| `error_code` | `varchar(80)` | sí | ninguno | ninguno | Código público de fallo. |
| `error_message` | `text` | sí | ninguno | ninguno | Mensaje saneado y acotado. |
`jobs` reemplaza ambos objetos V2. V2 copiaba una fila a `playwright_jobs_history` y borraba la activa al terminar, como documenta `.research/02-jobs-workers.md` §1.11. Ese traslado duplica DDL, rompe referencias potenciales y dificulta auditoría y reintentos. La fila V3 no cambia de tabla. Solo transiciona de estado y agrega hechos a `job_events`. El resultado se separa en `job_results` por tamaño, retención y variación del payload.
El claim necesita permanecer rápido cuando existan millones de jobs terminales. Se crean índices parciales sobre estados no terminales, incluido uno exacto para selección `PENDIENTE`, y otro para recuperación de leases de `ASIGNADO` o `CORRIENDO`. Los terminales no ocupan esas estructuras. Se mantiene también un índice por usuario y uno por bot para API, soporte y métricas.
#### `job_events`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Orden aproximado del hecho. |
| `job_id` | `uuid` | no | ninguno | FK `CASCADE`, índice | Job afectado. |
| `attempt` | `integer` | no | ninguno | UQ con tipo y clave | Intento que emitió evento. |
| `event_type` | `varchar(32)` | no | ninguno | CHECK | Transición, progreso o diagnóstico. |
| `event_key` | `varchar(128)` | no | ninguno | UQ `(job_id, attempt, event_key)` | Idempotencia de callback. |
| `payload` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Datos saneados del evento. |
| `occurred_at` | `timestamptz` | no | `now()` | índice por job | Momento recibido o declarado validado. |
#### `job_results`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `job_id` | `uuid` | no | ninguno | PK, FK `CASCADE` | Relación 1:0..1 con job. |
| `attempt` | `integer` | no | ninguno | CHECK `> 0` | Intento que produjo resultado. |
| `result` | `varchar(16)` | no | ninguno | CHECK | `OK`, `PARCIAL` o `ERROR`. |
| `payload` | `jsonb` | no | `'{}'::jsonb` | GIN `jsonb_path_ops`, CHECK object | Datos variables por bot. |
| `summary` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Campos públicos pequeños para UI. |
| `received_at` | `timestamptz` | no | `now()` | ninguno | Recepción idempotente del callback. |
#### `job_artifacts`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Identidad del artefacto. |
| `job_id` | `uuid` | no | ninguno | FK `CASCADE`, índice | Job productor. |
| `kind` | `varchar(32)` | no | ninguno | CHECK | `ARCHIVO`, `CAPTURA`, `TRACE`, `OTRO`. |
| `object_key` | `text` | no | ninguno | UQ | Clave estable de object storage. |
| `filename` | `text` | no | ninguno | ninguno | Nombre descargable saneado. |
| `content_type` | `varchar(255)` | sí | ninguno | ninguno | MIME declarado. |
| `size_bytes` | `bigint` | sí | ninguno | CHECK `>= 0` | Tamaño observado. |
| `sha256` | `char(64)` | sí | ninguno | CHECK hexadecimal | Integridad. |
| `created_at` | `timestamptz` | no | `now()` | ninguno | Registro, no URL. |
| `expires_at` | `timestamptz` | sí | ninguno | índice | Política de eliminación del objeto. |
### 5.4 Flota
#### `workers`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Identidad estable emitida por central. |
| `name` | `varchar(120)` | no | ninguno | UQ | Nombre operativo. |
| `status` | `varchar(16)` | no | `'REGISTRANDO'` | CHECK, índice parcial | Disponibilidad administrada. |
| `endpoint` | `text` | no | ninguno | UQ | Dirección privada validada. |
| `protocol_version` | `varchar(32)` | no | ninguno | ninguno | Compatibilidad. |
| `app_version` | `varchar(64)` | no | ninguno | ninguno | Build reportado. |
| `capacity` | `smallint` | no | `5` | CHECK `1..5` | Tope duro V3 por worker. |
| `running_jobs` | `smallint` | no | `0` | CHECK `0..5` | Snapshot de heartbeat. |
| `queued_jobs` | `integer` | no | `0` | CHECK `>= 0` | Cola local reportada. |
| `last_heartbeat_at` | `timestamptz` | sí | ninguno | índice | Detección de worker caído. |
| `registered_at` | `timestamptz` | no | `now()` | ninguno | Registro inicial. |
| `drained_at` | `timestamptz` | sí | ninguno | ninguno | Fin de retiro. |
#### `worker_heartbeats`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Hecho de salud. |
| `worker_id` | `uuid` | no | ninguno | FK `CASCADE`, índice | Worker emisor. |
| `status` | `varchar(16)` | no | ninguno | CHECK | Estado reportado. |
| `running_jobs` | `smallint` | no | ninguno | CHECK `0..5` | Ejecuciones en curso. |
| `queued_jobs` | `integer` | no | ninguno | CHECK `>= 0` | Trabajo aceptado local. |
| `capacity` | `smallint` | no | ninguno | CHECK `1..5` | Capacidad declarada. |
| `metrics` | `jsonb` | no | `'{}'::jsonb` | CHECK object | CPU, memoria y diagnóstico no canónico. |
| `received_at` | `timestamptz` | no | `now()` | índice de retención | Hora de central. |
### 5.5 Facturación
#### `plans`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Identidad de versión comercial. |
| `code` | `varchar(64)` | no | ninguno | UQ | Código inmutable de plan. |
| `name` | `varchar(120)` | no | ninguno | ninguno | Nombre visible. |
| `included_units` | `integer` | no | ninguno | CHECK `>= 0` | Cuota por período. |
| `period_days` | `smallint` | no | `30` | CHECK `1..366` | Longitud contractual. |
| `price_cents` | `integer` | no | ninguno | CHECK `>= 0` | Precio ARS en centavos. |
| `currency` | `char(3)` | no | `'ARS'` | CHECK ISO básico | Moneda. |
| `active` | `boolean` | no | `true` | índice parcial | Puede dejar de venderse sin alterar contratos. |
| `metadata` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Metadatos no contables. |
#### `subscriptions`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Suscripción del usuario. |
| `user_id` | `uuid` | no | ninguno | FK `RESTRICT`, UQ activa parcial | Titular. |
| `plan_id` | `uuid` | no | ninguno | FK `RESTRICT` | Plan contratado vigente. |
| `status` | `varchar(16)` | no | ninguno | CHECK | Ciclo comercial. |
| `provider` | `varchar(32)` | sí | ninguno | CHECK | `MERCADOPAGO` en fase inicial. |
| `provider_subscription_id` | `varchar(160)` | sí | ninguno | UQ parcial | Referencia externa. |
| `current_period_start` | `timestamptz` | sí | ninguno | ninguno | Cache de navegación, no fuente de cuota. |
| `current_period_end` | `timestamptz` | sí | ninguno | ninguno | Cache de navegación. |
| `cancel_at_period_end` | `boolean` | no | `false` | ninguno | Política de renovación. |
| `created_at` | `timestamptz` | no | `now()` | ninguno | Alta contractual. |
| `ended_at` | `timestamptz` | sí | ninguno | índice | Final real. |
#### `subscription_periods`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Período facturable. |
| `subscription_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Suscripción origen. |
| `starts_at` | `timestamptz` | no | ninguno | UQ con suscripción | Inicio inclusivo. |
| `ends_at` | `timestamptz` | no | ninguno | CHECK `> starts_at`, índice | Fin exclusivo. |
| `status` | `varchar(16)` | no | `'ABIERTO'` | CHECK | Apertura, cierre o anulación. |
| `included_units` | `integer` | no | ninguno | CHECK `>= 0` | Snapshot del plan. |
| `credit_unit_price_cents` | `integer` | no | `0` | CHECK `>= 0` | Precio de exceso congelado. |
| `opened_at` | `timestamptz` | no | `now()` | ninguno | Auditoría de apertura. |
| `closed_at` | `timestamptz` | sí | ninguno | ninguno | Cierre de ciclo. |
#### `usage_ledger`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Hecho contable de cuota. |
| `period_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Período consumido. |
| `job_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Job causante. |
| `event_type` | `varchar(16)` | no | ninguno | CHECK, UQ con job | `RESERVA`, `CONFIRMACION`, `REEMBOLSO`. |
| `units` | `integer` | no | ninguno | CHECK por tipo | Positivo en reserva, cero confirmación, negativo reembolso. |
| `reason` | `varchar(160)` | sí | ninguno | ninguno | Motivo de compensación. |
| `created_at` | `timestamptz` | no | `now()` | índice | Hecho inmutable. |
#### `credit_ledger`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Movimiento de créditos. |
| `user_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Saldo por cliente. |
| `payment_id` | `uuid` | sí | ninguno | FK `RESTRICT`, índice | Origen de compra si aplica. |
| `job_id` | `uuid` | sí | ninguno | FK `RESTRICT`, índice | Débito de ejecución si aplica. |
| `entry_type` | `varchar(16)` | no | ninguno | CHECK | `COMPRA`, `DEBITO`, `REEMBOLSO`, `AJUSTE`. |
| `units` | `integer` | no | ninguno | CHECK no cero | Crédito positivo o débito negativo. |
| `idempotency_key` | `varchar(160)` | no | ninguno | UQ | Evita doble acreditación. |
| `reason` | `varchar(160)` | sí | ninguno | ninguno | Explicación de ajuste. |
| `created_at` | `timestamptz` | no | `now()` | índice | Hecho inmutable. |
#### `payments`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Orden de pago interna. |
| `user_id` | `uuid` | no | ninguno | FK `RESTRICT`, índice | Pagador. |
| `subscription_id` | `uuid` | sí | ninguno | FK `RESTRICT` | Renovación asociada si existe. |
| `provider` | `varchar(32)` | no | ninguno | CHECK | Proveedor de cobro. |
| `provider_payment_id` | `varchar(160)` | sí | ninguno | UQ parcial | ID externo. |
| `kind` | `varchar(16)` | no | ninguno | CHECK | Suscripción, crédito o reembolso. |
| `status` | `varchar(16)` | no | `'PENDIENTE'` | CHECK, índice | Estado de pago. |
| `amount_cents` | `integer` | no | ninguno | CHECK `>= 0` | Importe. |
| `currency` | `char(3)` | no | `'ARS'` | CHECK | Moneda. |
| `created_at` | `timestamptz` | no | `now()` | índice | Creación de intento. |
| `approved_at` | `timestamptz` | sí | ninguno | ninguno | Aprobación final. |
#### `payment_events`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Evento recibido. |
| `payment_id` | `uuid` | sí | ninguno | FK `RESTRICT`, índice | Pago resuelto, puede llegar después. |
| `provider` | `varchar(32)` | no | ninguno | CHECK | Emisor del evento. |
| `provider_event_id` | `varchar(160)` | no | ninguno | UQ con proveedor | Idempotencia de webhook. |
| `event_type` | `varchar(80)` | no | ninguno | ninguno | Tipo original saneado. |
| `payload` | `jsonb` | no | ninguno | CHECK object | Webhook minimizado y firmado. |
| `received_at` | `timestamptz` | no | `now()` | índice | Hecho append-only. |
### 5.6 Auditoría
#### `audit_log`
| Columna | Tipo | Nulo | Default | Constraint / índice | Nota |
|---|---|---:|---|---|---|
| `id` | `uuid` | no | aplicación UUIDv7 | PK | Evento auditable. |
| `occurred_at` | `timestamptz` | no | `now()` | índice | Instante de la acción. |
| `actor_type` | `varchar(16)` | no | ninguno | CHECK | `ADMIN`, `USER`, `SERVICE`, `SYSTEM`. |
| `actor_id` | `uuid` | sí | ninguno | índice | Referencia lógica, no FK polimórfica. |
| `action` | `varchar(120)` | no | ninguno | índice | Verbo estable, p. ej. `api_key.revoked`. |
| `target_type` | `varchar(48)` | no | ninguno | ninguno | Tipo de objeto afectado. |
| `target_id` | `uuid` | sí | ninguno | índice | UUID afectado cuando exista. |
| `request_id` | `uuid` | sí | ninguno | índice | Correlación HTTP. |
| `remote_addr` | `inet` | sí | ninguno | ninguno | IP del actor. |
| `user_agent` | `text` | sí | ninguno | ninguno | Cliente informado. |
| `metadata` | `jsonb` | no | `'{}'::jsonb` | CHECK object | Diff saneado, sin credenciales. |
`audit_log` reemplaza y amplía `admin_fiscal_credential_audits` de V2, cuyos campos eran `timestamp`, `admin_username`, `action`, `table_name`, `row_id`, `affected_rows`, `remote_addr` y `user_agent` según `app/models/admin_fiscal_credential_audit.py`. No se usa una FK polimórfica a todos los destinos. La integridad de la acción queda en el evento aunque el destino se retenga.
## 6. Unificación de las 28 tablas `consulta_*_logs`
### 6.1 Diagnóstico de V2
El inventario exhaustivo en `.research/03-database-models.md` declara 28 tablas de consulta. Todas comparten `id integer`, `user_id`, `timestamp`, `job_id`, `status`, `error_message`, `archivos`, `response_data` y el mixin `clave_encriptada`. Casi todas agregan `cuit_representante`, `cuit_representado` y `clave_representante` o `clave`. V2 indexa `job_id`, pero ese campo es string nullable y no FK a jobs.
| Columnas comunes o casi universales V2 | Destino V3 | Motivo |
|---|---|---|
| `id` entero autoincremental | `jobs.id` UUIDv7 y `job_results.job_id` | Elimina IDs correlativos y filas duplicadas. |
| `user_id` | `jobs.user_id` FK UUIDv4 | Ownership y autorización tipada. |
| `timestamp` | `jobs.created_at`, `started_at`, `finished_at`, `job_results.received_at` | Ciclo de vida preciso. |
| `job_id` string sin FK | `jobs.id` y FKs de resultados/eventos/artefactos | Integridad referencial. |
| `status` | `jobs.status` y `job_results.result` | Estado de proceso separado de resultado. |
| `error_message` | `jobs.error_code`, `jobs.error_message` | Error saneado de estado terminal. |
| `response_data` | `job_results.payload` | Estructura cambiante por bot. |
| `archivos` | `job_artifacts` | Artefactos consultables y con retención propia. |
| `cuit_representante`, `cuit_representado` | `jobs.request_payload` saneado | Son entrada de ejecución, no columnas repetidas. |
| `clave`, `clave_representante`, `clave_encriptada` | `jobs.credential_ciphertext` RSA | Solo ciphertext en DB; texto claro efímero en memoria y auditoría de acceso. |
El listener V2 `app/utils/persistence.py` retiraba URLs y redactaba datos al bind, pero también podía convertir strings URL en `NULL`. V3 valida y clasifica los payloads en el borde de la central, sin mutaciones ORM implícitas. `object_key` es la única referencia durable a un objeto, y una URL se firma al leer.
### 6.2 Campos genuinamente específicos en `job_results.payload`
| Bot V2 o tabla | Campos específicos reales que viven en `payload` |
|---|---|
| `consulta_aportes_en_linea_logs` | Sin campos de resultado extra aparte de `response_data` y artefactos. |
| `consulta_ccma_logs` | `response_ccma`. |
| `consulta_certificado_mipyme_logs` | `opciones_encontradas`. |
| `consulta_controladores_fiscales_logs` | `cantidad_archivos`, `resultados`. |
| `consulta_declaracion_en_linea_logs` | `representado_nombre`, `periodo_desde`, `periodo_hasta`. |
| `consulta_facturometro_logs` | `monto_facturado`, `tope_facturacion`, `categoria`. |
| `consulta_hacienda_logs` | `desde`, `hasta`, `nombre_representado`, `cbtes`. |
| `consulta_libros_iva_logs` | `denominacion`, `periodo_desde`, `periodo_hasta`, `periodos_descargados`, `periodos_error`. |
| `consulta_liquidacion_granos_logs` | `desde`, `hasta`, `nombre_representado`, `cbtes`. |
| `consulta_mc_logs` | `desde`, `hasta`, `nombre_representado`, `emitidos`, `recibidos`. |
| `consulta_mis_facilidades_logs` | `denominacion`. |
| `consulta_mis_retenciones_logs` | `denominacion`, `periodo_desde`, `periodo_hasta`. |
| `consulta_mis_retenciones_iva_simple_logs` | `denominacion`, `periodo_desde`, `periodo_hasta`. |
| `consulta_moa_logs` | `despachos`, `despachos_procesados`, `despachos_exitosos`, `despachos_con_error`. |
| `consulta_pago_devoluciones_logs` | `request_carga_minio`, `request_proxy`, `archivo_nombre`, `archivo_path`, `errores_por_seccion`. |
| `consulta_portal_iva_logs` | `periodo`, `request_carga_minio`, `request_proxy`, `descarga_csv_ventas`, `descarga_csv_compras`, `importar_txt_ventas`, `importar_txt_compras`, `importaciones`. |
| `consulta_portal_iva_carga_logs` | `periodo`, `operaciones_ng_o_e`, `prorrateo_global`, `prorrateo_asignacion_directa`, `prorrateo_ambos`, `resultados_ventas`, `resultados_compras`, `resultados_aperturas`, `archivos_recibidos`. |
| `consulta_rcel_logs` | `desde`, `hasta`, `nombre_representado`, `cbtes`. |
| `consulta_retenciones_percepciones_iibb_agip_logs` | `usuario`, `denominacion`, `periodo_desde`, `periodo_hasta`, `request_carga_minio`, `request_proxy`. |
| `consulta_retenciones_percepciones_iibb_arba_logs` | `cuit`, `denominacion`, `periodo`, `request_carga_minio`, `request_proxy`. |
| `consulta_retenciones_percepciones_iibb_misiones_logs` | `denominacion`, `periodo_desde`, `periodo_hasta`, `request_carga_minio`, `request_proxy`. |
| `consulta_sct_logs` | Sin campo específico persistente adicional. |
| `consulta_sct_compensaciones_logs` | `desde`, `hasta`, `request_excel`, `request_csv`, `request_pdf`. |
| `consulta_sifere_logs` | `periodo`, `representado_nombre`. |
| `consulta_siper_logs` | Sin campo específico persistente adicional. |
| `consulta_srt_logs` | `cuits_consultados`. |
| `consulta_vep_logs` | `medio_pago`, `archivo_nombre`, `response_nombre_archivo`, `response_ruta_archivo`. |
| `consulta_vep_ccma_logs` | `medio_pago`, `response_volante_data`, `response_total_seleccionado`, `response_nombre_archivo`, `response_nombre_qr`. |
La lista corresponde a los modelos `app/models/logs_*.py` recogidos en `.research/03-database-models.md` §§2 y 4. Un bot adicional no exige nueva tabla, migración, modelo y registro en cuatro lugares. Exige contrato Pydantic versionado, manifest de operación y validación de su `payload`.
### 6.3 Índice JSONB y consulta concreta
Se crea `GIN (payload jsonb_path_ops)` en `job_results`. Es apropiado para consultas de contención frecuentes y de soporte, con menor tamaño que `jsonb_ops`. No se usa como sustituto de columnas relacionales. Si un campo se convierte en filtro de producto con alta cardinalidad y alta frecuencia, se promueve a columna tipada o a un índice de expresión específico, acompañado de migración.
Una petición de soporte como “dame los `periodos_error`” para Libros IVA se resuelve con índice GIN por contención y una proyección exacta:
```sql
SELECT j.id AS job_id,
       j.user_id,
       j.finished_at,
       r.payload -> 'periodos_error' AS periodos_error
FROM job_results r
JOIN jobs j ON j.id = r.job_id
WHERE j.bot = 'libros_iva'
  AND r.payload @> '{"periodos_error": []}'::jsonb
ORDER BY j.finished_at DESC
LIMIT 100;
```
Si se necesita “clave presente y arreglo no vacío”, `@>` solo prueba presencia compatible. Se agrega el filtro exacto, que normalmente corre sobre el subconjunto seleccionado por GIN:
```sql
SELECT j.id, r.payload -> 'periodos_error' AS periodos_error
FROM job_results r
JOIN jobs j ON j.id = r.job_id
WHERE j.bot = 'libros_iva'
  AND r.payload ? 'periodos_error'
  AND jsonb_array_length(r.payload -> 'periodos_error') > 0
ORDER BY j.finished_at DESC;
```
## 7. Enums y estados
### 7.1 Decisión: `varchar` con `CHECK`, no ENUM nativo
Los estados se modelan como `varchar(n)` con constraints `CHECK` nombradas. PostgreSQL ENUM ofrece tipos compactos y validación, pero agregar un valor requiere DDL de tipo y puede introducir coordinación o lock de migración que no es necesario para estados de jobs y proveedores que crecerán. V2 ya usa estados españoles y V3 incorpora `ASIGNADO`, lease y retry. Un `CHECK` permite la migración expand/contract: primero ampliar valores aceptados, desplegar código, después retirar comportamiento obsoleto.
La central y `packages/mrbot-contracts` son la fuente de constantes. El check es defensa persistente y se prueba contra esas constantes. Ningún endpoint acepta un estado libre.
### 7.2 Conjuntos completos
| Dominio | Valores permitidos | Decisión operacional |
|---|---|---|
| `jobs.status` | `PENDIENTE`, `ASIGNADO`, `CORRIENDO`, `COMPLETO`, `FALLIDO`, `CANCELADO` | Terminales: `COMPLETO`, `FALLIDO`, `CANCELADO`. |
| `jobs.result`, `job_results.result` | `OK`, `PARCIAL`, `ERROR` | Solo resultado de ejecución. `CANCELADO` deja `result` NULL. |
| `jobs.cancelled_by` | `USER`, `ADMIN`, `SYSTEM` | Actor que solicitó o ejecutó cancelación. |
| `job_events.event_type` | `CREADO`, `RESERVADO`, `ASIGNADO`, `INICIADO`, `PROGRESO`, `LEASE_RENOVADO`, `REINTENTO`, `COMPLETADO`, `FALLIDO`, `CANCELADO`, `RESULTADO_RECIBIDO` | Hechos de transición y telemetría. |
| `job_artifacts.kind` | `ARCHIVO`, `CAPTURA`, `TRACE`, `OTRO` | Tipo de object storage. |
| `workers.status`, `worker_heartbeats.status` | `REGISTRANDO`, `SANO`, `DEGRADADO`, `DRENANDO`, `CAIDO` | `DRENANDO` no recibe asignaciones. |
| `subscriptions.status` | `PENDIENTE`, `ACTIVA`, `PAUSADA`, `CANCELADA`, `VENCIDA` | Una UQ parcial limita activa o pendiente por usuario. |
| `subscription_periods.status` | `ABIERTO`, `CERRADO`, `ANULADO` | Solo `ABIERTO` admite reservas. |
| `usage_ledger.event_type` | `RESERVA`, `CONFIRMACION`, `REEMBOLSO` | Hechos inmutables de cuota. |
| `credit_ledger.entry_type` | `COMPRA`, `DEBITO`, `REEMBOLSO`, `AJUSTE` | Movimientos positivos o negativos. |
| `payments.provider`, `payment_events.provider` | `MERCADOPAGO` | Expandible por check en migración. |
| `payments.kind` | `SUSCRIPCION`, `CREDITOS`, `REEMBOLSO` | Naturaleza comercial. |
| `payments.status` | `PENDIENTE`, `APROBADO`, `RECHAZADO`, `CANCELADO`, `REEMBOLSADO` | Estado derivado de eventos verificados. |
| `audit_log.actor_type` | `ADMIN`, `USER`, `SERVICE`, `SYSTEM` | Actor lógico auditado. |
Las transiciones no se garantizan solo con checks de fila. El servicio ejecuta `UPDATE ... WHERE status IN (...)` dentro de transacción y agrega `job_events` en la misma transacción. Pruebas de integración ejercen el diagrama de estados del plan maestro §8.
## 8. DDL de referencia PostgreSQL 17
El siguiente DDL es la referencia de la migración inicial. Los UUID se reciben desde la aplicación, por lo que no hay `DEFAULT` de secuencia ni extensión requerida. En Alembic se descompone en revisiones, pero el resultado final debe ser equivalente.

`api_keys.encrypted_value` guarda, para las claves emitidas desde la migración 0017, un sobre híbrido cifrado con la clave pública RSA de la central: RSA-OAEP-SHA256 protege la clave Fernet aleatoria y Fernet cifra la API key completa. La privada correspondiente (`RSA_PRIVATE_KEY`) solo se usa en la central para emitir y revelar claves autorizadas. El vault actual admite una sola clave RSA configurada y verifica su huella, por lo que no se debe rotar sin antes planificar una migración/re-encriptado de los sobres existentes. Los registros históricos con `encrypted_value IS NULL` no se pueden recuperar desde su HMAC, por lo que deben reemitirse para habilitar la acción de copia.

```sql
CREATE TABLE users (     id uuid NOT NULL,
    email text NOT NULL,     habilitado boolean NOT NULL DEFAULT false,
    CONSTRAINT pk_users PRIMARY KEY (id),     CONSTRAINT ck_users_email_nonblank CHECK (btrim(email) <> '')
); CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email));
CREATE TABLE api_keys (     id uuid NOT NULL,
    user_id uuid NOT NULL,     key_prefix varchar(16) NOT NULL,
    verifier_hmac text NOT NULL,     encrypted_value text,     label varchar(120),
    scopes jsonb NOT NULL DEFAULT '[]'::jsonb,     expires_at timestamptz,
    revoked_at timestamptz,     replaces_key_id uuid,
    last_used_at timestamptz,     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_api_keys PRIMARY KEY (id),     CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE RESTRICT,     CONSTRAINT fk_api_keys_replaces FOREIGN KEY (replaces_key_id)
        REFERENCES api_keys(id) ON DELETE RESTRICT,     CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix),
    CONSTRAINT uq_api_keys_verifier UNIQUE (verifier_hmac),     CONSTRAINT ck_api_keys_scopes_array CHECK (jsonb_typeof(scopes) = 'array'),
    CONSTRAINT ck_api_keys_lifetime CHECK (         expires_at IS NULL OR expires_at > created_at
    ) );
CREATE INDEX ix_api_keys_user_id ON api_keys (user_id); CREATE INDEX ix_api_keys_active ON api_keys (key_prefix)
    WHERE revoked_at IS NULL; CREATE TABLE bots (
    id uuid NOT NULL,     code varchar(80) NOT NULL,
    display_name varchar(160) NOT NULL,     enabled boolean NOT NULL DEFAULT true,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_bots PRIMARY KEY (id),     CONSTRAINT uq_bots_code UNIQUE (code),
    CONSTRAINT ck_bots_code_nonblank CHECK (btrim(code) <> ''),     CONSTRAINT ck_bots_manifest_object CHECK (jsonb_typeof(manifest) = 'object')
); CREATE INDEX ix_bots_enabled ON bots (code) WHERE enabled;
CREATE TABLE bot_operations (     id uuid NOT NULL,
    bot_code varchar(80) NOT NULL,     code varchar(80) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,     unit_cost integer NOT NULL DEFAULT 1,
    input_schema_version varchar(32) NOT NULL,     timeout_seconds integer NOT NULL DEFAULT 600,
    effect_class varchar(16) NOT NULL DEFAULT 'EFECTO',
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,     CONSTRAINT pk_bot_operations PRIMARY KEY (id),
    CONSTRAINT fk_bot_operations_bot FOREIGN KEY (bot_code)         REFERENCES bots(code) ON DELETE RESTRICT,
    CONSTRAINT uq_bot_operations_code UNIQUE (bot_code, code),     CONSTRAINT ck_bot_operations_unit_cost CHECK (unit_cost > 0),
    CONSTRAINT ck_bot_operations_timeout CHECK (timeout_seconds BETWEEN 1 AND 3600),     CONSTRAINT ck_bot_operations_manifest_object CHECK (jsonb_typeof(manifest) = 'object'),
    CONSTRAINT ck_bot_operations_effect_class CHECK (effect_class IN ('CONSULTA', 'EFECTO'))
); CREATE INDEX ix_bot_operations_enabled ON bot_operations (bot_code, code)
    WHERE enabled; CREATE TABLE workers (
    id uuid NOT NULL,     name varchar(120) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'REGISTRANDO',     endpoint text NOT NULL,
    protocol_version varchar(32) NOT NULL,     app_version varchar(64) NOT NULL,
    capacity smallint NOT NULL DEFAULT 5,     running_jobs smallint NOT NULL DEFAULT 0,
    queued_jobs integer NOT NULL DEFAULT 0,     last_heartbeat_at timestamptz,
    registered_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,     drained_at timestamptz,
    CONSTRAINT pk_workers PRIMARY KEY (id),     CONSTRAINT uq_workers_name UNIQUE (name),
    CONSTRAINT uq_workers_endpoint UNIQUE (endpoint),     CONSTRAINT ck_workers_status CHECK (
        status IN ('REGISTRANDO', 'SANO', 'DEGRADADO', 'DRENANDO', 'CAIDO')     ),
    CONSTRAINT ck_workers_capacity CHECK (capacity BETWEEN 1 AND 5),     CONSTRAINT ck_workers_running CHECK (running_jobs BETWEEN 0 AND 5),
    CONSTRAINT ck_workers_queued CHECK (queued_jobs >= 0),     CONSTRAINT ck_workers_running_capacity CHECK (running_jobs <= capacity)
); CREATE INDEX ix_workers_schedulable ON workers (last_heartbeat_at, running_jobs)
    WHERE status = 'SANO'; CREATE TABLE jobs (
    id uuid NOT NULL,     user_id uuid NOT NULL,
    bot varchar(80) NOT NULL,     operation varchar(80) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'PENDIENTE',     result varchar(16),
    request_payload jsonb NOT NULL DEFAULT '{}'::jsonb,     idempotency_key varchar(128),
    priority smallint NOT NULL DEFAULT 100,     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    assigned_at timestamptz,     started_at timestamptz,
    finished_at timestamptz,     worker_id uuid,
    lease_expires_at timestamptz,     attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,     app_version varchar(64),
    protocol_version varchar(32) NOT NULL,     cancel_reason text,
    cancelled_by varchar(16),     error_code varchar(80),
    error_message text,     CONSTRAINT pk_jobs PRIMARY KEY (id),
    CONSTRAINT fk_jobs_user FOREIGN KEY (user_id)         REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_jobs_operation FOREIGN KEY (bot, operation)         REFERENCES bot_operations(bot_code, code) ON DELETE RESTRICT,
    CONSTRAINT fk_jobs_worker FOREIGN KEY (worker_id)         REFERENCES workers(id) ON DELETE SET NULL,
    CONSTRAINT ck_jobs_status CHECK (         status IN ('PENDIENTE', 'ASIGNADO', 'CORRIENDO', 'COMPLETO', 'FALLIDO', 'CANCELADO')
    ),     CONSTRAINT ck_jobs_result CHECK (result IS NULL OR result IN ('OK', 'PARCIAL', 'ERROR')),
    CONSTRAINT ck_jobs_payload_object CHECK (jsonb_typeof(request_payload) = 'object'),     CONSTRAINT ck_jobs_priority CHECK (priority BETWEEN 0 AND 1000),
    CONSTRAINT ck_jobs_attempts CHECK (attempts >= 0 AND max_attempts BETWEEN 1 AND 20),     CONSTRAINT ck_jobs_cancelled_by CHECK (
        cancelled_by IS NULL OR cancelled_by IN ('USER', 'ADMIN', 'SYSTEM')     ),
    CONSTRAINT ck_jobs_terminal_shape CHECK (         (status = 'COMPLETO' AND result IN ('OK', 'PARCIAL', 'ERROR') AND finished_at IS NOT NULL)
        OR (status = 'FALLIDO' AND result = 'ERROR' AND finished_at IS NOT NULL)         OR (status = 'CANCELADO' AND result IS NULL AND finished_at IS NOT NULL)
        OR (status IN ('PENDIENTE', 'ASIGNADO', 'CORRIENDO') AND finished_at IS NULL)     )
); CREATE UNIQUE INDEX uq_jobs_idempotency ON jobs (user_id, bot, operation, idempotency_key)
    WHERE idempotency_key IS NOT NULL; CREATE INDEX ix_jobs_queue_pending ON jobs (priority ASC, created_at ASC, id ASC)
    WHERE status = 'PENDIENTE'; CREATE INDEX ix_jobs_queue_live ON jobs (status, priority ASC, created_at ASC, id ASC)
    WHERE status IN ('PENDIENTE', 'ASIGNADO', 'CORRIENDO'); CREATE INDEX ix_jobs_lease_recovery ON jobs (lease_expires_at ASC, id ASC)
    WHERE status IN ('ASIGNADO', 'CORRIENDO'); CREATE INDEX ix_jobs_user_created ON jobs (user_id, created_at DESC, id DESC);
CREATE INDEX ix_jobs_bot_finished ON jobs (bot, finished_at DESC, id DESC)     WHERE status IN ('COMPLETO', 'FALLIDO', 'CANCELADO');
CREATE TABLE job_events (     id uuid NOT NULL,
    job_id uuid NOT NULL,     attempt integer NOT NULL,
    event_type varchar(32) NOT NULL,     event_key varchar(128) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,     occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_job_events PRIMARY KEY (id),     CONSTRAINT fk_job_events_job FOREIGN KEY (job_id)
        REFERENCES jobs(id) ON DELETE CASCADE,     CONSTRAINT uq_job_events_idempotency UNIQUE (job_id, attempt, event_key),
    CONSTRAINT ck_job_events_attempt CHECK (attempt >= 0),     CONSTRAINT ck_job_events_type CHECK (
        event_type IN ('CREADO', 'RESERVADO', 'ASIGNADO', 'INICIADO', 'PROGRESO',                        'LEASE_RENOVADO', 'REINTENTO', 'COMPLETADO', 'FALLIDO',
                       'CANCELADO', 'RESULTADO_RECIBIDO')     ),
    CONSTRAINT ck_job_events_payload_object CHECK (jsonb_typeof(payload) = 'object') );
CREATE INDEX ix_job_events_job_time ON job_events (job_id, occurred_at ASC, id ASC); CREATE TABLE job_results (
    job_id uuid NOT NULL,     attempt integer NOT NULL,
    result varchar(16) NOT NULL,     payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,     received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_job_results PRIMARY KEY (job_id),     CONSTRAINT fk_job_results_job FOREIGN KEY (job_id)
        REFERENCES jobs(id) ON DELETE CASCADE,     CONSTRAINT ck_job_results_attempt CHECK (attempt > 0),
    CONSTRAINT ck_job_results_result CHECK (result IN ('OK', 'PARCIAL', 'ERROR')),     CONSTRAINT ck_job_results_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_job_results_summary_object CHECK (jsonb_typeof(summary) = 'object') );
CREATE INDEX ix_job_results_payload_gin ON job_results USING gin (payload jsonb_path_ops); CREATE TABLE job_artifacts (
    id uuid NOT NULL,     job_id uuid NOT NULL,
    kind varchar(32) NOT NULL,     object_key text NOT NULL,
    filename text NOT NULL,     content_type varchar(255),
    size_bytes bigint,     sha256 char(64),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,     expires_at timestamptz,
    CONSTRAINT pk_job_artifacts PRIMARY KEY (id),     CONSTRAINT fk_job_artifacts_job FOREIGN KEY (job_id)
        REFERENCES jobs(id) ON DELETE CASCADE,     CONSTRAINT uq_job_artifacts_object_key UNIQUE (object_key),
    CONSTRAINT ck_job_artifacts_kind CHECK (kind IN ('ARCHIVO', 'CAPTURA', 'TRACE', 'OTRO')),     CONSTRAINT ck_job_artifacts_size CHECK (size_bytes IS NULL OR size_bytes >= 0),
    CONSTRAINT ck_job_artifacts_sha256 CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$') );
CREATE INDEX ix_job_artifacts_job_id ON job_artifacts (job_id, created_at ASC); CREATE INDEX ix_job_artifacts_expiry ON job_artifacts (expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE worker_heartbeats (     id uuid NOT NULL,
    worker_id uuid NOT NULL,     status varchar(16) NOT NULL,
    running_jobs smallint NOT NULL,     queued_jobs integer NOT NULL,
    capacity smallint NOT NULL,     metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,     CONSTRAINT pk_worker_heartbeats PRIMARY KEY (id),
    CONSTRAINT fk_worker_heartbeats_worker FOREIGN KEY (worker_id)         REFERENCES workers(id) ON DELETE CASCADE,
    CONSTRAINT ck_worker_heartbeats_status CHECK (         status IN ('REGISTRANDO', 'SANO', 'DEGRADADO', 'DRENANDO', 'CAIDO')
    ),     CONSTRAINT ck_worker_heartbeats_running CHECK (running_jobs BETWEEN 0 AND 5),
    CONSTRAINT ck_worker_heartbeats_queue CHECK (queued_jobs >= 0),     CONSTRAINT ck_worker_heartbeats_capacity CHECK (capacity BETWEEN 1 AND 5),
    CONSTRAINT ck_worker_heartbeats_load CHECK (running_jobs <= capacity),     CONSTRAINT ck_worker_heartbeats_metrics CHECK (jsonb_typeof(metrics) = 'object')
); CREATE INDEX ix_worker_heartbeats_worker_time ON worker_heartbeats (worker_id, received_at DESC);
CREATE TABLE plans (     id uuid NOT NULL,
    code varchar(64) NOT NULL,     name varchar(120) NOT NULL,
    included_units integer NOT NULL,     period_days smallint NOT NULL DEFAULT 30,
    price_cents integer NOT NULL,     currency char(3) NOT NULL DEFAULT 'ARS',
    active boolean NOT NULL DEFAULT true,     metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pk_plans PRIMARY KEY (id),     CONSTRAINT uq_plans_code UNIQUE (code),
    CONSTRAINT ck_plans_units CHECK (included_units >= 0),     CONSTRAINT ck_plans_period CHECK (period_days BETWEEN 1 AND 366),
    CONSTRAINT ck_plans_price CHECK (price_cents >= 0),     CONSTRAINT ck_plans_currency CHECK (currency ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_plans_metadata_object CHECK (jsonb_typeof(metadata) = 'object') );
CREATE INDEX ix_plans_active ON plans (code) WHERE active; CREATE TABLE subscriptions (
    id uuid NOT NULL,     user_id uuid NOT NULL,
    plan_id uuid NOT NULL,     status varchar(16) NOT NULL,
    provider varchar(32),     provider_subscription_id varchar(160),
    current_period_start timestamptz,     current_period_end timestamptz,
    cancel_at_period_end boolean NOT NULL DEFAULT false,     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at timestamptz,     CONSTRAINT pk_subscriptions PRIMARY KEY (id),
    CONSTRAINT fk_subscriptions_user FOREIGN KEY (user_id)         REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_subscriptions_plan FOREIGN KEY (plan_id)         REFERENCES plans(id) ON DELETE RESTRICT,
    CONSTRAINT ck_subscriptions_status CHECK (         status IN ('PENDIENTE', 'ACTIVA', 'PAUSADA', 'CANCELADA', 'VENCIDA')
    ),     CONSTRAINT ck_subscriptions_provider CHECK (
        provider IS NULL OR provider IN ('MERCADOPAGO')     ),
    CONSTRAINT ck_subscriptions_period CHECK (         current_period_end IS NULL OR current_period_start IS NULL
        OR current_period_end > current_period_start     )
); CREATE UNIQUE INDEX uq_subscriptions_active_user ON subscriptions (user_id)
    WHERE status IN ('PENDIENTE', 'ACTIVA', 'PAUSADA'); CREATE UNIQUE INDEX uq_subscriptions_provider_external ON subscriptions (provider, provider_subscription_id)
    WHERE provider_subscription_id IS NOT NULL; CREATE TABLE subscription_periods (
    id uuid NOT NULL,     subscription_id uuid NOT NULL,
    starts_at timestamptz NOT NULL,     ends_at timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'ABIERTO',     included_units integer NOT NULL,
    credit_unit_price_cents integer NOT NULL DEFAULT 0,     opened_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at timestamptz,     CONSTRAINT pk_subscription_periods PRIMARY KEY (id),
    CONSTRAINT fk_subscription_periods_subscription FOREIGN KEY (subscription_id)         REFERENCES subscriptions(id) ON DELETE RESTRICT,
    CONSTRAINT uq_subscription_periods_start UNIQUE (subscription_id, starts_at),     CONSTRAINT ck_subscription_periods_range CHECK (ends_at > starts_at),
    CONSTRAINT ck_subscription_periods_status CHECK (status IN ('ABIERTO', 'CERRADO', 'ANULADO')),     CONSTRAINT ck_subscription_periods_units CHECK (included_units >= 0),
    CONSTRAINT ck_subscription_periods_price CHECK (credit_unit_price_cents >= 0) );
CREATE INDEX ix_subscription_periods_open ON subscription_periods (starts_at, ends_at)     WHERE status = 'ABIERTO';
CREATE TABLE payments (     id uuid NOT NULL,
    user_id uuid NOT NULL,     subscription_id uuid,
    provider varchar(32) NOT NULL,     provider_payment_id varchar(160),
    kind varchar(16) NOT NULL,     status varchar(16) NOT NULL DEFAULT 'PENDIENTE',
    amount_cents integer NOT NULL,     currency char(3) NOT NULL DEFAULT 'ARS',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,     approved_at timestamptz,
    CONSTRAINT pk_payments PRIMARY KEY (id),     CONSTRAINT fk_payments_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE RESTRICT,     CONSTRAINT fk_payments_subscription FOREIGN KEY (subscription_id)
        REFERENCES subscriptions(id) ON DELETE RESTRICT,     CONSTRAINT ck_payments_provider CHECK (provider IN ('MERCADOPAGO')),
    CONSTRAINT ck_payments_kind CHECK (kind IN ('SUSCRIPCION', 'CREDITOS', 'REEMBOLSO')),     CONSTRAINT ck_payments_status CHECK (
        status IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'CANCELADO', 'REEMBOLSADO')     ),
    CONSTRAINT ck_payments_amount CHECK (amount_cents >= 0),     CONSTRAINT ck_payments_currency CHECK (currency ~ '^[A-Z]{3}$')
); CREATE UNIQUE INDEX uq_payments_provider_payment ON payments (provider, provider_payment_id)
    WHERE provider_payment_id IS NOT NULL; CREATE INDEX ix_payments_user_created ON payments (user_id, created_at DESC);
CREATE TABLE usage_ledger (     id uuid NOT NULL,
    period_id uuid NOT NULL,     job_id uuid NOT NULL,
    event_type varchar(16) NOT NULL,     units integer NOT NULL,
    reason varchar(160),     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_usage_ledger PRIMARY KEY (id),     CONSTRAINT fk_usage_ledger_period FOREIGN KEY (period_id)
        REFERENCES subscription_periods(id) ON DELETE RESTRICT,     CONSTRAINT fk_usage_ledger_job FOREIGN KEY (job_id)
        REFERENCES jobs(id) ON DELETE RESTRICT,     CONSTRAINT uq_usage_ledger_job_event UNIQUE (job_id, event_type),
    CONSTRAINT ck_usage_ledger_type CHECK (event_type IN ('RESERVA', 'CONFIRMACION', 'REEMBOLSO')),     CONSTRAINT ck_usage_ledger_units CHECK (
        (event_type = 'RESERVA' AND units > 0)         OR (event_type = 'CONFIRMACION' AND units = 0)
        OR (event_type = 'REEMBOLSO' AND units < 0)     )
); CREATE INDEX ix_usage_ledger_period_created ON usage_ledger (period_id, created_at);
CREATE TABLE credit_ledger (     id uuid NOT NULL,
    user_id uuid NOT NULL,     payment_id uuid,
    job_id uuid,     entry_type varchar(16) NOT NULL,
    units integer NOT NULL,     idempotency_key varchar(160) NOT NULL,
    reason varchar(160),     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_credit_ledger PRIMARY KEY (id),     CONSTRAINT fk_credit_ledger_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE RESTRICT,     CONSTRAINT fk_credit_ledger_payment FOREIGN KEY (payment_id)
        REFERENCES payments(id) ON DELETE RESTRICT,     CONSTRAINT fk_credit_ledger_job FOREIGN KEY (job_id)
        REFERENCES jobs(id) ON DELETE RESTRICT,     CONSTRAINT uq_credit_ledger_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_credit_ledger_type CHECK (entry_type IN ('COMPRA', 'DEBITO', 'REEMBOLSO', 'AJUSTE')),     CONSTRAINT ck_credit_ledger_units CHECK (units <> 0)
); CREATE INDEX ix_credit_ledger_user_created ON credit_ledger (user_id, created_at DESC);
CREATE TABLE payment_events (     id uuid NOT NULL,
    payment_id uuid,     provider varchar(32) NOT NULL,
    provider_event_id varchar(160) NOT NULL,     event_type varchar(80) NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_payment_events_payment FOREIGN KEY (payment_id)         REFERENCES payments(id) ON DELETE RESTRICT,
    CONSTRAINT uq_payment_events_provider_event UNIQUE (provider, provider_event_id),     CONSTRAINT ck_payment_events_provider CHECK (provider IN ('MERCADOPAGO')),
    CONSTRAINT ck_payment_events_payload_object CHECK (jsonb_typeof(payload) = 'object') );
CREATE INDEX ix_payment_events_received ON payment_events (received_at DESC); CREATE TABLE admin_users (
    id uuid NOT NULL,     email text NOT NULL,
    password_hash text NOT NULL,     roles jsonb NOT NULL DEFAULT '[]'::jsonb,
    habilitado boolean NOT NULL DEFAULT true,     mfa_secret_ref text,
    last_login_at timestamptz,     created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_admin_users PRIMARY KEY (id),     CONSTRAINT ck_admin_users_roles_array CHECK (jsonb_typeof(roles) = 'array')
); CREATE UNIQUE INDEX uq_admin_users_email_lower ON admin_users (lower(email));
CREATE TABLE audit_log (     id uuid NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,     actor_type varchar(16) NOT NULL,
    actor_id uuid,     action varchar(120) NOT NULL,
    target_type varchar(48) NOT NULL,     target_id uuid,
    request_id uuid,     remote_addr inet,
    user_agent text,     metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pk_audit_log PRIMARY KEY (id),     CONSTRAINT ck_audit_log_actor_type CHECK (
        actor_type IN ('ADMIN', 'USER', 'SERVICE', 'SYSTEM')     ),
    CONSTRAINT ck_audit_log_metadata_object CHECK (jsonb_typeof(metadata) = 'object') );
CREATE INDEX ix_audit_log_occurred_at ON audit_log (occurred_at DESC); CREATE INDEX ix_audit_log_actor ON audit_log (actor_type, actor_id, occurred_at DESC);
CREATE INDEX ix_audit_log_target ON audit_log (target_type, target_id, occurred_at DESC);
```
El DDL aplica `CASCADE` solo a dependientes técnicamente inseparables de un job o worker, como eventos, resultados, artefactos y heartbeats. Las identidades, contratos y hechos financieros se protegen con `RESTRICT`, ya que se retienen y se anonimizan o deshabilitan antes de una eliminación excepcional.
## 9. Migraciones con Alembic
### 9.1 Configuración V3
Alembic se inicializa en `services/central-api/alembic/` con una única base PostgreSQL y una única cabeza. `env.py` toma `DATABASE_URL` de configuración tipada, usa engine síncrono o async coherente con el stack elegido, y crea conexiones de migración con `NullPool`. El metadata es el único metadata declarativo de la central.
La convención global de nombres reduce diffs espurios y permite downgrade predecible:
```python
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```
No se habilita `render_as_batch=True`. V2 lo habilitaba globalmente en `alembic/env.py` como solución a limitaciones de `ALTER TABLE` en SQLite, según `.research/03-database-models.md` §1. PostgreSQL 17 soporta `ALTER TABLE` nativo. Batch recrearía tablas innecesariamente, aumenta locks, puede reconstruir índices y es incompatible con la expectativa de cambios online.
Autogenerate es ayuda de revisión, no un generador ciego. Cada revisión se inspecciona por tipos PostgreSQL, índices parciales, condiciones de lock y SQL de datos. CI ejecuta `alembic upgrade head`, confirma exactamente una cabeza con `alembic heads`, corre el escaneo de secuencias de §3.4 y verifica que `alembic check` no detecte drift.
### 9.2 Regla expand/contract
1. **Expandir:** agregar tabla, columna nullable, índice concurrente o nuevo valor permitido sin cambiar lectores existentes.
2. **Desplegar escritura dual o lectura compatible:** aplicación entiende ambas formas y rellena el dato nuevo.
3. **Backfill en lotes:** transacciones pequeñas, observables y reanudables. Nunca un `UPDATE` masivo bloqueante en una migración de despliegue.
4. **Cambiar lectura:** consumir la forma nueva después de medir completitud.
5. **Contraer:** remover la columna, check o camino anterior solo en una revisión posterior y después de ventana de compatibilidad.
Para crear índices grandes en producción se utiliza `op.create_index(..., postgresql_concurrently=True)` dentro de una migración con `transaction_per_migration` o bloque autocommit, ya que PostgreSQL prohíbe `CREATE INDEX CONCURRENTLY` dentro de transacción. Las FKs grandes pueden añadirse `NOT VALID`, validar después y convertirse en `NOT NULL` tras backfill.
### 9.3 Serie inicial ordenada
1. `0001_public_extensions_and_identity`: crea esquemas `public` y `legacy` si corresponde, `users`, `api_keys`, `admin_users` e índices de email. No instala extensión UUID obligatoria.
2. `0002_catalog`: crea `bots` y `bot_operations`, carga el manifiesto inicial de bots y operaciones desde contrato versionado.
3. `0003_workers`: crea `workers`, `worker_heartbeats` y sus índices de salud.
4. `0004_jobs_core`: crea `jobs`, sus checks, FKs e índices parciales de cola e idempotencia.
5. `0005_job_outputs`: crea `job_events`, `job_results`, GIN de payload y `job_artifacts`.
6. `0006_billing_catalog`: crea `plans`, `subscriptions`, `subscription_periods` y carga planes de configuración versionada.
7. `0007_billing_ledgers`: crea `payments`, `usage_ledger`, `credit_ledger`, `payment_events` e índices de conciliación.
8. `0008_audit`: crea `audit_log`, índices por actor y destino, y permisos de append-only.
9. `0009_operational_views_and_roles`: crea vistas de salud, usuario de runtime de mínimo privilegio y revoca DML directo a roles no centrales.
10. `0010_legacy_import`: crea objetos `legacy`, importa copia V2 de solo lectura y construye `legacy.user_id_map`.
## 10. Esquema `legacy` e importación V2
La fase inicial no transforma la historia de ejecución. Se crea el schema PostgreSQL `legacy` y se importan las 28 `consulta_*_logs`, `users`, `playwright_jobs_active`, `playwright_jobs_history` y `admin_fiscal_credential_audits` tal como estaban. Sus PK integer, nombres de columnas, valores de estado y `job_id` string se conservan para que informes históricos no cambien significado.
El import se ejecuta sobre una copia consistente de `data/sql_app.db`, que el inventario estima en aproximadamente 133 MB. No se apunta V3 al archivo SQLite en producción. El proceso exporta, valida conteos y checksums por tabla y carga a PostgreSQL con tipos compatibles. Las credenciales históricas no se exponen por la central y las tablas quedan vedadas a cualquier endpoint nuevo.
```sql
CREATE SCHEMA IF NOT EXISTS legacy;
CREATE TABLE legacy.user_id_map (
    legacy_id integer NOT NULL,
    user_id uuid NOT NULL,
    mapped_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_legacy_user_id_map PRIMARY KEY (legacy_id),
    CONSTRAINT uq_legacy_user_id_map_user UNIQUE (user_id),
    CONSTRAINT fk_legacy_user_id_map_user FOREIGN KEY (user_id)
        REFERENCES public.users(id) ON DELETE RESTRICT
);
```
`legacy.user_id_map` preserva el enlace entre `legacy.users.id` integer y `public.users.id` UUIDv4. La migración de usuarios genera V4 nuevos, normaliza emails cuando sea posible, importa el verificador HMAC existente a una fila `api_keys` revocable y guarda la relación. Conflictos de email, usuarios incompletos o claves ya revocadas se registran en un reporte de importación y requieren resolución antes de habilitar la cuenta V3.
El rol de runtime de `central-api` recibe `USAGE` sobre `legacy` y `SELECT` solo para endpoints explícitos de historia. No recibe `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `CREATE` ni `ALTER`. El rol de worker no tiene ningún privilegio de DB. Se añade una prueba que intenta escribir `legacy.consulta_mc_logs` con el rol runtime y espera error de permisos.
No se crea FK desde datos legado hacia `public`: los datos V2 contenían `job_id` sin FK y podrían violar integridad. La única relación nueva garantizada es el mapa de usuario. Los datos nuevos nunca se escriben en `legacy`.
## 11. Consultas operativas
### 11.1 Claim de cola con `FOR UPDATE SKIP LOCKED`
El scheduler central invoca esta transacción por slot disponible de un worker sano. Antes debe elegir el worker de manera determinista fuera o dentro de la misma transacción, verificar heartbeat y comprobar `running_jobs < capacity`. La actualización de `workers.running_jobs` y la asignación deben ser atómicas.
```sql
BEGIN;
WITH next_job AS (
    SELECT id
    FROM jobs
    WHERE status = 'PENDIENTE'
    ORDER BY priority ASC, created_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
), reserved_worker AS (
    UPDATE workers
    SET running_jobs = running_jobs + 1
    WHERE id = :worker_id
      AND status = 'SANO'
      AND last_heartbeat_at >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'
      AND running_jobs < capacity
      -- Obligatorio. Sin este EXISTS el contador se incrementa aunque
      -- next_job no haya devuelto nada, porque en PostgreSQL las CTE que
      -- modifican datos se ejecutan siempre, no se cortocircuitan por el
      -- JOIN posterior. Ver la nota de §11.1.1.
      AND EXISTS (SELECT 1 FROM next_job)
    RETURNING id
)
UPDATE jobs j
SET status = 'ASIGNADO',
    worker_id = rw.id,
    assigned_at = CURRENT_TIMESTAMP,
    lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '90 seconds',
    attempts = j.attempts + 1,
    app_version = :worker_app_version,
    protocol_version = :protocol_version
FROM next_job nj
JOIN reserved_worker rw ON true
WHERE j.id = nj.id
RETURNING j.*;
COMMIT;
```
Si el `UPDATE` no retorna fila, se hace `ROLLBACK` y no se llama al worker. Al recibir aceptación HTTP, se conserva `ASIGNADO`; al callback de inicio pasa a `CORRIENDO`. Si el worker rechaza o el lease vence, una transacción condicionada baja el job a `PENDIENTE`, libera `running_jobs` una vez y agrega evento `REINTENTO`. Nunca se hace un `SELECT` sin lock seguido de `UPDATE` optimista como V2.

#### 11.1.1 Trampa de las CTE que modifican datos

Esta advertencia surge de ejecutar la consulta contra PostgreSQL 17, no de una
revisión teórica. En PostgreSQL **todas las CTE que modifican datos se ejecutan
exactamente una vez, independientemente de que sus filas sean consumidas por el
resto de la consulta.** No existe cortocircuito por el `JOIN` posterior.

Sin la cláusula `EXISTS (SELECT 1 FROM next_job)`, la CTE `reserved_worker`
incrementa `running_jobs` incluso cuando la cola está vacía y no se asigna
ningún job. Comprobación ejecutada sobre una cola vacía:

| Momento | `running_jobs` | Jobs asignados |
|---|---:|---:|
| Antes del claim | 0 | 0 |
| Después del claim sin `EXISTS` | **1** | 0 |
| Después del claim con `EXISTS` | 0 | 0 |

La consecuencia operativa es grave y silenciosa: cada ciclo del scheduler que
encuentra la cola vacía consumiría un slot permanente del worker. Tras cinco
ciclos en vacío el worker aparecería `SATURADO` sin ejecutar nada, y la flota se
degradaría hasta quedar inerte sin ningún error visible.

Reglas que se derivan y son obligatorias para cualquier implementación:

1. Toda CTE que modifica datos y depende de otra debe condicionar su `WHERE` con
   un `EXISTS` sobre la CTE de la que depende.
2. El scheduler debe tratar `RETURNING` vacío como `ROLLBACK`, nunca como éxito
   parcial.
3. La prueba de aceptación 13 debe incluir un caso de **cola vacía** y afirmar
   que `running_jobs` no cambia. Un test que solo ejercita la cola con trabajo
   no detecta este defecto.
4. La alternativa a la CTE única es hacer dos sentencias dentro de la misma
   transacción: primero el `SELECT ... FOR UPDATE SKIP LOCKED`, y solo si
   devuelve fila, el `UPDATE` del contador y el del job. Es más verbosa pero su
   semántica es obvia. Si el equipo prefiere legibilidad sobre atomicidad en una
   sola sentencia, esta opción es aceptable porque la transacción ya garantiza
   el aislamiento.
La reserva de cuota o crédito se hace antes de que el job sea elegible, en la transacción de admisión. Por esa razón el claim no vuelve a consultar ni mutar contadores de usuario.
### 11.2 Vista de salud de flota
```sql
CREATE OR REPLACE VIEW worker_fleet_health AS
SELECT w.id,
       w.name,
       w.status,
       w.protocol_version,
       w.app_version,
       w.capacity,
       w.running_jobs,
       w.queued_jobs,
       w.last_heartbeat_at,
       CURRENT_TIMESTAMP - w.last_heartbeat_at AS heartbeat_age,
       CASE
           WHEN w.status = 'DRENANDO' THEN 'DRENANDO'
           WHEN w.last_heartbeat_at IS NULL THEN 'CAIDO'
           WHEN w.last_heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '30 seconds'
               THEN 'CAIDO'
           WHEN w.running_jobs >= w.capacity THEN 'SATURADO'
           WHEN w.status = 'SANO' THEN 'SANO'
           ELSE 'DEGRADADO'
       END AS effective_health
FROM workers w;
SELECT *
FROM worker_fleet_health
ORDER BY effective_health, heartbeat_age DESC NULLS FIRST, name;
```
El panel muestra `effective_health`, pero nunca cambia `workers.status` solo por leer la vista. Un proceso de reconciliación puede marcar `CAIDO` y registrar auditoría si supera el umbral.
### 11.3 Consumo de cuota de un período
```sql
SELECT sp.id AS period_id,
       sp.included_units,
       COALESCE(SUM(ul.units), 0) AS reserved_or_consumed_units,
       sp.included_units - COALESCE(SUM(ul.units), 0) AS remaining_units
FROM subscription_periods sp
LEFT JOIN usage_ledger ul ON ul.period_id = sp.id
WHERE sp.id = :period_id
GROUP BY sp.id, sp.included_units;
```
Para reserva segura, la aplicación toma `FOR UPDATE` sobre el período y evalúa la suma bajo la misma transacción serializable o con lock de advisory por `user_id`. Inserta `RESERVA` solo si el resultado más `unit_cost` no excede `included_units`. Así no existe check e incremento separados como V2.
### 11.4 Saldo de créditos
```sql
SELECT u.id AS user_id,
       COALESCE(SUM(cl.units), 0) AS credit_balance
FROM users u
LEFT JOIN credit_ledger cl ON cl.user_id = u.id
WHERE u.id = :user_id
GROUP BY u.id;
```
Un débito usa `INSERT ... SELECT` con lock de advisory transaccional por usuario o una fila de agregación bloqueable diseñada en el plan de billing. Nunca se edita un saldo calculado para corregirlo. La auditoría explica cualquier `AJUSTE`.
### 11.5 Estadísticas de jobs por bot
```sql
SELECT j.bot,
       j.operation,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE j.status = 'PENDIENTE') AS pendientes,
       COUNT(*) FILTER (WHERE j.status = 'ASIGNADO') AS asignados,
       COUNT(*) FILTER (WHERE j.status = 'CORRIENDO') AS corriendo,
       COUNT(*) FILTER (WHERE j.status = 'COMPLETO' AND j.result = 'OK') AS ok,
       COUNT(*) FILTER (WHERE j.status = 'COMPLETO' AND j.result = 'PARCIAL') AS parcial,
       COUNT(*) FILTER (WHERE j.status = 'FALLIDO') AS fallidos,
       COUNT(*) FILTER (WHERE j.status = 'CANCELADO') AS cancelados,
       percentile_cont(0.95) WITHIN GROUP (
           ORDER BY EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
       ) FILTER (WHERE j.finished_at IS NOT NULL AND j.started_at IS NOT NULL) AS p95_seconds
FROM jobs j
WHERE j.created_at >= :from_at
  AND j.created_at < :to_at
GROUP BY j.bot, j.operation
ORDER BY j.bot, j.operation;
```
## 12. Rendimiento, capacidad y retención
### 12.1 Estrategia de índices
| Carga | Índice | Razón |
|---|---|---|
| Claim FIFO | `ix_jobs_queue_pending` parcial por `PENDIENTE` | Árbol pequeño aunque los terminales crezcan. |
| Recuperación | `ix_jobs_lease_recovery` parcial por `ASIGNADO`,`CORRIENDO` | Detecta lease vencido sin escanear historia. |
| API de cliente | `ix_jobs_user_created` | Lista de jobs por usuario y paginación estable. |
| Métricas por bot | `ix_jobs_bot_finished` | Finalizados por bot y rango temporal. |
| Eventos | `ix_job_events_job_time` | Timeline de job en orden. |
| Payload variable | `ix_job_results_payload_gin` | Contención JSONB de soporte. |
| Artefactos | `ix_job_artifacts_expiry` | Poda por vencimiento. |
| Workers | `ix_workers_schedulable` | Selección de sanos con heartbeat reciente. |
| Ledger | índices `(period_id, created_at)` y `(user_id, created_at)` | Balance y cuota por período. |
| Webhooks | UQ proveedor + evento | Idempotencia antes de efecto económico. |
| Auditoría | actor, target, `occurred_at` | Investigación forense acotada. |
No se indexa todo JSONB ni cada columna de texto. Cada índice se justifica por consulta, se mide con `EXPLAIN (ANALYZE, BUFFERS)` y se elimina si no demuestra uso. Los índices parciales se revisan cuando se agregue un estado.
### 12.2 Volumen inicial y particionamiento posterior
V2 `data/sql_app.db` pesa aproximadamente 133 MB según `.research/03-database-models.md` §7. Es una pista de volumen local, no una previsión de producción. V3 debe registrar métricas de filas, bytes, bloat, duración de claim, tamaño de índices y latencia de p95 desde el primer despliegue.
`jobs` y `job_events` no se particionan el día uno. El volumen inicial y los índices parciales hacen preferible un esquema simple para verificar queries y FK. Particionar prematuramente aumenta complejidad de claves únicas globales, migraciones y mantenimiento.
Se habilita particionamiento mensual cuando se cumpla una condición medida: por ejemplo más de 5 millones de jobs terminales, más de 20 millones de eventos o poda mensual que exceda la ventana operativa. La clave será `created_at` para `jobs` y `occurred_at` para `job_events`. Antes de particionar se valida la estrategia de PK y unicidad de idempotencia contra las limitaciones de índices únicos en tablas particionadas. `job_results` sigue el ciclo de retención del job y puede archivarse con el job terminado.
### 12.3 Pool de conexiones
Solo las réplicas de `central-api` abren pool. No se multiplica por workers porque estos no conectan. Para `N` réplicas, `pool_size=P`, `max_overflow=O`, el máximo teórico es `N × (P + O)` conexiones de aplicación, más conexiones de Alembic, métricas y administración. Esa suma debe quedar por debajo de `max_connections` dejando 20% de reserva.
Configuración inicial conservadora: `P=10`, `O=5`. Con 4 réplicas, máximo 60 conexiones, más 10 reservadas para administración, bajo un PostgreSQL de 100 conexiones. Si se ejecutan 12 réplicas, el máximo sería 180 y es inaceptable sin PgBouncer. En ese caso se usa PgBouncer en modo transaction, se baja `P` a 5 y se conserva menos de 80 conexiones de backend.
Cada request usa sesión corta. Scheduler, webhook y callback comparten el mismo pool pero no sostienen una transacción durante HTTP al worker. El claim confirma DB antes de hacer `POST` de asignación. `pool_pre_ping=true`, timeout explícito y métricas de wait time son obligatorios.
### 12.4 Retención y poda
Un proceso de mantenimiento propiedad de `central-api` ejecuta lotes idempotentes y auditados:
1. Expira y elimina en object storage los objetos `job_artifacts.expires_at < now()`, luego elimina la fila o marca `object_deleted_at` en una expansión futura.
2. Retiene jobs terminales y `job_results` por la política contractual configurada. Primero exporta a archivo cifrado verificable si corresponde, luego borra en lotes por PK y fecha.
3. Poda `worker_heartbeats` de alta frecuencia después de 30 días, conservando `workers.last_heartbeat_at` como snapshot actual.
4. Retiene `job_events` el período operacional acordado, por ejemplo 90 días, excepto eventos vinculados a disputa o auditoría.
5. Conserva `usage_ledger`, `credit_ledger`, `payments`, `payment_events` y `audit_log` conforme a retención legal y financiera. Nunca los poda por la política corta de logs técnicos.
6. Ejecuta `VACUUM (ANALYZE)` mediante autovacuum ajustado. No programa `VACUUM FULL` en producción normal.
Cada lote limita filas, emite `audit_log` con conteo y rango temporal, y tiene dry-run. V2 permitía borrar logs dinámicamente desde el admin y limpiaba history por `finished_at`, según `.research/03-database-models.md` §7. V3 lo convierte en política explícita y no permite borrado ad hoc sin autorización y evidencia.
## 13. Criterios de aceptación

### 13.0 Verificación ya ejecutada contra PostgreSQL 17 real

El DDL de §8 se ejecutó sin modificaciones en un contenedor `postgres:17-alpine`
durante la redacción de este plan. Los resultados no son una expectativa, son
una observación. Reproducible con `infra/postgres/verify-ddl.sh`.

| Criterio | Comprobación ejecutada | Resultado observado |
|---|---|---|
| 1 | `information_schema.tables` en `public` | 19 tablas, coincidencia exacta con la lista canónica. Cero faltantes, cero extras |
| 2, 3 | Tipo de toda PK vía `pg_index` | 18 de 18 PK son `uuid`. `job_results` usa `job_id` como PK |
| 4 | `is_identity='YES' OR column_default LIKE 'nextval%'`, y `pg_class.relkind='S'` | 0 filas y 0 secuencias. **I-1 e I-2 confirmadas empíricamente** |
| 5 | Columnas de `users` | Exactamente `id`, `email`, `habilitado`. Ninguna de las tres columnas prohibidas. **I-3 confirmada** |
| 9, 10 | Dos `INSERT` con igual `(user_id, bot, operation, idempotency_key)` | El segundo falla por `uq_jobs_idempotency`. **S-2 confirmada** |
| 12 | `ck_jobs_terminal_shape` | `COMPLETO` sin `finished_at` es rechazado. Con `result` y `finished_at` es aceptado |
| 13 seguridad | 5 ráfagas de 12 claims concurrentes, verificando la invariante después de cada ráfaga | Nunca hubo doble asignación, sobrecapacidad, job `ASIGNADO` sin worker ni `running_jobs` desalineado del conteo real |
| 13 vivacidad | Cola de 10 jobs, 2 workers de capacidad 5 | Se drena por completo, 5 y 5. Ver la nota sobre `SKIP LOCKED` abajo |
| 13 cola vacía | Claim contra cola vacía | `running_jobs` no se mueve. Este caso **detectó un defecto real** en la consulta del plan. Ver §11.1.1 |
| 13 | `capacity=6` y `running_jobs=6` | Rechazados por `ck_workers_capacity` y `ck_workers_running`. El tope de 5 de W-2 está respaldado por la base, no solo por el worker |
| 14 | `EXPLAIN (ANALYZE, BUFFERS)` del claim con 50.000 jobs terminales y 50 pendientes | `Index Only Scan using ix_jobs_queue_pending`, 3 buffers, 0,120 ms. El índice parcial se comporta como se diseñó |
| 15 | `job_results.payload @> '{"periodos_error":["202403"]}'` sobre un job de `libros_iva` | Devuelve la fila. La unificación de las 28 tablas es consultable sin tabla por bot |
| 20 | Vista `worker_fleet_health` | Deriva `SATURADO` con `running_jobs = capacity`, `SANO` al liberar capacidad, y `CAIDO` a los 41 s sin latido con umbral de 30 s |

Nota de diseño confirmada por la prueba: `SATURADO` y `RETIRADO` **no** están en
el `CHECK` de `workers.status` a propósito. `SATURADO` se deriva de
`running_jobs >= capacity` en la vista, y no se almacena, porque almacenarlo
duplicaría un hecho ya representado por los contadores y podría desincronizarse.
Los estados persistidos son `REGISTRANDO`, `SANO`, `DEGRADADO`, `DRENANDO` y
`CAIDO`. El diagrama del plan maestro §8 describe la máquina de estados
observable, que es la unión de los persistidos y los derivados.

Criterios pendientes de verificación por requerir código: 6, 7, 8, 11, 16, 17,
18, 19, 21, 22, 23, 24, 25, 26, 27.

#### Dos correcciones que surgieron de ejecutar, no de revisar

**1. Fuga de capacidad en el claim.** La primera versión de la consulta de
§11.1 incrementaba `running_jobs` aunque no hubiera job para asignar. El
defecto es invisible en una revisión de código y en cualquier prueba que use
una cola con trabajo. Se detectó solo al probar el caso de cola vacía.
Corregido con `EXISTS (SELECT 1 FROM next_job)` y documentado en §11.1.1.
Es un test de regresión real: al quitar el `EXISTS`, la verificación falla en
tres puntos distintos.

**2. `SKIP LOCKED` no garantiza drenar en una sola pasada.** La primera
aserción de la prueba concurrente exigía que 12 claims simultáneos asignaran
los 10 jobs. Falla de forma intermitente, y la aserción estaba mal, no el
diseño. `SKIP LOCKED` es deliberadamente no bloqueante: si dos schedulers
apuntan a la misma fila, uno la saltea y termina con las manos vacías en vez de
esperar. Eso es exactamente lo que evita el efecto convoy, y es la razón por la
que se eligió. La consecuencia de diseño es explícita:

> El scheduler **debe** ciclar. Una pasada que no asigna nada no significa que
> la cola esté vacía, puede significar contención. El bucle del scheduler no
> debe interpretar `RETURNING` vacío como señal para dormir el intervalo
> completo si la cola tiene profundidad mayor a cero.

Por eso la verificación separa dos propiedades, y toda prueba futura del
scheduler debe hacer lo mismo:

| Propiedad | Qué afirma | Cuándo se verifica |
|---|---|---|
| Seguridad | Nunca dos workers reciben el mismo job, nunca se excede la capacidad, el contador nunca se desalinea | Después de **cada** ráfaga. Debe valer siempre |
| Vivacidad | La cola termina drenada | Al final, tras varias pasadas. Requiere reintento |

Confundir ambas produce pruebas intermitentes que el equipo acaba desactivando.

### 13.1 Lista completa

1. PostgreSQL 17 contiene exactamente las **19** tablas V3 bajo `public`: `users`, `api_keys`, `admin_users`, `bots`, `bot_operations`, `jobs`, `job_events`, `job_results`, `job_artifacts`, `workers`, `worker_heartbeats`, `plans`, `subscriptions`, `subscription_periods`, `usage_ledger`, `credit_ledger`, `payments`, `payment_events`, `audit_log`. Esta es la lista canónica del plan maestro agrupada por dominio.
2. `users.id` es `uuid` UUIDv4 generado por aplicación y no tiene secuencia, identidad ni default UUIDv7.
3. Cada PK nueva fuera de `users` es `uuid` UUIDv7 generado por aplicación. La consulta de §3.4 devuelve cero filas para `public`.
4. No existe `serial`, `bigserial`, `GENERATED AS IDENTITY` ni secuencia usada por una columna de `public`.
5. `users` no contiene `fecha_ultimo_reset`, `created_at`, `updated_at`, `api_key`, `maximas_consultas_mensuales` ni `consultas_realizadas`.
6. `api_keys` persiste solo HMAC verificable, permite dos claves activas y una rotación revocable para un usuario, y nunca devuelve el verificador por API.
7. La autenticación de API key no escribe cuotas ni crea períodos. Un job admitido reserva cuota o crédito en una transacción atómica.
8. Un usuario sin período abierto es rechazado por entitlement o consume crédito según política, y no queda permanentemente `PENDIENTE` sin reserva.
9. La unicidad de idempotencia de job es `(user_id, bot, operation, idempotency_key)` cuando la clave no es NULL.
10. Una creación repetida concurrente con la misma clave retorna el mismo `jobs.id` y solo genera una reserva de consumo.
11. `jobs` reemplaza `playwright_jobs_active` y `playwright_jobs_history`; un job finalizado conserva su misma fila y su timeline en `job_events`.
12. `jobs` contiene `worker_id`, `assigned_at`, `lease_expires_at`, `attempts`, `max_attempts`, `app_version` y `protocol_version` con checks y FKs descritos.
13. El claim concurrente con dos schedulers usa `FOR UPDATE SKIP LOCKED`, no entrega el mismo job dos veces y no excede capacidad cinco de un worker.
14. Los índices parciales de cola existen y `EXPLAIN` de claim muestra uso de `ix_jobs_queue_pending` para una cola con historia terminal significativa.
15. `job_results.payload` tiene GIN `jsonb_path_ops` y una consulta sobre `periodos_error` retorna resultados de `libros_iva` sin consultar tablas `consulta_*` nuevas.
16. No se persisten credenciales fiscales, claves API en claro, URL prefirmadas ni secretos de worker en `jobs`, resultados, eventos, artefactos o auditoría.
17. `usage_ledger`, `credit_ledger`, `payment_events`, `job_events` y `audit_log` se prueban append-only mediante permisos o trigger de protección y hechos de compensación.
18. La suma de `usage_ledger` para una reserva seguida de reembolso devuelve el consumo neto correcto sin actualizar filas anteriores.
19. El saldo de créditos se deriva de `credit_ledger` y los eventos MercadoPago duplicados no generan doble pago ni doble crédito.
20. `workers` y `worker_heartbeats` exponen en la vista de salud `SANO`, `SATURADO`, `DRENANDO` y `CAIDO` con umbral de 30 segundos.
21. `alembic upgrade head` desde una base vacía completa con una sola cabeza y `alembic check` no reporta drift.
22. `render_as_batch` está ausente o es explícitamente `False` en Alembic V3.
23. La importación crea `legacy`, conserva los enteros V2 sin reescritura y llena `legacy.user_id_map` uno a uno para usuarios migrados.
24. El rol runtime de central tiene solo `SELECT` sobre `legacy` y un intento de DML en ese esquema falla.
25. No hay ningún DSN ni usuario de PostgreSQL en la configuración, imagen o secretos de `bot-worker`; una prueba de integración demuestra que el worker solo puede reportar por HTTP.
26. La política de retención ejecuta dry-run, borra en lotes, no toca ledgers financieros y deja entrada de auditoría.
27. El pool de conexiones se calcula como `N × (pool_size + max_overflow)`, se mantiene bajo el presupuesto PostgreSQL y se alarma antes de saturar.
