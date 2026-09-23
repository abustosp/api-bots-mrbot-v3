# Runbook de cutover V2 → V3

> Alcance: corte de escritura V2, importación `legacy`, ETL de usuarios y
> apertura de `/api/v3`. Diseño completo en
> [`plans/07-migracion/plan.md`](../../../plans/07-migracion/plan.md) §§3.7–3.9
> y 6. Tiempos: objetivos operativos a ensayar con clon de tamaño comparable.

## Precondiciones (antes de T-14 días)

- [ ] Ensayo completo de import + restore con duración medida (dueño: datos;
      pendiente de Postgres con DSN; comandos listos en §Comandos y en
      `infra/legacy/README.md`).
- [ ] Hashes de imágenes fijados (central y worker por digest; dueño: release
      manager; fuera de este scope).
- [ ] Inventario de clientes críticos contactado (dueño: soporte; pendiente
      el contacto). Estrategia de API keys APROBADA: reemisión total, sin
      compat `legacy-hmac` (decisión cerrada y censada en
      `infra/legacy/etl_users.py`; el ETL ya no crea filas en `api_keys`).
- [ ] Backup V2 y Postgres base con restore probado (dueño: operaciones;
      pendiente; evidenciaReq: `sqlite-freeze.sha256` + restore probado).
- [x] `infra/legacy/02-legacy-tables.generated.sql` regenerado contra el
      SQLite real y versionado como artefacto de release (verificado:
      regeneración idéntica, 33 tablas; `integrity_check = ok`,
      sha256 `21c4ec99…f1046`).

## Cronograma (horas UTC, responsable y gate por paso)

| Tiempo | Acción | Dueño | Gate |
|---|---|---|---|
| T-14 días | ensayo completo de import y restore | datos | duración conocida, reporte limpio |
| T-7 días | anunciar mantenimiento y reemisión de claves | soporte | clientes críticos contactados |
| T-48 h | backup verificable V2 y Postgres base | operaciones | restore probado |
| T-24 h | freeze de deploys V2 y V3 | release manager | hashes de imágenes fijados |
| T-60 min | V2 en drain, negar nuevas ejecuciones | operaciones | cola a cero o lista explícita |
| T-45 min | mantenimiento público | soporte | 503 controlado en V2 |
| T-40 min | detener escritores V2 y snapshot SQLite | datos | `integrity_check = ok` |
| T-30 min | importar `legacy` y ETL users (§Comandos) | datos | ETL sin errores bloqueantes |
| T-15 min | reconciliación + revisión humana | datos + seguridad | todos los umbrales verdes |
| T-10 min | sembrar catálogo, planes y período inicial (`python3 infra/legacy/seed_periods.py --apply`, plan `migrado-v2`; sidecar de consumo heredado sin secretos) | central | DDL y permisos validados |
| T-5 min | central + workers V3 sin tráfico | operaciones | health, heartbeat y fake job verdes |
| T-0 | abrir `/api/v3`, habilitar DNS/ruta | release manager | aprobación go |
| T+15 min | canarios de bots y autenticación | QA | sin fuga ni duplicación |
| T+60 min | cerrar observación inicial | incident commander | métricas en rango |

## Comandos (ventana T-40 a T-15)

```bash
export SQLITE=/mnt/v2-freeze/sql_app.db   # snapshot inmutable del freeze
export DATABASE_URL="$(cat /run/secrets/database_url)"

# T-40: integridad + hash del snapshot (guardar salida como evidencia)
python3 -c "import sqlite3;print(sqlite3.connect('file:$SQLITE?mode=ro',uri=True).execute('PRAGMA integrity_check').fetchone()[0])"
sha256sum "$SQLITE" | tee /tmp/sqlite-freeze.sha256

# T-30: esquema, carga, ETL (detalle en infra/legacy/README.md)
psql "$DATABASE_URL" -f infra/legacy/01-legacy-schema.sql
psql "$DATABASE_URL" -f infra/legacy/02-legacy-tables.generated.sql
python3 infra/legacy/import_legacy.py --source "$SQLITE" \
  --manifest /tmp/legacy-manifest.json --audit
python3 infra/legacy/etl_users.py --source "$SQLITE" \
  --exceptions /tmp/etl-users-exceptions.json
psql "$DATABASE_URL" -f infra/legacy/03-legacy-readonly.sql

# T-15: reconciliación (exige GO; un NO-GO aborta el corte)
python3 infra/legacy/reconcile_counts.py --source "$SQLITE" \
  --manifest /tmp/legacy-manifest.json \
  --report /tmp/reconciliacion.json --csv /tmp/reconciliacion.csv
```

## Go / no-go (antes de T-0, los 8 del plan 07 §3.9)

1. Hashes de backup, dump y reporte coinciden.
2. Conteos de las 33 tablas y mapa de usuarios correctos.
3. Sin datos V3 que violen IDs ni columnas R17.
4. La central no puede escribir en `legacy` (probar un INSERT y ver el 42501).
5. Al menos un worker sano acepta y completa un fake job.
6. Canarios `consulta_cuit` y `mis_comprobantes` cumplen contrato
   (máx. 5 jobs simultáneos por worker, sin `DATABASE_URL` en el worker).
7. Un cliente piloto autentica con el mecanismo acordado.
8. Negocio, operaciones y seguridad aprueban explícitamente (nominal).

## Punto de no retorno y rollback

- **Punto de no retorno:** primer job productivo V3 aceptado o primer
  consumo V3 confirmado (lo que ocurra primero).
- **Antes:** apagar V3, reactivar V2 con el snapshot intacto (ensayo fallido).
- **Después:** solo restauración completa o modo incidente: congelar
  aceptación V3, preservar audit logs, enumerar jobs en vuelo y conciliar
  uso, créditos, pagos y artefactos. Nunca reinyectar un job con efectos
  en V2 sin verificación manual.

## Evidencia mínima archivada

`sqlite-freeze.sha256`, `/tmp/legacy-manifest.json`,
`/tmp/etl-users-exceptions.json`, `/tmp/reconciliacion.{json,csv}`,
`/tmp/seed-periods.{sql,sidecar.json}`, fila de `legacy.migration_audit`,
aprobación nominal con hora UTC.
Sin claves API en claro en ninguno de estos artefactos.

Evidencia pre-corte (sin Postgres, modo `--manifest-only`): 33/33 conteos
OK, 0 fallas, 20976 filas, `source_sha256 = 21c4ec99…f1046`, informe GO en
`/tmp/reconciliacion.{json,csv}`. El GO total exige re-correr T-15 con DSN
(modo completo: conteos PG, nulos, mapa 100%, unicidad).

Divergencia registrada (fuera de este scope, bloquea F1): `legacy.user_id_map`
de `infra/legacy/01-legacy-schema.sql` (`legacy_user_id, v3_user_id,
migrated_at, source_mail, source_row_checksum`, plan 07 §3.4) no coincide con
`central-api/alembic 0010` (`legacy_id, user_id, mapped_at`). La central debe
alinear su revisión; este scope usa los nombres del plan 07.
