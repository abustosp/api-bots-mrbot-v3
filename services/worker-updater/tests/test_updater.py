"""Updater de host con docker simulado: éxito, imagen no permitida, rollback y env file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import updater as u  # noqa: E402

REPO = "registry.local/mrbot-bot-worker"
VIEJA, NUEVA = f"{REPO}:1.0.0", f"{REPO}:2.0.0"


class FakeDocker:
    """Simula `docker pull`, `compose up`/`ps` e `inspect` sobre un único contenedor."""

    def __init__(self, env_file: Path, *, imagen_sana: set[str], fallar_pull: bool = False) -> None:
        self.env_file = env_file
        self.imagen_sana = imagen_sana
        self.fallar_pull = fallar_pull
        self.imagen = VIEJA
        self.llamadas: list[list[str]] = []

    def __call__(self, args: list[str], timeout: float = 600) -> str:
        self.llamadas.append(args)
        if args[:2] == ["docker", "pull"]:
            if self.fallar_pull:
                raise u.UpdateError("pull falló: manifest unknown")
            return ""
        if "up" in args:
            # compose toma WORKER_IMAGE del env file, igual que el real.
            self.imagen = u.read_env(str(self.env_file))["WORKER_IMAGE"] or ""
            return ""
        if "ps" in args:
            return "abc123\n"
        if args[:2] == ["docker", "inspect"]:
            sano = self.imagen in self.imagen_sana
            return json.dumps([{
                "Config": {"Image": self.imagen},
                "State": {"Running": True, "Health": {"Status": "healthy" if sano else "unhealthy"}},
            }])
        raise AssertionError(args)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.worker"
    env_file.write_text(
        f"CENTRAL_URL=http://central:8000\nWORKER_IMAGE={VIEJA}\nAPP_VERSION=1.0.0\n"
        "WORKER_IMAGE_DIGEST=\nWORKER_CONCURRENCY=5\n"
    )
    env_file.chmod(0o600)
    cfg = u.Config(
        central_url="http://central:8000", token="t", node="host:8080",
        compose_file=str(tmp_path / "worker.compose.yaml"), env_file=str(env_file),
        project="mrbot-v3-worker", allowed_repos=(REPO,), health_timeout=0.5,
    )
    reloj = iter(i * 0.01 for i in range(100_000))
    monkeypatch.setattr(u.time, "monotonic", lambda: next(reloj))

    def armar(**kw):
        docker = FakeDocker(env_file, **kw)
        return u.Updater(cfg, runner=docker, sleep=lambda _s: None), docker, env_file

    return armar


CMD = {"request_id": "r1", "image": NUEVA, "app_version": "2.0.0", "image_digest": ""}


def test_actualiza_y_persiste_la_version_en_el_env_file(entorno) -> None:
    updater, docker, env_file = entorno(imagen_sana={VIEJA, NUEVA})
    assert updater.apply(CMD) == ("HECHO", "")
    env = u.read_env(str(env_file))
    assert env["WORKER_IMAGE"] == NUEVA and env["APP_VERSION"] == "2.0.0"
    texto = env_file.read_text()
    assert "CENTRAL_URL=http://central:8000" in texto and "WORKER_CONCURRENCY=5" in texto
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert ["docker", "pull", NUEVA] in docker.llamadas


def test_rechaza_imagenes_fuera_de_la_allowlist(entorno) -> None:
    updater, docker, _ = entorno(imagen_sana={VIEJA})
    estado, error = updater.apply({**CMD, "image": "evil.example/miner:latest"})
    assert estado == "FALLIDO" and "allowlist" in error
    assert not any(c[:2] == ["docker", "pull"] for c in docker.llamadas)


def test_rollback_si_la_imagen_nueva_no_queda_healthy(entorno) -> None:
    updater, docker, env_file = entorno(imagen_sana={VIEJA})
    estado, error = updater.apply(CMD)
    assert estado == "REVERTIDO" and "healthy" in error
    assert u.read_env(str(env_file))["WORKER_IMAGE"] == VIEJA
    assert u.read_env(str(env_file))["APP_VERSION"] == "1.0.0"
    assert docker.imagen == VIEJA


def test_fallo_de_pull_no_toca_nada(entorno) -> None:
    updater, docker, env_file = entorno(imagen_sana={VIEJA}, fallar_pull=True)
    antes = env_file.read_text()
    estado, error = updater.apply(CMD)
    assert estado == "FALLIDO" and "manifest unknown" in error
    assert env_file.read_text() == antes
    assert not any("up" in c for c in docker.llamadas)


def test_comando_repetido_con_la_imagen_ya_desplegada_reporta_hecho(entorno) -> None:
    updater, docker, _ = entorno(imagen_sana={VIEJA, NUEVA})
    docker.imagen = NUEVA
    assert updater.apply(CMD) == ("HECHO", "")
    assert not any(c[:2] == ["docker", "pull"] for c in docker.llamadas)


def test_rollback_fallido_se_reporta_como_fallido(entorno) -> None:
    updater, _, _ = entorno(imagen_sana=set())
    estado, error = updater.apply(CMD)
    assert estado == "FALLIDO"


@pytest.mark.parametrize("imagen,ok", [
    (f"{REPO}:2.0.0", True),
    (f"{REPO}@sha256:" + "a" * 64, True),
    (f"{REPO}:2.0.0@sha256:" + "a" * 64, True),
    (f"{REPO}-evil:2.0.0", False),
    ("otro/mrbot-bot-worker:2.0.0", False),
    ("registry.local:5000/mrbot-bot-worker:1", False),
])
def test_allowlist(imagen, ok) -> None:
    assert u.image_allowed(imagen, (REPO,)) is ok


def test_registry_con_puerto_en_la_allowlist() -> None:
    repo = "registry.local:5000/mrbot-bot-worker"
    assert u.image_allowed(f"{repo}:3", (repo,)) is True


def test_config_aborta_si_faltan_variables_o_archivos(tmp_path) -> None:
    compose = tmp_path / "worker.compose.yaml"
    env = tmp_path / ".env.worker"
    compose.write_text("")
    env.write_text("")
    base = {
        "UPDATER_CENTRAL_URL": "http://c:8000/", "UPDATER_TOKEN": "t", "UPDATER_NODE": "h:8080",
        "UPDATER_ALLOWED_REPOS": REPO, "UPDATER_COMPOSE_FILE": str(compose),
        "UPDATER_ENV_FILE": str(env), "UPDATER_PROJECT": "p",
    }
    assert u.Config.from_env(base).central_url == "http://c:8000"
    with pytest.raises(SystemExit, match="UPDATER_TOKEN"):
        u.Config.from_env({**base, "UPDATER_TOKEN": ""})
    with pytest.raises(SystemExit, match="UPDATER_HOST_DIR"):
        u.Config.from_env({**base, "UPDATER_ENV_FILE": "/no/existe/.env.worker"})
