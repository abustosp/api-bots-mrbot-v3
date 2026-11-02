#!/usr/bin/env python3
"""Adopta archivos legacy V1 (sin fila en DB) bajo un usuario sintético.

Cada archivo legacy de los buckets de bots se copia a
``jobs/{job}/{archivo}`` dentro del MISMO bucket, con un job sintético
COMPLETO propiedad de ``legacy-data@mrbot.com.ar`` y su fila en
``job_artifacts``. Agrupa un job por (bot, cuit) para no crear un job por
archivo. Nunca borra el original (fase 1; no hay fase 2).

Corre DENTRO del contenedor de la central (igual que migrar_layout.py).
Por defecto SIMULA (lee S3 y la base, no escribe nada)::

    python - [--aplicar] [--limite N] [--bot BOT] [--hilos N] [--auto-chequeo]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

LEGACY_EMAIL = "legacy-data@mrbot.com.ar"
MANIFIESTO_KEY = "_adopciones_legacy_manifiesto.json"
MANIFIESTO_LOCAL = "/tmp/adoptar_legacy_manifiesto.json"
MAX_BYTES = 60 * 1024 * 1024
LOTE = 50

# bucket -> bot cuando el bucket es de un solo bot.
BUCKET_BOT = {
    "aportesenlinea": "aportes_en_linea",
    "retper-iibb-arba": "arba",
    "ccma": "ccma",
    "certificadomipyme": "certificado_mipyme",
    "controladoresfiscales": "controladores_fiscales",
    "declaracionenlinea": "declaracion_en_linea",
    "facturometro": "facturometro",
    "hacienda": "hacienda",
    "lsd": "libro_sueldos_digital",
    "librosiva": "libros_portal_iva",
    "liquidaciongranos": "liquidacion_granos",
    "misfacilidades": "mis_facilidades",
    "miscomprobantes": "mis_comprobantes",
    "moa": "moa",
    "pagodevoluciones": "pago_devoluciones",
    "portaliva": "portal_iva",
    "rcel": "rcel",
    "retper-iibb-agip": "retper_iibb_agip",
    "retper-iibb-misiones": "retper_iibb_misiones",
    "sct": "sct",
    "sifere": "sifere",
    "siper": "siper",
    "srt": "srt",
}
# (bucket, subcadena en minúsculas, bot) para buckets compartidos.
REGLAS = [
    # shortcut: comprobantes y mis_comprobantes comparten bucket y patrón de
    # nombre; se atribuye todo a mis_comprobantes (revisar si hace falta
    # separar el bot viejo).
    ("ccma", "compensacion", "ccma_compensacion"),
    ("misretenciones", "iva-simple", "mis_retenciones_iva_simple"),
    ("misretenciones", "iva_simple", "mis_retenciones_iva_simple"),
    ("misretenciones", "", "mis_retenciones"),
    ("vep", "ccma", "vep_ccma"),
    ("vep", "pagos_vep", "consulta_pagos_vep"),
    ("vep", "", "vep_archivo"),
]
_GENERAL_REGLAS = [
    ("compensacion", "compensaciones"),
    ("cpg", "certificacion_primaria_granos"),
    ("certificado_deposito", "certificacion_primaria_granos"),
]

_CUIT = re.compile(r"(\d{11})")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def extraer_cuit(clave: str) -> str:
    """Primer CUIT (11 dígitos) en la clave; 's-cuit' si no hay."""
    m = _CUIT.search(clave or "")
    return m.group(1) if m else "s-cuit"


def clasificar_bot(bucket: str, clave: str, general: str) -> str | None:
    """Bot dueño del objeto, o None si no se adopta (jobs/, _migraciones/, desconocido)."""
    if not clave or clave.startswith("jobs/") or clave.startswith("_migraciones/"):
        return None
    bajo = clave.lower()
    if bucket == general:
        # El bucket general puede coincidir con el de un bot: primero las
        # reglas generales y si no matchean, las del propio bucket.
        for sub, bot in _GENERAL_REGLAS:
            if sub in bajo:
                return bot
    for b, sub, bot in REGLAS:
        if b == bucket and sub in bajo:
            return bot
    return BUCKET_BOT.get(bucket)


def con_sufijo(nombre: str, n: int) -> str:
    raiz, ext = os.path.splitext(nombre)
    return f"{raiz}-{n}{ext}"


def _seguro(nombre: str) -> str:
    """Mismo saneado que la central; fallback stdlib para el auto-chequeo local."""
    try:
        from central_api.api.uploads import nombre_archivo_seguro

        return nombre_archivo_seguro(nombre)
    except Exception:  # noqa: BLE001 - sin deps en auto-chequeo
        base = (nombre or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "archivo"
        return base[:255]


def tipo_contenido(nombre: str) -> str:
    c, _ = mimetypes.guess_type(nombre)
    return c or "application/octet-stream"


def clave_idempotencia(bot: str, cuit: str) -> str:
    return f"legacy-adopt-{bot}-{cuit}"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def auto_chequeo() -> int:
    assert extraer_cuit("ccma_20123456789_20240101.pdf") == "20123456789"
    assert extraer_cuit("srt_alicuotas.json") == "s-cuit"
    assert clasificar_bot("ccma", "ccma_20240101.pdf", "g") == "ccma"
    assert clasificar_bot("ccma", "ccma_compensacion_x.pdf", "g") == "ccma_compensacion"
    assert clasificar_bot("vep", "pagos_vep_1.csv", "g") == "consulta_pagos_vep"
    assert clasificar_bot("vep", "x - VEP CCMA.pdf", "g") == "vep_ccma"
    assert clasificar_bot("vep", "detalle.pdf", "g") == "vep_archivo"
    assert clasificar_bot("ccma", "jobs/uuid/ccma_x.pdf", "g") is None
    assert clasificar_bot("g", "sct_compensaciones_1.csv", "g") == "compensaciones"
    assert clasificar_bot("g", "cosa_rara.bin", "g") is None
    assert clasificar_bot("miscomprobantes", "0 - MCE - x.zip", "g") == "mis_comprobantes"
    assert clasificar_bot("portaliva", "0 - x - compras.zip", "g") == "portal_iva"
    assert clasificar_bot("miscomprobantes", "0 - MCE - x.zip", "miscomprobantes") == \
        "mis_comprobantes"
    assert clasificar_bot("miscomprobantes", "sct_compensaciones_x.csv",
                          "miscomprobantes") == "compensaciones"
    assert con_sufijo("a.csv", 2) == "a-2.csv"
    assert clave_idempotencia("srt", "s-cuit") == "legacy-adopt-srt-s-cuit"
    print("auto-chequeo ok")
    return 0


def _firmar(s, secreto: str, dia: str, region: str) -> bytes:
    k = hmac.new(("AWS4" + secreto).encode(), dia.encode(), hashlib.sha256).digest()
    for x in (region, "s3"):
        k = hmac.new(k, x.encode(), hashlib.sha256).digest()
    return hmac.new(k, b"aws4_request", hashlib.sha256).digest()


def listar_objetos(s, bucket: str):
    """Genera (key, size, last_modified). Solo lectura."""
    from urllib.parse import quote

    host = (s.object_storage_endpoint or "").rstrip("/").split("://", 1)[-1]
    token = None
    while True:
        ahora = datetime.now(timezone.utc)
        dia = ahora.strftime("%Y%m%d")
        alcance = f"{dia}/{s.object_storage_region}/s3/aws4_request"
        par = {"list-type": "2", "max-keys": "1000",
               "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
               "X-Amz-Credential": f"{s.object_storage_access_key}/{alcance}",
               "X-Amz-Date": ahora.strftime("%Y%m%dT%H%M%SZ"),
               "X-Amz-Expires": "60", "X-Amz-SignedHeaders": "host"}
        if token:
            par["continuation-token"] = token
        canon = "&".join(f"{quote(k, safe='')}={quote(v, safe='-_.~')}"
                         for k, v in sorted(par.items()))
        txt = f"GET\n/{bucket}\n{canon}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
        firma = hmac.new(
            _firmar(s, s.object_storage_secret_key, dia, s.object_storage_region),
            f"AWS4-HMAC-SHA256\n{par['X-Amz-Date']}\n{alcance}\n"
            f"{hashlib.sha256(txt.encode()).hexdigest()}".encode(),
            hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"https://{host}/{bucket}?{canon}&X-Amz-Signature={firma}", method="GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            cuerpo = r.read()
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        raiz = ET.fromstring(cuerpo)
        for n in raiz.findall(".//s:Contents", ns):
            yield (n.findtext("s:Key", namespaces=ns),
                   int(n.findtext("s:Size", namespaces=ns) or 0),
                   n.findtext("s:LastModified", namespaces=ns))
        if (raiz.findtext(".//s:IsTruncated", namespaces=ns) or "").lower() != "true":
            return
        token = raiz.findtext(".//s:NextContinuationToken", namespaces=ns)


class RepoSQL:
    """Acceso a la base (sustituible en tests)."""

    async def ops_validas(self) -> dict[str, list[str]]:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            filas = (await ses.execute(
                text("SELECT bot_code, code FROM bot_operations ORDER BY 1, 2"))).all()
        ops: dict[str, list[str]] = defaultdict(list)
        for b, c in filas:
            ops[b].append(c)
        return ops

    async def sha_existentes(self) -> dict[tuple[str, str], set[str]]:
        """(bot, filename) -> shas ya registrados (para no duplicar contenido)."""
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        mapa: dict[tuple[str, str], set[str]] = defaultdict(set)
        async with nueva_sesion() as ses:
            filas = (await ses.execute(text(
                "SELECT j.bot, a.filename, a.sha256 FROM job_artifacts a "
                "JOIN jobs j ON j.id = a.job_id WHERE a.sha256 IS NOT NULL"))).all()
        for b, f, h in filas:
            mapa[(b, f)].add(h)
        return mapa

    async def usuario(self, email: str):
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            r = (await ses.execute(
                text("SELECT id FROM users WHERE lower(email) = lower(:e)"),
                {"e": email})).first()
            if r:
                return str(r[0])
            r = (await ses.execute(
                text("INSERT INTO users (id, email, habilitado) "
                     "VALUES (gen_random_uuid(), :e, TRUE) RETURNING id"),
                {"e": email})).first()
            await ses.commit()
            return str(r[0])

    async def existe_job(self, user_id: str, bot: str, op: str, clave: str) -> str | None:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            r = (await ses.execute(text(
                "SELECT id FROM jobs WHERE user_id = CAST(:u AS uuid) AND bot = :b "
                "AND operation = :o AND idempotency_key = :k"), {"u": user_id, "b": bot,
                                                                "o": op, "k": clave})).first()
        return str(r[0]) if r else None

    async def crear_job(self, user_id: str, bot: str, op: str, clave: str,
                        cuit: str, fuente: str, cuando: str) -> str:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        payload = json.dumps({"representado_cuit": cuit, "legacy": True,
                              "source": fuente, "adoptado": True},
                             ensure_ascii=False)
        async with nueva_sesion() as ses:
            r = (await ses.execute(text(
                "INSERT INTO jobs (id, user_id, bot, operation, status, result, "
                "request_payload, idempotency_key, finished_at, created_at) "
                "VALUES (gen_random_uuid(), CAST(:u AS uuid), :b, :o, 'COMPLETO', 'OK', "
                "CAST(:p AS jsonb), :k, CAST(:f AS timestamptz), "
                "CAST(:f AS timestamptz)) RETURNING id"),
                {"u": user_id, "b": bot, "o": op, "p": payload,
                 "k": clave, "f": cuando})).first()
            await ses.commit()
            return str(r[0])

    async def crear_artefactos(self, lote: list[dict]) -> int:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        n = 0
        async with nueva_sesion() as ses:
            for it in lote:
                r = await ses.execute(text(
                    "INSERT INTO job_artifacts (id, job_id, kind, object_key, filename, "
                    "content_type, size_bytes, sha256, created_at) VALUES "
                    "(gen_random_uuid(), CAST(:j AS uuid), 'ARCHIVO', :k, :f, :c, :s, :h, "
                    "CAST(:t AS timestamptz)) ON CONFLICT (object_key) DO NOTHING"),
                    {"j": it["job"], "k": it["nueva"], "f": it["nombre"],
                     "c": it["content_type"], "s": it["size"], "h": it["sha"],
                     "t": it["cuando"]})
                n += r.rowcount or 0
            await ses.commit()
        return n

    async def tiene_artefacto(self, nueva: str) -> bool:
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        async with nueva_sesion() as ses:
            r = (await ses.execute(text(
                "SELECT 1 FROM job_artifacts WHERE object_key = :k"),
                {"k": nueva})).first()
        return r is not None


def operacion(bot: str, ops: dict[str, list[str]]) -> str | None:
    """Operación válida determinista: prefiere consultar, luego descargar."""
    validas = ops.get(bot, [])
    for favorita in ("consultar", "descargar"):
        if favorita in validas:
            return favorita
    return min(validas) if validas else None


async def inventariar(s, repo, solo_bot: str | None):
    """Lee S3 + DB y agrupa por (bot, cuit). Solo lectura."""
    ops = await repo.ops_validas()
    shas = await repo.sha_existentes()
    general = s.object_storage_bucket
    buckets = sorted(set(list(BUCKET_BOT) + [b for b, _, _ in REGLAS
                                             if b not in BUCKET_BOT] + [general]))
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    cuenta = Counter(vistos=0, sin_bot=0, grandes=0)
    for bucket in buckets:
        try:
            objetos = list(listar_objetos(s, bucket))
        except Exception:  # noqa: BLE001 - bucket inexistente o sin acceso
            cuenta["bucket_inaccesible"] += 1
            continue
        for key, size, ultimo in objetos:
            cuenta["vistos"] += 1
            bot = clasificar_bot(bucket, key, general)
            if solo_bot and bot != solo_bot:
                continue
            if bot is None:
                cuenta["sin_bot"] += 1
                continue
            op = operacion(bot, ops)
            if op is None:
                cuenta["sin_bot"] += 1
                continue
            nombre = _seguro(key.rsplit("/", 1)[-1])
            grupos[(bot, extraer_cuit(key), op)].append(
                {"bucket": bucket, "key": key, "size": size, "cuando": ultimo,
                 "nombre": nombre, "op": op})
    items: list[dict] = []
    for (bot, cuit, op), archs in sorted(grupos.items()):
        usados: set[str] = set()
        for a in sorted(archs, key=lambda x: (x["cuando"] or "", x["key"])):
            base, n = a["nombre"], 1
            while base in usados:
                n += 1
                base = con_sufijo(a["nombre"], n)
            usados.add(base)
            if a["size"] > MAX_BYTES:
                cuenta["grandes"] += 1
                continue
            items.append({**a, "bot": bot, "cuit": cuit, "nombre": base,
                          "nueva": None, "clave": clave_idempotencia(bot, cuit)})
    return items, shas, cuenta


def _cabeza(s, kw: dict, bucket: str, key: str) -> tuple[str | None, int | None]:
    """HEAD barato: (etag sin comillas, size). None si no existe o falla."""
    import urllib.request
    from urllib.parse import quote

    from central_api.storage import _firmar_clave

    host = (s.object_storage_endpoint or "").rstrip("/").split("://", 1)[-1]
    ahora = datetime.now(timezone.utc)
    dia = ahora.strftime("%Y%m%d")
    alcance = f"{dia}/{s.object_storage_region}/s3/aws4_request"
    par = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256",
           "X-Amz-Credential": f"{kw['access_key']}/{alcance}",
           "X-Amz-Date": ahora.strftime("%Y%m%dT%H%M%SZ"),
           "X-Amz-Expires": "60", "X-Amz-SignedHeaders": "host"}
    canon = "&".join(f"{quote(k, safe='')}={quote(v, safe='-_.~')}"
                     for k, v in sorted(par.items()))
    uri = "/" + bucket + "/" + "/".join(quote(p, safe="-_.~") for p in key.split("/"))
    txt = (f"HEAD\n{uri}\n{canon}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD")
    ambito = (f"AWS4-HMAC-SHA256\n{par['X-Amz-Date']}\n{alcance}\n"
              f"{hashlib.sha256(txt.encode()).hexdigest()}")
    firma = hmac.new(_firmar_clave(kw["secret_key"], dia, s.object_storage_region),
                     ambito.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"https://{host}{uri}?{canon}&X-Amz-Signature={firma}", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            etag = (r.headers.get("ETag") or "").strip('"')
            size = r.headers.get("Content-Length")
            return etag or None, int(size) if size else None
    except Exception:  # noqa: BLE001 - no existe o sin acceso
        return None, None


def _subir_verificado(s, kw: dict, a: dict) -> None:
    """Sube si falta y verifica por HEAD (ETag+size); reintenta una vez."""
    from central_api.storage import (get_object_bytes, object_exists,
                                     put_object_bytes)

    bucket = s.bucket_para_bot(a["bot"])
    for intento in range(2):
        if not object_exists(**kw, bucket=bucket, object_key=a["nueva"]):
            put_object_bytes(**kw, bucket=bucket, object_key=a["nueva"],
                             content=a["contenido"], content_type=a["content_type"])
        etag, size = _cabeza(s, kw, bucket, a["nueva"])
        if etag and "-" not in etag and etag == a["sha"] and size == len(a["contenido"]):
            return
        if intento == 0:
            # Destino parcial o ETag no estándar: se re-sube y se re-verifica.
            put_object_bytes(**kw, bucket=bucket, object_key=a["nueva"],
                             content=a["contenido"], content_type=a["content_type"])
            continue
        leido = get_object_bytes(**kw, bucket=bucket, object_key=a["nueva"],
                                 max_bytes=MAX_BYTES)
        if _sha(leido) != a["sha"]:
            raise ValueError("destino releido distinto")
        return


def _subir_o_falla(s, kw: dict, a: dict):
    """Sube y verifica un archivo; libera sus bytes. Devuelve True o el error."""
    try:
        _subir_verificado(s, kw, a)
        return True
    except (RuntimeError, ValueError) as e:
        return f"{a.get('viejo', '?')}: {e}"[:200]
    finally:
        a.pop("contenido", None)


def _bajar(s, kw: dict, shas: dict, a: dict) -> tuple[str, dict]:
    """Baja un archivo y calcula su sha. Sin escribir en DB ni en S3."""
    from central_api.storage import get_object_bytes, object_exists

    try:
        if not object_exists(**kw, bucket=a["bucket"], object_key=a["key"]):
            return "ORIGEN_AUSENTE", {"bot": a["bot"], "viejo": a["key"]}
        contenido = get_object_bytes(**kw, bucket=a["bucket"], object_key=a["key"],
                                     max_bytes=MAX_BYTES)
    except (RuntimeError, ValueError) as e:
        return "ERROR", {"bot": a["bot"], "viejo": a["key"], "error": str(e)[:200]}
    sha = _sha(contenido)
    if sha in shas.get((a["bot"], a["nombre"]), set()):
        return "duplicado", {"bot": a["bot"], "viejo": a["key"]}
    return "listo", {**a, "sha": sha,
                     "content_type": tipo_contenido(a["nombre"]),
                     "contenido": contenido}


def _procesar(s, shas: dict, it: dict, descargar: bool) -> tuple[str, dict]:
    """Decide copiar o saltear. Solo descarga bytes si hace falta (aplicar o
    posible duplicado); la simulación no baja el contenido."""
    from central_api.storage import get_object_bytes, object_exists

    kw = dict(endpoint=s.object_storage_endpoint, region=s.object_storage_region,
              access_key=s.object_storage_access_key,
              secret_key=s.object_storage_secret_key)
    ev = {"bot": it["bot"], "viejo": it["key"]}
    candidatos = shas.get((it["bot"], it["nombre"]), set())
    if not descargar and not candidatos:
        return "listo", {**it, "nueva": f"jobs/TODO/{it['nombre']}",
                         "sha": None, "content_type": tipo_contenido(it["nombre"])}
    try:
        if not object_exists(**kw, bucket=it["bucket"], object_key=it["key"]):
            return "ORIGEN_AUSENTE", ev
        contenido = get_object_bytes(**kw, bucket=it["bucket"],
                                     object_key=it["key"], max_bytes=MAX_BYTES)
    except (RuntimeError, ValueError) as e:
        return "ERROR", {**ev, "error": f"leyendo origen: {e}"}
    sha = _sha(contenido)
    if sha in candidatos:
        return "duplicado", ev
    return "listo", {**it, "nueva": f"jobs/TODO/{it['nombre']}", "sha": sha,
                     "content_type": tipo_contenido(it["nombre"]),
                     "contenido": contenido}


async def adoptar(repo, s, *, aplicar: bool, limite: int | None,
                  bot: str | None, hilos: int = 4) -> dict:
    from central_api.storage import get_object_bytes, object_exists, put_object_bytes

    items, shas, cuenta = await inventariar(s, repo, bot)
    if limite is not None:
        items = items[:limite]
    cuenta["planificados"] = len(items)
    loop = asyncio.get_running_loop()
    # En simulación solo se descargan los posibles duplicados para
    # reportarlos; en aplicar no se descarga nada por adelantado (cada grupo
    # baja lo suyo más abajo): así reanudar no redescarga lo ya adoptado.
    if aplicar:
        procesados = [("listo", {**it, "nueva": None, "sha": None,
                                 "content_type": tipo_contenido(it["nombre"])})
                      for it in items]
        pool = None
    else:
        with ThreadPoolExecutor(hilos) as pool:
            procesados = await asyncio.gather(
                *[loop.run_in_executor(pool, _procesar, s, shas, it, True)
                  for it in items])
    listos = [ev for estado, ev in procesados if estado == "listo"]
    for estado, ev in procesados:
        if estado != "listo":
            cuenta[estado] += 1
    if not aplicar:
        cuenta["se_adoptaria"] = len(listos)
        sim = defaultdict(int)
        for it in items:
            sim[it["bot"]] += 1
        return {"modo": "SIMULACION", **{k: cuenta[k] for k in sorted(cuenta)},
                "por_bot": {b: {"planificados": n} for b, n in sorted(sim.items())},
                "_errores": []}
    user_id = await repo.usuario(LEGACY_EMAIL)
    kw = dict(endpoint=s.object_storage_endpoint, region=s.object_storage_region,
              access_key=s.object_storage_access_key,
              secret_key=s.object_storage_secret_key)
    errores: list[dict] = []
    por_bot: dict[str, Counter] = defaultdict(Counter)
    creados = 0
    grupos: dict[tuple, list] = {}
    for ev in listos:
        grupos.setdefault((ev["bot"], ev["cuit"], ev["op"]), []).append(ev)
    ordenados = sorted(grupos.items())
    loop = asyncio.get_running_loop()
    # Memoria acotada: se procesa por tandas chicas y se libera cada una.
    # Si el proceso muere, reejecutar retoma donde quedó (ON CONFLICT +
    # existe_job + tiene_artefacto).
    with ThreadPoolExecutor(hilos) as pool:
        for n_grupo, ((b, cuit, op), archs) in enumerate(ordenados, 1):
            clave = clave_idempotencia(b, cuit)
            job = await repo.existe_job(user_id, b, op, clave)
            if job is None:
                cuando = max([a["cuando"] or "" for a in archs] or [""])
                if not cuando:
                    cuando = datetime.now(timezone.utc).isoformat()
                job = await repo.crear_job(user_id, b, op, clave, cuit,
                                           archs[0]["bucket"] + "/" +
                                           archs[0]["key"].rsplit("/", 1)[0], cuando)
            for a in archs:
                nueva = f"jobs/{job}/{a['nombre']}"
                a["nueva"], a["job"] = nueva, job
            if n_grupo % 200 == 1:
                print(f"grupo {n_grupo}/{len(ordenados)}: {b}/{cuit} "
                      f"({len(archs)} archivos)", flush=True)
            pendientes = []
            for a in archs:
                # La fila DB manda: si ya existe, no se toca S3.
                if await repo.tiene_artefacto(a["nueva"]):
                    por_bot[b]["ya_existian"] += 1
                    continue
                pendientes.append(a)
            for ini in range(0, len(pendientes), 100):
                tanda = pendientes[ini:ini + 100]
                try:
                    bajados = await asyncio.gather(*[
                        loop.run_in_executor(pool, _bajar, s, kw, shas, a)
                        for a in tanda])
                    fila: list[dict] = []
                    for estado, a in bajados:
                        if estado == "duplicado":
                            por_bot[b]["duplicados"] += 1
                            continue
                        if estado != "listo":
                            raise ValueError(a.get("error", estado))
                        fila.append(a)
                    subidos = await asyncio.gather(*[
                        loop.run_in_executor(pool, _subir_o_falla, s, kw, a)
                        for a in fila])
                    for ok in subidos:
                        if ok is not True:
                            raise ValueError(str(ok))
                    n = await repo.crear_artefactos(
                        [{k: a[k] for k in
                          ("job", "nueva", "nombre", "content_type",
                           "size", "sha", "cuando")} for a in fila])
                    del fila
                    creados += n
                    por_bot[b]["adoptados"] += n
                except (RuntimeError, ValueError) as e:
                    errores.append({"bot": b, "error": str(e)[:200],
                                    "tanda": f"{ini}-{ini + len(tanda)}"})
                    por_bot[b]["ERROR"] += len(tanda)
    return {"modo": "APLICADO", **{k: cuenta[k] for k in sorted(cuenta)},
            "artefactos_creados": creados,
            "por_bot": {b: dict(c) for b, c in sorted(por_bot.items())},
            "_errores": errores}


def main(argv: list[str] | None = None) -> int:
    import json as _json

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--bot")
    ap.add_argument("--hilos", type=int, default=4)
    ap.add_argument("--auto-chequeo", action="store_true")
    a = ap.parse_args(argv)
    if a.auto_chequeo:
        return auto_chequeo()
    from central_api.settings import get_settings

    s = get_settings()
    r = asyncio.run(adoptar(RepoSQL(), s, aplicar=a.aplicar, limite=a.limite,
                            bot=a.bot, hilos=a.hilos))
    errores = r.pop("_errores")
    ruta = f"/tmp/adoptar_legacy_errores_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"
    with open(ruta, "w", encoding="utf-8") as fh:
        for e in errores:
            fh.write(_json.dumps(e, ensure_ascii=False) + "\n")
    print(_json.dumps({k: v for k, v in r.items() if k != "contenido"},
                      indent=1, ensure_ascii=False, default=str))
    print("errores en", ruta, f"({len(errores)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
