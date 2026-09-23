"""ETL de usuarios V2 (SQLite) a V3 (`public.users` + `legacy.user_id_map`).

Unicos datos legacy que se convierten para operar V3 (plan 07 3.4):

  V2 `users.id` (entero)      -> nuevo `public.users.id` UUIDv4 (nunca el entero)
  V2 `mail`                   -> `public.users.email` normalizado (minusculas)
  V2 `habilitado`             -> `public.users.habilitado`
  V2 `api_key`                -> NO se importa (DECISION CERRADA: reemision
                                 total; ver nota de decision mas abajo).
  V2 `created_at/updated_at/fecha_ultimo_reset` -> NO se copian a `users`
                                 (R17, decision consciente del plan).
  Cada fila migrada           -> `legacy.user_id_map` con checksum SHA-256
                                 del canon JSON de la fila origen.

DECISION CERRADA (cutover, estrategia de API keys): reemision total, sin
compat `legacy-hmac`. Motivos verificados contra el codigo real:

  1. La central V3 solo acepta `mbk_<key_id>_<secreto>` y verifica con
     `HMAC-SHA-256(server_secret, secreto)` en hexadecimal sin prefijo
     (`central_api/security/api_keys.py`, `central_api/security/__init__.py`).
     `parse_api_key` rechaza cualquier otro formato con 401, asi que una
     fila `legacy-hmac$...` seria una fila muerta que jamas autentica.
  2. El DDL real (`alembic/versions/0001_identity.py`) no tiene columna
     `hash_version` para doble verificacion (plan 07 3.5 la suponia); no hay
     donde marcar una clave de transicion sin cambiar otro scope.
  3. `api_keys.key_prefix` es UNIQUE y el ETL anterior repetia `'v2'` para
     cada usuario: la segunda fila migrada violaba la restriccion.
  4. Censo del SQLite real (solo conteos, sin secretos): los 8 valores V2
     de `api_key` son cadenas de 76 caracteres SIN el prefijo `hmac-sha256$`
     (0/8 digests, 0/8 `[REDACTED]`, 0/8 vacias). Caen en la rama legado de
     `verify_api_key` de V2 (`app/utils/api_keys.py`): son material
     equivalente a secreto, no verificadores importables. Copiarlos a
     `verifier_hmac` guardaria material secreto donde debe haber solo un
     verificador calculado sobre un secreto ya rotado.

Por tanto este ETL no crea filas en `public.api_keys`. Cada usuario migrado
queda con `claves pendientes de reemision` (campo `claves_reemitir` del
archivo de excepciones) y la primera clave V3 se emite por el flujo normal
de la central (panel o canal verificado) segun el runbook de cutover.

Re-ejecutable: UPSERT por `legacy_user_id` en el mapa; si la fila ya existe
con el mismo checksum, se omite; si existe con distinto checksum, se aborta
(el origen cambio tras el freeze: incidente, no se maquilla). Las inserciones
en `public` usan el `v3_user_id` ya asignado (idempotentes por PK).

Auditable: archivo de excepciones JSON sin secretos (motivo + columnas no
sensibles). Una fila invalida no se arregla en silencio: se excluye con
motivo y bloquea el go/no-go si el cliente estaba habilitado.

Sobre sellado: `api_key` y `mail` jamas se imprimen; el archivo de
excepciones solo lleva `legacy_user_id`, motivo y checksum. Comparacion de
verificadores con `hmac.compare_digest` donde aplique.

Regla W-1: corre con el DSN de la central; el worker no participa.

Uso:
    python3 infra/legacy/etl_users.py \
        --source ../api-bots-mrbot-v2/data/sql_app.db \
        --dsn "$DATABASE_URL" \
        --exceptions /tmp/etl-users-exceptions.json
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone

PROTOCOL_VERSION: int = 1
"""Version del sobre de migracion (entero, 1)."""

ETL_VERSION = "users-etl/1.0"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def canonical_row(row: dict) -> str:
    """Serializa una fila origen en JSON canonico para su checksum."""
    return json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)


def row_checksum(row: dict) -> str:
    """SHA-256 del canon de la fila (trazabilidad en `user_id_map`)."""
    return hashlib.sha256(canonical_row(row).encode("utf-8")).hexdigest()


def normalize_email(raw: object) -> str | None:
    """Normaliza un email V2; None si ausente o con forma invalida."""
    if raw is None:
        return None
    mail = str(raw).strip().lower()
    return mail if EMAIL_RE.match(mail) else None


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


def sql_text(value: str) -> str:
    """Escapa un literal de texto SQL."""
    return "'" + value.replace("'", "''") + "'"


def migrate_users(source: str, dsn: str) -> tuple[list[dict], list[dict]]:
    """Migra `users` V2; devuelve (migrados, excepciones sin secretos)."""
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    migrated: list[dict] = []
    exceptions: list[dict] = []
    try:
        rows = list(con.execute("SELECT * FROM users ORDER BY id"))
    finally:
        con.close()

    seen_mails: set[str] = set()
    for sqlite_row in rows:
        row = dict(sqlite_row)
        legacy_id = row["id"]
        checksum = row_checksum(row)
        email = normalize_email(row.get("mail"))

        reason: str | None = None
        if email is None:
            reason = "email ausente o invalido"
        elif email in seen_mails:
            reason = "email duplicado en origen"
        if reason is None:
            # Duplicado contra V3 ya cargado (re-ejecucion o colision real).
            found = psql(
                dsn,
                f"SELECT id FROM public.users WHERE lower(email) = {sql_text(email)}",
            )
            if found:
                reason = "email ya existe en public.users"
        if reason is not None:
            exceptions.append(
                {
                    "legacy_user_id": legacy_id,
                    "reason": reason,
                    "habilitado": bool(row.get("habilitado")),
                    "source_row_checksum": checksum,
                }
            )
            continue
        seen_mails.add(email)

        existing = psql(
            dsn, f"SELECT v3_user_id FROM legacy.user_id_map WHERE legacy_user_id = {int(legacy_id)}"
        )
        if existing:
            same = psql(
                dsn,
                "SELECT source_row_checksum FROM legacy.user_id_map "
                f"WHERE legacy_user_id = {int(legacy_id)}",
            )
            if not hmac.compare_digest(same.strip(), checksum):
                raise RuntimeError(
                    f"origen cambio tras freeze para legacy_user_id={legacy_id}: "
                    "abortar, no maquillar"
                )
            v3_user_id = existing.strip()
        else:
            v3_user_id = str(uuid.uuid4())
            enabled = "true" if row.get("habilitado") else "false"
            psql(
                dsn,
                "INSERT INTO public.users (id, email, habilitado) VALUES "
                f"('{v3_user_id}', {sql_text(email)}, {enabled})",
            )
            # Sin filas en public.api_keys: reemision total (ver docstring).
            psql(
                dsn,
                "INSERT INTO legacy.user_id_map "
                "(legacy_user_id, v3_user_id, migrated_at, source_mail, source_row_checksum) VALUES "
                f"({int(legacy_id)}, '{v3_user_id}', "
                f"'{datetime.now(timezone.utc).isoformat()}', "
                f"{sql_text(email)}, '{checksum}')",
            )
        migrated.append({"legacy_user_id": legacy_id, "v3_user_id": v3_user_id})
    return migrated, exceptions


def main() -> int:
    """Punto de entrada CLI del ETL de usuarios."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SQLite V2 (modo ro)")
    parser.add_argument("--dsn", default=None, help="DSN Postgres (o DATABASE_URL)")
    parser.add_argument("--exceptions", required=True, help="Destino de excepciones JSON")
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        dsn_file = os.environ.get("DATABASE_URL_FILE", "/run/secrets/database_url")
        if os.path.isfile(dsn_file):
            with open(dsn_file, encoding="utf-8") as fh:
                dsn = fh.read().strip()
    if not dsn:
        print("ERROR: definir --dsn, DATABASE_URL o DATABASE_URL_FILE", file=sys.stderr)
        return 1

    migrated, exceptions = migrate_users(args.source, dsn)
    with open(args.exceptions, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "etl": ETL_VERSION,
                "envelope_protocol_version": PROTOCOL_VERSION,
                "run_at_utc": datetime.now(timezone.utc).isoformat(),
                "migrated": len(migrated),
                "exceptions": exceptions,
                "estrategia_claves": "reemision-total",
                "claves_reemitir": len(migrated),
            },
            fh,
            indent=2,
            sort_keys=True,
        )
    blocked = [e for e in exceptions if e.get("habilitado")]
    print(f"OK: {len(migrated)} usuarios migrados, {len(exceptions)} excepciones")
    if blocked:
        print(
            f"BLOQUEO go/no-go: {len(blocked)} excepciones afectan a "
            "clientes habilitados (ver archivo de excepciones)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
