"""Pruebas estructurales de la revisión Alembic 0015, sin importar la app."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[3]
REVISION_PATH = ROOT / "services/central-api/alembic/versions/0015_bot_job_tables.py"
REGISTRY_PATH = ROOT / "services/bot-worker/src/bot_worker/bots"


def _load_revision():
    spec = importlib.util.spec_from_file_location("bot_jobs_revision_0015", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


class _MigrationRecorder:
    def __init__(self) -> None:
        self.tables: dict[str, tuple[object, ...]] = {}
        self.indexes: list[tuple[str, str, tuple[str, ...], dict[str, object]]] = []
        self.statements: list[str] = []
        self.dropped_tables: list[str] = []

    def create_table(self, name, *elements, **kwargs) -> None:
        self.tables[name] = elements

    def create_index(self, name, table, columns, **kwargs) -> None:
        self.indexes.append((name, table, tuple(columns), kwargs))

    def execute(self, statement) -> None:
        self.statements.append(str(statement))

    def drop_table(self, name) -> None:
        self.dropped_tables.append(name)


def _schema() -> tuple[object, _MigrationRecorder]:
    revision = _load_revision()
    recorder = _MigrationRecorder()
    revision.op = recorder
    revision.upgrade()
    return revision, recorder


def test_frozen_codes_match_all_v3_plugin_manifest_names() -> None:
    revision = _load_revision()
    pattern = re.compile(
        r"\bmanifest\s*=\s*BotManifest\s*\(\s*nombre\s*=\s*\"([a-z0-9_]+)\"",
        re.MULTILINE,
    )
    registry_codes = {
        match.group(1)
        for plugin_file in REGISTRY_PATH.glob("*/plugin.py")
        for match in pattern.finditer(plugin_file.read_text(encoding="utf-8"))
    }

    assert len(revision.BOT_CODES) == 32
    assert len(set(revision.BOT_CODES)) == 32
    assert set(revision.BOT_CODES) == registry_codes
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", code) for code in revision.BOT_CODES)


def test_upgrade_defines_32_consistent_tables_with_references_and_indexes() -> None:
    revision, recorder = _schema()

    assert len(recorder.tables) == 32
    assert set(recorder.tables) == {f"bot_jobs_{code}" for code in revision.BOT_CODES}
    expected_columns = set(revision._DETAIL_COLUMNS)

    for table_name, elements in recorder.tables.items():
        columns = {element.name for element in elements if isinstance(element, sa.Column)}
        constraints = [
            element for element in elements
            if isinstance(element, (sa.CheckConstraint, sa.ForeignKeyConstraint, sa.PrimaryKeyConstraint))
        ]
        assert columns == expected_columns, table_name
        assert all(len(element.name) <= 63 for element in constraints)
        assert any(
            isinstance(element, sa.PrimaryKeyConstraint)
            and tuple(element._pending_colargs) == ("job_id",)
            for element in constraints
        )
        foreign_keys = {
            (
                tuple(element.column_keys),
                tuple(foreign_key.target_fullname for foreign_key in element.elements),
                element.ondelete,
            )
            for element in constraints
            if isinstance(element, sa.ForeignKeyConstraint)
        }
        assert (("job_id",), ("jobs.id",), "CASCADE") in foreign_keys
        assert (("user_id",), ("users.id",), "RESTRICT") in foreign_keys
        assert (("worker_id",), ("workers.id",), "SET NULL") in foreign_keys
        assert (
            ("bot", "operation"),
            ("bot_operations.bot_code", "bot_operations.code"),
            "RESTRICT",
        ) in foreign_keys

    indexes_by_table: dict[str, set[str]] = {}
    for name, table, _columns, _kwargs in recorder.indexes:
        assert len(name) <= 63
        indexes_by_table.setdefault(table, set()).add(name)
    for table in recorder.tables:
        assert indexes_by_table[table] == {
            f"ix_{table}_user_created",
            f"ix_{table}_status_created",
            f"ix_{table}_operation_created",
            f"ix_{table}_request_gin",
            f"ix_{table}_response_gin",
        }


def test_upgrade_installs_atomic_triggers_and_recursively_sanitizes_json() -> None:
    _revision, recorder = _schema()
    sql = "\n".join(recorder.statements)
    assert all(not sa.text(statement)._bindparams for statement in recorder.statements)

    assert "LOCK TABLE jobs, job_results, job_artifacts IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "CREATE TRIGGER trg_jobs_bot_detail_sync" in sql
    assert "CREATE TRIGGER trg_job_results_bot_detail_sync" in sql
    assert "CREATE TRIGGER trg_job_artifacts_bot_detail_sync" in sql
    assert "CREATE FUNCTION sanitize_bot_job_json(p_value jsonb)" in sql
    assert "sanitize_bot_job_json(r.payload)" in sql
    assert "sanitize_bot_job_json(r.summary)" in sql
    assert "sanitize_bot_job_json(j.request_payload)" in sql
    assert "sanitize_bot_job_json(j.credential_metadata)" in sql
    assert "jsonb_array_elements(p_value)" in sql
    assert "SELECT * INTO job_row FROM jobs WHERE id = p_job_id FOR NO KEY UPDATE" in sql
    assert "[REDACTED_SECRET]" in sql
    assert "(bearer|basic)" in sql
    assert "REDACTED_URL" in sql
    assert "FOR row_job IN SELECT id FROM jobs LOOP" in sql
    assert "object_key" not in sql
    assert "credential_ciphertext" not in sql
    assert "error_message" not in sql


def test_inline_secret_value_patterns_redact_authorization_and_api_keys() -> None:
    revision = _load_revision()
    sql = revision._sanitizer_sql()
    patterns = (
        revision._AUTHORIZATION_VALUE_PATTERN,
        revision._INLINE_SECRET_PATTERN,
        revision._BEARER_VALUE_PATTERN,
    )
    assert all(pattern in sql for pattern in patterns)

    def sanitize(value: str) -> str:
        if re.search(revision._URL_VALUE_PATTERN, value, re.IGNORECASE):
            return "[REDACTED_URL]"
        for pattern in patterns:
            value = re.sub(
                pattern,
                revision._SECRET_REPLACEMENT,
                value,
                flags=re.IGNORECASE,
            )
        return value

    for value in (
        "Authorization: Bearer dummy_bearer_value",
        "api_key=dummy_api_key_value",
        "token=dummy_token_value",
    ):
        sanitized = sanitize(value)
        assert "dummy_" not in sanitized
        assert "[REDACTED_SECRET]" in sanitized


def test_sensitive_key_filter_keeps_unrelated_security_fields() -> None:
    revision = _load_revision()
    sql = revision._sanitizer_sql()
    assert revision._SECRET_KEY_PATTERN in sql
    assert revision._URL_KEY_PATTERN in sql

    def filtered(key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        return bool(
            re.search(revision._SECRET_KEY_PATTERN, normalized)
            or re.search(revision._URL_KEY_PATTERN, normalized)
        )

    assert filtered("security") is False
    assert filtered("access_token") is True
    assert filtered("api_key") is True
    assert filtered("callback_url") is True
    assert filtered("upload_uri") is True


def test_downgrade_drops_only_revision_objects_in_reverse_table_order() -> None:
    revision = _load_revision()
    recorder = _MigrationRecorder()
    revision.op = recorder
    revision.downgrade()

    assert recorder.dropped_tables == [
        f"bot_jobs_{code}" for code in reversed(revision.BOT_CODES)
    ]
    statements = "\n".join(recorder.statements)
    assert "DROP TRIGGER IF EXISTS trg_jobs_bot_detail_sync ON jobs" in statements
    assert "DROP FUNCTION IF EXISTS sync_bot_job_detail(uuid)" in statements
    assert "DROP FUNCTION IF EXISTS sanitize_bot_job_json(jsonb)" in statements
    assert " CASCADE" not in statements
