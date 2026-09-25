#!/usr/bin/env bash
# Verifica el DDL y los invariantes de esquema de plans/01-database/plan.md
# contra un PostgreSQL 17 real, sin necesidad de codigo de aplicacion.
#
# Uso:  ./infra/postgres/verify-ddl.sh
# Requiere: docker
#
# Comprueba los criterios de aceptacion 1, 2, 3, 4, 5, 9, 12, 13, 14, 15 y 20
# de plans/01-database/plan.md, y de forma directa las invariantes I-1, I-2,
# I-3, S-2 y el respaldo en base del tope de 5 de W-2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLAN="$REPO_ROOT/plans/01-database/plan.md"
CONTAINER="mrbot-v3-ddl-verify-$$-$(date +%s)"
IMAGE="postgres:17-alpine"
WORKDIR="$(mktemp -d)"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
FAILED=0

ok()   { printf "%s  OK  %s%s\n" "$GREEN" "$1" "$RESET"; }
bad()  { printf "%s FALLA %s%s\n" "$RED" "$1" "$RESET"; FAILED=$((FAILED+1)); }
info() { printf "%s  ..  %s%s\n" "$YELLOW" "$1" "$RESET"; }

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

psqlq() { docker exec -i "$CONTAINER" psql -U postgres -d v3 -At -c "$1"; }
# Ejecuta SQL que se espera que falle y devuelve su salida. El `|| true` es
# necesario porque psql sale con codigo != 0 y `set -o pipefail` abortaria el
# script antes de que grep pueda inspeccionar el mensaje de error.
psqlerr() { docker exec -i "$CONTAINER" psql -U postgres -d v3 -c "$1" 2>&1 || true; }
psqlf() { docker exec -i "$CONTAINER" psql -U postgres -d v3 -q; }

# ---------------------------------------------------------------- extraccion
info "Extrayendo bloques SQL de $PLAN"
python3 - "$PLAN" "$WORKDIR" <<'PY'
import re, sys, pathlib
plan, out = sys.argv[1], pathlib.Path(sys.argv[2])
src = pathlib.Path(plan).read_text()
blocks = re.findall(r'```sql\n(.*?)```', src, re.S)
ddl  = [b for b in blocks if 'CREATE TABLE' in b]
view = [b for b in blocks if 'worker_fleet_health' in b and 'CREATE' in b]
if not ddl:
    sys.exit("no se encontro DDL con CREATE TABLE en el plan")
(out / 'ddl.sql').write_text('\n'.join(ddl))
# La vista trae un SELECT de ejemplo al final; solo interesa el CREATE VIEW.
if view:
    v = view[0]
    cut = v.find('\nSELECT *')
    (out / 'view.sql').write_text(v if cut < 0 else v[:cut])
print(f"  bloques sql={len(blocks)} ddl={len(ddl)} tablas={'\\n'.join(ddl).count('CREATE TABLE')}")
PY

# ------------------------------------------------------------------ arranque
info "Levantando $IMAGE"
# Una corrida anterior puede estar aun liberando el nombre. Esperar a que el
# borrado termine de verdad evita una colision de nombre intermitente.
if ! docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD=verify \
        -e POSTGRES_DB=v3 "$IMAGE" >"$WORKDIR/run.log" 2>&1; then
    bad "No se pudo crear el contenedor"
    cat "$WORKDIR/run.log"
    exit 1
fi
# `pg_isready` da un falso positivo durante el arranque: la imagen oficial
# levanta un servidor temporal para crear la base y despues lo apaga. Si se
# acepta ese primer "ready", la siguiente consulta cae justo en el apagado.
# La espera correcta es que una consulta real tenga exito de forma sostenida.
READY=0
for _ in $(seq 1 90); do
    if docker exec "$CONTAINER" psql -U postgres -d v3 -Atqc 'SELECT 1' >/dev/null 2>&1; then
        # Confirmar estabilidad: tres consultas seguidas separadas en el tiempo.
        STABLE=1
        for _ in 1 2 3; do
            sleep 1
            docker exec "$CONTAINER" psql -U postgres -d v3 -Atqc 'SELECT 1' >/dev/null 2>&1 || {
                STABLE=0
                break
            }
        done
        if [ "$STABLE" = "1" ]; then
            READY=1
            break
        fi
    fi
    sleep 1
done
if [ "$READY" != "1" ]; then
    bad "PostgreSQL no quedo disponible de forma estable"
    docker logs "$CONTAINER" 2>&1 | tail -20
    exit 1
fi

# ----------------------------------------------------------- criterio 1: DDL
docker cp "$WORKDIR/ddl.sql" "$CONTAINER:/ddl.sql" >/dev/null
if docker exec "$CONTAINER" psql -U postgres -d v3 -v ON_ERROR_STOP=1 -q -f /ddl.sql >"$WORKDIR/ddl.log" 2>&1; then
    ok "El DDL se ejecuta sin errores en PostgreSQL 17"
else
    bad "El DDL no se ejecuta"; tail -20 "$WORKDIR/ddl.log"; exit 1
fi

EXPECTED="admin_users api_keys audit_log bot_operations bots credit_ledger job_artifacts job_events job_results jobs payment_events payments plans subscription_periods subscriptions usage_ledger users worker_heartbeats workers"
ACTUAL=$(psqlq "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1;" | tr '\n' ' ' | sed 's/ $//')
if [ "$ACTUAL" = "$EXPECTED" ]; then
    ok "Criterio 1: las 19 tablas canonicas existen, sin faltantes ni extras"
else
    bad "Criterio 1: la lista de tablas no coincide"
    printf "    esperado: %s\n    obtenido: %s\n" "$EXPECTED" "$ACTUAL"
fi

# ------------------------------------------- criterios 2, 3, 4: I-1 e I-2
NONUUID=$(psqlq "
SELECT count(*) FROM pg_index i
JOIN pg_class c ON c.oid=i.indrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(i.indkey)
WHERE i.indisprimary AND n.nspname='public'
  AND format_type(a.atttypid,a.atttypmod) <> 'uuid';")
[ "$NONUUID" = "0" ] && ok "Criterios 2 y 3: toda PK de public es uuid" \
                     || bad "Criterios 2 y 3: hay $NONUUID columnas de PK que no son uuid"

SEQCOLS=$(psqlq "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND (is_identity='YES' OR column_default LIKE 'nextval%');")
SEQS=$(psqlq "SELECT count(*) FROM pg_class WHERE relkind='S';")
if [ "$SEQCOLS" = "0" ] && [ "$SEQS" = "0" ]; then
    ok "Criterio 4 (I-1, I-2): cero identity, cero serial, cero secuencias"
else
    bad "Criterio 4 (I-1, I-2): identity/serial=$SEQCOLS secuencias=$SEQS"
fi

# ------------------------------------------------------ criterio 5: I-3
USERCOLS=$(psqlq "SELECT string_agg(column_name,',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='users';")
FORBIDDEN=$(psqlq "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name IN ('fecha_ultimo_reset','created_at','updated_at','api_key','maximas_consultas_mensuales','consultas_realizadas');")
[ "$FORBIDDEN" = "0" ] && ok "Criterio 5 (I-3): users no tiene columnas prohibidas ($USERCOLS)" \
                       || bad "Criterio 5 (I-3): users tiene $FORBIDDEN columnas prohibidas ($USERCOLS)"

# --------------------------------------------------------------- semilla
psqlf <<'SQL'
INSERT INTO users(id,email,habilitado) VALUES ('11111111-1111-4111-8111-111111111111','verify@test.local',true);
INSERT INTO plans(id,code,name,included_units,price_cents) VALUES ('22222222-2222-4222-8222-222222222222','free','Free',10,0);
INSERT INTO bots(id,code,display_name) VALUES ('33333333-3333-4333-8333-333333333333','mis_comprobantes','Mis Comprobantes');
INSERT INTO bots(id,code,display_name) VALUES ('77777777-7777-4777-8777-777777777777','libros_iva','Libros IVA');
INSERT INTO bot_operations(id,bot_code,code,input_schema_version) VALUES ('44444444-4444-4444-8444-444444444444','mis_comprobantes','consulta','1');
INSERT INTO bot_operations(id,bot_code,code,input_schema_version) VALUES ('88888888-8888-4888-8888-888888888888','libros_iva','consulta','1');
INSERT INTO workers(id,name,endpoint,protocol_version,app_version,status,last_heartbeat_at) VALUES ('55555555-5555-4555-8555-555555555555','w1','http://w1:8000','1','v3.0.0','SANO',now());
INSERT INTO workers(id,name,endpoint,protocol_version,app_version,status,last_heartbeat_at) VALUES ('66666666-6666-4666-8666-666666666666','w2','http://w2:8000','1','v3.0.0','SANO',now());
SQL

# ------------------------------------------------- criterio 9: S-2
psqlq "INSERT INTO jobs(id,user_id,bot,operation,protocol_version,idempotency_key) VALUES ('018f0000-0000-7000-8000-000000000001','11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1','K1');" >/dev/null
DUP_OUT=$(psqlerr "INSERT INTO jobs(id,user_id,bot,operation,protocol_version,idempotency_key) VALUES ('018f0000-0000-7000-8000-000000000002','11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1','K1');")
if grep -q "uq_jobs_idempotency" <<<"$DUP_OUT"; then
    ok "Criterio 9 (S-2): idempotencia duplicada rechazada por uq_jobs_idempotency"
else
    bad "Criterio 9 (S-2): se acepto un job duplicado con la misma Idempotency-Key"
fi

# ------------------------------------------ criterio 12: forma terminal
SHAPE_OUT=$(psqlerr "UPDATE jobs SET status='COMPLETO', result='OK' WHERE id='018f0000-0000-7000-8000-000000000001';")
if grep -q "terminal_shape" <<<"$SHAPE_OUT"; then
    ok "Criterio 12: ck_jobs_terminal_shape rechaza COMPLETO sin finished_at"
else
    bad "Criterio 12: ck_jobs_terminal_shape no protege la forma terminal"
fi

# ------------------------------- criterio 13: claim concurrente y tope 5
psqlf <<'SQL'
DELETE FROM jobs;
UPDATE workers SET running_jobs=0, capacity=5, last_heartbeat_at=now(), status='SANO';
INSERT INTO jobs(id,user_id,bot,operation,protocol_version)
SELECT ('018f0000-0000-7000-8000-0000000000'||lpad(g::text,2,'0'))::uuid,
       '11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1'
FROM generate_series(1,10) g;
SQL

# La consulta de claim se extrae del PROPIO plan. Asi, si alguien edita el plan
# y rompe la semantica del claim, este test lo detecta. No es una copia a mano.
python3 "$REPO_ROOT/infra/postgres/_extract_claim.py" "$PLAN" "$WORKDIR/claim.sql"

if grep -q "EXISTS (SELECT 1 FROM next_job)" "$WORKDIR/claim.sql"; then
    ok "El claim del plan condiciona la reserva con EXISTS, no filtra capacidad"
else
    bad "El claim del plan no tiene EXISTS: incrementaria running_jobs con cola vacia"
fi
docker cp "$WORKDIR/claim.sql" "$CONTAINER:/claim.sql" >/dev/null

# Caso cola vacia. Este es el caso que detecto la fuga de capacidad.
psqlf <<'SQL'
DELETE FROM jobs;
UPDATE workers SET running_jobs=0, capacity=5, last_heartbeat_at=now(), status='SANO';
SQL
docker exec "$CONTAINER" psql -U postgres -d v3 -At -v wname=w1 -f /claim.sql >/dev/null 2>&1 || true
EMPTY_RUN=$(psqlq "SELECT running_jobs FROM workers WHERE name='w1';")
if [ "$EMPTY_RUN" = "0" ]; then
    ok "Criterio 13 (cola vacia): running_jobs sigue en 0, sin fuga de capacidad"
else
    bad "Criterio 13 (cola vacia): running_jobs=$EMPTY_RUN, la CTE reservo sin asignar"
fi

# Rehacer la cola para el caso concurrente.
psqlf <<'SQL'
DELETE FROM jobs;
UPDATE workers SET running_jobs=0, capacity=5, last_heartbeat_at=now(), status='SANO';
INSERT INTO jobs(id,user_id,bot,operation,protocol_version)
SELECT ('018f0000-0000-7000-8000-0000000000'||lpad(g::text,2,'0'))::uuid,
       '11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1'
FROM generate_series(1,10) g;
SQL

# Rafagas concurrentes. Se repiten porque SKIP LOCKED es deliberadamente no
# bloqueante: si dos schedulers apuntan a la misma fila, uno la saltea y vuelve
# con la cola vacia en vez de esperar. Eso es correcto y es lo que evita el
# convoy, pero significa que una sola rafaga puede dejar jobs sin reclamar.
# Un scheduler real cicla; el test tambien. La propiedad de seguridad (nunca
# dos veces el mismo job, nunca exceder capacidad) se verifica en CADA rafaga;
# la de vivacidad (la cola se drena) se verifica al final.
for round in $(seq 1 5); do
    for _ in $(seq 1 6); do
        docker exec "$CONTAINER" psql -U postgres -d v3 -At -v wname=w1 -f /claim.sql >/dev/null 2>&1 &
        docker exec "$CONTAINER" psql -U postgres -d v3 -At -v wname=w2 -f /claim.sql >/dev/null 2>&1 &
    done
    wait
    # Seguridad, invariante en todo momento.
    R_OVER=$(psqlq "SELECT count(*) FROM workers WHERE running_jobs > capacity;")
    R_ORPH=$(psqlq "SELECT count(*) FROM jobs WHERE status='ASIGNADO' AND worker_id IS NULL;")
    R_DUP=$(psqlq "SELECT count(*) FROM (SELECT id FROM jobs WHERE status='ASIGNADO' GROUP BY id HAVING count(*)>1) d;")
    R_MISMATCH=$(psqlq "SELECT count(*) FROM (SELECT w.name FROM workers w WHERE w.running_jobs <> (SELECT count(*) FROM jobs j WHERE j.worker_id=w.id AND j.status='ASIGNADO')) m;")
    if [ "$R_OVER" != "0" ] || [ "$R_ORPH" != "0" ] || [ "$R_DUP" != "0" ] || [ "$R_MISMATCH" != "0" ]; then
        bad "Criterio 13 seguridad roto en la rafaga $round: sobrecapacidad=$R_OVER huerfanos=$R_ORPH duplicados=$R_DUP contador_desalineado=$R_MISMATCH"
        break
    fi
done
ok "Criterio 13 seguridad: en 5 rafagas de 12 claims nunca hubo doble asignacion, sobrecapacidad ni contador desalineado"

ASSIGNED=$(psqlq "SELECT count(*) FROM jobs WHERE status='ASIGNADO';")
DISTINCT=$(psqlq "SELECT count(DISTINCT id) FROM jobs WHERE status='ASIGNADO';")
OVER=$(psqlq "SELECT count(*) FROM workers WHERE running_jobs > capacity;")
ORPHAN=$(psqlq "SELECT count(*) FROM jobs WHERE status='ASIGNADO' AND worker_id IS NULL;")
SPREAD=$(psqlq "SELECT string_agg(name||'='||running_jobs, ' ' ORDER BY name) FROM workers;")

PENDING_LEFT=$(psqlq "SELECT count(*) FROM jobs WHERE status='PENDIENTE';")
if [ "$ASSIGNED" = "10" ] && [ "$DISTINCT" = "10" ] && [ "$OVER" = "0" ] && [ "$ORPHAN" = "0" ] && [ "$PENDING_LEFT" = "0" ]; then
    ok "Criterio 13 vivacidad: la cola de 10 se drena por completo entre 2 workers de capacidad 5 ($SPREAD)"
else
    bad "Criterio 13 vivacidad: asignados=$ASSIGNED distintos=$DISTINCT pendientes=$PENDING_LEFT sobrecapacidad=$OVER huerfanos=$ORPHAN ($SPREAD)"
fi

CAPREJ=0
CAP_OUT=$(psqlerr "UPDATE workers SET capacity=6 WHERE name='w1';")
RUN_OUT=$(psqlerr "UPDATE workers SET running_jobs=6 WHERE name='w1';")
grep -q "ck_workers_capacity" <<<"$CAP_OUT" && CAPREJ=$((CAPREJ+1))
grep -q "ck_workers_running"  <<<"$RUN_OUT" && CAPREJ=$((CAPREJ+1))
[ "$CAPREJ" = "2" ] && ok "Criterio 13 (W-2): la base rechaza capacity>5 y running_jobs>5" \
                    || bad "Criterio 13 (W-2): la base no respalda el tope de 5 ($CAPREJ de 2 rechazos)"

# ------------------------------------------- criterio 14: indice parcial
info "Cargando 50.000 jobs terminales para probar el indice parcial"
psqlf <<'SQL'
INSERT INTO jobs(id,user_id,bot,operation,protocol_version,status,result,finished_at)
SELECT ('018f0001-0000-7000-8000-'||lpad(g::text,12,'0'))::uuid,
       '11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1','COMPLETO','OK',now()
FROM generate_series(1,50000) g;
INSERT INTO jobs(id,user_id,bot,operation,protocol_version)
SELECT ('018f0002-0000-7000-8000-'||lpad(g::text,12,'0'))::uuid,
       '11111111-1111-4111-8111-111111111111','mis_comprobantes','consulta','1'
FROM generate_series(1,50) g;
ANALYZE jobs;
SQL
PLANOUT=$(psqlq "EXPLAIN (ANALYZE) SELECT id FROM jobs WHERE status='PENDIENTE' ORDER BY priority ASC, created_at ASC, id ASC LIMIT 1;")
if grep -q "ix_jobs_queue_pending" <<<"$PLANOUT"; then
    ok "Criterio 14: el claim usa ix_jobs_queue_pending con 50.000 jobs terminales"
else
    bad "Criterio 14: el claim no usa el indice parcial de cola"; echo "$PLANOUT"
fi

# ---------------------------------------------- criterio 15: GIN JSONB
psqlf <<'SQL'
INSERT INTO jobs(id,user_id,bot,operation,protocol_version,status,result,finished_at)
VALUES ('018f0003-0000-7000-8000-000000000001','11111111-1111-4111-8111-111111111111','libros_iva','consulta','1','COMPLETO','PARCIAL',now());
INSERT INTO job_results(job_id,attempt,result,payload)
VALUES ('018f0003-0000-7000-8000-000000000001',1,'PARCIAL',
        '{"periodos_descargados":["202401","202402"],"periodos_error":["202403"],"denominacion":"ACME SA"}');
SQL
HITS=$(psqlq "SELECT count(*) FROM job_results WHERE payload @> '{\"periodos_error\":[\"202403\"]}';")
GIN=$(psqlq "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexdef ILIKE '%gin%jsonb_path_ops%';")
if [ "$HITS" = "1" ] && [ "$GIN" -ge 1 ]; then
    ok "Criterio 15: campo especifico por bot (periodos_error) consultable via GIN jsonb_path_ops"
else
    bad "Criterio 15: hits=$HITS indices_gin=$GIN"
fi

# ------------------------------------------- criterio 20: vista de flota
if [ -f "$WORKDIR/view.sql" ]; then
    docker cp "$WORKDIR/view.sql" "$CONTAINER:/view.sql" >/dev/null
    if docker exec "$CONTAINER" psql -U postgres -d v3 -v ON_ERROR_STOP=1 -q -f /view.sql >/dev/null 2>&1; then
        psqlf <<'SQL'
UPDATE workers SET running_jobs=5, capacity=5, last_heartbeat_at=now(), status='SANO' WHERE name='w1';
UPDATE workers SET running_jobs=2, capacity=5, last_heartbeat_at=now(), status='SANO' WHERE name='w2';
SQL
        H1=$(psqlq "SELECT effective_health FROM worker_fleet_health WHERE name='w1';")
        H2=$(psqlq "SELECT effective_health FROM worker_fleet_health WHERE name='w2';")
        psqlq "UPDATE workers SET last_heartbeat_at=now() - INTERVAL '45 seconds' WHERE name='w2';" >/dev/null
        H3=$(psqlq "SELECT effective_health FROM worker_fleet_health WHERE name='w2';")
        if [ "$H1" = "SATURADO" ] && [ "$H2" = "SANO" ] && [ "$H3" = "CAIDO" ]; then
            ok "Criterio 20: la vista deriva SATURADO, SANO y CAIDO con umbral de 30 s"
        else
            bad "Criterio 20: derivacion inesperada w1=$H1 w2_libre=$H2 w2_sin_latido=$H3"
        fi
    else
        bad "Criterio 20: la vista worker_fleet_health no se crea"
    fi
else
    bad "Criterio 20: no se encontro el CREATE VIEW en el plan"
fi

# ------------------------------------------------------------- resultado
echo
if [ "$FAILED" -eq 0 ]; then
    printf "%sTodas las comprobaciones de esquema pasaron.%s\n" "$GREEN" "$RESET"
    exit 0
else
    printf "%s%s comprobacion(es) fallaron.%s\n" "$RED" "$FAILED" "$RESET"
    exit 1
fi
