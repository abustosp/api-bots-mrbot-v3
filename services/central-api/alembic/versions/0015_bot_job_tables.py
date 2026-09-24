"""0015: detalle físico por bot, mantenido como proyección transaccional.

El catálogo es una copia congelada de los 32 nombres ``BotManifest.nombre``
del registry V3. No se importa código de la aplicación desde Alembic.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# Nombres canónicos tomados del catálogo declarativo de plugins V3. Mantener
# este inventario estático evita importar la aplicación durante una migración.
BOT_CODES = (
    "apoc",
    "aportes_en_linea",
    "arba",
    "carga_portal_iva",
    "ccma",
    "certificado_mipyme",
    "compensaciones",
    "comprobantes",
    "consulta_cuit",
    "consulta_pagos_vep",
    "controladores_fiscales",
    "declaracion_en_linea",
    "facturometro",
    "hacienda",
    "libros_portal_iva",
    "liquidacion_granos",
    "mis_comprobantes",
    "mis_facilidades",
    "mis_retenciones",
    "mis_retenciones_iva_simple",
    "moa",
    "pago_devoluciones",
    "portal_iva",
    "rcel",
    "retper_iibb_agip",
    "retper_iibb_misiones",
    "sct",
    "sifere",
    "siper",
    "srt",
    "vep_archivo",
    "vep_ccma",
)

_URL_VALUE_PATTERN = r"([A-Za-z][A-Za-z0-9+.-]*://|www[.])"
_AUTHORIZATION_VALUE_PATTERN = (
    r"((^|[^A-Za-z0-9_])authorization\s*[:=]\s*)"
    r"(bearer\s+|basic\s+)?[^,\s;]+"
)
_INLINE_SECRET_PATTERN = (
    r"((^|[^A-Za-z0-9_])(password|passwd|passphrase|secret|token|credential|"
    r"credencial|clave|api[_ -]?key|access[_ -]?key|client[_ -]?secret|"
    r"private[_ -]?key)(\s*[:=]\s*|\s+))[^,\s;]+"
)
_BEARER_VALUE_PATTERN = (
    r"((^|[^A-Za-z0-9_])(bearer|basic)\s+)[A-Za-z0-9._~+/-]+=*"
)
_SECRET_REPLACEMENT = r"\1[REDACTED_SECRET]"
_SECRET_KEY_PATTERN = (
    r"(password|passwd|passphrase|^pass$|secret|token|credential|credencial|"
    r"clave|authorization|cookie|ciphertext|sealed|privatekey|apikey|clientsecret)"
)
_URL_KEY_PATTERN = r"(^url|url$|^uri|uri$)"


_DETAIL_COLUMNS = (
    "job_id",
    "user_id",
    "worker_id",
    "bot",
    "operation",
    "status",
    "result",
    "priority",
    "attempts",
    "max_attempts",
    "app_version",
    "protocol_version",
    "cancel_reason",
    "cancelled_by",
    "error_code",
    "request_payload",
    "response_payload",
    "response_summary",
    "response_received_at",
    "credential_metadata",
    "created_at",
    "assigned_at",
    "started_at",
    "finished_at",
    "updated_at",
    "artifact_names",
    "artifact_metadata",
)


def _table_name(bot_code: str) -> str:
    return f"bot_jobs_{bot_code}"


def _create_detail_tables() -> None:
    for bot_code in BOT_CODES:
        table = _table_name(bot_code)
        op.create_table(
            table,
            sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("bot", sa.String(80), nullable=False),
            sa.Column("operation", sa.String(80), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("result", sa.String(16), nullable=True),
            sa.Column("priority", sa.SmallInteger(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("app_version", sa.String(64), nullable=True),
            sa.Column("protocol_version", sa.Integer(), nullable=False),
            sa.Column("cancel_reason", sa.Text(), nullable=True),
            sa.Column("cancelled_by", sa.String(16), nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column(
                "request_payload",
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column("response_payload", postgresql.JSONB(), nullable=True),
            sa.Column("response_summary", postgresql.JSONB(), nullable=True),
            sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "credential_metadata",
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
            sa.Column(
                "artifact_names",
                postgresql.ARRAY(sa.Text()),
                server_default=sa.text("'{}'::text[]"),
                nullable=False,
            ),
            sa.Column(
                "artifact_metadata",
                postgresql.JSONB(),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.CheckConstraint(f"bot = '{bot_code}'", name=f"ck_{table}_bot"),
            sa.CheckConstraint(
                "status IN ('PENDIENTE','ASIGNADO','CORRIENDO','COMPLETO','FALLIDO','CANCELADO')",
                name=f"ck_{table}_status",
            ),
            sa.CheckConstraint(
                "result IS NULL OR result IN ('OK','PARCIAL','ERROR')",
                name=f"ck_{table}_result",
            ),
            sa.CheckConstraint(
                "priority BETWEEN 0 AND 1000 AND attempts >= 0 AND max_attempts BETWEEN 1 AND 20",
                name=f"ck_{table}_attempts",
            ),
            sa.CheckConstraint(
                "cancelled_by IS NULL OR cancelled_by IN ('USER','ADMIN','SYSTEM')",
                name=f"ck_{table}_cancelled_by",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(request_payload) = 'object'",
                name=f"ck_{table}_request_object",
            ),
            sa.CheckConstraint(
                "response_payload IS NULL OR jsonb_typeof(response_payload) = 'object'",
                name=f"ck_{table}_response_object",
            ),
            sa.CheckConstraint(
                "response_summary IS NULL OR jsonb_typeof(response_summary) = 'object'",
                name=f"ck_{table}_summary_object",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(credential_metadata) = 'object'",
                name=f"ck_{table}_cred_meta_obj",
            ),
            sa.CheckConstraint(
                "jsonb_typeof(artifact_metadata) = 'array'",
                name=f"ck_{table}_artifact_metadata_array",
            ),
            sa.ForeignKeyConstraint(
                ["job_id"], ["jobs.id"], ondelete="CASCADE", name=f"fk_{table}_job"
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], ondelete="RESTRICT", name=f"fk_{table}_user"
            ),
            sa.ForeignKeyConstraint(
                ["worker_id"], ["workers.id"], ondelete="SET NULL", name=f"fk_{table}_worker"
            ),
            sa.ForeignKeyConstraint(
                ["bot", "operation"],
                ["bot_operations.bot_code", "bot_operations.code"],
                ondelete="RESTRICT",
                name=f"fk_{table}_operation",
            ),
            sa.PrimaryKeyConstraint("job_id", name=f"pk_{table}"),
        )
        op.create_index(
            f"ix_{table}_user_created", table, ["user_id", "created_at", "job_id"]
        )
        op.create_index(
            f"ix_{table}_status_created", table, ["status", "created_at", "job_id"]
        )
        op.create_index(
            f"ix_{table}_operation_created", table, ["operation", "created_at", "job_id"]
        )
        op.create_index(
            f"ix_{table}_request_gin",
            table,
            ["request_payload"],
            postgresql_using="gin",
            postgresql_ops={"request_payload": "jsonb_path_ops"},
        )
        op.create_index(
            f"ix_{table}_response_gin",
            table,
            ["response_payload"],
            postgresql_using="gin",
            postgresql_ops={"response_payload": "jsonb_path_ops"},
            postgresql_where=sa.text("response_payload IS NOT NULL"),
        )


def _sanitizer_sql() -> str:
    return rf"""
CREATE FUNCTION sanitize_bot_job_json(p_value jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
AS $function$
DECLARE
    item record;
    normalized_key text;
    cleaned jsonb;
BEGIN
    IF p_value IS NULL THEN
        RETURN NULL;
    END IF;

    CASE jsonb_typeof(p_value)
        WHEN 'object' THEN
            cleaned := '{{}}'::jsonb;
            FOR item IN SELECT key, value FROM jsonb_each(p_value) LOOP
                normalized_key := regexp_replace(lower(item.key), '[^a-z0-9]', '', 'g');
                IF normalized_key ~ '{_SECRET_KEY_PATTERN}'
                   OR normalized_key ~ '{_URL_KEY_PATTERN}' THEN
                    CONTINUE;
                END IF;
                cleaned := cleaned || jsonb_build_object(
                    item.key, sanitize_bot_job_json(item.value)
                );
            END LOOP;
            RETURN cleaned;
        WHEN 'array' THEN
            SELECT COALESCE(
                jsonb_agg(sanitize_bot_job_json(element)), '[]'::jsonb
            ) INTO cleaned
            FROM jsonb_array_elements(p_value) AS item_row(element);
            RETURN cleaned;
        WHEN 'string' THEN
            cleaned := p_value #>> '{{}}';
            IF cleaned ~* '{_URL_VALUE_PATTERN}' THEN
                RETURN '"[REDACTED_URL]"'::jsonb;
            END IF;
            cleaned := regexp_replace(
                cleaned,
                '{_AUTHORIZATION_VALUE_PATTERN}',
                '{_SECRET_REPLACEMENT}',
                'gi'
            );
            cleaned := regexp_replace(
                cleaned,
                '{_INLINE_SECRET_PATTERN}',
                '{_SECRET_REPLACEMENT}',
                'gi'
            );
            cleaned := regexp_replace(
                cleaned,
                '{_BEARER_VALUE_PATTERN}',
                '{_SECRET_REPLACEMENT}',
                'gi'
            );
            RETURN to_jsonb(cleaned);
        ELSE
            RETURN p_value;
    END CASE;
END;
$function$;
"""


def _sync_functions_sql() -> tuple[str, ...]:
    bot_case = "\n".join(
        f"        WHEN '{bot_code}' THEN '{_table_name(bot_code)}'"
        for bot_code in BOT_CODES
    )
    old_bot_case = "\n".join(
        f"                WHEN '{bot_code}' THEN '{_table_name(bot_code)}'"
        for bot_code in BOT_CODES
    )
    script = f"""
CREATE FUNCTION sync_bot_job_detail(p_job_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    job_row jobs%ROWTYPE;
    target_table text;
BEGIN
    -- NO KEY UPDATE serializes concurrent result/artifact callbacks and is
    -- compatible with the KEY SHARE lock taken by their FK checks. A waiter
    -- refreshes from a fresh READ COMMITTED snapshot after the prior commit.
    SELECT * INTO job_row FROM jobs WHERE id = p_job_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    target_table := CASE job_row.bot
{bot_case}
        ELSE NULL
    END;
    IF target_table IS NULL THEN
        RAISE EXCEPTION 'no physical job detail table registered for canonical bot %', job_row.bot;
    END IF;

    EXECUTE format($sync$
        INSERT INTO %I (
            job_id, user_id, worker_id, bot, operation, status, result,
            priority, attempts, max_attempts, app_version, protocol_version,
            cancel_reason, cancelled_by, error_code, request_payload,
            response_payload, response_summary, response_received_at,
            credential_metadata, created_at, assigned_at, started_at,
            finished_at, updated_at, artifact_names, artifact_metadata
        )
        SELECT j.id, j.user_id, j.worker_id, j.bot, j.operation, j.status,
               j.result, j.priority, j.attempts, j.max_attempts, j.app_version,
               j.protocol_version, j.cancel_reason, j.cancelled_by, j.error_code,
               sanitize_bot_job_json(j.request_payload),
               CASE WHEN r.job_id IS NULL THEN NULL
                    ELSE sanitize_bot_job_json(r.payload) END,
               CASE WHEN r.job_id IS NULL THEN NULL
                    ELSE sanitize_bot_job_json(r.summary) END,
               r.received_at,
               sanitize_bot_job_json(j.credential_metadata),
               j.created_at, j.assigned_at, j.started_at, j.finished_at,
               CURRENT_TIMESTAMP,
               COALESCE((
                   SELECT array_agg(
                       CASE WHEN a.filename ~* '{_URL_VALUE_PATTERN}'
                            THEN '[REDACTED_URL]' ELSE a.filename END
                       ORDER BY a.created_at, a.id
                   )
                   FROM job_artifacts AS a WHERE a.job_id = j.id
               ), ARRAY[]::text[]),
               COALESCE((
                   SELECT jsonb_agg(
                       jsonb_build_object(
                           'kind', a.kind,
                           'filename', CASE
                               WHEN a.filename ~* '{_URL_VALUE_PATTERN}'
                               THEN '[REDACTED_URL]' ELSE a.filename END,
                           'content_type', a.content_type,
                           'size_bytes', a.size_bytes,
                           'sha256', a.sha256,
                           'created_at', a.created_at,
                           'expires_at', a.expires_at
                       ) ORDER BY a.created_at, a.id
                   )
                   FROM job_artifacts AS a WHERE a.job_id = j.id
               ), '[]'::jsonb)
        FROM jobs AS j
        LEFT JOIN job_results AS r ON r.job_id = j.id
        WHERE j.id = $1
        ON CONFLICT (job_id) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            worker_id = EXCLUDED.worker_id,
            bot = EXCLUDED.bot,
            operation = EXCLUDED.operation,
            status = EXCLUDED.status,
            result = EXCLUDED.result,
            priority = EXCLUDED.priority,
            attempts = EXCLUDED.attempts,
            max_attempts = EXCLUDED.max_attempts,
            app_version = EXCLUDED.app_version,
            protocol_version = EXCLUDED.protocol_version,
            cancel_reason = EXCLUDED.cancel_reason,
            cancelled_by = EXCLUDED.cancelled_by,
            error_code = EXCLUDED.error_code,
            request_payload = EXCLUDED.request_payload,
            response_payload = EXCLUDED.response_payload,
            response_summary = EXCLUDED.response_summary,
            response_received_at = EXCLUDED.response_received_at,
            credential_metadata = EXCLUDED.credential_metadata,
            created_at = EXCLUDED.created_at,
            assigned_at = EXCLUDED.assigned_at,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            updated_at = CURRENT_TIMESTAMP,
            artifact_names = EXCLUDED.artifact_names,
            artifact_metadata = EXCLUDED.artifact_metadata
    $sync$, target_table) USING p_job_id;
END;
$function$;

CREATE FUNCTION sync_bot_job_detail_from_job()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    old_table text;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.bot IS DISTINCT FROM NEW.bot THEN
        old_table := CASE OLD.bot
{old_bot_case}
            ELSE NULL
        END;
        IF old_table IS NOT NULL THEN
            EXECUTE format('DELETE FROM %I WHERE job_id = $1', old_table)
            USING OLD.id;
        END IF;
    END IF;
    PERFORM sync_bot_job_detail(NEW.id);
    RETURN NEW;
END;
$function$;

CREATE FUNCTION sync_bot_job_detail_from_child()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM sync_bot_job_detail(OLD.job_id);
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.job_id IS DISTINCT FROM NEW.job_id THEN
        PERFORM sync_bot_job_detail(OLD.job_id);
    END IF;
    PERFORM sync_bot_job_detail(NEW.job_id);
    RETURN NEW;
END;
$function$;
"""
    return tuple(
        "CREATE FUNCTION " + statement.strip()
        for statement in script.split("CREATE FUNCTION ")[1:]
    )


def _trigger_sql() -> tuple[str, ...]:
    return (
        "CREATE TRIGGER trg_jobs_bot_detail_sync "
        "AFTER INSERT OR UPDATE ON jobs FOR EACH ROW "
        "EXECUTE FUNCTION sync_bot_job_detail_from_job()",
        "CREATE TRIGGER trg_job_results_bot_detail_sync "
        "AFTER INSERT OR UPDATE OR DELETE ON job_results FOR EACH ROW "
        "EXECUTE FUNCTION sync_bot_job_detail_from_child()",
        "CREATE TRIGGER trg_job_artifacts_bot_detail_sync "
        "AFTER INSERT OR UPDATE OR DELETE ON job_artifacts FOR EACH ROW "
        "EXECUTE FUNCTION sync_bot_job_detail_from_child()",
    )


def _backfill_guard_sql() -> str:
    literal_codes = ", ".join("'" + code + "'" for code in BOT_CODES)
    return f"""
DO $guard$
DECLARE
    unsupported_bot text;
BEGIN
    SELECT bot INTO unsupported_bot
    FROM jobs
    WHERE bot NOT IN ({literal_codes})
    LIMIT 1;
    IF unsupported_bot IS NOT NULL THEN
        RAISE EXCEPTION 'jobs contains bot code without a physical detail table: %', unsupported_bot;
    END IF;
END;
$guard$;
"""


def upgrade() -> None:
    # Bloquea escritores durante la instalación y el backfill. PostgreSQL
    # conserva estos locks hasta el commit/rollback de la revisión Alembic.
    op.execute("LOCK TABLE jobs, job_results, job_artifacts IN SHARE ROW EXCLUSIVE MODE")
    op.execute(_backfill_guard_sql())
    _create_detail_tables()
    op.execute(_sanitizer_sql())
    for function_sql in _sync_functions_sql():
        op.execute(function_sql)
    for trigger_sql in _trigger_sql():
        op.execute(trigger_sql)
    op.execute(
        "DO $backfill$ DECLARE row_job record; BEGIN "
        "FOR row_job IN SELECT id FROM jobs LOOP "
        "PERFORM sync_bot_job_detail(row_job.id); "
        "END LOOP; END; $backfill$;"
    )


def downgrade() -> None:
    for trigger_sql in (
        "DROP TRIGGER IF EXISTS trg_job_artifacts_bot_detail_sync ON job_artifacts",
        "DROP TRIGGER IF EXISTS trg_job_results_bot_detail_sync ON job_results",
        "DROP TRIGGER IF EXISTS trg_jobs_bot_detail_sync ON jobs",
    ):
        op.execute(trigger_sql)
    for function_signature in (
        "sync_bot_job_detail_from_child()",
        "sync_bot_job_detail_from_job()",
        "sync_bot_job_detail(uuid)",
        "sanitize_bot_job_json(jsonb)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function_signature}")
    for bot_code in reversed(BOT_CODES):
        op.drop_table(_table_name(bot_code))
