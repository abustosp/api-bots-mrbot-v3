-- 03-legacy-readonly.sql: sellado del esquema `legacy`.
--
-- Diseno: plans/07-migracion/plan.md seccion 3.2 (bloque SQL de permisos).
-- Se aplica DESPUES de la carga y del ETL, antes de exponer la central
-- (plan 07 seccion 3.7, paso 8). A partir de aqui el rol de aplicacion
-- V3 no tiene DML sobre `legacy`: el historial es inmutable.
--
-- Roles esperados (los crea la capa de base de datos, plan 01):
--   * central_app      : la central. Solo SELECT en legacy.
--   * central_readonly : panel admin / soporte. Solo SELECT en legacy.
-- El operador que corre la migracion usa otro rol con DML temporal.

-- Sin escritura para la aplicacion, incluyendo secuencias por si alguna
-- tabla futura las tuviera (las V2 no traen: PK enteras sin AUTOINCREMENT
-- de aplicacion, solo rowid implicito).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA legacy FROM central_app;
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA legacy FROM central_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA legacy FROM central_readonly;
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA legacy FROM central_readonly;

GRANT USAGE ON SCHEMA legacy TO central_app;
GRANT SELECT ON ALL TABLES IN SCHEMA legacy TO central_app;
GRANT USAGE ON SCHEMA legacy TO central_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA legacy TO central_readonly;

-- El mapa y la auditoria siguen siendo de la migracion: ni siquiera la
-- central los escribe; solo el operador en una ventana posterior.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE legacy.user_id_map FROM central_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE legacy.migration_audit FROM central_app;
