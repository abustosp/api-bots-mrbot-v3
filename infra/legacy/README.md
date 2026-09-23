# legacy: importación V2 a PostgreSQL V3

Historial V2 congelado en solo lectura + ETL mínimo de usuarios.
Diseño: [`plans/07-migracion/plan.md`](../../plans/07-migracion/plan.md) §3.

> **Reglas del scope:** todo `.py` compila con `py_compile`; sobre de
> migración con versión de protocolo entera `1`; la central es el único
> proceso con `DATABASE_URL` (**W-1**: el worker no recibe DB nunca);
> columnas sensibles (`clave_*`, `api_key`, ...) viajan opacas en sobre
> sellado y jamás aparecen en manifiestos ni reportes; docstrings en español.

## Contenido

```
infra/legacy/
├── README.md                        # este documento
├── 01-legacy-schema.sql             # esquema + user_id_map + migration_audit
├── 02-legacy-tables.generated.sql   # GENERADO (33 tablas V2 tal cual)
├── 03-legacy-readonly.sql           # sellado: app sin DML en legacy
├── generate_legacy_ddl.py           # SQLite (ro) -> DDL determinista
├── import_legacy.py                 # carga re-ejecutable + manifest.json
├── etl_users.py                     # users -> public.users (UUIDv4) + mapa
├── seed_periods.py                  # cuota V2 -> plan/suscripciones/periodos
└── reconcile_counts.py              # conciliación origen vs legacy
```

## Orden de ejecución (ventana T-40 a T-15 del runbook)

```bash
# 0. Sintaxis de todo el scope
python3 -m py_compile infra/legacy/*.py

# 1. Generar el DDL versionado desde el SQLite congelado (solo lectura)
python3 infra/legacy/generate_legacy_ddl.py \
  --source ../api-bots-mrbot-v2/data/sql_app.db \
  --out infra/legacy/02-legacy-tables.generated.sql

# 2. Esquema + tablas
psql "$DATABASE_URL" -f infra/legacy/01-legacy-schema.sql
psql "$DATABASE_URL" -f infra/legacy/02-legacy-tables.generated.sql

# 3. Carga auditable (integrity_check + SHA-256 + conteos por tabla)
python3 infra/legacy/import_legacy.py \
  --source ../api-bots-mrbot-v2/data/sql_app.db \
  --manifest /tmp/legacy-manifest.json --audit

# 4. ETL de usuarios (UUIDv4 + user_id_map; excepciones sin secretos)
python3 infra/legacy/etl_users.py \
  --source ../api-bots-mrbot-v2/data/sql_app.db \
  --exceptions /tmp/etl-users-exceptions.json

# 5. Sellar ANTES de exponer la central
psql "$DATABASE_URL" -f infra/legacy/03-legacy-readonly.sql

# 5b. Siembra inicial de cuota migrada (plan `migrado-v2`, suscripciones
#     ACTIVA/CANCELADA, un periodo ABIERTO por habilitado; consumo heredado
#     en sidecar JSON, sin filas de ledgers). Se aplica con el mapa ya creado
#     por el ETL; re-ejecutable sin duplicar:
python3 infra/legacy/seed_periods.py --apply

# 6. Reconciliación (umbral: igualdad exacta en los 33 conteos)
python3 infra/legacy/reconcile_counts.py \
  --source ../api-bots-mrbot-v2/data/sql_app.db \
  --manifest /tmp/legacy-manifest.json \
  --report /tmp/reconciliacion.json --csv /tmp/reconciliacion.csv
```

## Cómo validar conteos (sin base de datos, en CI)

```bash
# El manifiesto basta para validar formato y coherencia interna:
python3 infra/legacy/reconcile_counts.py \
  --source ../api-bots-mrbot-v2/data/sql_app.db \
  --manifest /tmp/legacy-manifest.json \
  --report /tmp/reconciliacion.json --manifest-only
```

Consulta de conteo de referencia (plan 07 §3.8):

```sql
SELECT 'consulta_mc_logs', count(*) FROM legacy.consulta_mc_logs
UNION ALL SELECT 'users', count(*) FROM legacy.users
UNION ALL SELECT 'user_id_map', count(*) FROM legacy.user_id_map;
```

## Decisiones cerradas y pendientes (cutover)

- Claves API: DECISION CERRADA, reemisión total, sin compat `legacy-hmac`
  (detalle y censo en el docstring de `etl_users.py`). La central V3 solo
  acepta `mbk_<key_id>_<secreto>`, el DDL real no tiene `hash_version` y
  `key_prefix` es UNIQUE: una fila migrada no autenticaría nunca. El ETL no
  crea filas en `public.api_keys`; la primera clave V3 se emite por el flujo
  normal de la central. `packages/mrbot-contracts` no se tocó.
- Siembra inicial de cuota: `seed_periods.py` (este scope) genera plan
  `migrado-v2`, suscripciones y primer periodo por usuario migrado según
  plan 07 §3.6 contra el DDL real (`0006`). Sin filas de `usage_ledger` ni
  `credit_ledger`: el primero exige `job_id` y el segundo no admite dinero
  inferido (plan 04 §1.1). Lo aplica el operador con DSN en T-10.
- Divergencia fuera de scope (no se toca aquí, ver runbook): el DDL de este
  scope usa `legacy.user_id_map(legacy_user_id, v3_user_id, migrated_at,
  source_mail, source_row_checksum)` (plan 07 §3.4) mientras
  `central-api/alembic 0010` crea `legacy.user_id_map(legacy_id, user_id,
  mapped_at)`. La central debe alinear su revisión antes de F1; el ETL y la
  reconciliación usan los nombres del plan 07.
- Tope de 5 jobs por worker: lo respeta el canario del runbook de cutover
  (`T+15`), verificado por `postgres/verify-ddl.sh` (criterio 13).
