-- Inicializacion de PostgreSQL 17 para la V3.
-- Se monta en /docker-entrypoint-initdb.d y corre solo al crear el
-- volumen por primera vez. Debe ser idempotente y no asumir nada
-- fuera del esquema que crea.
--
-- Diseno: plans/01-database/plan.md. La estrategia canonica genera
-- los UUID en la aplicacion antes del INSERT, asi que no hay una
-- extension UUID obligatoria. pgcrypto queda como defensa opcional
-- para scripts SQL administrativos (gen_random_uuid).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
