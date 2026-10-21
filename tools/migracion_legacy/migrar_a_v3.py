#!/usr/bin/env python3
"""Migracion de las bases V1/V2 (SQLite) a la central V3 (PostgreSQL).

Fuentes (carpeta ``DBs``) y lineamientos:

1. ``sql_app_api2.db``     solo el usuario ``alvitea@ltyasoc.com.ar`` y sus logs.
2. ``sql_app_testing.db``  todos los usuarios y logs. Cuenta TESTING. Si un
                           usuario esta deshabilitado se mantiene asi, salvo
                           que en otra base figure habilitado.
3. ``sql_app_mrbot1.db``   todos los usuarios, con estado, cupo mensual y logs.

Un mismo mail en varias bases es un unico usuario: gana la base de mayor
prioridad (mrbot1, api2, testing) para tipo de cuenta y cupo, habilitado si lo
esta en alguna, y se migran los logs de todas las bases.

Cada fila ``consulta_*_logs`` (``consulta_logs`` y ``consulta_mc_logs`` son
Mis Comprobantes) pasa a ser un ``job`` terminal de V3 (``app_version =
'legacy-v1v2'``) con su ``job_results``. Los triggers de V3 pueblan solos las
tablas ``bot_jobs_*`` y las vistas ``consulta_*_logs`` siguen funcionando.
Nunca se migran claves fiscales ni URLs de almacenamiento.

API keys: las de V2 con prefijo ``hmac-sha256$`` son irreversibles. Para
usuarios habilitados se genera una clave nueva (dentro de la central) y se
envia por mail. Las claves en claro de V1 se conservan.

Subcomandos::

    stage            arma DBs/bkp_local_v3.db desde las 3 bases
    dump-central     DBs/central-v3-dump.db (o --out) desde la central
    rehearsal        ensaya la carga sobre un clon temporal de la base central
    apply            backup pg_dump + carga real (+ --send-emails)
    token            cambia ADMIN_TOKEN de la central y la recrea
    all              stage, dump, ensayo, apply, dump posterior y token
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DBS = HERE.parents[2] / "DBs"
LOADER = HERE / "loader_central.py"

HOST = "conciliabot2"
CENTRAL_CTR = "mrbot-v3-central-api-1"
PG_CTR = "mrbot-v3-postgres-1"
PG_USER, PG_DB = "mrbot_app_v3", "mrbot_v3"
REMOTE_COMPOSE = "/home/abp/api-bots-mrbot-v3/infra/compose"
NEW_ADMIN_TOKEN = "pWVxsuqrA5oXag6mMUimAI3GY4dhQyOZmc0QgNtT"

DELETE_USERS = """
f345fe9c-a721-4122-802b-6f84aea40f63 30b4920b-2944-46c9-96d4-4594001ecf57
de244bf9-0c67-4ea6-82dd-759ba85d2f7d f0edda4d-3f0d-46d5-aac3-64e8a18e53cd
2f03bbe5-b77d-47c4-aa85-998951838e6f f6a54836-732f-4df9-aa0d-fff294228c24
1f4e773f-b012-4970-a175-2fe67e392815 5ab3dde3-b3a5-4fb3-bef0-b6dc8ec16e19
ab42092d-f6b6-4074-b590-2603a811fab1 89b49470-2bd3-4e1c-9217-c0d65f94cf37
a57999e8-4b37-4e1e-854d-2aac762f88b4 30a02a93-e176-4d0c-b677-8a3c5dde8c5b
2b822c68-8dd5-4842-9717-23f883abb46a
""".split()

HASH_PREFIX = "hmac-sha256$"
ART = timezone(timedelta(hours=-3))
NS = uuid.UUID("6f1f4c3e-0a53-4b8c-9d57-3d6b2a0c9e11")

# Prioridad de bases: la primera que contiene al usuario define tipo y cupo.
SOURCES = (
    ("mrbot1", "sql_app_mrbot1.db", None),
    ("api2", "sql_app_api2.db", {"alvitea@ltyasoc.com.ar"}),
    ("testing", "sql_app_testing.db", None),
)

# tabla legacy -> (bot V3, operacion V3). Todas existen en bot_operations.
TABLES = {
    "consulta_logs": ("mis_comprobantes", "consultar"),
    "consulta_mc_logs": ("mis_comprobantes", "consultar"),
    "consulta_ccma_logs": ("ccma", "consultar"),
    "consulta_ccma_compensacion_logs": ("ccma_compensacion", "compensar"),
    "consulta_siper_logs": ("siper", "consultar"),
    "consulta_rcel_logs": ("rcel", "descargar"),
    "consulta_sct_logs": ("sct", "consultar"),
    "consulta_sct_compensaciones_logs": ("compensaciones", "consultar"),
    "consulta_vep_logs": ("vep_archivo", "generar"),
    "consulta_vep_ccma_logs": ("vep_ccma", "generar"),
    "consulta_aportes_en_linea_logs": ("aportes_en_linea", "descargar"),
    "consulta_sifere_logs": ("sifere", "consultar"),
    "consulta_declaracion_en_linea_logs": ("declaracion_en_linea", "consultar"),
    "consulta_mis_facilidades_logs": ("mis_facilidades", "consultar"),
    "consulta_mis_retenciones_logs": ("mis_retenciones", "consultar"),
    "consulta_hacienda_logs": ("hacienda", "consultar"),
    "consulta_liquidacion_granos_logs": ("liquidacion_granos", "consultar"),
    "consulta_pago_devoluciones_logs": ("pago_devoluciones", "consultar"),
    "consulta_portal_iva_logs": ("portal_iva", "descargar"),
    "consulta_portal_iva_carga_logs": ("carga_portal_iva", "cargar"),
    "consulta_srt_logs": ("srt", "consultar_alicuotas"),
    "consulta_retenciones_percepciones_iibb_misiones_logs": ("retper_iibb_misiones", "consultar"),
    "consulta_retenciones_percepciones_iibb_agip_logs": ("retper_iibb_agip", "consultar"),
    "consulta_retenciones_percepciones_iibb_arba_logs": ("arba", "descargar"),
    "consulta_certificado_mipyme_logs": ("certificado_mipyme", "descargar"),
    "consulta_moa_logs": ("moa", "consultar"),
    "consulta_libros_iva_logs": ("libros_portal_iva", "descargar"),
    "consulta_facturometro_logs": ("facturometro", "consultar"),
    "consulta_controladores_fiscales_logs": ("controladores_fiscales", "presentar"),
}

# Columnas que describen la respuesta (el resto describe la solicitud).
RESPONSE_COLS = {
    "response_header", "response_mce", "response_mcr", "response_ccma",
    "response_ccma_compensacion", "opciones_encontradas", "resultados",
    "resultados_ventas", "resultados_compras", "resultados_aperturas",
    "importaciones", "errores_por_seccion", "periodos_descargados",
    "periodos_error", "response_volante_data", "archivos_recibidos",
    "archivos", "response_total_seleccionado", "response_nombre_archivo",
    "response_nombre_qr", "monto_facturado", "tope_facturacion", "categoria",
}
BOTH = {"archivo_nombre", "despachos", "cantidad_archivos"}
NEVER = {
    "id", "user_id", "timestamp", "status", "error_message", "clave",
    "clave_representante", "cuit_representante", "cuit_representado",
    "request_proxy", "request_excel", "request_csv", "request_pdf",
    "archivo_path", "response_ruta_archivo",
}
RENAME = {"desde": "fecha_desde", "hasta": "fecha_hasta"}


# ---------------------------------------------------------------- utilidades
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def scrub(value):
    """Quita URLs y claves de cualquier estructura JSON."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if "url" not in k.lower() and "clave" not in k.lower()
                and "password" not in k.lower() and "token" not in k.lower()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and "://" in value:
        return "[REDACTED_URL]"
    return value


_ERR_SECRET = re.compile(r"(clave|password|passwd|token|secret|api[_ -]?key)\s*[:=]\s*\S+", re.I)
_ERR_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")


def scrub_text(text: str | None) -> str | None:
    if not text:
        return None
    return _ERR_URL.sub("[REDACTED_URL]", _ERR_SECRET.sub(r"\1=[REDACTED_SECRET]", text))[:2000]


def utc_iso(text: str | None) -> str | None:
    if not text:
        return None
    try:
        d = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def same_month_art(reset: str | None) -> bool:
    iso = utc_iso(reset)
    if not iso:
        return False
    d = datetime.fromisoformat(iso).astimezone(ART)
    n = datetime.now(ART)
    return (d.year, d.month) == (n.year, n.month)


def sh(cmd: str, *, stdin=None, stdout=None, check=True, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, stdin=stdin, stdout=stdout, check=check,
        capture_output=capture, text=capture,
    )


def ssh(remote: str, **kw) -> subprocess.CompletedProcess:
    return sh(f"ssh -o BatchMode=yes {HOST} {shlex.quote(remote)}", **kw)


# -------------------------------------------------------------------- stage
def read_source(dbs: Path, key: str, filename: str, only: set[str] | None):
    con = sqlite3.connect((dbs / filename).as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    users = {}
    for r in con.execute("SELECT * FROM users ORDER BY id"):
        mail = (r["mail"] or "").strip().lower()
        if mail and (only is None or mail in only):
            users[r["id"]] = {**dict(r), "mail": mail}
    return con, users


def stage(dbs: Path, out: Path) -> dict:
    log(f"armando {out.name}")
    srcs = {}
    for key, fname, only in SOURCES:
        srcs[key] = read_source(dbs, key, fname, only)

    # ---- usuarios fusionados por mail
    merged: dict[str, dict] = {}
    for key, _f, _o in SOURCES:
        for u in srcs[key][1].values():
            m = merged.setdefault(u["mail"], {"recs": []})
            m["recs"].append((key, u))

    st_users, st_acc, st_per, st_keys = [], [], [], []
    stats = {"regeneradas": 0, "conservadas": 0, "sin_clave_deshabilitado": 0}
    for mail, m in sorted(merged.items()):
        recs = m["recs"]
        key0, prim = recs[0]
        habilitado = any(bool(u["habilitado"]) for _k, u in recs)
        uid = str(uuid.uuid5(NS, mail))
        st_users.append((uid, mail, int(habilitado), ",".join(k for k, _ in recs)))

        solo_testing = all(k == "testing" for k, _ in recs)
        vigente = same_month_art(prim["fecha_ultimo_reset"])
        consumo = int(prim["consultas_realizadas"] or 0) if vigente else 0
        if solo_testing:
            st_acc.append((uid, "TESTING", None, consumo))
        else:
            cupo = max(1, min(int(prim["maximas_consultas_mensuales"] or 1), 10_000_000))
            st_acc.append((uid, "NORMAL", cupo, 0))
            if vigente:
                st_per.append((uid, cupo, consumo))

        # La clave sale del registro habilitado (el que realmente se usaba).
        en_uso = next((u for _k, u in recs if u["habilitado"]), prim)
        clave = (en_uso["api_key"] or "").strip()
        irreversible = (not clave) or clave.startswith(HASH_PREFIX) \
            or len(clave) < 3 or any(c.isspace() for c in clave)
        if habilitado and irreversible:
            st_keys.append((uid, mail, "regenerada", None, 1))
            stats["regeneradas"] += 1
        elif not irreversible:
            st_keys.append((uid, mail, "conservada", clave, 0))
            stats["conservadas"] += 1
        else:
            stats["sin_clave_deshabilitado"] += 1
    uid_by_mail = {u[1]: u[0] for u in st_users}

    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, habilitado INTEGER, origen TEXT);
        CREATE TABLE billing_accounts (user_id TEXT PRIMARY KEY, account_type TEXT, max_requests INTEGER, requests_consumed INTEGER);
        CREATE TABLE billing_periods (user_id TEXT PRIMARY KEY, included_requests INTEGER, requests_consumed INTEGER);
        CREATE TABLE api_keys_pending (user_id TEXT, email TEXT, kind TEXT, key_plain TEXT, send_email INTEGER);
        CREATE TABLE delete_users (id TEXT PRIMARY KEY);
        CREATE TABLE jobs (id TEXT PRIMARY KEY, user_id TEXT, bot TEXT, operation TEXT, status TEXT, result TEXT,
            request_payload TEXT, response_payload TEXT, credential_metadata TEXT, created_at TEXT,
            error_code TEXT, error_message TEXT, source_db TEXT, source_table TEXT, source_id INTEGER);
    """)
    db.executemany("INSERT INTO users VALUES (?,?,?,?)", st_users)
    db.executemany("INSERT INTO billing_accounts VALUES (?,?,?,?)", st_acc)
    db.executemany("INSERT INTO billing_periods VALUES (?,?,?)", st_per)
    db.executemany("INSERT INTO api_keys_pending VALUES (?,?,?,?,?)", st_keys)
    db.executemany("INSERT INTO delete_users VALUES (?)", [(i,) for i in DELETE_USERS])

    # ---- logs -> jobs
    por_tabla: dict[str, int] = {}
    for key, _f, _o in SOURCES:
        con, users = srcs[key]
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, (bot, op) in TABLES.items():
            if table not in names:
                continue
            info = {r["name"]: (r["type"] or "").upper() for r in con.execute(f'PRAGMA table_info("{table}")')}
            lote = []
            for r in con.execute(f'SELECT * FROM "{table}"'):
                u = users.get(r["user_id"])
                if u is None:
                    continue
                lote.append(build_job(key, table, bot, op, dict(r), info, uid_by_mail[u["mail"]]))
            db.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lote)
            por_tabla[f"{key}:{table}"] = len(lote)
    db.commit()
    total = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    db.close()
    res = {"usuarios": len(st_users), "jobs": total, **stats}
    log(f"stage ok: {res}")
    return {**res, "por_tabla": por_tabla}


def conv(value, typ: str):
    if value is None:
        return None
    if typ == "JSON":
        try:
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            return value
    if typ == "BOOLEAN":
        return bool(value)
    return value


def build_job(src: str, table: str, bot: str, op: str, row: dict, info: dict, uid: str) -> tuple:
    req: dict = {}
    resp: dict = {}
    for col, raw in row.items():
        if col in NEVER or "minio" in col or col.startswith("clave"):
            continue
        val = conv(raw, info.get(col, ""))
        if val is None:
            continue
        if col in RESPONSE_COLS:
            resp[col] = scrub(val)
        else:
            req[RENAME.get(col, col)] = scrub(val)
            if col in BOTH:
                resp[col] = scrub(val)
    if "nombre_representado" in req:
        req["representado_nombre"] = req["nombre_representado"]
    rep = row.get("cuit_representado")
    if rep:
        req["representado_cuit"] = rep
    if table.endswith("arba_logs") and row.get("cuit"):
        req["representado_cuit"] = row["cuit"]
    if table == "consulta_srt_logs" and row.get("cuits_consultados"):
        req["cuits_consulta"] = [c.strip() for c in str(row["cuits_consultados"]).split(",") if c.strip()]
    header = resp.get("response_header")
    if bot == "mis_comprobantes" and isinstance(header, dict):
        desc = header.get("Descarga") or {}
        for k in ("emitidos", "recibidos"):
            if k in desc:
                req[k] = bool(desc[k])
    ctx = {}
    rep_user = row.get("cuit_representante") or row.get("usuario") or row.get("cuit")
    if rep_user:
        ctx["cuit_representante"] = str(rep_user)
    if rep:
        ctx["representado_cuit"] = str(rep)
    ok = (row.get("status") or "").lower() == "success"
    err = None if ok else scrub_text(row.get("error_message")) or "Error registrado en la API anterior"
    if err:
        resp["error_message"] = err
    ts = utc_iso(row.get("timestamp")) or "1970-01-01T00:00:00+00:00"
    jid = str(uuid.uuid5(NS, f"{src}:{table}:{row['id']}"))
    return (
        jid, uid, bot, op, "COMPLETO" if ok else "FALLIDO", "OK" if ok else "ERROR",
        json.dumps(req, ensure_ascii=False), json.dumps(resp, ensure_ascii=False),
        json.dumps({"context": ctx, "legacy": True}), ts,
        None if ok else "legacy_error", err, src, table, row["id"],
    )


# ------------------------------------------------------------------ central
#: El dump lleva las credenciales cifradas Y en claro (depuración); ``--sin-claves-en-claro`` lo evita.
DUMP_FLAGS = "--sin-claves-en-claro" if "--sin-claves-en-claro" in sys.argv else ""


def dump_central(out: Path) -> None:
    log(f"volcando central -> {out.name}")
    tmp = "/tmp/central-dump.db"
    with open(LOADER, "rb") as fh:
        ssh(f"docker exec -i {CENTRAL_CTR} python - dump {tmp} {DUMP_FLAGS}", stdin=fh)
    with open(out, "wb") as fh:
        ssh(f"docker exec {CENTRAL_CTR} cat {tmp}", stdout=fh)
    ssh(f"docker exec {CENTRAL_CTR} rm -f {tmp}")
    con = sqlite3.connect(out)
    n = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
         for t in ("users", "jobs", "api_keys", "billing_accounts")}
    con.close()
    log(f"dump ok ({out.stat().st_size // 1024} KB): {n}")


def push_stage(bkp: Path) -> None:
    with open(bkp, "rb") as fh:
        ssh(f"docker exec -i {CENTRAL_CTR} sh -c 'cat > /tmp/bkp_local_v3.db'", stdin=fh)


def run_load(report_local: Path, *, rehearsal_db: str | None, dry_run: bool, send: bool) -> dict:
    flags = ("--dry-run " if dry_run else "") + ("--send-emails" if send else "")
    env = f"-e REHEARSAL_DB={rehearsal_db} " if rehearsal_db else ""
    with open(LOADER, "rb") as fh:
        r = ssh(
            f"docker exec -i {env}{CENTRAL_CTR} python - load /tmp/bkp_local_v3.db /tmp/migracion-report.json {flags}",
            stdin=fh, capture=True, check=False,
        )
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"la carga fallo (rc={r.returncode}); la transaccion se revirtio")
    with open(report_local, "wb") as fh:
        ssh(f"docker exec {CENTRAL_CTR} cat /tmp/migracion-report.json", stdout=fh)
    return json.loads(report_local.read_text())


def psql_admin(sql: str, db: str = "postgres") -> None:
    ssh(f"docker exec {PG_CTR} psql -U {PG_USER} -d {db} -v ON_ERROR_STOP=1 -c {shlex.quote(sql)}")


def do_rehearsal(dbs: Path) -> dict:
    name = "mrbot_v3_rehearsal"
    log(f"ensayo sobre clon {name}")
    psql_admin(f'DROP DATABASE IF EXISTS "{name}"')
    psql_admin(f'CREATE DATABASE "{name}"')
    try:
        ssh(f"docker exec {PG_CTR} sh -c 'pg_dump -U {PG_USER} {PG_DB} | psql -q -U {PG_USER} -d {name}' >/dev/null")
        push_stage(dbs / "bkp_local_v3.db")
        rep = run_load(dbs / "rehearsal-report.json", rehearsal_db=name, dry_run=False, send=False)
    finally:
        psql_admin(f'DROP DATABASE IF EXISTS "{name}"')
    return rep


def do_apply(dbs: Path, send: bool) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log("backup pg_dump previo en el servidor")
    ssh(f"mkdir -p ~/backups && docker exec {PG_CTR} pg_dump -U {PG_USER} -Fc {PG_DB} > ~/backups/mrbot_v3-pre-migracion-{ts}.dump")
    push_stage(dbs / "bkp_local_v3.db")
    rep = run_load(dbs / "migracion-report.json", rehearsal_db=None, dry_run=False, send=send)
    ssh(f"docker exec {CENTRAL_CTR} rm -f /tmp/bkp_local_v3.db /tmp/migracion-report.json")
    return rep


def set_admin_token(token: str) -> None:
    log("cambiando ADMIN_TOKEN en la central")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    env = f"{REMOTE_COMPOSE}/.env"
    ssh(f"cp {env} {env}.bak-{ts} && sed -i 's|^ADMIN_TOKEN=.*|ADMIN_TOKEN={token}|' {env} && grep -c '^ADMIN_TOKEN=' {env}")
    ssh(f"cd {REMOTE_COMPOSE} && docker compose up -d --no-deps --force-recreate central-api")
    import time
    for _ in range(30):
        time.sleep(2)
        r = ssh("curl -s -o /dev/null -w '%{http_code}' localhost:8000/health", capture=True, check=False)
        if r.stdout.strip() == "200":
            break
    else:
        raise SystemExit("la central no volvio a responder /health")
    base = "curl -s -o /dev/null -w '%{http_code}' 'localhost:8000/admin/users?limit=1'"
    bueno = ssh(f"{base} -H 'Authorization: Bearer {token}'", capture=True, check=False).stdout.strip()
    malo = ssh(f"{base} -H 'Authorization: Bearer token-incorrecto'", capture=True, check=False).stdout.strip()
    log(f"token nuevo -> HTTP {bueno} (esperado 200), token invalido -> HTTP {malo} (esperado 403)")
    if bueno != "200" or malo != "403":
        raise SystemExit("verificacion del ADMIN_TOKEN fallida")


# ---------------------------------------------------------------------- cli
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["stage", "dump-central", "rehearsal", "apply", "token", "all"])
    p.add_argument("--dbs-dir", type=Path, default=DEFAULT_DBS)
    p.add_argument("--out", type=Path, help="destino de dump-central")
    p.add_argument("--send-emails", action="store_true", help="enviar las claves nuevas por mail (apply/all)")
    p.add_argument("--sin-claves-en-claro", action="store_true", help="dump-central: no agrega credential_plaintext/api_key_plaintext")
    p.add_argument("--yes", action="store_true", help="confirma operaciones sobre la central real")
    a = p.parse_args()
    d: Path = a.dbs_dir

    if a.cmd in ("stage", "all"):
        s = stage(d, d / "bkp_local_v3.db")
        (d / "stage-report.json").write_text(json.dumps(s, indent=1))
    if a.cmd in ("dump-central", "all"):
        dump_central(a.out or d / "central-v3-dump.db")
    if a.cmd in ("rehearsal", "all"):
        do_rehearsal(d)
    if a.cmd in ("apply", "all"):
        if not a.yes:
            raise SystemExit("apply modifica la central real: repetir con --yes")
        do_apply(d, a.send_emails)
        dump_central(d / "central-v3-dump-after-migration.db")
    if a.cmd in ("token", "all"):
        if not a.yes:
            raise SystemExit("token modifica la central real: repetir con --yes")
        set_admin_token(NEW_ADMIN_TOKEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
