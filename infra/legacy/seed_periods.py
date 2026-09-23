"""Siembra inicial de planes, suscripciones y periodos para usuarios migrados.

Convierte la cuota V2 (`maximas_consultas_mensuales`, `consultas_realizadas`,
`fecha_ultimo_reset`) en el primer periodo de suscripcion V3 (plan 07 3.6)
contra el DDL real (`alembic 0006`: `plans`, `subscriptions`,
`subscription_periods`):

  `maximas_consultas_mensuales` -> `subscription_periods.included_units`
  `fecha_ultimo_reset`          -> `subscription_periods.starts_at` (heredado
                                   validado; si es nulo o invalido, la hora
                                   del corte `--cutover-at`, documentado)
  proximo mes (30 dias)         -> `subscription_periods.ends_at`
  `consultas_realizadas`        -> NO se escribe como consumo: el consumo V3
                                   se deriva de `usage_ledger` y esa tabla
                                   exige `job_id` (FK a `jobs`) por fila, asi
                                   que no existe fila de ajuste sin job que
                                   la respalde. El consumo heredado queda
                                   registrado en el sidecar JSON (solo
                                   conteos) para soporte; saldo inicial
                                   efectivo = `max(0, included - heredado)`.
                                   No se fabrican filas de `usage_ledger` ni
                                   de `credit_ledger` (plan 04 1.1: no inferir
                                   dinero ni creditos desde contadores V2).

Reglas:

  * Solo usuarios habilitados reciben suscripcion `ACTIVA` + periodo
    `ABIERTO`. Los deshabilitados reciben suscripcion `CANCELADA` sin
    periodo (sin derecho de consumo).
  * Los `users.id` V3 salen de `legacy.user_id_map` (modo `--apply`) o del
    `--map-json` aportado (modo `--sql-out`). Este script nunca inventa el
    UUID del usuario en corte real.
  * IDs V3 nuevos (`plans`, `subscriptions`, `periods`) con UUIDv7;
    `users.id` referenciados son UUIDv4 del ETL (plan 01 3.1).
  * Sobre sellado: no se leen ni escriben `api_key` ni `mail`; el sidecar
    solo lleva IDs, cuotas y ventanas (conteos, sin secretos).
  * Regla W-1: corre con el DSN de la central; el worker no participa.

Modos:

  --apply    lee el mapa desde Postgres via `psql`, reutiliza suscripcion
             y periodo ya existentes (idempotente) y aplica lo que falte.
  --sql-out  genera un .sql idempotente por contenido (reutilizable si se
             aplica una vez) + sidecar JSON. Con `--demo-uuids` inventa los
             UUIDv4 de usuario solo para revisar la forma del SQL; la salida
             queda marcada como MUESTRA y no sirve para el corte.

Uso:

    python3 infra/legacy/seed_periods.py --apply
    python3 infra/legacy/seed_periods.py --source ../api-bots-mrbot-v2/data/sql_app.db \\
        --map-json /tmp/user_id_map.json --sql-out /tmp/seed-periods.sql \\
        --sidecar-out /tmp/seed-periods-sidecar.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

PROTOCOL_VERSION: int = 1
"""Version del sobre de migracion (entero, 1)."""

PLAN_CODE = "migrado-v2"
PERIOD_DAYS = 30


def new_uuid7() -> str:
    """Genera un UUIDv7 (ms UTC + aleatorio) como texto."""
    ms = time.time_ns() // 1_000_000
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    value = (ms << 80) | (7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def parse_reset(raw: object) -> datetime | None:
    """Interpreta `fecha_ultimo_reset` V2 como UTC; None si nula o invalida."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def period_window(reset: datetime | None, cutover_at: datetime) -> tuple[datetime, bool]:
    """Calcula el inicio del periodo y si se uso correccion manual."""
    if reset is not None:
        return reset, False
    return cutover_at, True


def sql_text(value: str) -> str:
    """Escapa un literal de texto SQL."""
    return "'" + value.replace("'", "''") + "'"


def psql(dsn: str, sql: str) -> str:
    """Ejecuta SQL via `psql`; ante error, mensaje neutro sin DSN ni datos."""
    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql fallo: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


def read_v2_users(source: str) -> list[dict]:
    """Lee la cuota V2 por usuario desde el SQLite (modo ro, sin secretos)."""
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = list(
            con.execute(
                "SELECT id, maximas_consultas_mensuales, consultas_realizadas,"
                " fecha_ultimo_reset, habilitado FROM users ORDER BY id"
            )
        )
    finally:
        con.close()
    return [dict(r) for r in rows]


def build_rows(
    users: list[dict],
    mapping: dict[int, str],
    cutover_at: datetime,
) -> tuple[dict, list[dict]]:
    """Arma plan, suscripciones, periodos y sidecar (sin tocar la base)."""
    plan = {
        "id": new_uuid7(),
        "code": PLAN_CODE,
        "name": "Plan de migracion V2 (cuota heredada)",
        "included_units": 0,
        "period_days": PERIOD_DAYS,
        "price_cents": 0,
        "currency": "ARS",
    }
    subscriptions: list[dict] = []
    periods: list[dict] = []
    sidecar: list[dict] = []
    for row in users:
        legacy_id = int(row["id"])
        v3_user_id = mapping.get(legacy_id)
        if v3_user_id is None:
            raise KeyError(f"sin v3_user_id para legacy_user_id={legacy_id}")
        enabled = bool(row.get("habilitado"))
        included = row.get("maximas_consultas_mensuales")
        included = int(included) if included is not None else 0
        included = max(0, included)
        consumed = row.get("consultas_realizadas")
        consumed = int(consumed) if consumed is not None else 0
        consumed = max(0, consumed)
        sub = {
            "id": new_uuid7(),
            "user_id": v3_user_id,
            "status": "ACTIVA" if enabled else "CANCELADA",
        }
        subscriptions.append(sub)
        entry: dict = {
            "legacy_user_id": legacy_id,
            "v3_user_id": v3_user_id,
            "subscription_id": sub["id"],
            "included_units": included,
            "consumed_heredado": consumed,
            "saldo_inicial": max(0, included - consumed),
            "subscription_status": sub["status"],
        }
        if enabled:
            starts_at, corrected = period_window(
                parse_reset(row.get("fecha_ultimo_reset")), cutover_at
            )
            ends_at = starts_at + timedelta(days=PERIOD_DAYS)
            period = {
                "id": new_uuid7(),
                "subscription_id": sub["id"],
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "status": "ABIERTO",
                "included_units": included,
            }
            periods.append(period)
            entry["period_id"] = period["id"]
            entry["starts_at"] = period["starts_at"]
            entry["ends_at"] = period["ends_at"]
            entry["inicio_corregido"] = corrected
        sidecar.append(entry)
    return plan, subscriptions, periods, sidecar


def render_sql(
    plan: dict, subscriptions: list[dict], periods: list[dict], demo: bool
) -> str:
    """Genera el .sql idempotente por contenido para la siembra inicial."""
    lines = [
        "-- Siembra inicial de cuota migrada V2 (plan 07 3.6).",
        f"-- Sobre de migracion, version de protocolo: {PROTOCOL_VERSION}.",
        "-- Sin filas de usage_ledger/credit_ledger (ver docstring).",
    ]
    if demo:
        lines.append(
            "-- MUESTRA OFFLINE: UUIDv4 de usuario inventados para revisar"
            " la forma; en corte usar --apply contra Postgres."
        )
    lines.append("BEGIN;")
    lines.append(
        "INSERT INTO public.plans (id, name, code, included_units,"
        " period_days, price_cents, currency, active, metadata) VALUES "
        f"('{plan['id']}', {sql_text(plan['name'])}, '{plan['code']}', "
        f"{plan['included_units']}, {plan['period_days']}, {plan['price_cents']}, "
        f"'{plan['currency']}', true, '{{\"origen\": \"migracion-v2\"}}'::jsonb) "
        "ON CONFLICT (code) DO NOTHING;"
    )
    for sub in subscriptions:
        lines.append(
            "INSERT INTO public.subscriptions (id, user_id, plan_id, status,"
            " cancel_at_period_end) VALUES "
            f"('{sub['id']}', '{sub['user_id']}', "
            f"(SELECT id FROM public.plans WHERE code = '{PLAN_CODE}'), "
            f"'{sub['status']}', false) ON CONFLICT DO NOTHING;"
        )
    for period in periods:
        lines.append(
            "INSERT INTO public.subscription_periods (id, subscription_id,"
            " starts_at, ends_at, status, included_units,"
            " credit_unit_price_cents) VALUES "
            f"('{period['id']}', '{period['subscription_id']}', "
            f"'{period['starts_at']}', '{period['ends_at']}', "
            f"'{period['status']}', {period['included_units']}, 0) "
            "ON CONFLICT DO NOTHING;"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def apply_mode(dsn: str, source: str, cutover_at: datetime) -> tuple[int, int]:
    """Aplica la siembra contra Postgres reutilizando lo ya sembrado."""
    users = read_v2_users(source)
    created_subs = 0
    created_periods = 0
    plan_id = psql(dsn, f"SELECT id FROM public.plans WHERE code = {sql_text(PLAN_CODE)}")
    if not plan_id:
        plan_id = new_uuid7()
        psql(
            dsn,
            "INSERT INTO public.plans (id, name, code, included_units,"
            " period_days, price_cents, currency, active, metadata) VALUES "
            f"('{plan_id}', 'Plan de migracion V2 (cuota heredada)', "
            f"'{PLAN_CODE}', 0, {PERIOD_DAYS}, 0, 'ARS', true, "
            "'{\"origen\": \"migracion-v2\"}'::jsonb)",
        )
    else:
        plan_id = plan_id.strip()
    for row in users:
        legacy_id = int(row["id"])
        v3_user_id = psql(
            dsn,
            "SELECT v3_user_id FROM legacy.user_id_map "
            f"WHERE legacy_user_id = {legacy_id}",
        ).strip()
        if not v3_user_id:
            raise RuntimeError(f"sin mapa para legacy_user_id={legacy_id}")
        enabled = bool(row.get("habilitado"))
        want_status = "ACTIVA" if enabled else "CANCELADA"
        sub_id = psql(
            dsn,
            "SELECT id FROM public.subscriptions WHERE user_id "
            f"= '{v3_user_id}' AND status = '{want_status}' LIMIT 1",
        ).strip()
        if not sub_id:
            sub_id = new_uuid7()
            psql(
                dsn,
                "INSERT INTO public.subscriptions (id, user_id, plan_id,"
                " status, cancel_at_period_end) VALUES "
                f"('{sub_id}', '{v3_user_id}', '{plan_id}', "
                f"'{want_status}', false)",
            )
            created_subs += 1
        if not enabled:
            continue
        included = max(0, int(row.get("maximas_consultas_mensuales") or 0))
        starts_at, _ = period_window(parse_reset(row.get("fecha_ultimo_reset")), cutover_at)
        ends_at = starts_at + timedelta(days=PERIOD_DAYS)
        period_id = psql(
            dsn,
            "SELECT id FROM public.subscription_periods WHERE subscription_id "
            f"= '{sub_id}' AND status = 'ABIERTO' LIMIT 1",
        ).strip()
        if not period_id:
            psql(
                dsn,
                "INSERT INTO public.subscription_periods (id, subscription_id,"
                " starts_at, ends_at, status, included_units,"
                " credit_unit_price_cents) VALUES "
                f"('{new_uuid7()}', '{sub_id}', '{starts_at.isoformat()}', "
                f"'{ends_at.isoformat()}', 'ABIERTO', {included}, 0)",
            )
            created_periods += 1
    return created_subs, created_periods


def main() -> int:
    """Punto de entrada CLI de la siembra inicial de periodos."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="../api-bots-mrbot-v2/data/sql_app.db")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--cutover-at", default=None,
                        help="ISO UTC para inicios corregidos (defecto: ahora)")
    parser.add_argument("--apply", action="store_true",
                        help="Aplicar contra Postgres via psql")
    parser.add_argument("--map-json", default=None,
                        help="Mapa {legacy_user_id: v3_user_id} para --sql-out")
    parser.add_argument("--sql-out", default=None)
    parser.add_argument("--sidecar-out", default=None)
    parser.add_argument("--demo-uuids", action="store_true",
                        help="Inventar UUIDv4 solo para muestra offline")
    args = parser.parse_args()

    cutover_at = (
        datetime.fromisoformat(args.cutover_at)
        if args.cutover_at
        else datetime.now(timezone.utc)
    )
    if cutover_at.tzinfo is None:
        cutover_at = cutover_at.replace(tzinfo=timezone.utc)

    if args.apply:
        dsn = args.dsn or os.environ.get("DATABASE_URL")
        if not dsn:
            dsn_file = os.environ.get("DATABASE_URL_FILE", "/run/secrets/database_url")
            if os.path.isfile(dsn_file):
                with open(dsn_file, encoding="utf-8") as fh:
                    dsn = fh.read().strip()
        if not dsn:
            print("ERROR: definir --dsn, DATABASE_URL o DATABASE_URL_FILE",
                  file=sys.stderr)
            return 1
        subs, periods = apply_mode(dsn, args.source, cutover_at)
        print(f"OK: {subs} suscripciones y {periods} periodos creados")
        return 0

    if not args.sql_out or not args.sidecar_out:
        print("ERROR: --sql-out y --sidecar-out son obligatorios sin --apply",
              file=sys.stderr)
        return 1
    users = read_v2_users(args.source)
    if args.map_json:
        with open(args.map_json, encoding="utf-8") as fh:
            mapping = {int(k): str(v) for k, v in json.load(fh).items()}
    elif args.demo_uuids:
        mapping = {int(r["id"]): str(uuid.uuid4()) for r in users}
    else:
        print("ERROR: aportar --map-json o --demo-uuids (muestra)", file=sys.stderr)
        return 1
    plan, subscriptions, periods, sidecar = build_rows(users, mapping, cutover_at)
    with open(args.sql_out, "w", encoding="utf-8") as fh:
        fh.write(render_sql(plan, subscriptions, periods, args.demo_uuids))
    with open(args.sidecar_out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "plan": PLAN_CODE,
                "envelope_protocol_version": PROTOCOL_VERSION,
                "cutover_at_utc": cutover_at.isoformat(),
                "muestra_offline": args.demo_uuids,
                "suscripciones": len(subscriptions),
                "periodos": len(periods),
                "usuarios": sidecar,
            },
            fh,
            indent=2,
            sort_keys=True,
        )
        fh.write("\n")
    print(f"OK: {len(subscriptions)} suscripciones, {len(periods)} periodos"
          f" -> {args.sql_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
