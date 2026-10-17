#!/usr/bin/env python3
"""Ejecutor local de un plugin del bot-worker contra el portal real.

Replica el camino de produccion del worker (``copy.copy(plugin)`` +
``configure(service)`` + ``BotRuntime`` con ``PlaywrightBrowserFactory`` real)
sin la central, sin sobre sellado y sin subida de artefactos: sirve para
reproducir y diagnosticar los fallos por modulo desde la maquina del operador.

Modos:

* ``bot``    (por defecto) valida y ejecuta el plugin completo.
* ``login``  solo inicia sesion ARCA y volca el catalogo de servicios visible
             (botones/enlaces), con screenshot y HTML de la pantalla.
* ``portal`` login + ``open_service`` + (opcional) ``seleccionar_representado``
             del portal pedido, con screenshot y HTML.

Ejemplos:

    # Reproducir SCT con credenciales reales (sin proxy)
    tools/local_run/run_bot.py --bot sct --operacion consultar \
        --cuit-representante 20370074829 --clave 'X@-*5@Tg9$iifAv' \
        --representado 20370074829 \
        --payload-json '{"secciones":["deudas"],"formatos":["pdf"]}' \
        --proxy off --out /tmp/sct_local

    # Ver que servicios ofrece la cuenta
    tools/local_run/run_bot.py --mode login --cuit-representante ... --clave ...

    # Abrir el portal de un bot y ver si el representado es seleccionable
    tools/local_run/run_bot.py --mode portal --bot sct --operacion consultar \
        --cuit-representante ... --clave ... --representado ...

Salida: reporte JSON en stdout (y ``report.json`` en ``--out``) con
``outcome``/``result``/``diagnostic_code``/``category``, mas evidencia
(``final.png``, ``final.html``, ``catalog.txt``) en el directorio de salida.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _cargar_dotenv() -> None:
    for candidato in (RAIZ / ".env", RAIZ / "bots_dev" / ".env"):
        if not candidato.is_file():
            continue
        for linea in candidato.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            os.environ.setdefault(clave, valor)


def _proxy_config(modo: str):
    """ProxyConfig para el runner: ``off``, ``on`` (env V1) o ``auto`` (default)."""
    from bot_worker.runtime.context import ProxyConfig
    from random import randint

    def flag(nombre: str, default: str = "False") -> bool:
        return os.getenv(nombre, default).strip().lower() in {"1", "true", "yes", "y", "on"}

    if modo == "off":
        return None
    activo = flag("SERVER_PROXY")
    if modo == "auto" and not activo:
        return None
    if not activo and modo == "on":
        print("aviso: SERVER_PROXY no esta activo en el entorno; sin proxy", file=sys.stderr)
        return None
    host = os.getenv("SERVER_PROXY_HOST", "")
    if not host:
        return None
    host = host.replace("http://", "").replace("https://", "").strip("/")
    if flag("PUERTO_PROXY_FIJO"):
        puerto = int(os.getenv("PUERTO_PROXY_FIJO_NUMERO", "0") or 0)
    else:
        minimo = int(os.getenv("PUERTO_PROXY_MINIMO", "0") or 0)
        maximo = int(os.getenv("PUERTO_PROXY_MAXIMO", "0") or 0)
        puerto = randint(minimo, maximo) if maximo >= minimo > 0 else 7777
    return ProxyConfig(
        mode="standard",
        host=f"http://{host}:{puerto}",
        username=os.getenv("USERNAME_PROXY") or None,
        password=os.getenv("PASSWORD_PROXY") or None,
        country=os.getenv("PROXY_COUNTRY", "ar"),
    )


def _service_section(extra: dict | None) -> dict:
    """Seccion ``service`` del sobre: claves de captcha y extras del runner."""
    seccion: dict = {
        "captcha": {
            "enabled": os.getenv("ARCA_SOLVE_CAPTCHA", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            "arca_key": os.getenv("ARCA_CAPMONSTER_API_KEY") or os.getenv("SRT_CAPMONSTER_API_KEY"),
            "srt_key": os.getenv("SRT_CAPMONSTER_API_KEY"),
            "api_key": os.getenv("ARCA_CAPMONSTER_API_KEY") or os.getenv("SRT_CAPMONSTER_API_KEY"),
            "timeout_seconds": int(os.getenv("ARCA_CAPMONSTER_TIMEOUT_SECONDS", "60") or 60),
            "poll_seconds": int(os.getenv("ARCA_CAPMONSTER_POLL_SECONDS", "3") or 3),
            "max_attempts": int(os.getenv("ARCA_CAPMONSTER_MAX_ATTEMPTS", "5") or 5),
            "case_sensitive": os.getenv("ARCA_CAPMONSTER_CASE", "true").strip().lower()
            in {"1", "true", "yes", "on"},
        },
    }
    if extra:
        seccion.update(extra)
    return seccion


class _Sink:
    """EventSink que imprime progreso y lo guarda para el reporte."""

    def __init__(self, out: Path) -> None:
        self.eventos: list[dict] = []
        self._log = (out / "progress.log").open("a", encoding="utf-8")

    async def progress(self, phase: str, percent: int, message: str) -> None:
        registro = {"ts": time.time(), "phase": phase, "percent": percent, "message": message}
        self.eventos.append(registro)
        print(f"[progreso] {percent:>3}% {phase}: {message}", flush=True)
        self._log.write(json.dumps(registro, ensure_ascii=False) + "\n")
        self._log.flush()


class _CapturingFactory:
    """PlaywrightBrowserFactory que guarda screenshot/HTML al cerrar la sesion."""

    def __init__(self, base, out: Path, *, screenshot: bool) -> None:
        self._base = base
        self._out = out
        self._screenshot = screenshot
        self.paginas: list[dict] = []
        self._paginas_interes: dict[str, Any] = {}

    def __getattr__(self, nombre: str) -> Any:
        """Delega en la fábrica real (los portales leen ``_solvers``, etc.)."""
        if nombre == "_base":
            raise AttributeError(nombre)
        return getattr(self._base, nombre)

    @property
    def proxy(self):
        return self._base.proxy

    def new_context(self, **kwargs):
        return self._base.new_context(**kwargs)

    def registrar_pagina(self, etiqueta: str, page: Any) -> None:
        """Guarda una página de interés para capturar evidencia al cerrar."""
        self._paginas_interes[etiqueta] = page

    async def _evidencia(self, session, etapa: str) -> None:
        objetivos: list[tuple[str, Any]] = []
        try:
            objetivos.append((etapa, session.page))
        except Exception as exc:  # noqa: BLE001 - se reporta, no se oculta
            pass
        for etiqueta, page in list(self._paginas_interes.items()):
            objetivos.append((f"{etapa}_{etiqueta}", page))
        problemas: list[str] = []
        for nombre, page in objetivos:
            if page is None:
                continue
            url = getattr(page, "url", "")
            self.paginas.append({"etapa": nombre, "url": url})
            if self._screenshot:
                try:
                    await page.screenshot(path=str(self._out / f"{nombre}.png"), full_page=True)
                except Exception as exc:  # noqa: BLE001
                    problemas.append(f"{nombre} screenshot: {type(exc).__name__}: {exc}")
            try:
                (self._out / f"{nombre}.html").write_text(await page.content(), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                problemas.append(f"{nombre} html: {type(exc).__name__}: {exc}")
            try:
                (self._out / f"{nombre}.url.txt").write_text(str(url), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                problemas.append(f"{nombre} url: {type(exc).__name__}: {exc}")
            with contextlib.suppress(Exception):
                marcos = list(getattr(page, "frames", []) or [])
                (self._out / f"{nombre}.frames.txt").write_text(
                    "\n".join(str(getattr(f, "url", "")) for f in marcos), encoding="utf-8"
                )
        if problemas:
            with contextlib.suppress(OSError):
                (self._out / "evidencia_errores.txt").write_text(
                    "\n".join(problemas), encoding="utf-8"
                )

    def arca_session(self, credentials, **kwargs):
        fabrica = self
        base_cm = self._base.arca_session(credentials, **kwargs)

        class _Sesion:
            """Delega en la sesión real y registra la página del servicio."""

            def __init__(self, sesion: Any) -> None:
                self._sesion = sesion

            def __getattr__(self, nombre: str) -> Any:
                return getattr(self._sesion, nombre)

            async def open_service(self, *args: Any, **opts: Any) -> Any:
                manejador = await self._sesion.open_service(*args, **opts)
                pagina = getattr(manejador, "page", None)
                if pagina is not None:
                    fabrica.registrar_pagina("servicio", pagina)
                return manejador

        class _Wrapper:
            def __init__(self) -> None:
                self.session = None

            async def __aenter__(self):
                self._cm = base_cm
                self.session = _Sesion(await self._cm.__aenter__())
                return self.session

            async def __aexit__(self, *exc):
                with contextlib.suppress(Exception):
                    await fabrica._evidencia(self.session, "final")
                return await self._cm.__aexit__(*exc)

        return _Wrapper()


async def _catalogo(page) -> list[str]:
    """Textos visibles de botones/enlaces/tarjetas de la pantalla actual."""
    vistos: list[str] = []
    for creador in (
        lambda: page.get_by_role("button"),
        lambda: page.get_by_role("link"),
        lambda: page.locator("a, button, [role='button'], [onclick]"),
    ):
        with contextlib.suppress(Exception):
            loc = creador()
            for indice in range(min(await loc.count(), 200)):
                with contextlib.suppress(Exception):
                    texto = (await loc.nth(indice).inner_text() or "").strip()
                    if texto and texto not in vistos:
                        vistos.append(texto)
    return vistos


async def _evidencia_pagina(page, out: Path, etapa: str, screenshot: bool = True) -> None:
    with contextlib.suppress(Exception):
        if screenshot:
            await page.screenshot(path=str(out / f"{etapa}.png"), full_page=True)
    with contextlib.suppress(Exception):
        (out / f"{etapa}.html").write_text(await page.content(), encoding="utf-8")
    with contextlib.suppress(Exception):
        (out / f"{etapa}.url.txt").write_text(str(getattr(page, "url", "")), encoding="utf-8")


async def _modo_login(args, out: Path, credentials, proxy, factory) -> dict:
    from bot_worker.runtime.arca_login import ArcaSession
    from bot_worker.runtime.captcha import CaptchaSolver

    perfiles = _service_section(None).get("captcha") or {}
    solver = CaptchaSolver.from_profile("arca", perfiles)
    async with factory.new_context() as (_, context):
        page = await context.new_page()
        sesion = ArcaSession(credentials, solver, page=page, context=context)
        try:
            await sesion.login()
            url = getattr(page, "url", "")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=20_000)
            catalogo = await _catalogo(page)
            (out / "catalog.txt").write_text("\n".join(catalogo), encoding="utf-8")
            await _evidencia_pagina(page, out, "catalogo", args.screenshot)
            return {"outcome": "login_ok", "url": url, "catalogo": catalogo}
        except Exception as exc:  # noqa: BLE001 - diagnostico
            await _evidencia_pagina(page, out, "login_error", args.screenshot)
            return {
                "outcome": "excepcion",
                "exception": type(exc).__name__,
                "category": getattr(exc, "categoria", None),
                "diagnostic_code": getattr(exc, "diagnostic_code", None),
                "message": str(exc)[:500],
            }
        finally:
            await sesion.close()


async def _modo_portal(args, out: Path, credentials, proxy, factory) -> dict:
    from bot_worker.bots.registry import get_plugin_para_payload
    from bot_worker.runtime.captcha import CaptchaSolver

    perfiles = _service_section(None).get("captcha") or {}
    solver = CaptchaSolver.from_profile("arca", perfiles)
    plugin = copy.copy(get_plugin_para_payload(args.bot, {"operacion": args.operacion}))
    manifiesto = getattr(plugin, "manifest", None)
    servicio = args.servicio or _servicio_para_bot(args.bot, manifiesto)
    try:
        async with factory.arca_session(credentials, proxy=proxy) as sesion:
            if args.portal_login_solo:
                return {"outcome": "login_ok", "url": sesion.page.url}
            manejador = await sesion.open_service(servicio, portal=args.bot)
            info: dict = {"servicio": servicio, "url": sesion.page.url}
            if args.representado:
                catalogo = await _catalogo(sesion.page)
                (out / "catalogo_portal.txt").write_text("\n".join(catalogo), encoding="utf-8")
                await _evidencia_pagina(sesion.page, out, "portal_antes_representado", args.screenshot)
                await manejador.seleccionar_representado(args.representado)
                info["representado"] = args.representado
                info["url_post"] = sesion.page.url
            return {"outcome": "portal_ok", **info}
    except Exception as exc:  # noqa: BLE001 - diagnostico
        return {
            "outcome": "excepcion",
            "exception": type(exc).__name__,
            "category": getattr(exc, "categoria", None),
            "diagnostic_code": getattr(exc, "diagnostic_code", None),
            "message": str(exc)[:500],
            "servicio": servicio,
        }


def _servicio_para_bot(bot: str, manifiesto) -> str:
    """Nombre de servicio ARCA usado por los bots V1/V2 (fallback del portal)."""
    nombres = {
        "sct": "Sistema de Cuentas Tributarias",
        "sifere": "Convenio Multilateral",
        "mis_facilidades": "Mis Facilidades",
        "pago_devoluciones": "Pago de Devoluciones",
        "certificado_mipyme": "Certificado MiPyME",
        "liquidacion_granos": "Liquidacion de Granos",
        "compensaciones": "Compensaciones",
        "hacienda": "Hacienda",
        "declaracion_en_linea": "Declaracion en Linea",
        "portal_iva": "Portal IVA",
        "libros_portal_iva": "Libros IVA",
        "facturometro": "Monotributo",
        "srt": "SRT",
    }
    return nombres.get(bot, bot)


async def _modo_bot(args, out: Path, credentials, proxy, factory) -> dict:
    from bot_worker.bots.registry import get_plugin_para_payload
    from bot_worker.runtime.context import (
        ArtifactSlot,
        ArtifactStore,
        BotRuntime,
        CancellationToken,
        DeadlineBudget,
    )

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8")) if args.payload else {}
    if args.payload_json:
        payload.update(json.loads(args.payload_json))
    payload.setdefault("operacion", args.operacion)

    base_plugin = get_plugin_para_payload(args.bot, payload)
    manifiesto = getattr(base_plugin, "manifest", None)
    declarados = {
        spec.nombre: (tuple(spec.content_types), int(spec.max_bytes))
        for spec in (getattr(manifiesto, "artefactos_produce", ()) or ())
    }
    slots = {
        spec.nombre: ArtifactSlot(
            artifact_id=spec.nombre, put_url="", object_key="",
            max_bytes=spec.max_bytes, content_types=tuple(spec.content_types),
        )
        for spec in (getattr(manifiesto, "artefactos_produce", ()) or ())
    }
    sink = _Sink(out)
    cancellation = CancellationToken()
    # El worker crea el work_dir por job (workdir.new_workdir); replicarlo.
    (out / "work").mkdir(parents=True, exist_ok=True)
    runtime = BotRuntime(
        job_id="00000000-0000-4000-8000-000000000001",
        work_dir=out / "work",
        deadline=DeadlineBudget(deadline_epoch=time.time() + args.timeout),
        credentials=credentials,
        proxy=proxy,
        artifact_store=ArtifactStore(out / "work", slots, declarados=declarados),
        event_sink=sink,
        browser_factory=factory,
        cancellation=cancellation,
    )
    plugin = copy.copy(base_plugin)
    if hasattr(plugin, "configure"):
        plugin.configure(_service_section(json.loads(args.service_json) if args.service_json else None))
    if args.portal:
        with contextlib.suppress(Exception):
            plugin.configure({"servicio": args.portal})
    inicio = time.time()
    try:
        validado = await plugin.validate(dict(payload))
        resultado = await asyncio.wait_for(plugin.execute(validado, runtime), timeout=args.timeout)
        return {
            "outcome": "ejecutado",
            "result": resultado.result,
            "data": resultado.data,
            "warnings": resultado.warnings,
            "errors": [asdict(e) for e in resultado.errors],
            "artifacts": resultado.artifacts,
            "elapsed_s": round(time.time() - inicio, 1),
            "eventos": sink.eventos,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostico del runner
        categoria = getattr(exc, "categoria", None)
        codigo = getattr(exc, "diagnostic_code", None)
        return {
            "outcome": "excepcion",
            "exception": type(exc).__name__,
            "category": categoria,
            "diagnostic_code": codigo,
            "message": str(exc)[:500],
            "elapsed_s": round(time.time() - inicio, 1),
            "eventos": sink.eventos,
            "traceback": traceback.format_exc()[-3000:],
        }


_SECCIONES_DESDE_LOGS: dict[str, tuple[str, str]] = {
    "vencimientos_excel": ("vencimientos", "xlsx"),
    "vencimientos_xlsx": ("vencimientos", "xlsx"),
    "vencimientos_csv": ("vencimientos", "csv"),
    "vencimientos_pdf": ("vencimientos", "pdf"),
    "deudas_excel": ("deudas", "xlsx"),
    "deudas_xlsx": ("deudas", "xlsx"),
    "deudas_csv": ("deudas", "csv"),
    "deudas_pdf": ("deudas", "pdf"),
    "ddjj_pendientes_excel": ("ddjj_pendientes", "xlsx"),
    "ddjj_pendientes_xlsx": ("ddjj_pendientes", "xlsx"),
    "ddjj_pendientes_csv": ("ddjj_pendientes", "csv"),
    "ddjj_pendientes_pdf": ("ddjj_pendientes", "pdf"),
}


def _fecha_v1(valor: Any) -> Any:
    """``AAAAMMDD`` (V1) a ``DD/MM/AAAA`` como valida el plugin."""
    texto = str(valor or "").strip()
    if len(texto) == 8 and texto.isdigit():
        return f"{texto[6:8]}/{texto[4:6]}/{texto[:4]}"
    return valor


def _payload_desde_fila(fila: dict, propiedades: set[str], representado: str = "") -> dict:
    """Campos de negocio de la fila que el esquema del plugin acepta."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from creds import CAMPOS_PAYLOAD

    cuerpo: dict = {}
    if "representado_cuit" in propiedades and representado:
        cuerpo["representado_cuit"] = representado
    # El plugin usa nombres distintos de la tabla V1 en dos casos.
    if "fecha_desde" in propiedades and "fecha_desde" not in fila:
        valor_desde = fila.get("desde", fila.get("periodo_desde"))
        if valor_desde not in (None, "", "null"):
            fila = {**fila, "fecha_desde": _fecha_v1(valor_desde)}
    if "fecha_hasta" in propiedades and "fecha_hasta" not in fila:
        valor_hasta = fila.get("hasta", fila.get("periodo_hasta"))
        if valor_hasta not in (None, "", "null"):
            fila = {**fila, "fecha_hasta": _fecha_v1(valor_hasta)}
    if "representado_nombre" in propiedades and not fila.get("representado_nombre"):
        for campo_nombre in ("denominacion", "nombre_representado"):
            valor_nombre = fila.get(campo_nombre)
            if valor_nombre not in (None, "", "null"):
                fila = {**fila, "representado_nombre": valor_nombre}
                break
    if "cuits_consulta" in propiedades and not fila.get("cuits_consulta"):
        crudo = fila.get("cuits_consultados") or fila.get("cuits")
        if isinstance(crudo, str) and crudo.strip():
            fila = {**fila, "cuits_consulta": [c.strip() for c in crudo.split(",") if c.strip()]}
        elif isinstance(crudo, list):
            fila = {**fila, "cuits_consulta": crudo}
    for campo in CAMPOS_PAYLOAD:
        if campo not in propiedades or campo not in fila:
            continue
        valor = fila.get(campo)
        if valor in (None, "", "null"):
            continue
        if isinstance(valor, str) and valor.strip().startswith("["):
            with contextlib.suppress(ValueError, json.JSONDecodeError):
                valor = json.loads(valor)
        cuerpo[campo] = valor
    for campo in ("fecha_desde", "fecha_hasta"):
        if campo in propiedades and campo in fila and campo not in cuerpo:
            cuerpo[campo] = fila[campo]
    # V1 registraba los formatos pedidos como request_excel/request_csv/request_pdf.
    for formato in ("excel", "csv", "pdf"):
        if formato in propiedades and formato not in cuerpo and f"request_{formato}" in fila:
            if bool(fila[f"request_{formato}"]):
                cuerpo[formato] = True
    if "cuits_consulta" in propiedades and "cuits_consulta" in fila and "cuits_consulta" not in cuerpo:
        cuerpo["cuits_consulta"] = fila["cuits_consulta"]
    # SCT: V1 pedia salidas por seccion y formato con nueve banderas; el log
    # deja la URL de cada archivo entregado, que revela seccion y formato.
    if "secciones" in propiedades and "secciones" not in cuerpo:
        secciones: list[str] = []
        formatos: list[str] = []
        for columna, (seccion, formato) in _SECCIONES_DESDE_LOGS.items():
            for sufijo in ("_url_minio", "_minio_url", ""):
                valor = fila.get(f"{columna}{sufijo}")
                if isinstance(valor, str) and valor.strip():
                    secciones.append(seccion)
                    formatos.append(formato)
                    break
        if secciones:
            cuerpo["secciones"] = sorted(set(secciones))
            if "formatos" in propiedades:
                cuerpo["formatos"] = sorted(set(formatos))
    return cuerpo


def _resolver_desde_db(args) -> dict:
    """Toma credenciales (y campos de negocio) de la base de logs indicada.

    Devuelve ``{"cuit_representante", "clave", "representado", "payload",
    "origen"}`` sin imprimir la clave.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from creds import credenciales_de_fila, filas

    status = None if str(args.db_status).lower() in {"any", "all"} else args.db_status
    candidatas = filas(
        modulo=args.from_db, tabla=args.from_table, status=status,
        limit=max(1, args.db_index + 1), cuit=args.db_cuit, db_name=args.db_name,
    )
    if not candidatas:
        raise SystemExit("--from-db: sin registros para ese modulo/tabla")
    if args.db_index >= len(candidatas):
        raise SystemExit(f"--from-db: solo hay {len(candidatas)} registros disponibles")
    elegida = candidatas[args.db_index]
    datos = credenciales_de_fila(elegida["row"])
    if not (datos["cuit_representante"] and datos["clave"]):
        raise SystemExit("--from-db: el registro no trae CUIT y clave utilizables")
    propiedades: set[str] = set()
    if args.bot:
        from bot_worker.bots.registry import get_plugin_para_payload

        plugin = get_plugin_para_payload(args.bot, {"operacion": args.operacion})
        esquema = getattr(getattr(plugin, "manifest", None), "esquema_entrada", None)
        if isinstance(esquema, dict):
            propiedades = set((esquema.get("properties") or {}).keys())
    return {
        **datos,
        "payload": _payload_desde_fila(elegida["row"], propiedades, datos["representado"]),
        "origen": f"{elegida['db']}/{elegida['table']}/id={elegida['row'].get('id')}",
    }


async def _run(args) -> dict:
    from bot_worker.runtime.browser import PlaywrightBrowserFactory
    from bot_worker.runtime.context import FiscalCredentials

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proxy = _proxy_config(args.proxy)
    desde_db: dict = {}
    desde_archivo: dict = {}
    if args.creds_file:
        desde_archivo = json.loads(Path(args.creds_file).read_text(encoding="utf-8"))
    if args.from_db or args.from_table:
        desde_db = _resolver_desde_db(args)
        args.cuit_representante = args.cuit_representante or desde_db["cuit_representante"]
        args.clave = args.clave or desde_db["clave"]
        args.representado = args.representado or desde_db["representado"]
        if desde_db["payload"]:
            previo = json.loads(args.payload_json) if args.payload_json else {}
            args.payload_json = json.dumps({**desde_db["payload"], **previo})
    if desde_archivo:
        args.cuit_representante = args.cuit_representante or str(desde_archivo.get("cuit_representante") or "")
        args.clave = args.clave or str(desde_archivo.get("clave") or "")
        args.representado = args.representado or str(desde_archivo.get("representado") or "")
        if isinstance(desde_archivo.get("payload"), dict):
            previo = json.loads(args.payload_json) if args.payload_json else {}
            args.payload_json = json.dumps({**desde_archivo["payload"], **previo})
    credentials = FiscalCredentials(
        cuit_representante=args.cuit_representante, clave=args.clave
    )
    perfiles = _service_section(None).get("captcha") or {}
    plugin = None
    if args.bot:
        from bot_worker.bots.registry import get_plugin_para_payload

        plugin = get_plugin_para_payload(args.bot, {"operacion": args.operacion})
    manifiesto = getattr(plugin, "manifest", None)
    base = PlaywrightBrowserFactory(
        proxy=proxy,
        headless=True,
        captcha_profiles=perfiles,
        stealth=bool(getattr(manifiesto, "stealth", False)),
        canal=str(getattr(manifiesto, "canal_navegador", "") or ""),
        pantalla_virtual=bool(getattr(manifiesto, "pantalla_virtual", False)),
    )
    factory = _CapturingFactory(base, out, screenshot=args.screenshot)

    if args.mode == "login":
        reporte = await _modo_login(args, out, credentials, proxy, factory)
    elif args.mode == "portal":
        reporte = await _modo_portal(args, out, credentials, proxy, factory)
    else:
        reporte = await _modo_bot(args, out, credentials, proxy, factory)

    reporte = {
        "bot": args.bot,
        "operacion": args.operacion,
        "mode": args.mode,
        "cuit_representante": args.cuit_representante,
        "representado": args.representado,
        "proxy": bool(proxy),
        "origen_credenciales": desde_db.get("origen"),
        "paginas": factory.paginas,
        **reporte,
    }
    # Copiar artefactos producidos dentro de work/
    work = out / "work"
    if work.is_dir():
        destino = out / "artifacts"
        destino.mkdir(exist_ok=True)
        for archivo in work.rglob("*"):
            if archivo.is_file() and archivo.name != "resultado.json":
                try:
                    shutil.copy2(archivo, destino / archivo.name)
                except OSError:
                    pass
    (out / "report.json").write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return reporte


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", help="Bot/plugin a ejecutar (sct, sifere, ...).")
    parser.add_argument("--operacion", default="consultar")
    parser.add_argument("--mode", choices=("bot", "login", "portal"), default="bot")
    parser.add_argument("--cuit-representante", default="", help="CUIT que inicia sesion.")
    parser.add_argument("--clave", default="", help="Clave fiscal (evite pasarla por CLI si puede).")
    parser.add_argument("--representado", default="")
    parser.add_argument(
        "--from-db",
        help=(
            "Toma CUIT, clave y campos de negocio del registro mas reciente de la "
            "tabla de logs del modulo (ver tools/local_run/creds.py). Evita exponer "
            "la clave en la linea de comandos."
        ),
    )
    parser.add_argument("--from-table", help="Tabla de logs explicita (alternativa a --from-db).")
    parser.add_argument("--db-index", type=int, default=0, help="0 = registro mas reciente.")
    parser.add_argument("--db-status", default="success", help="success|any.")
    parser.add_argument("--db-cuit", help="Filtra los registros por CUIT.")
    parser.add_argument("--db-name", help="Limita la busqueda a una base (p. ej. sql_app_mrbot1.db).")
    parser.add_argument(
        "--creds-file",
        help=(
            'JSON {"cuit_representante","clave","representado","payload"?} con las '
            "credenciales; evita exponerlas en la linea de comandos o en la base."
        ),
    )
    parser.add_argument("--payload", help="Archivo JSON con el payload del bot.")
    parser.add_argument("--payload-json", help="Payload JSON inline (se fusiona).")
    parser.add_argument("--service-json", help="Extras para la seccion service del sobre.")
    parser.add_argument("--portal", help="Nombre de servicio ARCA a abrir (modo portal).")
    parser.add_argument("--servicio", help="Alias de --portal.")
    parser.add_argument("--portal-login-solo", action="store_true")
    parser.add_argument("--proxy", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", default="tools/local_run/out")
    parser.add_argument("--screenshot", action="store_true", default=True)
    parser.add_argument("--no-screenshot", dest="screenshot", action="store_false")
    args = parser.parse_args()

    if args.mode in {"bot", "portal"} and not args.bot:
        parser.error("--bot es obligatorio en modo bot/portal")
    if not (args.from_db or args.from_table or args.creds_file) and not (args.cuit_representante and args.clave):
        parser.error("indique --cuit-representante y --clave, o bien --from-db/--from-table/--creds-file")

    _cargar_dotenv()
    reporte = asyncio.run(_run(args))
    print(json.dumps(reporte, ensure_ascii=False, indent=2, default=str))
    if reporte.get("outcome") in {"excepcion"}:
        return 1
    if reporte.get("result") == "ERROR":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
