"""Cargador de la migracion V1/V2 -> V3. Corre DENTRO del contenedor central-api.

No se ejecuta a mano: lo invoca ``migrar_a_v3.py`` por ``docker exec``. Vive en
la central porque ahi estan el secreto HMAC de API keys, la clave RSA de
custodia y el SMTP, y porque es el unico proceso con acceso a PostgreSQL.

Modos (primer argumento):

* ``dump <salida.db>``       vuelca todas las tablas de ``public`` y
                             ``legacy.user_id_map`` a un SQLite.
* ``load <staging.db> <reporte.json> [--send-emails] [--dry-run]``
                             aplica ``bkp_local_v3.db`` a PostgreSQL en UNA
                             transaccion (todo o nada).

Variables:

* ``REHEARSAL_DB``  nombre de una base clonada. Si esta definida, se opera
                    sobre ella en vez de la base real (ensayo sin riesgo).
"""

from __future__ import annotations

import json
import os
import secrets
import string
import smtplib
import sqlite3
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage

import psycopg
from psycopg.types.json import Jsonb

BATCH = 1000


# ----------------------------------------------------------------- conexion
def dsn() -> str:
    from central_api.db import normalizar_dsn
    from central_api.settings import get_settings

    url = normalizar_dsn(get_settings().database_url).replace("+psycopg", "", 1)
    rehearsal = os.environ.get("REHEARSAL_DB")
    if rehearsal:
        base, _, _ = url.rpartition("/")
        url = f"{base}/{rehearsal}"
    return url


# --------------------------------------------------------------------- dump
_SQLITE_TYPES = {
    "integer": "INTEGER", "smallint": "INTEGER", "bigint": "INTEGER",
    "boolean": "INTEGER", "real": "REAL", "double precision": "REAL",
    "numeric": "REAL", "bytea": "BLOB",
}


def _sqlite_value(value):
    if value is None or isinstance(value, (int, float, bytes)):
        return int(value) if isinstance(value, bool) else value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def do_dump(out_path: str) -> None:
    if os.path.exists(out_path):
        os.remove(out_path)
    out = sqlite3.connect(out_path)
    summary = {}
    with psycopg.connect(dsn()) as pg:
        tablas = [
            (r[0], r[1])
            for r in pg.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND "
                "(table_schema = 'public' OR (table_schema = 'legacy' AND table_name = 'user_id_map')) "
                "ORDER BY 1, 2"
            )
        ]
        for schema, tabla in tablas:
            cols = pg.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, tabla),
            ).fetchall()
            nombre = tabla if schema == "public" else f"{schema}_{tabla}"
            ddl = ", ".join(f'"{c}" {_SQLITE_TYPES.get(t, "TEXT")}' for c, t in cols)
            out.execute(f'CREATE TABLE "{nombre}" ({ddl})')
            qs = ",".join("?" * len(cols))
            n = 0
            with pg.cursor(name=f"dump_{nombre}"[:60]) as cur:
                cur.itersize = 2000
                cur.execute(f'SELECT * FROM "{schema}"."{tabla}"')
                while True:
                    filas = cur.fetchmany(2000)
                    if not filas:
                        break
                    out.executemany(
                        f'INSERT INTO "{nombre}" VALUES ({qs})',
                        [[_sqlite_value(v) for v in f] for f in filas],
                    )
                    n += len(filas)
            summary[nombre] = n
    out.commit()
    out.close()
    print(json.dumps({"dump": out_path, "tablas": summary}, indent=1))


# --------------------------------------------------------------------- load
def _rows(con: sqlite3.Connection, sql: str, *args) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def _jsonb(text: str | None):
    return Jsonb(json.loads(text)) if text else None


def send_mail(settings, destinatario: str, clave: str) -> tuple[bool, str]:
    if not (settings.smtp_server and settings.smtp_user and settings.smtp_password):
        return False, "smtp_no_configurado"
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = destinatario
    msg["Subject"] = "Mr Bot: nueva API key"
    msg.set_content(
        "Hola,\n\n"
        "Migramos Mr Bot a una nueva version de la API. Tu API key anterior "
        "no puede trasladarse (se guardaba de forma irreversible), por eso "
        "generamos una nueva. Desde ahora debes usar esta:\n\n"
        f"{clave}\n\n"
        "Tu usuario, tu estado y tu cupo de consultas se conservaron.\n"
        "Guardala en un lugar seguro y no la compartas.\n"
    )
    try:
        with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        return False, f"smtp_error:{type(exc).__name__}"
    return True, "enviado"


def do_load(staging: str, report_path: str, send_emails: bool, dry_run: bool) -> int:
    from central_api.api.dependencies import _selector_y_secreto
    from central_api.billing.entitlements import month_bounds
    from central_api.models.base import new_uuid7
    from central_api.security.api_key_vault import encrypt_api_key
    from central_api.security.api_keys import fingerprint_secret
    from central_api.settings import get_settings

    settings = get_settings()
    hmac_secret = settings.api_key_hmac_secret
    if not hmac_secret:
        raise SystemExit("API_KEY_HMAC_SECRET no configurado")

    st = sqlite3.connect(staging)
    users = _rows(st, "SELECT * FROM users")
    accounts = {r["user_id"]: r for r in _rows(st, "SELECT * FROM billing_accounts")}
    periods = {r["user_id"]: r for r in _rows(st, "SELECT * FROM billing_periods")}
    pending = _rows(st, "SELECT * FROM api_keys_pending")
    delete_ids = [r["id"] for r in _rows(st, "SELECT id FROM delete_users")]
    n_jobs_expected = st.execute("SELECT count(*) FROM jobs").fetchone()[0]

    report: dict = {
        "rehearsal_db": os.environ.get("REHEARSAL_DB"),
        "dry_run": dry_run,
        "usuarios_creados": 0, "usuarios_existentes": [], "jobs_insertados": 0,
        "borrados": {}, "claves_conservadas": 0, "claves_regeneradas": [],
        "correos": [],
    }

    with psycopg.connect(dsn()) as pg:
        with pg.transaction():
            cur = pg.cursor()
            # ---------------------------------------------------- borrado
            if delete_ids:
                ids = delete_ids
                vivos = cur.execute(
                    "SELECT count(*) FROM jobs WHERE user_id = ANY(%s::uuid[]) "
                    "AND status IN ('PENDIENTE','ASIGNADO','CORRIENDO')", (ids,)
                ).fetchone()[0]
                if vivos:
                    raise SystemExit(f"abortado: {vivos} jobs vivos de usuarios a borrar")
                for t in ("credit_ledger", "payments", "subscriptions", "upload_tickets",
                          "billing_usage", "billing_account_periods"):
                    n = cur.execute(f"SELECT count(*) FROM {t} WHERE user_id = ANY(%s::uuid[])", (ids,)).fetchone()[0]
                    if n:
                        raise SystemExit(f"abortado: {t} tiene {n} filas de usuarios a borrar")
                dele = report["borrados"]
                dele["api_idempotency"] = cur.execute("DELETE FROM api_idempotency WHERE user_id = ANY(%s::uuid[])", (ids,)).rowcount
                dele["jobs"] = cur.execute("DELETE FROM jobs WHERE user_id = ANY(%s::uuid[])", (ids,)).rowcount
                dele["api_keys"] = cur.execute("DELETE FROM api_keys WHERE user_id = ANY(%s::uuid[])", (ids,)).rowcount
                dele["billing_accounts"] = cur.execute("DELETE FROM billing_accounts WHERE user_id = ANY(%s::uuid[])", (ids,)).rowcount
                dele["legacy_user_id_map"] = cur.execute("DELETE FROM legacy.user_id_map WHERE user_id = ANY(%s::uuid[])", (ids,)).rowcount
                dele["users"] = cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (ids,)).rowcount
                if dele["users"] != len(ids):
                    raise SystemExit(f"abortado: se esperaba borrar {len(ids)} usuarios y fueron {dele['users']}")
                # Los bot_jobs_* caen por ON DELETE CASCADE; verificar que no queden.
                huerfanas = 0
                for (t,) in cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'bot_jobs_%'").fetchall():
                    huerfanas += cur.execute(f"SELECT count(*) FROM {t} WHERE user_id = ANY(%s::uuid[])", (ids,)).fetchone()[0]
                if huerfanas:
                    raise SystemExit(f"abortado: {huerfanas} filas bot_jobs_* huerfanas")

            # ------------------------------------------------- usuarios
            umap: dict[str, str] = {}  # id staging -> id central
            creados: set[str] = set()
            for u in users:
                fila = cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (u["email"],)).fetchone()
                if fila:
                    umap[u["id"]] = str(fila[0])
                    report["usuarios_existentes"].append(u["email"])
                    continue
                cur.execute("INSERT INTO users (id, email, habilitado) VALUES (%s, %s, %s)",
                            (u["id"], u["email"], bool(u["habilitado"])))
                a = accounts[u["id"]]
                cur.execute(
                    "INSERT INTO billing_accounts (user_id, account_type, max_requests, requests_consumed) "
                    "VALUES (%s, %s, %s, %s)",
                    (u["id"], a["account_type"], a["max_requests"], a["requests_consumed"]),
                )
                p = periods.get(u["id"])
                if p:
                    inicio, fin = month_bounds()
                    cur.execute(
                        "INSERT INTO billing_account_periods (id, user_id, plan_code, account_type, "
                        "starts_at, ends_at, status, included_requests, requests_consumed) "
                        "VALUES (%s, %s, NULL, 'NORMAL', %s, %s, 'ABIERTO', %s, %s)",
                        (str(new_uuid7()), u["id"], inicio, fin,
                         p["included_requests"], p["requests_consumed"]),
                    )
                umap[u["id"]] = u["id"]
                creados.add(u["id"])
            report["usuarios_creados"] = len(creados)

            # ------------------------------------------------- API keys
            por_enviar: list[tuple[str, str]] = []
            for k in pending:
                if k["user_id"] not in creados:
                    continue
                valor = k["key_plain"]
                if k["kind"] == "regenerada":
                    # Se genera aca y solo viaja por correo: nunca toca un archivo.
                    alfabeto = string.ascii_letters + string.digits
                    valor = "".join(secrets.choice(alfabeto) for _ in range(48))
                selector, secreto = _selector_y_secreto(valor)
                verificador = fingerprint_secret(hmac_secret, secreto)
                libre = cur.execute(
                    "SELECT 1 FROM api_keys WHERE revoked_at IS NULL AND (key_prefix = %s OR verifier_hmac = %s)",
                    (selector, verificador),
                ).fetchone()
                if libre:
                    raise SystemExit(f"abortado: clave en conflicto para {k['email']}")
                cur.execute(
                    "INSERT INTO api_keys (id, user_id, key_prefix, verifier_hmac, encrypted_value, scopes, label) "
                    "VALUES (%s, %s, %s, %s, %s, '[]'::jsonb, 'migracion-v1v2')",
                    (str(new_uuid7()), k["user_id"], selector, verificador, encrypt_api_key(valor)),
                )
                if k["kind"] == "regenerada":
                    report["claves_regeneradas"].append(k["email"])
                    if k["send_email"]:
                        por_enviar.append((k["email"], valor))
                else:
                    report["claves_conservadas"] += 1

            # ----------------------------------------------------- jobs
            sql_job = (
                "INSERT INTO jobs (id, user_id, bot, operation, status, result, request_payload, priority, "
                "created_at, assigned_at, started_at, finished_at, attempts, max_attempts, app_version, "
                "protocol_version, error_code, error_message, credential_metadata) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,100,%s,%s,%s,%s,1,3,'legacy-v1v2',1,%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING"
            )
            sql_res = (
                "INSERT INTO job_results (job_id, attempt, result, payload, summary, received_at) "
                "VALUES (%s, 1, %s, %s, %s, %s) ON CONFLICT (job_id) DO NOTHING"
            )
            st.row_factory = sqlite3.Row
            filas = st.execute("SELECT * FROM jobs ORDER BY created_at, id")
            while True:
                lote = filas.fetchmany(BATCH)
                if not lote:
                    break
                jobs, ress = [], []
                for j in lote:
                    uid = umap[j["user_id"]]
                    jobs.append((
                        j["id"], uid, j["bot"], j["operation"], j["status"], j["result"],
                        _jsonb(j["request_payload"]), j["created_at"], j["created_at"],
                        j["created_at"], j["created_at"], j["error_code"], j["error_message"],
                        _jsonb(j["credential_metadata"]),
                    ))
                    ress.append((
                        j["id"], j["result"], _jsonb(j["response_payload"]) or Jsonb({}),
                        Jsonb({"legacy": True, "origen": f"{j['source_db']}:{j['source_table']}:{j['source_id']}"}),
                        j["created_at"],
                    ))
                cur.executemany(sql_job, jobs)
                cur.executemany(sql_res, ress)
            total = cur.execute(
                "SELECT count(*) FROM jobs WHERE app_version = 'legacy-v1v2'").fetchone()[0]
            report["jobs_insertados"] = total
            if total < n_jobs_expected:
                raise SystemExit(f"abortado: jobs legacy {total} < esperados {n_jobs_expected}")

            # ---------------------------------------------- consistencia
            bad = cur.execute(
                "SELECT count(*) FROM jobs j WHERE app_version='legacy-v1v2' AND NOT EXISTS "
                "(SELECT 1 FROM job_results r WHERE r.job_id = j.id)").fetchone()[0]
            if bad:
                raise SystemExit(f"abortado: {bad} jobs sin resultado")
            report["usuarios_total_central"] = cur.execute("SELECT count(*) FROM users").fetchone()[0]
            report["cuentas_por_tipo"] = dict(cur.execute(
                "SELECT account_type, count(*) FROM billing_accounts GROUP BY 1").fetchall())
            if dry_run:
                raise psycopg.Rollback()

    # El commit ya ocurrio: recien ahora se envian los correos.
    if send_emails and not dry_run:
        for email, clave in por_enviar:
            ok, detalle = send_mail(settings, email, clave)
            report["correos"].append({"email": email, "ok": ok, "detalle": detalle})
            time.sleep(0.5)
    else:
        report["correos_pendientes"] = [e for e, _ in por_enviar]

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in report.items()}, indent=1, default=str))
    return 0


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "dump":
        do_dump(sys.argv[2])
        return 0
    if modo == "load":
        return do_load(
            sys.argv[2], sys.argv[3],
            send_emails="--send-emails" in sys.argv,
            dry_run="--dry-run" in sys.argv,
        )
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
