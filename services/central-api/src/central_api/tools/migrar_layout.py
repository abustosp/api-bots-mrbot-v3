#!/usr/bin/env python3
"""Migra objetos S4 del layout viejo al nuevo, dentro del MISMO bucket del modulo.

    viejo: jobs/{job}/{intento}/{artifact_id}/{sufijo}
    nuevo: jobs/{job}/{archivo}

Corre DENTRO del contenedor de la central (``ssh conciliabot2 'docker exec -i
mrbot-v3-central-api-1 python - [opciones]' < migrar_layout.py``). Por defecto
SIMULA (lee los origenes y calcula todo, no escribe nada).

Fase 1 (``--aplicar``), por objeto y solo si el paso anterior salio bien:
  1. lee el origen y calcula sha256 (si ``job_artifacts.sha256`` existe debe coincidir),
  2. copia a la clave nueva,
  3. relee el destino y compara sha256 con el origen y con ``job_artifacts``,
  4. recien entonces ``UPDATE job_artifacts SET object_key, filename`` (lotes, un commit
     por lote, ``WHERE id AND object_key = viejo``). Los triggers por bot corren solos.
Nunca borra nada.

Fase 2 (``--borrar-originales``, aparte, tambien simula salvo ``--aplicar``): borra la
clave vieja SOLO si la fila ya apunta a la nueva y el sha256 del destino, releido en
ese momento, coincide con el de la migracion y con ``job_artifacts``. Las claves
viejas se conocen por un manifiesto que escribe la fase 1 (en el bucket general,
``_migraciones/migrar_layout_manifiesto.json``, y copia local en /tmp).

Nombres: ``jobs/{job}/{filename}`` (saneado). CCMA/RCEL se renombran por tipo
(``ccma_saldos_{cuit}.csv``, ...). Si el nombre ya esta tomado en el job se agrega
``-2``, ``-3`` antes de la extension (orden por created_at, id). Idempotente.

    python - [--aplicar] [--limite N] [--bot BOT] [--hilos N] [--borrar-originales]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from central_api.api.uploads import segmento_seguro
from central_api.settings import get_settings
from central_api.storage import delete_object_bytes, get_object_bytes, object_exists, put_object_bytes
from mrbot_contracts import datos_csv

MAX_BYTES = 60 * 1024 * 1024
LOTE = 50
MANIFIESTO_KEY = "_migraciones/migrar_layout_manifiesto.json"
MANIFIESTO_LOCAL = "/tmp/migrar_layout_manifiesto.json"
_VIEJO = re.compile(r"^jobs/[^/]+/[^/]+/[^/]+/[^/]+")

_SQL = """
SELECT a.id::text AS id, a.job_id::text AS job, j.bot AS bot, j.request_payload AS rq,
       a.object_key AS key, a.filename AS filename, a.sha256 AS sha256,
       a.content_type AS content_type, a.created_at AS created_at
FROM job_artifacts a JOIN jobs j ON j.id = a.job_id
WHERE (CAST(:bot AS text) IS NULL OR j.bot = CAST(:bot AS text))
ORDER BY a.job_id, a.created_at, a.id
"""


def _kw(s):
    return dict(
        endpoint=s.object_storage_endpoint, region=s.object_storage_region,
        access_key=s.object_storage_access_key, secret_key=s.object_storage_secret_key,
    )


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def es_viejo(clave: str) -> bool:
    return bool(_VIEJO.match(clave or ""))


def _cuit(rq) -> str | None:
    if isinstance(rq, str):
        try:
            rq = json.loads(rq)
        except ValueError:
            return None
    if not isinstance(rq, dict):
        return None
    v = rq.get("representado_cuit") or rq.get("cuit_representado")
    return str(v).strip() if v else None


def _base(filename: str | None) -> str:
    ultimo = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return segmento_seguro(ultimo, defecto="archivo")


def _nombre(fn_name: str, plantilla: str, cuit: str) -> str:
    """Usa ``datos_csv`` y, si el contrato desplegado aun tiene otra firma, el patron literal."""
    try:
        return getattr(datos_csv, fn_name)(cuit)
    except (AttributeError, TypeError):
        return plantilla.format(re.sub(r"[^A-Za-z0-9._-]+", "_", cuit).strip("_") or "sin_dato")


def nombre_destino(clave: str, filename: str | None, rq) -> str:
    """Nombre base (sin desambiguar) que le toca al objeto en el layout nuevo."""
    partes = clave.split("/")
    artefacto = partes[3] if len(partes) > 3 else ""
    cuit = _cuit(rq)
    if cuit:
        if artefacto == "ccma_saldos.csv":
            return _nombre("nombre_ccma_saldos", "ccma_saldos_{}.csv", cuit)
        if artefacto == "ccma_movimientos.csv":
            return _nombre("nombre_ccma_movimientos", "ccma_movimientos_{}.csv", cuit)
        if artefacto == "ccma_resumen.pdf":
            return _nombre("nombre_ccma_resumen", "ccma_resumen_{}.pdf", cuit)
        if artefacto == "rcel_facturas.csv":
            return _nombre("nombre_rcel_facturas", "rcel_facturas_{}.csv", cuit)
        if artefacto == "ccma_compensacion.pdf":
            return _nombre("_no_existe", "ccma_compensacion_{}.pdf", cuit)
    return _base(filename)


def _con_sufijo(nombre: str, n: int) -> str:
    raiz, ext = os.path.splitext(nombre)
    return f"{raiz}-{n}{ext}"


def planificar(filas) -> tuple[list[dict], int]:
    """(objetos a migrar con su destino, cantidad de filas ya en layout nuevo).

    ``filas`` debe venir ordenada por job, created_at, id. Determinista.
    """
    por_job: dict[str, list] = defaultdict(list)
    for f in filas:
        por_job[f["job"]].append(f)
    items: list[dict] = []
    ya_nuevo = 0
    for job, lista in por_job.items():
        lista = sorted(lista, key=lambda f: (str(f.get("created_at") or ""), f["id"]))
        usados = set()
        for f in lista:
            if not es_viejo(f["key"]):
                usados.add(f["key"].rsplit("/", 1)[-1])
                ya_nuevo += 1
        for f in lista:
            if not es_viejo(f["key"]):
                continue
            base = nombre_destino(f["key"], f["filename"], f["rq"])
            nombre, n = base, 1
            while nombre in usados:
                n += 1
                nombre = _con_sufijo(base, n)
            usados.add(nombre)
            items.append({**f, "nombre": nombre, "nueva": f"jobs/{job}/{nombre}", "desambiguado": n > 1})
    return items, ya_nuevo


def _procesar(s, aplicar: bool, it: dict) -> tuple[str, dict]:
    """Pasos 1 a 3 para UN objeto. Devuelve (estado, evento). Estado ``listo`` = verificado."""
    bucket = s.bucket_para_bot(it["bot"])
    ev = {"id": it["id"], "bot": it["bot"], "viejo": it["key"], "nuevo": it["nueva"]}
    try:
        if not object_exists(**_kw(s), bucket=bucket, object_key=it["key"]):
            return "ORIGEN_AUSENTE", ev
        origen = get_object_bytes(**_kw(s), bucket=bucket, object_key=it["key"], max_bytes=MAX_BYTES)
    except (RuntimeError, ValueError) as e:
        return "ERROR", {**ev, "error": f"leyendo origen: {e}"}
    sha = _sha(origen)
    ev["sha256"] = sha
    if it["sha256"] and it["sha256"] != sha:
        return "SHA_NO_COINCIDE", {**ev, "detalle": "origen distinto de job_artifacts.sha256"}
    try:
        if object_exists(**_kw(s), bucket=bucket, object_key=it["nueva"]):
            previo = get_object_bytes(**_kw(s), bucket=bucket, object_key=it["nueva"], max_bytes=MAX_BYTES)
            if _sha(previo) != sha:
                return "ERROR", {**ev, "error": "destino ocupado con otro contenido"}
            ya_copiado = True
        else:
            ya_copiado = False
        if not aplicar:
            return "se_migraria", ev
        if not ya_copiado:
            put_object_bytes(**_kw(s), bucket=bucket, object_key=it["nueva"], content=origen,
                             content_type=it["content_type"] or "application/octet-stream")
        leido = get_object_bytes(**_kw(s), bucket=bucket, object_key=it["nueva"], max_bytes=MAX_BYTES)
    except (RuntimeError, ValueError) as e:
        return "ERROR", {**ev, "error": f"copiando: {e}"}
    if _sha(leido) != sha or (it["sha256"] and _sha(leido) != it["sha256"]):
        return "SHA_NO_COINCIDE", {**ev, "detalle": "destino releido distinto"}
    return "listo", ev


class RepoSQL:
    """Acceso a la base (sustituible en tests)."""

    async def filas(self, bot):
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            return [dict(r) for r in (await ses.execute(text(_SQL), {"bot": bot})).mappings().all()]

    async def actualizar(self, lote: list[dict]) -> int:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        n = 0
        async with nueva_sesion() as ses:
            for it in lote:
                r = await ses.execute(
                    text("UPDATE job_artifacts SET object_key = :n, filename = :f "
                         "WHERE id = CAST(:id AS uuid) AND object_key = :v"),
                    {"n": it["nueva"], "f": it["nombre"], "id": it["id"], "v": it["key"]},
                )
                n += r.rowcount or 0
            await ses.commit()
        return n

    async def fila(self, id_: str):
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            r = (await ses.execute(
                text("SELECT object_key AS key, sha256 FROM job_artifacts WHERE id = CAST(:id AS uuid)"),
                {"id": id_})).mappings().first()
        return dict(r) if r else None


def _leer_manifiesto(s) -> dict:
    datos: dict = {}
    try:
        datos = json.loads(get_object_bytes(**_kw(s), bucket=s.object_storage_bucket,
                                            object_key=MANIFIESTO_KEY, max_bytes=MAX_BYTES))
    except (RuntimeError, ValueError):
        pass
    if os.path.exists(MANIFIESTO_LOCAL):
        try:
            datos = {**json.load(open(MANIFIESTO_LOCAL, encoding="utf-8")), **datos}
        except ValueError:
            pass
    return datos


def _guardar_manifiesto(s, datos: dict) -> None:
    cuerpo = json.dumps(datos, ensure_ascii=False).encode()
    with open(MANIFIESTO_LOCAL, "w", encoding="utf-8") as fh:
        fh.write(cuerpo.decode())
    put_object_bytes(**_kw(s), bucket=s.object_storage_bucket, object_key=MANIFIESTO_KEY,
                     content=cuerpo, content_type="application/json")


async def migrar(repo, s, *, aplicar: bool, limite: int | None, bot: str | None, hilos: int = 12) -> dict:
    filas = await repo.filas(bot)
    items, ya_nuevo = planificar(filas)
    if limite is not None:
        items = items[:limite]
    cuenta: Counter = Counter(ya_nuevo=ya_nuevo)
    por_bot: dict[str, Counter] = defaultdict(Counter)
    errores: list[dict] = []
    manifiesto = _leer_manifiesto(s) if aplicar else {}
    loop = asyncio.get_running_loop()

    def contar(estado: str, it: dict) -> None:
        cuenta[estado] += 1
        por_bot[it["bot"]][estado] += 1

    with ThreadPoolExecutor(hilos) as pool:
        resultados = await asyncio.gather(*[loop.run_in_executor(pool, _procesar, s, aplicar, it) for it in items])
    listos: list[dict] = []
    for it, (estado, ev) in zip(items, resultados):
        if estado == "listo":
            listos.append(it)
        else:
            contar(estado, it)
            if estado in ("ERROR", "SHA_NO_COINCIDE"):
                errores.append({"estado": estado, **ev})
        if it["desambiguado"]:
            cuenta["desambiguados"] += 1
    try:
        for i in range(0, len(listos), LOTE):
            lote = listos[i:i + LOTE]
            ok = await repo.actualizar(lote)
            if ok != len(lote):
                errores.append({"estado": "ERROR", "error": f"lote con {ok}/{len(lote)} filas actualizadas"})
            for it in lote:
                contar("migrados", it)
                manifiesto[it["id"]] = {"viejo": it["key"], "nuevo": it["nueva"], "bot": it["bot"]}
    finally:
        if aplicar and listos:
            _guardar_manifiesto(s, manifiesto)
    # nombres de contador pedidos
    res = {
        "modo": "APLICADO" if aplicar else "SIMULACION",
        "fase": "migrar",
        "planificados": len(items),
        "migrados": cuenta["migrados"], "se_migraria": cuenta["se_migraria"],
        "ya_nuevo": cuenta["ya_nuevo"], "origen_ausente": cuenta["ORIGEN_AUSENTE"],
        "desambiguados": cuenta["desambiguados"], "sha_no_coincide": cuenta["SHA_NO_COINCIDE"],
        "errores": cuenta["ERROR"] + sum(1 for e in errores if "lote" in str(e.get("error", ""))),
        "por_bot": {b: dict(c) for b, c in sorted(por_bot.items())},
    }
    res["_errores"] = errores
    return res


def _borrar(s, aplicar: bool, id_: str, m: dict, fila: dict | None) -> str:
    bucket = s.bucket_para_bot(m["bot"])
    if fila is None or fila["key"] != m["nuevo"]:
        return "OMITIDO_fila_no_apunta_a_nuevo"
    try:
        if not object_exists(**_kw(s), bucket=bucket, object_key=m["viejo"]):
            return "ya_borrado"
        destino = get_object_bytes(**_kw(s), bucket=bucket, object_key=m["nuevo"], max_bytes=MAX_BYTES)
        sha = _sha(destino)
        if fila["sha256"] and sha != fila["sha256"]:
            return "OMITIDO_sha_no_coincide"
        origen = get_object_bytes(**_kw(s), bucket=bucket, object_key=m["viejo"], max_bytes=MAX_BYTES)
        if _sha(origen) != sha:
            return "OMITIDO_sha_no_coincide"
        if not aplicar:
            return "se_borraria"
        delete_object_bytes(**_kw(s), bucket=bucket, object_key=m["viejo"])
    except (RuntimeError, ValueError):
        return "ERROR"
    return "borrado"


async def borrar_originales(repo, s, *, aplicar: bool, limite: int | None, bot: str | None, hilos: int = 12) -> dict:
    manifiesto = _leer_manifiesto(s)
    pend = [(i, m) for i, m in manifiesto.items() if not bot or m["bot"] == bot]
    if limite is not None:
        pend = pend[:limite]
    filas = {i: await repo.fila(i) for i, _ in pend}
    loop = asyncio.get_running_loop()
    cuenta: Counter = Counter()
    por_bot: dict[str, Counter] = defaultdict(Counter)
    with ThreadPoolExecutor(hilos) as pool:
        ests = await asyncio.gather(*[loop.run_in_executor(pool, _borrar, s, aplicar, i, m, filas[i]) for i, m in pend])
    for (i, m), est in zip(pend, ests):
        cuenta[est] += 1
        por_bot[m["bot"]][est] += 1
        if aplicar and est in ("borrado", "ya_borrado"):
            manifiesto.pop(i, None)
    if aplicar and pend:
        _guardar_manifiesto(s, manifiesto)
    return {"modo": "APLICADO" if aplicar else "SIMULACION", "fase": "borrar_originales",
            "candidatos": len(pend), **cuenta, "por_bot": {b: dict(c) for b, c in sorted(por_bot.items())},
            "_errores": []}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--borrar-originales", action="store_true")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--bot")
    ap.add_argument("--hilos", type=int, default=12)
    a = ap.parse_args(argv)
    s = get_settings()
    fn = borrar_originales if a.borrar_originales else migrar
    r = asyncio.run(fn(RepoSQL(), s, aplicar=a.aplicar, limite=a.limite, bot=a.bot, hilos=a.hilos))
    errores = r.pop("_errores")
    ruta = f"/tmp/migrar_layout_errores_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"
    with open(ruta, "w", encoding="utf-8") as fh:
        for e in errores:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(json.dumps(r, indent=1, ensure_ascii=False))
    print("errores en", ruta, f"({len(errores)})", file=sys.stderr)
    return 1 if r.get("errores") or r.get("ERROR") or r.get("sha_no_coincide") else 0


if __name__ == "__main__":
    raise SystemExit(main())
