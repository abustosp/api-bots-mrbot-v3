# postgres/init

Scripts de inicializacion de PostgreSQL 17. Se montan en
`/docker-entrypoint-initdb.d` desde `../compose/docker-compose.yml` y
corren solo al crear el volumen `postgres_data` por primera vez.

| Archivo | Proposito |
|---|---|
| `01-extensions.sql` | Habilita `pgcrypto` como defensa opcional para UUID generados por la base |

La estrategia canonica genera los UUID en la aplicacion antes del
`INSERT` (ver `../../plans/01-database/plan.md`), asi que ninguna
extension es obligatoria para el esquema. No agregar aqui DDL de
tablas: el esquema lo crean las migraciones Alembic mediante el job
one-off `migrate`, nunca el `CMD` de un contenedor.
