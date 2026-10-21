"""Modo logger: evidencia completa de una ejecución para depurar bots.

Se activa con la clave ``logger: true`` de la solicitud (no documentada en
OpenAPI). El plugin corre igual que siempre; la fábrica de navegador, al recibir
una ``LoggerCapture``, graba el HAR desde la creación del contexto y esta clase
guarda por paso captura y HTML, el log completo y las descargas. Los archivos
existen solo mientras el bot se ejecuta: al terminar se comprimen en un único
``{job_id}.tar.gz``, se sube al bucket ``logger`` y se borran todos los rastros.
Estructura dentro del tar.gz (carpeta raíz ``{job_id}/``)::

    logs/{bot}.log
    screenshots/001_<paso>.png
    html/001_<paso>.html
    har/{bot}_1.har
    descargas/<archivos descargados y artefactos del bot>
    request.json   (solicitud completa; claves solo cifradas con RSA)
    manifest.json

Todo es best-effort: ningún fallo de captura o de subida cambia el resultado
del job. Antes de comprimir se reemplazan los secretos del job (clave, proxy,
captcha) por ``***`` en logs, HTML y HAR. Solo la central firma las subidas.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import mimetypes
import re
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, quote_plus, urlsplit

log = logging.getLogger("bot_worker.logger_capture")

#: Job al que pertenece el contexto asyncio actual (filtra el log por job).
_JOB_ACTUAL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "logger_capture_job", default=""
)

SUBCARPETAS = ("logs", "screenshots", "html", "har", "descargas")
MAX_BYTES_SUBIDA = 1_073_741_824  # tope del presign de logger (central)
SUBIDAS_EN_PARALELO = 4
_MATERIAL_CLAVE = {".pem", ".key", ".pfx", ".p12", ".crt", ".cer", ".csr"}
TIMEOUT_SUBIDA_S = 300.0
_NO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")
_SOLO_TEXTO = re.compile(r"\d+")

PresignLogger = Callable[[str, str, int], Awaitable[dict[str, Any]]]


def _slug(texto: str, limite: int = 60) -> str:
    return _NO_SEGURO.sub("_", texto).strip("_")[:limite] or "paso"


def logger_solicitado(payload: dict[str, Any]) -> bool:
    """La central normaliza ``logger`` a ``True``; cualquier otro valor es no."""
    return payload.get("logger") is True


#: job -> LoggerCapture vivo (las trazas de acciones se escriben en su log).
_CAPTURAS: dict[str, "LoggerCapture"] = {}
_TRAZAS_INSTALADAS = False
_ACCIONES = (
    "goto", "click", "dblclick", "fill", "type", "press", "select_option",
    "check", "uncheck", "set_input_files", "hover", "wait_for_selector",
    "expect_download", "reload", "go_back",
)
#: acciones que cambian el estado visible: se captura la pantalla después (como
#: los ``debug.capture`` por paso de bots_dev), con tope y separación mínima.
_ACCIONES_CON_CAPTURA = {"click", "dblclick", "select_option", "check", "uncheck", "goto"}


def _describir(objeto: Any, nombre: str, args: tuple, kwargs: dict) -> str:
    """Descripción de una acción sin valores sensibles (fill/type/press no loguean texto)."""
    if nombre == "goto" and args:
        return _sin_query(str(args[0]))
    blanco = ""
    try:
        blanco = repr(objeto).replace("\n", " ")[:160]
    except Exception:  # noqa: BLE001
        pass
    if nombre in ("fill", "type", "press", "select_option", "set_input_files"):
        return f"{blanco} (valor omitido)"
    return blanco


def instalar_trazas() -> None:
    """Envuelve las acciones de Playwright para narrarlas en el log del job.

    Solo escribe cuando hay un job logger activo en el contexto; si no, la
    acción pasa tal cual. No altera argumentos, resultados ni excepciones.
    """
    global _TRAZAS_INSTALADAS
    if _TRAZAS_INSTALADAS:
        return
    try:
        from playwright.async_api import Locator, Page
    except ImportError:
        return
    import functools

    def envolver(clase: Any, nombre: str) -> None:
        original = getattr(clase, nombre, None)
        if original is None or not asyncio.iscoroutinefunction(original):
            return

        @functools.wraps(original)
        async def trazada(self: Any, *args: Any, **kwargs: Any) -> Any:
            captura = _CAPTURAS.get(_JOB_ACTUAL.get())
            if captura is None:
                return await original(self, *args, **kwargs)
            captura.nota("accion %s.%s %s", clase.__name__, nombre,
                         _describir(self, nombre, args, kwargs))
            try:
                resultado = await original(self, *args, **kwargs)
            except Exception as exc:
                captura.nota("accion_fallida %s.%s: %s", clase.__name__, nombre,
                             type(exc).__name__)
                raise
            if nombre in _ACCIONES_CON_CAPTURA:
                pagina = self if clase is Page else getattr(self, "page", None)
                if pagina is not None:
                    captura.capturar_tras_accion(pagina, f"tras_{nombre}")
            return resultado

        setattr(clase, nombre, trazada)

    for clase in (Page, Locator):
        for nombre in _ACCIONES:
            envolver(clase, nombre)
    _TRAZAS_INSTALADAS = True


class _FiltroJob(logging.Filter):
    def __init__(self, job_id: str) -> None:
        super().__init__()
        self._job_id = job_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return _JOB_ACTUAL.get() == self._job_id


class LoggerCapture:
    """Carpeta de evidencia de un job y utilidades de captura/subida."""

    _activos = 0          # jobs logger simultáneos (restaura el nivel al último)
    _nivel_base = logging.NOTSET

    def __init__(
        self, root: Path, job_id: str, bot: str, operation: str,
        secrets: list[str] | None = None,
    ) -> None:
        self.root = root
        self.job_id = job_id
        self.bot = bot
        self.operation = operation
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 4]
        for nombre in SUBCARPETAS:
            (root / nombre).mkdir(parents=True, exist_ok=True)
        self._paso = 0
        self._har = 0
        self._tareas: set[asyncio.Task[None]] = set()
        self._candado = asyncio.Lock()
        self._inicio = time.time()
        self._handler: logging.Handler | None = None
        self._notas: Any = None
        self._vigias: set[asyncio.Task[None]] = set()
        self._hash_pagina: dict[int, int] = {}
        self._nivel_previo: int | None = None
        self._contextos: list[Any] = []
        self._por_progreso = 0
        self._cambios = 0
        self._tras_accion = 0
        self._ultima_tras = 0.0

    # ---- log completo del job -------------------------------------------------
    def activar_log(self) -> None:
        """Vuelca a ``logs/{bot}.log`` todo log emitido desde este job."""
        _JOB_ACTUAL.set(self.job_id)
        handler = logging.FileHandler(
            self.root / "logs" / f"{self.bot}.log", encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        handler.addFilter(_FiltroJob(self.job_id))
        logging.getLogger().addHandler(handler)
        self._handler = handler
        _CAPTURAS[self.job_id] = self
        instalar_trazas()
        LoggerCapture._activos += 1
        if LoggerCapture._activos == 1:
            base = logging.getLogger("bot_worker")
            LoggerCapture._nivel_base = base.level
            if base.getEffectiveLevel() > logging.INFO:
                base.setLevel(logging.INFO)  # los INFO de los plugins quedan en el tar
        # Las notas de captura se escriben directo al archivo (no pasan por el
        # log del worker, que enmascara hosts): nada de esto queda fuera del tar.
        self._notas = open(self.root / "logs" / f"{self.bot}.log", "a", encoding="utf-8", buffering=1)
        self.nota("modo logger activo job=%s bot=%s operacion=%s",
                  self.job_id, self.bot, self.operation)

    def guardar_solicitud(
        self, solicitud: dict[str, Any] | None, payload: dict[str, Any]
    ) -> None:
        """``request.json``: toda la solicitud usada, credenciales ya cifradas.

        La central manda las claves cifradas con su clave pública; el worker
        nunca escribe una clave en claro (ni conoce la pública para cifrarla).
        """
        datos = dict(solicitud) if solicitud else {
            "job_id": self.job_id, "bot": self.bot,
            "operation": self.operation, "payload": dict(payload),
        }
        (self.root / "request.json").write_text(
            json.dumps(datos, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def nota(self, mensaje: str, *args: Any) -> None:
        if self._notas is None:
            return
        try:
            texto = (mensaje % args) if args else mensaje
            marca = time.strftime("%Y-%m-%d %H:%M:%S")
            self._notas.write(f"{marca} NOTA {texto}\n")
        except (OSError, ValueError, TypeError):
            pass

    def cerrar_log(self) -> None:
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler.close()
            self._handler = None
            LoggerCapture._activos = max(0, LoggerCapture._activos - 1)
            if LoggerCapture._activos == 0:
                logging.getLogger("bot_worker").setLevel(LoggerCapture._nivel_base)
        _CAPTURAS.pop(self.job_id, None)
        if self._notas is not None:
            self._notas.close()
            self._notas = None

    # ---- integración con la fábrica de navegador -------------------------------
    def nueva_ruta_har(self) -> str:
        self._har += 1
        return str(self.root / "har" / f"{self.bot}_{self._har}.har")

    def adjuntar(self, context: Any) -> None:
        """Engancha eventos del contexto (páginas existentes y futuras)."""
        self._contextos.append(context)
        context.on("page", self._pagina)
        for pagina in list(getattr(context, "pages", []) or []):
            self._pagina(pagina)

    def _lanzar(self, corrutina: Awaitable[None]) -> None:
        tarea = asyncio.ensure_future(corrutina)
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)

    def _pagina(self, page: Any) -> None:
        def al_cargar(*_: Any) -> None:
            self._lanzar(self.capturar(page, "load"))

        def al_descargar(descarga: Any) -> None:
            self._lanzar(self._guardar_descarga(descarga))

        def al_navegar(frame: Any) -> None:
            if frame == page.main_frame:
                self.nota("navegacion url=%s", _sin_query(frame.url))

        def al_consola(msg: Any) -> None:
            if msg.type in ("error", "warning"):
                self.nota("consola %s: %s", msg.type, str(msg.text)[:500])

        def al_error(exc: Any) -> None:
            self.nota("pageerror: %s", str(exc)[:500])

        def al_request_fallido(req: Any) -> None:
            self.nota("requestfailed %s %s: %s", req.method,
                      _sin_query(req.url), req.failure)

        def al_respuesta(resp: Any) -> None:
            if resp.status >= 400:
                self.nota("http %s %s", resp.status, _sin_query(resp.url))
            elif resp.request.resource_type == "document":
                # Las navegaciones de documento equivalen a los "Paso N: cargando ..."
                # de los loggers de bots_dev.
                self.nota("documento %s %s %s", resp.status, resp.request.method,
                          _sin_query(resp.url))

        vigia = asyncio.ensure_future(self._vigilar(page))
        self._vigias.add(vigia)
        vigia.add_done_callback(self._vigias.discard)
        page.on("load", al_cargar)
        page.on("download", al_descargar)
        page.on("framenavigated", al_navegar)
        page.on("console", al_consola)
        page.on("pageerror", al_error)
        page.on("requestfailed", al_request_fallido)
        page.on("response", al_respuesta)

    def capturar_tras_accion(self, page: Any, paso: str) -> None:
        """Programa una captura tras una acción; máx. 200 y ≥1,2 s entre capturas."""
        ahora = time.time()
        if self._tras_accion >= 200 or ahora - self._ultima_tras < 1.2:
            return
        self._tras_accion += 1
        self._ultima_tras = ahora
        self._lanzar(self.capturar(page, paso))

    async def _vigilar(self, page: Any) -> None:
        """Captura estados que no navegan (formularios, SPA, modales).

        Equivale a los ``debug.capture`` por paso de los loggers de bots_dev
        sin tocar el código del bot: si el DOM (sin dígitos) cambia y pasaron
        al menos 3 s desde la última captura, guarda un paso ``cambio``.
        """
        ultima = 0.0
        try:
            while not page.is_closed() and self._cambios < 150:
                await asyncio.sleep(1.5)
                try:
                    html = await page.content()
                except Exception:  # noqa: BLE001 - navegando: se reintenta
                    continue
                huella = hash(_SOLO_TEXTO.sub("", html))
                previa = self._hash_pagina.get(id(page))
                if previa is None:
                    self._hash_pagina[id(page)] = huella
                    continue
                if huella != previa and time.time() - ultima >= 3.0:
                    ultima = time.time()
                    self._cambios += 1
                    await self.capturar(page, "cambio")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - la evidencia nunca rompe el bot
            log.debug("vigía descartado", exc_info=True)

    async def capturar(self, page: Any, paso: str) -> None:
        """Screenshot y HTML numerados (``001_<paso>``); nunca lanza."""
        try:
            if page.is_closed():
                return
            async with self._candado:
                self._paso += 1
                prefijo = f"{self._paso:03d}_{_slug(paso)}_{_slug(urlsplit(page.url).path or 'raiz', 40)}"
            self.nota("captura %s url=%s", prefijo, _sin_query(page.url))
            try:
                await page.screenshot(
                    path=str(self.root / "screenshots" / f"{prefijo}.png"),
                    full_page=True, timeout=10_000,
                )
            except Exception as exc:  # noqa: BLE001 - la página puede estar navegando
                self.nota("screenshot_failed %s: %s", prefijo, type(exc).__name__)
            try:
                html = await page.content()
                self._hash_pagina[id(page)] = hash(_SOLO_TEXTO.sub("", html))
                (self.root / "html" / f"{prefijo}.html").write_text(
                    html, encoding="utf-8"
                )
            except Exception as exc:  # noqa: BLE001
                self.nota("html_failed %s: %s", prefijo, type(exc).__name__)
        except Exception:  # noqa: BLE001 - la evidencia nunca rompe el bot
            log.debug("captura descartada", exc_info=True)

    async def capturar_todas(self, paso: str) -> None:
        """Captura cada página abierta con el nombre del paso (máx. 80 por job)."""
        if self._por_progreso >= 80:
            return
        self._por_progreso += 1
        for ctx in list(self._contextos):
            for pagina in list(getattr(ctx, "pages", []) or []):
                await self.capturar(pagina, paso)

    async def _guardar_descarga(self, descarga: Any) -> None:
        try:
            nombre = Path(descarga.suggested_filename).name or "descarga"
            async with self._candado:
                self._paso += 1
                destino = self.root / "descargas" / f"{self._paso:03d}_{nombre}"
            await descarga.save_as(str(destino))
            self.nota("descarga %s", destino.name)
        except Exception as exc:  # noqa: BLE001
            self.nota("descarga_failed: %s", type(exc).__name__)

    async def antes_de_cerrar(self, context: Any) -> None:
        """Estado final de cada página abierta y espera de tareas pendientes."""
        for vigia in list(self._vigias):
            vigia.cancel()
        for pagina in list(getattr(context, "pages", []) or []):
            await self.capturar(pagina, "final")
        if self._tareas:
            await asyncio.gather(*list(self._tareas), return_exceptions=True)

    # ---- cierre: copiar artefactos, redactar, manifest, subir ------------------
    def copiar_artefactos(self, workdir: Path) -> None:
        """Copia los archivos que dejó el bot en su workdir a ``descargas/``."""
        try:
            for ruta in sorted(workdir.rglob("*")):
                if ruta.is_symlink() or not ruta.is_file():
                    continue
                if ruta.suffix.lower() in _MATERIAL_CLAVE:
                    # Entradas con material criptográfico (PEM de controladores
                    # fiscales, claves, certificados): nunca viajan en el tar.
                    self.nota("copia_omitida material_clave %s", ruta.name)
                    continue
                destino = self.root / "descargas" / "workdir" / ruta.relative_to(workdir)
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ruta, destino)
        except OSError as exc:
            self.nota("copia_workdir_failed: %s", type(exc).__name__)

    def guardar_resultado(self, resultado: dict[str, Any]) -> None:
        """``descargas/resultado_bot.json``: datos que devolvió el plugin (ya sin secretos)."""
        try:
            (self.root / "descargas" / "resultado_bot.json").write_text(
                json.dumps(resultado, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            self.nota("resultado_bot_failed: %s", type(exc).__name__)

    def redactar(self) -> None:
        """Reemplaza secretos del job por ``***`` en logs, HTML y HAR."""
        if not self._secrets:
            return
        variantes: list[bytes] = []
        for secreto in self._secrets:
            for forma in (
                secreto, json.dumps(secreto)[1:-1], quote(secreto, safe=""),
                quote_plus(secreto),
            ):
                variantes.append(forma.encode("utf-8"))
        for carpeta in ("logs", "html", "har"):
            for ruta in (self.root / carpeta).glob("*"):
                try:
                    datos = ruta.read_bytes()
                    nuevo = datos
                    for v in set(variantes):
                        nuevo = nuevo.replace(v, b"***")
                    if nuevo != datos:
                        ruta.write_bytes(nuevo)
                except OSError:
                    continue

    def _archivos(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*")
            if p.is_file() and not p.is_symlink() and p.name != "manifest.json"
        )

    def escribir_manifest(self, resultado: dict[str, Any]) -> None:
        archivos = []
        for p in self._archivos():
            archivos.append({
                "path": p.relative_to(self.root).as_posix(),
                "size_bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
                if p.stat().st_size <= 32_000_000 else None,
            })
        (self.root / "manifest.json").write_text(json.dumps({
            "job_id": self.job_id, "bot": self.bot, "operation": self.operation,
            "duracion_s": round(time.time() - self._inicio, 3),
            "resultado": resultado, "archivos": archivos,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def comprimir(self) -> Path:
        """Un único ``{job_id}.tar.gz`` (raíz ``{job_id}/``) junto a la carpeta."""
        destino = self.root.parent / f"{self.job_id}.tar.gz"
        with tarfile.open(destino, "w:gz") as tar:
            tar.add(self.root, arcname=self.job_id)
        return destino

    async def subir(self, client: Any, presign: PresignLogger, tar_gz: Path) -> bool:
        """Sube el tar.gz a ``logger/{job_id}.tar.gz`` con reintentos."""
        tamano = tar_gz.stat().st_size
        if tamano == 0 or tamano > MAX_BYTES_SUBIDA:
            log.warning("logger: tar.gz omitido por tamaño (%s bytes)", tamano)
            return False
        for intento in range(4):
            try:
                firmado = await presign(tar_gz.name, "application/gzip", tamano)
                respuesta = await client.put(
                    firmado["upload_url"], content=tar_gz.read_bytes(),
                    headers={"Content-Type": "application/gzip"},
                    timeout=TIMEOUT_SUBIDA_S,
                )
                respuesta.raise_for_status()
                return True
            except Exception as exc:  # noqa: BLE001 - nunca afecta al job
                log.warning(
                    "logger: falló la subida del tar.gz, intento %s (%s)",
                    intento + 1, type(exc).__name__,
                )
                await asyncio.sleep(2.0 * (intento + 1))
        return False

    def borrar_rastros(self, tar_gz: Path | None = None) -> None:
        """Borra carpeta de evidencia y tar.gz (la evidencia solo vive en ejecución)."""
        self.cerrar_log()
        shutil.rmtree(self.root, ignore_errors=True)
        if tar_gz is not None:
            tar_gz.unlink(missing_ok=True)


def _sin_query(url: str) -> str:
    """URL sin query ni fragmento: pueden traer tokens."""
    try:
        partes = urlsplit(url)
        return f"{partes.scheme}://{partes.netloc}{partes.path}"
    except ValueError:
        return "<url>"


class EventSinkConLog:
    """Decora el sink de progreso para dejar cada evento en el log del job."""

    def __init__(self, interno: Any, captura: LoggerCapture) -> None:
        self._interno = interno
        self._captura = captura

    async def progress(self, phase: str, percent: int, message: str) -> None:
        self._captura.nota("progreso %s %s%%: %s", phase, percent, message)
        # Captura por paso de negocio (equivale a ``debug.capture`` de bots_dev).
        await self._captura.capturar_todas(f"{phase}_{message}")
        await self._interno.progress(phase, percent, message)


__all__ = ["EventSinkConLog", "LoggerCapture", "logger_solicitado"]
