"""Updater de host: recrea el contenedor del worker con la imagen que ordena la central.

Corre junto al worker (un contenedor aparte con docker socket) y solo hace
conexiones salientes: consulta ``/internal/v1/updater/command`` y reporta el
resultado. El worker conserva su aislamiento (sin docker socket, sin entorno).

Una actualización es: validar imagen -> ``docker pull`` -> reescribir
``WORKER_IMAGE``/``APP_VERSION``/``WORKER_IMAGE_DIGEST`` en el env file del
compose -> ``compose up -d`` del servicio -> esperar a que esté ``healthy``.
Si falla, restaura los valores anteriores y recrea el worker anterior.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("worker_updater")

ENV_KEYS = ("WORKER_IMAGE", "APP_VERSION", "WORKER_IMAGE_DIGEST")


@dataclass(frozen=True)
class Config:
    central_url: str
    token: str
    node: str
    compose_file: str
    env_file: str
    project: str
    service: str = "bot-worker"
    allowed_repos: tuple[str, ...] = ()
    poll_seconds: float = 10.0
    health_timeout: float = 180.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env

        def req(name: str) -> str:
            valor = e.get(name, "").strip()
            if not valor:
                raise SystemExit(f"falta la variable {name}")
            return valor

        for nombre in ("UPDATER_COMPOSE_FILE", "UPDATER_ENV_FILE"):
            if not Path(req(nombre)).is_file():
                raise SystemExit(f"{nombre} no existe en el contenedor: {e.get(nombre)} (revisar UPDATER_HOST_DIR)")
        repos = tuple(r.strip() for r in req("UPDATER_ALLOWED_REPOS").split(",") if r.strip())
        return cls(
            central_url=req("UPDATER_CENTRAL_URL").rstrip("/"),
            token=req("UPDATER_TOKEN"),
            node=req("UPDATER_NODE"),
            compose_file=req("UPDATER_COMPOSE_FILE"),
            env_file=req("UPDATER_ENV_FILE"),
            project=req("UPDATER_PROJECT"),
            service=e.get("UPDATER_SERVICE", "bot-worker"),
            allowed_repos=repos,
            poll_seconds=float(e.get("UPDATER_POLL_SECONDS", "10")),
            health_timeout=float(e.get("UPDATER_HEALTH_TIMEOUT", "180")),
        )


class UpdateError(Exception):
    pass


def run(args: list[str], timeout: float = 600) -> str:
    """Ejecuta un comando sin shell y devuelve stdout; falla con UpdateError."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise UpdateError(f"{' '.join(args[:3])} falló: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return proc.stdout


def image_allowed(image: str, allowed: tuple[str, ...]) -> bool:
    """La imagen debe pertenecer a un repositorio de la allowlist (``repo:tag`` o ``repo@sha256``)."""
    repo = image.split("@", 1)[0]
    # El tag va tras el último ":" solo si viene después de la última "/".
    nombre = repo.rsplit("/", 1)[-1]
    if ":" in nombre:
        repo = repo[: repo.rfind(":")]
    return any(repo == a or repo == a.rstrip("/") for a in allowed)


def read_env(path: str) -> dict[str, str | None]:
    valores: dict[str, str | None] = {k: None for k in ENV_KEYS}
    for linea in Path(path).read_text().splitlines():
        clave, sep, valor = linea.partition("=")
        if sep and clave.strip() in valores:
            valores[clave.strip()] = valor
    return valores


def write_env(path: str, valores: dict[str, str | None]) -> None:
    """Reescribe solo las claves de ENV_KEYS, conservando el resto y los permisos."""
    destino = Path(path)
    pendientes = dict(valores)
    salida: list[str] = []
    for linea in destino.read_text().splitlines():
        clave = linea.partition("=")[0].strip()
        if clave in pendientes and "=" in linea:
            valor = pendientes.pop(clave)
            if valor is not None:
                salida.append(f"{clave}={valor}")
        else:
            salida.append(linea)
    salida += [f"{k}={v}" for k, v in pendientes.items() if v is not None]
    # Escritura atómica dentro del mismo directorio para no dejar el .env a medias.
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), prefix=".env-upd-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(salida) + "\n")
        st = destino.stat()
        os.chmod(tmp, st.st_mode & 0o777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)  # conserva el dueño del env file
        except PermissionError:
            pass
        os.replace(tmp, destino)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Updater:
    def __init__(self, cfg: Config, runner=run, sleep=time.sleep) -> None:
        self.cfg = cfg
        self.run = runner
        self.sleep = sleep

    # ------------------------------------------------------------ docker ---
    def _compose(self, *extra: str) -> list[str]:
        c = self.cfg
        return ["docker", "compose", "-p", c.project, "-f", c.compose_file, "--env-file", c.env_file, *extra]

    def _container_id(self) -> str:
        salida = self.run(self._compose("ps", "-q", self.cfg.service)).strip()
        return salida.splitlines()[0] if salida else ""

    def _inspect(self, cid: str) -> dict:
        data = json.loads(self.run(["docker", "inspect", cid]))
        return data[0]

    def _wait_healthy(self, image: str) -> bool:
        limite = time.monotonic() + self.cfg.health_timeout
        while time.monotonic() < limite:
            cid = self._container_id()
            if cid:
                info = self._inspect(cid)
                estado = info.get("State", {})
                salud = (estado.get("Health") or {}).get("Status")
                if info.get("Config", {}).get("Image") == image and estado.get("Running") and salud == "healthy":
                    return True
            self.sleep(3)
        return False

    def _current_image(self) -> str:
        cid = self._container_id()
        return self._inspect(cid).get("Config", {}).get("Image", "") if cid else ""

    # ----------------------------------------------------------- update ---
    def apply(self, cmd: dict) -> tuple[str, str]:
        """Aplica el comando y devuelve ``(estado, error)`` para reportar a la central."""
        image = str(cmd.get("image") or "")
        if not image or not image_allowed(image, self.cfg.allowed_repos):
            return "FALLIDO", "imagen fuera de la allowlist del updater"
        try:
            if self._current_image() == image and self._wait_healthy(image):
                return "HECHO", ""  # comando repetido: ya está desplegada
            self.run(["docker", "pull", image])
        except (UpdateError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return "FALLIDO", str(exc)[:300]

        previo = read_env(self.cfg.env_file)
        nuevo = {
            "WORKER_IMAGE": image,
            "APP_VERSION": str(cmd.get("app_version") or "") or previo["APP_VERSION"],
            "WORKER_IMAGE_DIGEST": str(cmd.get("image_digest") or ""),
        }
        try:
            write_env(self.cfg.env_file, nuevo)
            self.run(self._compose("up", "-d", "--no-deps", self.cfg.service))
            if self._wait_healthy(image):
                return "HECHO", ""
            motivo = "el worker nuevo no quedó healthy a tiempo"
        except (UpdateError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            motivo = str(exc)[:300]
        return self._rollback(previo, motivo)

    def _rollback(self, previo: dict[str, str | None], motivo: str) -> tuple[str, str]:
        log.error("actualización fallida (%s); restaurando versión anterior", motivo)
        try:
            write_env(self.cfg.env_file, previo)
            self.run(self._compose("up", "-d", "--no-deps", self.cfg.service))
            if previo.get("WORKER_IMAGE") and self._wait_healthy(previo["WORKER_IMAGE"] or ""):
                return "REVERTIDO", motivo
        except (UpdateError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            motivo = f"{motivo}; el rollback también falló: {exc}"[:500]
        return "FALLIDO", motivo

    # ---------------------------------------------------------- central ---
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.cfg.central_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.cfg.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - URL de configuración
            return json.loads(resp.read() or b"{}")

    def poll(self) -> dict:
        return self._request("GET", "/internal/v1/updater/command?" + urllib.parse.urlencode({"node": self.cfg.node}))

    def report(self, request_id: str, estado: str, error: str) -> None:
        cuerpo = {"node": self.cfg.node, "request_id": request_id, "estado": estado, "error": error}
        for intento in range(6):
            try:
                self._request("POST", "/internal/v1/updater/report", cuerpo)
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    return  # la central ya no espera este resultado
                log.warning("reporte rechazado (%s)", exc.code)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                log.warning("no se pudo reportar: %s", exc)
            self.sleep(min(30, 2 ** intento))

    def step(self) -> bool:
        """Un ciclo de polling; devuelve True si ejecutó una actualización."""
        try:
            cmd = self.poll()
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            log.warning("central no disponible: %s", exc)
            return False
        if cmd.get("action") != "update":
            return False
        log.info("actualizando a %s", cmd.get("image"))
        estado, error = self.apply(cmd)
        log.info("resultado: %s %s", estado, error)
        self.report(str(cmd.get("request_id") or ""), estado, error)
        return True

    def loop(self) -> None:
        while True:
            self.step()
            self.sleep(self.cfg.poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    log.info("updater de %s apuntando a %s", cfg.node, cfg.central_url)
    Updater(cfg).loop()


if __name__ == "__main__":
    sys.exit(main())
