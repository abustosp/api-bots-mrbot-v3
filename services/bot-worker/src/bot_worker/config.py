"""Configuración del worker: SOLO por argumentos CLI, tipada y validada.

El worker no lee ninguna variable de entorno para configurarse ni guarda
secretos en reposo: todo dato sensible u operativo (credenciales fiscales,
proxies, claves de captcha, endpoints de servicio, URLs prefirmadas) llega
por asignación desde la central, dentro del sobre sellado, y vive solo en
memoria. La única identidad local son rutas de certificados (archivos
montados, nunca secretos en env) para el futuro canal mTLS.

Arranque típico (lo invoca el orquestador, ver compose/DEPLOY.md)::

    python -m bot_worker --central-url http://central-api:8000 \
        --advertised-url http://10.0.0.11:8080

Cualquier variable de entorno con secretos (de este servicio o ajenos)
hace fallar el arranque: si hay secretos en el entorno, el despliegue
está mal filtrado.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

PROTOCOL_VERSION = 1  # espejo de mrbot_contracts.version.PROTOCOL_VERSION (int en el wire)

# Prefijos y nombres exactos que NUNCA deben estar en el entorno del worker:
# ni secretos propios (el worker no tiene ninguno) ni de otros servicios.
# Si alguno aparece, el orquestador filtró mal y se falla antes de ejecutar.
FORBIDDEN_ENV_PREFIXES = (
    "DATABASE_",
    "POSTGRES",
    "PG",
    "MINIO_",
    "MRBOT_KEYS",
    "RSA_",
    "JOB_SECRETS",
    "SMTP_",
    "MERCADOPAGO",
    "MP_",
    "ADMIN_",
    "SECRET_",
    "API_KEY_HMAC",
    "CENTRAL_",
    "WORKER_",
    "PROXY",
    "SERVER_PROXY",
    "CAPMONSTER",
    "ARCA_",
    "SRT_",
    "CONSULTA_CUIT",
    "CUIT_",
    "AI_",
)
FORBIDDEN_ENV_EXACT = frozenset(
    {
        "PORT",
        "WORK_DIR",
        "LOG_LEVEL",
        "IMAGE_VERSION",
        "TLS_CA_FILE",
        "TLS_CERT_FILE",
        "TLS_KEY_FILE",
        "TLS_CLIENT_CERT_FILE",
        "TLS_CLIENT_KEY_FILE",
        "TLS_REQUIRE_PEER",
        "TLS_EXPECTED_PEER_CN",
    }
)


def assert_no_forbidden_env() -> None:
    present = sorted(
        {
            name
            for name in os.environ
            if name in FORBIDDEN_ENV_EXACT
            or name.startswith(FORBIDDEN_ENV_PREFIXES)
        }
    )
    if present:
        raise RuntimeError(
            "bot-worker: el worker no usa variables de entorno; "
            "variables prohibidas presentes: " + ", ".join(present)
        )


@dataclass(frozen=True)
class WorkerConfig:
    """Configuración efectiva del worker. Solo CLI + defaults en código."""

    central_url: str
    advertised_url: str
    port: int = 8080
    worker_id: str = "sin-asignar"
    worker_concurrency: int = 5
    worker_local_queue_limit: int = 0
    heartbeat_interval_seconds: int = 10
    heartbeat_jitter_seconds: int = 2
    worker_drain_timeout_seconds: int = 120
    job_default_timeout_seconds: int = 1800
    callback_timeout_seconds: int = 3
    work_dir: str = "/work"
    log_level: str = "INFO"
    image_version: str = "3.0.0-dev"
    tls_ca_file: str = "/certs/ca.pem"
    tls_cert_file: str = "/certs/worker-server.pem"
    tls_key_file: str = "/certs/worker-server.key"
    tls_client_cert_file: str = "/certs/worker-client.pem"
    tls_client_key_file: str = "/certs/worker-client.key"
    tls_enabled: bool = False

    def __post_init__(self) -> None:
        """Invariantes válidas también en construcción programática (tests)."""
        for url in (self.central_url, self.advertised_url):
            rest = url.split("://", 1)
            if len(rest) != 2 or rest[0] not in ("http", "https"):
                raise ValueError(f"URL debe ser http(s)://...: {url!r}")
        host = self.advertised_url.split("://", 1)[1].split("/", 1)[0]
        if ":" not in host:
            raise ValueError("advertised-url debe incluir ip:port")
        if not 1 <= self.worker_concurrency <= 5:
            raise ValueError("concurrency debe estar entre 1 y 5 (W-2)")
        if not 0 <= self.worker_local_queue_limit <= 5:
            raise ValueError("queue-limit debe estar entre 0 y 5")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("log-level inválido")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bot_worker",
        description="Worker MrBot: ejecutor sin entorno; todo lo recibe de la central.",
    )
    p.add_argument("--central-url", required=True, help="URL base de la central")
    p.add_argument(
        "--advertised-url",
        required=True,
        help="URL propia http(s)://ip:port que la central usa para contactarlo",
    )
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--worker-id", default="sin-asignar")
    p.add_argument("--work-dir", default="/work")
    p.add_argument("--concurrency", dest="worker_concurrency", type=int, default=5)
    p.add_argument("--queue-limit", dest="worker_local_queue_limit", type=int, default=0)
    p.add_argument("--heartbeat-interval", dest="heartbeat_interval_seconds", type=int, default=10)
    p.add_argument("--heartbeat-jitter", dest="heartbeat_jitter_seconds", type=int, default=2)
    p.add_argument("--drain-timeout", dest="worker_drain_timeout_seconds", type=int, default=120)
    p.add_argument("--job-timeout", dest="job_default_timeout_seconds", type=int, default=1800)
    p.add_argument("--callback-timeout", dest="callback_timeout_seconds", type=int, default=3)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--image-version", default="3.0.0-dev")
    p.add_argument("--tls-ca", dest="tls_ca_file", default="/certs/ca.pem")
    p.add_argument("--tls-cert", dest="tls_cert_file", default="/certs/worker-server.pem")
    p.add_argument("--tls-key", dest="tls_key_file", default="/certs/worker-server.key")
    p.add_argument("--tls-client-cert", dest="tls_client_cert_file", default="/certs/worker-client.pem")
    p.add_argument("--tls-client-key", dest="tls_client_key_file", default="/certs/worker-client.key")
    p.add_argument("--tls", dest="tls_enabled", action="store_true",
                   help="Servir HTTPS/mTLS con la identidad de /certs (overlay mtls)")
    return p


def parse_args(argv: list[str] | None = None) -> WorkerConfig:
    """Construye la configuración solo desde argv. Nunca lee el entorno."""
    assert_no_forbidden_env()
    ns = build_parser().parse_args(argv)
    for url in (ns.central_url, ns.advertised_url):
        rest = url.split("://", 1)
        if len(rest) != 2 or rest[0] not in ("http", "https"):
            raise ValueError(f"URL debe ser http(s)://...: {url!r}")
    if ":" not in ns.advertised_url.split("://", 1)[1].split("/", 1)[0]:
        raise ValueError("advertised-url debe incluir ip:port")
    if not 1 <= ns.worker_concurrency <= 5:
        raise ValueError("--concurrency debe estar entre 1 y 5 (W-2)")
    if not 0 <= ns.worker_local_queue_limit <= 5:
        raise ValueError("--queue-limit debe estar entre 0 y 5")
    if ns.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ValueError("--log-level inválido")
    return WorkerConfig(
        central_url=ns.central_url,
        advertised_url=ns.advertised_url,
        port=ns.port,
        worker_id=ns.worker_id,
        worker_concurrency=ns.worker_concurrency,
        worker_local_queue_limit=ns.worker_local_queue_limit,
        heartbeat_interval_seconds=ns.heartbeat_interval_seconds,
        heartbeat_jitter_seconds=ns.heartbeat_jitter_seconds,
        worker_drain_timeout_seconds=ns.worker_drain_timeout_seconds,
        job_default_timeout_seconds=ns.job_default_timeout_seconds,
        callback_timeout_seconds=ns.callback_timeout_seconds,
        work_dir=ns.work_dir,
        log_level=ns.log_level,
        image_version=ns.image_version,
        tls_ca_file=ns.tls_ca_file,
        tls_cert_file=ns.tls_cert_file,
        tls_key_file=ns.tls_key_file,
        tls_client_cert_file=ns.tls_client_cert_file,
        tls_client_key_file=ns.tls_client_key_file,
        tls_enabled=ns.tls_enabled,
    )


# Compatibilidad de nombre con el módulo anterior (misma firma de uso).
def load_settings(argv: list[str] | None = None) -> WorkerConfig:
    return parse_args(argv)


__all__ = [
    "PROTOCOL_VERSION",
    "FORBIDDEN_ENV_PREFIXES",
    "WorkerConfig",
    "assert_no_forbidden_env",
    "build_parser",
    "parse_args",
    "load_settings",
]

# Nombre histórico usado por main.py y tests.
WorkerSettings = WorkerConfig
