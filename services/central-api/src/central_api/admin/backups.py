"""Backups de PostgreSQL del panel: dump propio, tar.gz en S3 y restore.

Decisiones de diseño (la imagen de la central NO trae ``pg_dump``/``pg_restore``
y no se van a instalar):

- El dump se hace 100% en Python con psycopg3: ``COPY (SELECT * FROM tabla) TO
  STDOUT`` en formato texto, una tabla por archivo ``<tabla>.copy`` dentro de un
  ``tar.gz`` junto a ``manifest.json``. Funciona con cualquier versión de
  servidor y permite la semántica ``overwrite``/``append`` por tabla, que
  ``pg_restore`` no sabe expresar.
- El esquema NO se recrea desde el dump: el restore asume el esquema ya migrado
  (``python -m central_api.migrate``). El manifiesto guarda la revisión de
  Alembic y el restore la informa; si difiere no falla, lo reporta como
  ``alembic_mismatch``.
- Las tablas se ordenan topológicamente por dependencias de claves foráneas con
  ``ORDER BY`` estable, de modo que dos dumps iguales dan el mismo orden.
- Los ``COPY`` son síncronos: cada operación corre en ``asyncio.to_thread`` para
  no bloquear el event loop de la central. El dump se escribe en disco
  (``BACKUP_DIR``) y el temporal se borra siempre en ``finally``.

Límites conocidos (documentados también en la respuesta de la API): las tablas
sin clave primaria no permiten decidir el "pisotón" en ``overwrite`` (solo se
inserta con ``ON CONFLICT DO NOTHING``); las vistas, secuencias y tipos custom
no se incluyen (solo tablas base de ``public``); y las secuencias de columnas
con ``serial`` no se reajustan tras un restore con IDs explícitos.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.db import normalizar_dsn
from central_api.settings import Settings, get_settings
from central_api.store import utcnow

router = APIRouter()

MANIFEST_NAME = "manifest.json"
LLAVES_NAME = "llaves.json"
#: Settings con llaves privadas que se guardan en el dump (sin ellas las
#: credenciales cifradas de la base no se pueden descifrar al recrear todo).
LLAVES_PRIVADAS = {
    "RSA_PRIVATE_KEY": "rsa_private_key",
    "ASSIGNMENT_SIGNING_KEY": "assignment_signing_key",
}
#: Tablas que nunca entran al dump: datos efímeros y voluminosos (un latido
#: por worker cada pocos segundos) que se regeneran solos al reconectar.
TABLAS_EXCLUIDAS = frozenset({"worker_heartbeats"})
MANIFEST_VERSION = 1
CONFIRMACION_RESTORE = "RESTAURAR"
MODO_OVERWRITE = "overwrite"
MODO_APPEND = "append"
COPY_CHUNK = 1 << 20
MAX_PAGINAS_LISTADO = 50
PRESIGN_EXPIRACION_SEGUNDOS = 300

#: Identificadores admisibles para tablas del dump (se citan igualmente al
#: construir el SQL; la lista blanca evita cualquier nombre inyectado).
_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
#: Tag del backup (entorno/branch): va en el nombre del objeto, sin ``/``.
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_KEY_CON_TAG = re.compile(r"^\d{8}T\d{6}Z-(?:(.+)-)?mrbot-db\.tar\.gz$")

SIN_BASE = "DATABASE_URL no configurada"
SIN_BACKUP = (
    "backup no configurado: faltan OBJECT_STORAGE_ENDPOINT, BACKUP_BUCKET "
    "o las credenciales OBJECT_STORAGE_ACCESS_KEY/OBJECT_STORAGE_SECRET_KEY"
)


class BackupInvalido(RuntimeError):
    """El objeto descargado no es un backup válido (o no coincide su hash)."""


class SolicitudInvalida(ValueError):
    """La solicitud de backup/restore no se puede satisfacer tal como viene."""


# --------------------------------------------------------------------------
# Utilidades puras (probadas sin base ni red)
# --------------------------------------------------------------------------


def _citar(identificador: str) -> str:
    """Cita un identificador para SQL (comillas dobles escapadas)."""
    return '"' + identificador.replace('"', '""') + '"'


def _validar_nombre_tabla(nombre: str) -> str:
    texto = (nombre or "").strip()
    if not _IDENTIFICADOR.match(texto):
        raise SolicitudInvalida(f"nombre de tabla inválido: {nombre!r}")
    return texto


def _orden_topologico(
    tablas: Sequence[str], dependencias: Sequence[tuple[str, str]]
) -> list[str]:
    """Ordena las tablas para que las referenciadas (padres) vayan primero.

    ``dependencias`` son pares ``(hijo, padre)``: el hijo tiene una clave
    foránea hacia el padre, así que el padre debe restaurarse antes. El orden es
    estable (a igualdad de nivel, alfabético) para que dos dumps del mismo
    esquema produzcan el mismo orden. Si hay un ciclo, se rompe de forma
    determinista emitiendo la tabla pendiente alfabéticamente menor: el dump
    nunca falla por un ciclo y el resultado sigue siendo reproducible.
    """
    conocidas = list(dict.fromkeys(tablas))
    conjunto = set(conocidas)
    pendientes: dict[str, set[str]] = {tabla: set() for tabla in conocidas}
    for hijo, padre in dependencias:
        if hijo in conjunto and padre in conjunto and hijo != padre:
            pendientes[hijo].add(padre)
    orden: list[str] = []
    while pendientes:
        listas = sorted(tabla for tabla, deps in pendientes.items() if not deps)
        if not listas:
            listas = [sorted(pendientes)[0]]
        for tabla in listas:
            del pendientes[tabla]
            for deps in pendientes.values():
                deps.discard(tabla)
            orden.append(tabla)
    return orden


def _validar_tag(tag: str | None) -> str:
    texto = (tag or "").strip()
    if texto and not _TAG.match(texto):
        raise SolicitudInvalida(
            f"tag inválido: {tag!r} (letras, números, '.', '_' o '-'; máx. 64)"
        )
    return texto


def _tag_de_key(key: str) -> str:
    """Tag embebido en el nombre del objeto ('' si el dump no tiene)."""
    coincide = _KEY_CON_TAG.match(key.rsplit("/", 1)[-1])
    return (coincide.group(1) or "") if coincide else ""


def _nombre_key(prefijo: str, momento: datetime, tag: str = "") -> str:
    """``<prefijo>/<YYYYmmddTHHMMSSZ>[-<tag>]-mrbot-db.tar.gz``."""
    sello = momento.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    limpio = (prefijo or "").strip("/")
    nombre = f"{sello}-{tag}-mrbot-db.tar.gz" if tag else f"{sello}-mrbot-db.tar.gz"
    return f"{limpio}/{nombre}" if limpio else nombre


def _sha256_archivo(ruta: str | Path) -> str:
    digest = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(COPY_CHUNK), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _dsn_psycopg(url: str) -> str:
    """DSN que entiende ``psycopg.connect`` a partir del de SQLAlchemy.

    Reutiliza ``normalizar_dsn`` (que deja ``postgresql+psycopg://``) y quita el
    sufijo de driver: libpq no acepta el esquema con ``+psycopg``.
    """
    return normalizar_dsn(url).replace("postgresql+psycopg://", "postgresql://", 1)


def _sql_contar_colisiones(tabla: str, pk: Sequence[str]) -> str:
    """Cuenta las filas del backup que ya existen en el destino (por PK)."""
    condicion = " AND ".join(f"b.{_citar(c)} = t.{_citar(c)}" for c in pk)
    return (
        f"SELECT count(*) FROM {_citar(f'_bkp_{tabla}')} AS b "
        f"JOIN public.{_citar(tabla)} AS t ON {condicion}"
    )


def _sql_insert(tabla: str, columnas: Sequence[str]) -> str:
    """INSERT desde la tabla temporal: nunca pisa filas ya presentes."""
    lista = ", ".join(_citar(c) for c in columnas)
    return (
        f"INSERT INTO public.{_citar(tabla)} ({lista}) "
        f"SELECT {lista} FROM {_citar(f'_bkp_{tabla}')} ON CONFLICT DO NOTHING"
    )


def _sql_upsert(tabla: str, columnas: Sequence[str], pk: Sequence[str]) -> str:
    """Upsert del modo overwrite: reemplaza por PK sin borrar la fila.

    Con ``DELETE`` + ``INSERT`` el reemplazo fallaba cuando otra tabla
    referenciaba la fila (``ON DELETE RESTRICT``), dejando esa tabla sin
    restaurar. ``ON CONFLICT DO UPDATE`` pisa los valores en el lugar.
    ``RETURNING (xmax = 0)`` permite contar por separado insertadas y
    reemplazadas en una sola sentencia.
    """
    lista = ", ".join(_citar(c) for c in columnas)
    claves = ", ".join(_citar(c) for c in pk)
    asignaciones = ", ".join(
        f"{_citar(c)} = EXCLUDED.{_citar(c)}" for c in columnas if c not in pk
    )
    conflicto = f"ON CONFLICT ({claves}) DO UPDATE SET {asignaciones}"
    if not asignaciones:
        # Tabla cuya PK son todas sus columnas: no hay nada que actualizar.
        conflicto = f"ON CONFLICT ({claves}) DO NOTHING"
    return (
        f"INSERT INTO public.{_citar(tabla)} ({lista}) "
        f"SELECT {lista} FROM {_citar(f'_bkp_{tabla}')} {conflicto} "
        "RETURNING (xmax = 0)"
    )


def _plan_sql(
    tabla: str, columnas: Sequence[str], pk: Sequence[str], modo: str
) -> list[str]:
    """Sentencias que aplicaría el restore (usadas como plan en ``dry_run``)."""
    pasos: list[str] = []
    if modo == MODO_OVERWRITE and pk:
        pasos.append(_sql_upsert(tabla, columnas, pk))
    else:
        pasos.append(_sql_insert(tabla, columnas))
    return pasos


def _iso(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.astimezone(timezone.utc).isoformat()
    return str(valor or "")


# --------------------------------------------------------------------------
# Acceso a PostgreSQL (síncrono: siempre se invoca vía asyncio.to_thread)
# --------------------------------------------------------------------------


def _conectar(dsn: str):
    import psycopg

    return psycopg.connect(dsn)


def _tablas_base(cur: Any) -> list[str]:
    """Tablas base (``relkind='r'``) de ``public``, en orden estable."""
    cur.execute(
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
    )
    return [str(fila[0]) for fila in cur.fetchall()]


def _dependencias_fk(cur: Any) -> list[tuple[str, str]]:
    """Pares ``(hijo, padre)`` de claves foráneas entre tablas base de public."""
    cur.execute(
        "SELECT hijo.relname, padre.relname FROM pg_constraint c "
        "JOIN pg_class hijo ON hijo.oid = c.conrelid "
        "JOIN pg_class padre ON padre.oid = c.confrelid "
        "JOIN pg_namespace n ON n.oid = hijo.relnamespace "
        "WHERE c.contype = 'f' AND n.nspname = 'public' "
        "AND hijo.relkind = 'r' AND padre.relkind = 'r' "
        "ORDER BY hijo.relname, padre.relname"
    )
    return [(str(fila[0]), str(fila[1])) for fila in cur.fetchall()]


def _columnas_tabla(cur: Any, tabla: str) -> list[str]:
    """Columnas en el orden de ``information_schema.columns`` (ordinal)."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (tabla,),
    )
    return [str(fila[0]) for fila in cur.fetchall()]


def _claves_primarias(cur: Any, tabla: str) -> list[str]:
    """Columnas de la PK en su orden (soporta PK compuesta); [] si no hay."""
    cur.execute(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
        "WHERE i.indisprimary AND n.nspname = 'public' AND c.relname = %s "
        "ORDER BY k.ord",
        (tabla,),
    )
    return [str(fila[0]) for fila in cur.fetchall()]


def _tabla_existe(cur: Any, tabla: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{tabla}",))
    fila = cur.fetchone()
    return bool(fila and fila[0])


def _revision_alembic(cur: Any) -> str | None:
    """Revisión de Alembic del destino, o None si no hay tabla/registro."""
    try:
        cur.execute("SELECT version_num FROM alembic_version")
    except Exception:  # noqa: BLE001 - sin alembic_version no hay revisión
        return None
    fila = cur.fetchone()
    return str(fila[0]) if fila and fila[0] is not None else None


def _nombre_bd(cur: Any) -> str:
    cur.execute("SELECT current_database()")
    fila = cur.fetchone()
    return str(fila[0]) if fila else ""


# --------------------------------------------------------------------------
# Dump
# --------------------------------------------------------------------------


def _volcar_tablas(
    *, dsn: str, tablas: Sequence[str], incluir_datos: bool, destino: str
) -> dict[str, Any]:
    """Escribe ``<tabla>.copy`` por tabla y devuelve los datos del manifiesto."""
    entradas: list[dict[str, Any]] = []
    total_filas = 0
    tamano_crudo = 0
    with _conectar(dsn) as conn, conn.cursor() as cur:
        catalogo = _tablas_base(cur)
        excluidas = sorted(t for t in (tablas or []) if t in TABLAS_EXCLUIDAS)
        if excluidas:
            raise SolicitudInvalida(
                "tablas excluidas del dump: " + ", ".join(excluidas)
            )
        seleccion = (
            list(tablas)
            if tablas
            else [t for t in catalogo if t not in TABLAS_EXCLUIDAS]
        )
        faltantes = [t for t in seleccion if t not in catalogo]
        if faltantes:
            raise SolicitudInvalida(
                "tablas inexistentes en la base: " + ", ".join(sorted(faltantes))
            )
        orden = _orden_topologico(seleccion, _dependencias_fk(cur))
        base = _nombre_bd(cur)
        revision = _revision_alembic(cur)
        for tabla in orden:
            columnas = _columnas_tabla(cur, tabla)
            if not columnas:
                raise SolicitudInvalida(f"la tabla {tabla} no tiene columnas en public")
            ruta = os.path.join(destino, f"{tabla}.copy")
            filtro = "" if incluir_datos else " WHERE false"
            filas = 0
            with open(ruta, "wb") as archivo:
                sentencia = (
                    f"COPY (SELECT * FROM public.{_citar(tabla)}{filtro}) TO STDOUT"
                )
                with cur.copy(sentencia) as copia:
                    for bloque in copia:
                        datos = bytes(bloque)
                        archivo.write(datos)
                        filas += datos.count(b"\n")
            tamano_crudo += os.path.getsize(ruta)
            entradas.append(
                {
                    "nombre": tabla,
                    "columnas": columnas,
                    "filas": filas,
                    "sha256": _sha256_archivo(ruta),
                }
            )
            total_filas += filas
    return {
        "database": base,
        "alembic_revision": revision,
        "tablas": entradas,
        "total_filas": total_filas,
        "size_raw": tamano_crudo,
    }


def _empaquetar(
    destino: str,
    tar_path: str,
    manifiesto: dict[str, Any],
    llaves: dict[str, str] | None = None,
) -> int:
    """Comprime ``<tabla>.copy`` + manifest.json (+ llaves.json) en tar.gz."""
    with open(os.path.join(destino, MANIFEST_NAME), "w", encoding="utf-8") as archivo:
        json.dump(manifiesto, archivo, ensure_ascii=False, indent=2)
    with tarfile.open(tar_path, "w:gz") as tar:
        for entrada in manifiesto.get("tablas") or []:
            nombre = f"{entrada['nombre']}.copy"
            tar.add(os.path.join(destino, nombre), arcname=nombre)
        tar.add(os.path.join(destino, MANIFEST_NAME), arcname=MANIFEST_NAME)
        if llaves:
            ruta = os.path.join(destino, LLAVES_NAME)
            with open(os.open(ruta, os.O_WRONLY | os.O_CREAT, 0o600), "w") as archivo:
                json.dump(llaves, archivo, indent=2)
            tar.add(ruta, arcname=LLAVES_NAME)
    return os.path.getsize(tar_path)


def _crear_dump(
    *,
    settings: Settings,
    tablas: Sequence[str],
    incluir_datos: bool,
    momento: datetime,
    tag: str = "",
) -> dict[str, Any]:
    """Dump completo: escribe en disco, empaqueta, sube y limpia el temporal."""
    base = Path(settings.backup_dir)
    base.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix="dump-", dir=str(base)))
    try:
        datos = _volcar_tablas(
            dsn=_dsn_psycopg(settings.database_url),
            tablas=tablas,
            incluir_datos=incluir_datos,
            destino=str(trabajo),
        )
        llaves = {
            nombre: valor
            for nombre, atributo in LLAVES_PRIVADAS.items()
            if (valor := str(getattr(settings, atributo, "") or "").strip())
        }
        manifiesto = {
            "version": MANIFEST_VERSION,
            "tag": tag,
            "llaves": sorted(llaves),
            "created_at": momento.astimezone(timezone.utc).isoformat(),
            "database": datos["database"],
            "alembic_revision": datos["alembic_revision"],
            "incluir_datos": incluir_datos,
            "tablas": datos["tablas"],
            "total_filas": datos["total_filas"],
        }
        key = _nombre_key(settings.backup_prefix, momento, tag)
        tar_path = trabajo / key.rsplit("/", 1)[-1]
        size_bytes = _empaquetar(str(trabajo), str(tar_path), manifiesto, llaves)
        sha256 = _sha256_archivo(tar_path)
        _subir_archivo(settings, tar_path, key)
        return {
            "success": True,
            "bucket": settings.backup_bucket,
            "key": key,
            "tag": tag,
            "llaves": sorted(llaves),
            "size_bytes": size_bytes,
            "size_raw": datos["size_raw"],
            "tablas": datos["tablas"],
            "cantidad_tablas": len(datos["tablas"]),
            "total_filas": datos["total_filas"],
            "sha256": sha256,
            "created_at": manifiesto["created_at"],
            "database": datos["database"],
            "alembic_revision": datos["alembic_revision"],
        }
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def _extraer_y_verificar(tar_path: str, destino: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Extrae el tar.gz y valida el sha256 de cada ``.copy`` del manifiesto.

    Se lee miembro a miembro (nunca ``extractall``) y los nombres se validan
    contra la lista blanca de identificadores: nada del archivo decide rutas.
    """
    rutas: dict[str, str] = {}
    with tarfile.open(tar_path, "r:gz") as tar:
        try:
            miembro = tar.getmember(MANIFEST_NAME)
        except KeyError as exc:
            raise BackupInvalido("el backup no contiene manifest.json") from exc
        if not miembro.isfile():
            raise BackupInvalido("el manifiesto del backup no es un archivo regular")
        crudo = tar.extractfile(miembro)
        if crudo is None:
            raise BackupInvalido("el manifiesto del backup no se puede leer")
        try:
            manifiesto = json.loads(crudo.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupInvalido("el manifiesto del backup no es JSON válido") from exc
        if not isinstance(manifiesto, dict):
            raise BackupInvalido("el manifiesto del backup no es un objeto JSON")
        entradas = manifiesto.get("tablas")
        if not isinstance(entradas, list) or not entradas:
            raise BackupInvalido("el manifiesto del backup no declara tablas")
        for entrada in entradas:
            if not isinstance(entrada, dict):
                raise BackupInvalido("el manifiesto declara una entrada de tabla inválida")
            nombre = str(entrada.get("nombre") or "")
            if not _IDENTIFICADOR.match(nombre):
                raise BackupInvalido(f"el manifiesto declara una tabla inválida: {nombre!r}")
            if not entrada.get("columnas"):
                raise BackupInvalido(f"el manifiesto no declara columnas de {nombre}")
            try:
                miembro = tar.getmember(f"{nombre}.copy")
            except KeyError as exc:
                raise BackupInvalido(f"falta {nombre}.copy dentro del backup") from exc
            if not miembro.isfile():
                raise BackupInvalido(
                    f"{nombre}.copy no es un archivo regular dentro del backup"
                )
            ruta = os.path.join(destino, f"{nombre}.copy")
            digest = hashlib.sha256()
            with tar.extractfile(miembro) as origen, open(ruta, "wb") as archivo:
                if origen is None:
                    raise BackupInvalido(f"no se pudo leer {nombre}.copy del backup")
                while True:
                    bloque = origen.read(COPY_CHUNK)
                    if not bloque:
                        break
                    digest.update(bloque)
                    archivo.write(bloque)
            esperado = str(entrada.get("sha256") or "")
            if not esperado or digest.hexdigest() != esperado:
                raise BackupInvalido(
                    f"sha256 no coincide para {nombre}.copy: el backup está corrupto"
                )
            rutas[nombre] = ruta
    return manifiesto, rutas


def _restaurar_tabla(
    cur: Any,
    *,
    entrada: dict[str, Any],
    ruta: str,
    modo: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Restaura una tabla desde su ``.copy`` dentro de la transacción en curso."""
    tabla = str(entrada["nombre"])
    columnas = [str(c) for c in entrada["columnas"]]
    pk = _claves_primarias(cur, tabla)
    presentes = set(_columnas_tabla(cur, tabla))
    faltan = [c for c in columnas if c not in presentes]
    if faltan:
        raise RuntimeError(
            f"la tabla {tabla} del destino no tiene las columnas: {', '.join(faltan)}"
        )
    temporal = _citar(f"_bkp_{tabla}")
    cur.execute(
        f"CREATE TEMP TABLE {temporal} "
        f"(LIKE public.{_citar(tabla)} INCLUDING DEFAULTS)"
    )
    lista = ", ".join(_citar(c) for c in columnas)
    with cur.copy(f"COPY {temporal} ({lista}) FROM STDIN") as copia:
        with open(ruta, "rb") as archivo:
            for bloque in iter(lambda: archivo.read(COPY_CHUNK), b""):
                copia.write(bloque)
    filas = max(0, int(cur.rowcount))
    resultado: dict[str, Any] = {
        "tabla": tabla,
        "modo": modo,
        "filas_archivo": filas,
        "insertadas": 0,
        "reemplazadas": 0,
        "omitidas": 0,
        "sin_pk": not pk,
        "dry_run": dry_run,
    }
    if dry_run:
        colisiones: int | None = None
        if pk:
            cur.execute(_sql_contar_colisiones(tabla, pk))
            fila = cur.fetchone()
            colisiones = int(fila[0]) if fila else 0
        resultado.update(
            {
                "colisionarian": colisiones,
                # En append nada se pisa: las colisiones se omitirían, no se reemplazan.
                "reemplazadas": (colisiones or 0) if modo == MODO_OVERWRITE else 0,
                "plan": _plan_sql(tabla, columnas, pk, modo),
            }
        )
        if not pk:
            resultado["advertencia"] = (
                "tabla sin clave primaria: no se puede decidir el pisotón; "
                "solo se puede insertar con ON CONFLICT DO NOTHING"
            )
        return resultado
    if modo == MODO_OVERWRITE and pk:
        # Upsert: no borra la fila, así no choca con las referencias de otras
        # tablas y el conteo sale de la propia sentencia.
        cur.execute(_sql_upsert(tabla, columnas, pk))
        marcas = [fila[0] for fila in cur.fetchall()]
        resultado["insertadas"] = sum(1 for es_nueva in marcas if es_nueva)
        resultado["reemplazadas"] = len(marcas) - resultado["insertadas"]
        resultado["omitidas"] = max(0, filas - len(marcas))
        return resultado
    cur.execute(_sql_insert(tabla, columnas))
    resultado["insertadas"] = max(0, int(cur.rowcount))
    resultado["omitidas"] = max(0, filas - resultado["insertadas"])
    if modo == MODO_OVERWRITE and not pk:
        resultado["advertencia"] = (
            "tabla sin clave primaria: no se puede decidir el pisotón; se insertó "
            "todo lo que la tabla aceptó con ON CONFLICT DO NOTHING"
        )
    return resultado


def _restaurar_sincrono(
    *,
    dsn: str,
    tar_path: str,
    modo: str,
    tablas: Sequence[str],
    dry_run: bool,
    destino: str,
) -> dict[str, Any]:
    """Restaura (o simula) el backup; una transacción por tabla."""
    manifiesto, rutas = _extraer_y_verificar(tar_path, destino)
    entradas = {str(e["nombre"]): e for e in manifiesto["tablas"]}
    orden = [str(e["nombre"]) for e in manifiesto["tablas"]]
    if tablas:
        pedidas = [_validar_nombre_tabla(t) for t in tablas]
        desconocidas = [t for t in pedidas if t not in entradas]
        if desconocidas:
            raise SolicitudInvalida(
                "el backup no contiene las tablas: " + ", ".join(sorted(desconocidas))
            )
        seleccion = [t for t in orden if t in set(pedidas)]
    else:
        seleccion = orden
    resultados: list[dict[str, Any]] = []
    detenido = False
    with _conectar(dsn) as conn, conn.cursor() as cur:
        revision_destino = _revision_alembic(cur)
        for nombre in seleccion:
            entrada = entradas[nombre]
            if not _tabla_existe(cur, nombre):
                resultados.append(
                    {
                        "tabla": nombre,
                        "modo": modo,
                        "dry_run": dry_run,
                        "filas_archivo": int(entrada.get("filas") or 0),
                        "insertadas": 0,
                        "reemplazadas": 0,
                        "omitidas": 0,
                        "sin_pk": False,
                        "error": "la tabla no existe en la base destino (esquema sin migrar?)",
                        "error_tipo": "tabla_ausente",
                    }
                )
                continue
            try:
                with conn.transaction():
                    resultados.append(
                        _restaurar_tabla(
                            cur,
                            entrada=entrada,
                            ruta=rutas[nombre],
                            modo=modo,
                            dry_run=dry_run,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - se informa por tabla y se corta
                resultados.append(
                    {
                        "tabla": nombre,
                        "modo": modo,
                        "dry_run": dry_run,
                        "filas_archivo": int(entrada.get("filas") or 0),
                        "insertadas": 0,
                        "reemplazadas": 0,
                        "omitidas": 0,
                        "sin_pk": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_tipo": "ejecucion",
                    }
                )
                detenido = True
                break
    con_error = [r["tabla"] for r in resultados if r.get("error")]
    aplicadas = [r["tabla"] for r in resultados if not r.get("error")]
    resumen = {
        "tablas_seleccionadas": len(seleccion),
        "tablas_procesadas": len(resultados) - len(con_error),
        "tablas_con_error": len(con_error),
        "insertadas": sum(int(r.get("insertadas") or 0) for r in resultados),
        "reemplazadas": sum(int(r.get("reemplazadas") or 0) for r in resultados),
        "omitidas": sum(int(r.get("omitidas") or 0) for r in resultados),
        "dry_run": dry_run,
        "detenido": detenido,
        "tablas_aplicadas": aplicadas,
        "tablas_con_error_detalle": con_error,
    }
    revision_dump = manifiesto.get("alembic_revision")
    alembic_mismatch = bool(
        revision_dump and revision_destino and revision_dump != revision_destino
    )
    advertencias: list[str] = []
    if not manifiesto.get("incluir_datos", True):
        advertencias.append(
            "el dump se creó sin datos (incluir_datos=false): solo restaura cabeceras"
        )
    if alembic_mismatch:
        advertencias.append(
            f"el dump es de la revisión {revision_dump} y el destino está en "
            f"{revision_destino}: el esquema no se recrea, ejecute "
            "python -m central_api.migrate si corresponde"
        )
    if alembic_mismatch and "alembic_version" in aplicadas:
        advertencias.append(
            "se restauró alembic_version: el marcador volvió a la revisión del dump"
        )
    sin_pk = [r["tabla"] for r in resultados if r.get("sin_pk")]
    if modo == MODO_OVERWRITE and sin_pk and not dry_run:
        advertencias.append(
            "tablas sin clave primaria (no se puede decidir el pisotón, solo se "
            f"insertó con ON CONFLICT DO NOTHING): {', '.join(sin_pk)}"
        )
    return {
        "success": not detenido and not con_error,
        "modo": modo,
        "dry_run": dry_run,
        "bucket_key_created_at": manifiesto.get("created_at"),
        "alembic_revision": revision_dump,
        "alembic_revision_destino": revision_destino,
        "alembic_mismatch": alembic_mismatch,
        "advertencias": advertencias,
        "tablas": resultados,
        "resumen": resumen,
    }


# --------------------------------------------------------------------------
# Object storage (boto3, binding path-style como el resto del proyecto)
# --------------------------------------------------------------------------


def _cliente_s3(settings: Settings):
    """Cliente S3 con firma v4 y direccionamiento por path (Mega S4/MinIO).

    Import diferido a propósito: la central solo necesita boto3 si se usa el
    panel de backups (o el autoscaler). ``Config`` evita el estilo virtual-host,
    que los endpoints compatibles S3 self-hosted no resuelven.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - depende del extra opcional
        raise RuntimeError(
            "boto3 no está instalado; instale central-api[autoscaler]"
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint or None,
        region_name=settings.object_storage_region or "us-east-1",
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
        use_ssl=settings.object_storage_use_ssl,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _subir_archivo(settings: Settings, ruta: str | Path, key: str) -> None:
    _cliente_s3(settings).upload_file(
        str(ruta),
        settings.backup_bucket,
        key,
        ExtraArgs={"ContentType": "application/gzip"},
    )


def _descargar_archivo(settings: Settings, key: str, ruta: str | Path) -> None:
    _cliente_s3(settings).download_file(settings.backup_bucket, key, str(ruta))


def _listar_objetos(settings: Settings) -> dict[str, Any]:
    """Lista el bucket con paginación explícita, ordenado del más nuevo al más viejo."""
    cliente = _cliente_s3(settings)
    prefijo = (settings.backup_prefix or "").strip("/")
    objetos: list[dict[str, Any]] = []
    token: str | None = None
    truncado = False
    for _ in range(MAX_PAGINAS_LISTADO):
        parametros: dict[str, Any] = {"Bucket": settings.backup_bucket, "MaxKeys": 1000}
        if prefijo:
            parametros["Prefix"] = f"{prefijo}/"
        if token:
            parametros["ContinuationToken"] = token
        respuesta = cliente.list_objects_v2(**parametros)
        for item in respuesta.get("Contents") or []:
            objetos.append(
                {
                    "key": str(item.get("Key") or ""),
                    "tag": _tag_de_key(str(item.get("Key") or "")),
                    "size_bytes": int(item.get("Size") or 0),
                    "last_modified": _iso(item.get("LastModified")),
                }
            )
        token = respuesta.get("NextContinuationToken")
        if not respuesta.get("IsTruncated") or not token:
            break
        truncado = True
    objetos.sort(key=lambda o: (o["last_modified"], o["key"]), reverse=True)
    return {
        "success": True,
        "bucket": settings.backup_bucket,
        "prefijo": prefijo,
        "objects": objetos,
        "count": len(objetos),
        "truncado": truncado,
    }


def _url_descarga(settings: Settings, key: str) -> str:
    return _cliente_s3(settings).generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.backup_bucket, "Key": key},
        ExpiresIn=PRESIGN_EXPIRACION_SEGUNDOS,
    )


# --------------------------------------------------------------------------
# Modelos de entrada
# --------------------------------------------------------------------------


class DumpBody(BaseModel):
    tag: str | None = Field(
        default=None,
        description=(
            "Etiqueta del backup (p. ej. produccion, testing o una branch); va en "
            "el nombre del objeto y en el manifiesto. Si se omite se usa BACKUP_TAG. "
            "Letras, números, '.', '_' y '-' (máx. 64)."
        ),
        examples=["produccion"],
    )
    motivo: str = Field(
        description="Justificación de auditoría del dump; entre 10 y 500 caracteres.",
        examples=["Respaldo previo a la migración de octubre"],
    )
    tablas: list[str] = Field(
        default_factory=list,
        description=(
            "Tablas a incluir. Vacío (por defecto) incluye todas las tablas base "
            "de public, ordenadas por dependencias de claves foráneas."
        ),
        examples=[["users", "jobs"]],
    )
    incluir_datos: bool = Field(
        default=True,
        description=(
            "Si es true (por defecto) el dump lleva los datos; si es false solo "
            "quedan las cabeceras de cada tabla (filas 0) para pruebas."
        ),
        examples=[True],
    )


class RestoreBody(BaseModel):
    key: str = Field(
        min_length=1,
        max_length=512,
        description="Clave del objeto en el bucket de backups (ver GET /admin/backups).",
        examples=["dump/20261016T183000Z-mrbot-db.tar.gz"],
    )
    modo: str = Field(
        default=MODO_OVERWRITE,
        pattern="^(overwrite|append)$",
        description=(
            "overwrite: los registros que se pisan por clave primaria se "
            "reemplazan. append: se agregan sin pisar los existentes "
            "(ON CONFLICT DO NOTHING)."
        ),
        examples=["overwrite"],
    )
    motivo: str = Field(
        description="Justificación de auditoría del restore; entre 10 y 500 caracteres.",
        examples=["Restauración solicitada por soporte"],
    )
    tablas: list[str] = Field(
        default_factory=list,
        description=(
            "Tablas a restaurar. Vacío (por defecto) restaura todas las del "
            "manifiesto, en su orden topológico."
        ),
        examples=[["users"]],
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Si es true solo simula: cuenta filas y colisiones y devuelve el plan "
            "SQL sin ejecutar DELETE ni INSERT."
        ),
        examples=[False],
    )
    confirmacion: str = Field(
        default="",
        description=(
            'Confirmación explícita del operador: debe ser exactamente "RESTAURAR". '
            "Es una operación destructiva en modo overwrite."
        ),
        examples=["RESTAURAR"],
    )


_AUTH_RESPONSES = {
    401: {
        "description": (
            "Falta la cabecera Bearer o no tiene el formato esperado. Envíe "
            "Authorization: Bearer <ADMIN_TOKEN>."
        )
    },
    403: {
        "description": (
            "El token administrativo no está configurado o no coincide. Configure "
            "ADMIN_TOKEN en el servicio o use el valor vigente."
        )
    },
}

_503_BACKUP = {
    503: {
        "description": (
            "No se pudo completar la operación de backup: falta configuración "
            "(BACKUP_BUCKET, endpoint o credenciales), no hay DATABASE_URL o el "
            "almacenamiento/base no respondió. El detalle informa el tipo de error."
        )
    }
}

_400_MOTIVO = {
    400: {
        "description": (
            "El motivo está vacío o no tiene entre 10 y 500 caracteres, la "
            'confirmación no es exactamente "RESTAURAR", la tabla pedida no existe '
            "o el backup descargado no es válido (hash o manifiesto)."
        )
    }
}


def _settings_backup() -> Settings:
    settings = get_settings()
    if not settings.backup_configurado:
        raise HTTPException(status_code=503, detail=SIN_BACKUP)
    return settings


def _sin_cache(respuesta: Response) -> None:
    respuesta.headers["Cache-Control"] = "private, no-store"


def _error_http(exc: Exception) -> HTTPException:
    """Traduce un fallo del dump/restore a una respuesta clara sin filtrar secretos."""
    if isinstance(exc, (BackupInvalido, SolicitudInvalida)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(
        status_code=503,
        detail=f"backup no disponible: {type(exc).__name__}: {exc}",
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.post(
    "/backups/dump",
    summary="Crear un dump de la base y subirlo al bucket de backups",
    description=(
        "Recorre las tablas base de public (o las indicadas en tablas) en orden "
        "topológico por claves foráneas, exporta cada una con COPY en formato "
        "texto, arma un tar.gz con un archivo por tabla y un manifest.json "
        "(revisión de Alembic, columnas, filas y sha256 de cada tabla) y lo sube "
        "a s3://BACKUP_BUCKET/BACKUP_PREFIX/<timestamp>[-<tag>]-mrbot-db.tar.gz, junto "
        "con llaves.json (RSA_PRIVATE_KEY y ASSIGNMENT_SIGNING_KEY): quien lea el "
        "bucket puede descifrar las credenciales, protéjalo. El dump "
        "se escribe en disco (BACKUP_DIR) y el temporal se borra al terminar. "
        "Requiere autorización administrativa y un motivo de auditoría."
    ),
    responses={
        **_AUTH_RESPONSES,
        **_400_MOTIVO,
        **_503_BACKUP,
        200: {
            "description": "Dump creado y subido al bucket de backups.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "bucket": "mrbot-bots-api-bkp-db",
                        "key": "dump/20261016T183000Z-mrbot-db.tar.gz",
                        "size_bytes": 184320,
                        "size_raw": 1200000,
                        "cantidad_tablas": 60,
                        "total_filas": 12345,
                        "sha256": "9f2c...",
                        "created_at": "2026-10-16T18:30:00+00:00",
                        "duracion_segundos": 4.2,
                    }
                }
            },
        },
    },
)
async def backups_dump(
    body: DumpBody,
    respuesta: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Crea el dump, lo comprime y lo sube; audita el resultado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    settings = _settings_backup()
    if not settings.database_url:
        raise HTTPException(status_code=503, detail=SIN_BASE)
    tablas = [_validar_nombre_tabla(t) for t in body.tablas]
    tag = _validar_tag(body.tag or settings.backup_tag)
    momento = utcnow()
    _sin_cache(respuesta)
    inicio = time.monotonic()
    try:
        resultado = await asyncio.to_thread(
            _crear_dump,
            settings=settings,
            tablas=tablas,
            incluir_datos=body.incluir_datos,
            momento=momento,
            tag=tag,
        )
    except Exception as exc:  # noqa: BLE001 - se audita y se informa el tipo
        log_event(
            "backup.dump",
            actor_id=actor,
            target_type="database-backup",
            target_id=settings.backup_bucket,
            request_id=request_id or "",
            result="error",
            reason=motivo,
            metadata={"tablas": tablas, "error": type(exc).__name__},
        )
        raise _error_http(exc) from exc
    resultado["duracion_segundos"] = round(time.monotonic() - inicio, 3)
    log_event(
        "backup.dump",
        actor_id=actor,
        target_type="database-backup",
        target_id=resultado["key"],
        request_id=request_id or "",
        reason=motivo,
        metadata={
            "bucket": resultado["bucket"],
            "size_bytes": resultado["size_bytes"],
            "total_filas": resultado["total_filas"],
            "cantidad_tablas": resultado["cantidad_tablas"],
            "incluir_datos": body.incluir_datos,
            "tag": tag,
            "llaves": resultado.get("llaves"),
        },
    )
    return resultado


@router.get(
    "/backups",
    summary="Listar los dumps del bucket de backups",
    description=(
        "Lista los objetos del bucket de backups (clave, tamaño y fecha) con "
        "paginación. Si el bucket no responde devuelve success=false y el tipo de "
        "error, para que el panel pueda mostrar el problema sin romperse. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_AUTH_RESPONSES,
        200: {
            "description": "Listado de dumps (o el problema de configuración/acceso).",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "bucket": "mrbot-bots-api-bkp-db",
                        "prefijo": "dump",
                        "count": 1,
                        "objects": [
                            {
                                "key": "dump/20261016T183000Z-mrbot-db.tar.gz",
                                "size_bytes": 184320,
                                "last_modified": "2026-10-16T18:30:04+00:00",
                            }
                        ],
                    }
                }
            },
        },
    },
)
async def backups_listar(
    respuesta: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    """Devuelve el inventario de dumps sin romper ante un bucket inaccesible."""
    require_admin(authorization)
    _sin_cache(respuesta)
    settings = get_settings()
    if not settings.backup_configurado:
        return {
            "success": False,
            "bucket": settings.backup_bucket,
            "prefijo": (settings.backup_prefix or "").strip("/"),
            "objects": [],
            "count": 0,
            "error": "no_configurado",
            "detalle": SIN_BACKUP,
        }
    try:
        return await asyncio.to_thread(_listar_objetos, settings)
    except Exception as exc:  # noqa: BLE001 - el panel muestra el problema
        return {
            "success": False,
            "bucket": settings.backup_bucket,
            "prefijo": (settings.backup_prefix or "").strip("/"),
            "objects": [],
            "count": 0,
            "error": type(exc).__name__,
            "detalle": str(exc),
        }


@router.post(
    "/backups/restore",
    summary="Restaurar un dump con modo overwrite o append",
    description=(
        "Descarga el tar.gz del bucket, valida el sha256 de cada tabla contra el "
        "manifiesto (si no coincide aborta sin tocar la base) y aplica tabla por "
        "tabla con una transacción por tabla: overwrite reemplaza los registros "
        "que se pisan por clave primaria y append agrega sin pisar nada. Con "
        "dry_run=true solo cuenta filas y colisiones y devuelve el plan SQL. "
        "Requiere confirmacion=RESTAURAR, autorización administrativa y motivo. "
        "El esquema destino debe estar ya migrado: el restore no recrea tablas."
    ),
    responses={
        **_AUTH_RESPONSES,
        **_400_MOTIVO,
        **_503_BACKUP,
        200: {
            "description": "Resultado del restore (o de la simulación) por tabla.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "modo": "overwrite",
                        "dry_run": False,
                        "alembic_revision": "0027",
                        "alembic_mismatch": False,
                        "tablas": [
                            {
                                "tabla": "users",
                                "filas_archivo": 10,
                                "insertadas": 9,
                                "reemplazadas": 1,
                                "omitidas": 0,
                                "sin_pk": False,
                            }
                        ],
                        "resumen": {"tablas_procesadas": 1, "insertadas": 9, "reemplazadas": 1},
                    }
                }
            },
        },
    },
)
async def backups_restore(
    body: RestoreBody,
    respuesta: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Restaura el dump indicado según el modo y la selección de tablas."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if body.confirmacion != CONFIRMACION_RESTORE:
        raise HTTPException(
            status_code=400,
            detail=(
                f'confirmacion debe ser exactamente "{CONFIRMACION_RESTORE}": el '
                "restore puede reemplazar registros y requiere confirmación explícita"
            ),
        )
    key = (body.key or "").strip()
    if not key or ".." in key.split("/"):
        raise HTTPException(status_code=400, detail="key de backup inválida")
    tablas = [_validar_nombre_tabla(t) for t in body.tablas]
    settings = _settings_backup()
    if not settings.database_url:
        raise HTTPException(status_code=503, detail=SIN_BASE)
    _sin_cache(respuesta)
    inicio = time.monotonic()
    try:
        resultado = await asyncio.to_thread(
            _restaurar_desde_bucket,
            settings=settings,
            key=key,
            modo=body.modo,
            tablas=tablas,
            dry_run=body.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - se audita y se informa el tipo
        log_event(
            "backup.restore",
            actor_id=actor,
            target_type="database-backup",
            target_id=key,
            request_id=request_id or "",
            result="error",
            reason=motivo,
            metadata={"modo": body.modo, "dry_run": body.dry_run, "error": type(exc).__name__},
        )
        raise _error_http(exc) from exc
    resultado["duracion_segundos"] = round(time.monotonic() - inicio, 3)
    resultado["key"] = key
    resumen = resultado.get("resumen") or {}
    log_event(
        "backup.restore",
        actor_id=actor,
        target_type="database-backup",
        target_id=key,
        request_id=request_id or "",
        result="success" if resultado.get("success") else "error",
        reason=motivo,
        metadata={
            "modo": body.modo,
            "dry_run": body.dry_run,
            "tablas": tablas,
            "insertadas": resumen.get("insertadas"),
            "reemplazadas": resumen.get("reemplazadas"),
            "tablas_con_error": resumen.get("tablas_con_error"),
            "alembic_mismatch": resultado.get("alembic_mismatch"),
        },
    )
    return resultado


def _restaurar_desde_bucket(
    *,
    settings: Settings,
    key: str,
    modo: str,
    tablas: Sequence[str],
    dry_run: bool,
) -> dict[str, Any]:
    """Descarga el objeto a BACKUP_DIR, restaura y borra el temporal."""
    base = Path(settings.backup_dir)
    base.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix="restore-", dir=str(base)))
    try:
        local = trabajo / os.path.basename(key)
        _descargar_archivo(settings, key, local)
        extraccion = trabajo / "extraido"
        extraccion.mkdir()
        return _restaurar_sincrono(
            dsn=_dsn_psycopg(settings.database_url),
            tar_path=str(local),
            modo=modo,
            tablas=tablas,
            dry_run=dry_run,
            destino=str(extraccion),
        )
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


@router.get(
    "/backups/{key:path}",
    summary="URL prefirmada de descarga de un dump",
    description=(
        "Devuelve una URL prefirmada de vigencia corta para descargar el dump "
        "indicado sin exponer las credenciales del bucket. Requiere autorización "
        "administrativa."
    ),
    responses={
        **_AUTH_RESPONSES,
        **_503_BACKUP,
        200: {
            "description": "URL de descarga prefirmada del objeto.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "bucket": "mrbot-bots-api-bkp-db",
                        "key": "dump/20261016T183000Z-mrbot-db.tar.gz",
                        "expires_seconds": 300,
                        "download_url": "https://...",
                    }
                }
            },
        },
    },
)
async def backups_descargar(
    key: str,
    respuesta: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    """Firma una descarga temporal del dump pedido."""
    require_admin(authorization)
    settings = _settings_backup()
    limpia = (key or "").strip().lstrip("/")
    if not limpia or ".." in limpia.split("/"):
        raise HTTPException(status_code=400, detail="key de backup inválida")
    _sin_cache(respuesta)
    try:
        url = await asyncio.to_thread(_url_descarga, settings, limpia)
    except Exception as exc:  # noqa: BLE001 - nunca se filtra la credencial
        raise HTTPException(
            status_code=503,
            detail=f"no se pudo firmar la descarga: {type(exc).__name__}",
        ) from exc
    return {
        "success": True,
        "bucket": settings.backup_bucket,
        "key": limpia,
        "expires_seconds": PRESIGN_EXPIRACION_SEGUNDOS,
        "download_url": url,
    }


__all__ = [
    "BackupInvalido",
    "MODO_APPEND",
    "MODO_OVERWRITE",
    "SolicitudInvalida",
    "router",
]
