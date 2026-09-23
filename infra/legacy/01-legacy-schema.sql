-- 01-legacy-schema.sql: esquema `legacy` de solo lectura para la V3.
--
-- Diseno: plans/07-migracion/plan.md secciones 3.2, 3.7 y 3.8.
-- Los datos V2 se importan TAL CUAL, sin transformar semantica: las 28
-- tablas `consulta_*_logs`, `users`, los jobs Playwright y la auditoria de
-- credenciales conservan su forma como historial auditable. No alimentan
-- tablas activas V3.
--
-- Contenido de este archivo:
--   * CREATE SCHEMA legacy (+ comentario).
--   * Tabla `legacy.user_id_map` (plan 07 seccion 3.4).
--   * Tabla `legacy.migration_audit` (manifiesto de cada importacion).
--   * El DDL por tabla V2 NO esta copiado a mano aqui: lo genera
--     `generate_legacy_ddl.py` como `02-legacy-tables.generated.sql`
--     determinista a partir del SQLite de origen. Ver README.md.
--
-- Orden de aplicacion:
--   1. psql -f 01-legacy-schema.sql
--   2. psql -f 02-legacy-tables.generated.sql
--   3. python3 import_legacy.py ... (carga)
--   4. python3 etl_users.py ... (solo users -> public + user_id_map)
--   5. psql -f 03-legacy-readonly.sql (sellado: la app deja de escribir)
--
-- Regla W-1: este esquema lo crea y carga el operador con el DSN de la
-- central. El worker no recibe DATABASE_URL nunca.

CREATE SCHEMA IF NOT EXISTS legacy;

COMMENT ON SCHEMA legacy IS
  'Historial V2 congelado en solo lectura (plan 07). Sin inserts/updates/deletes de aplicacion.';

-- Mapa de identidad historica: cada `users.id` V2 tiene una fila aqui
-- con el UUIDv4 V3 generado por el ETL. Permite enlazar registros
-- historicos sin alterar las FK enteras de las 28 tablas legacy.
CREATE TABLE IF NOT EXISTS legacy.user_id_map (
    legacy_user_id integer PRIMARY KEY,
    v3_user_id uuid NOT NULL UNIQUE,
    migrated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_mail text,
    source_row_checksum text NOT NULL
);

COMMENT ON TABLE legacy.user_id_map IS
  'Trazabilidad users V2 (INTEGER) -> users V3 (UUIDv4). Artefacto de migracion, plan 07 seccion 3.4.';

-- Manifiesto de cada importacion: hash de la fuente, version del ETL,
-- hora UTC y operador. Una fila por corrida; la reconciliacion la cita.
CREATE TABLE IF NOT EXISTS legacy.migration_audit (
    id uuid PRIMARY KEY,
    run_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_by text NOT NULL,
    etl_version text NOT NULL,
    envelope_protocol_version integer NOT NULL DEFAULT 1,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_integrity_check text NOT NULL,
    tables_loaded integer NOT NULL,
    rows_loaded bigint NOT NULL,
    manifest_sha256 text NOT NULL,
    notes text
);

COMMENT ON TABLE legacy.migration_audit IS
  'Una fila por importacion legacy con hashes de fuente y manifiesto (plan 07 seccion 3.7, pasos 3 y 9).';
