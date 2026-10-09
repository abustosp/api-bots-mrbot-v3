"""Configuración de central-api por variables de entorno.

Todo secreto se lee SOLO desde aquí (nunca con ``os.environ`` disperso en
routers): base de datos, HMAC de API keys, firma interna central-worker y
credenciales de MercadoPago. La central SOLO conoce a los workers por su
IP:puerto y los contacta por HTTP. El inventario inicial llega en
``WORKER_NODES`` (``"10.0.0.11:8080,10.0.0.12:8080"``); el panel de
administración puede sumar nodos en caliente (``POST /admin/workers``) y el
registro dinámico acepta la unión de ambos inventarios. ``ADMIN_TOKEN``
autoriza las mutaciones del panel; sin él, son 403.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from central_api.worker_nodes import parse_worker_nodes


def _secret_file_content(alias: str) -> str:
    """Lee ``<ALIAS>_FILE`` (Docker secrets). Vacío si no existe o ilegible."""
    nombres = (f"{alias}_FILE", f"{alias.upper()}_FILE")
    for nombre in dict.fromkeys(nombres):
        path = (os.environ.get(nombre) or "").strip()
        if not path:
            continue
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    environment: str = "development"
    central_api_host: str = "0.0.0.0"
    central_api_port: int = 8000

    database_url: str = ""
    database_pool_size: int = 10

    api_key_hmac_secret: str = ""

    # Firma de tokens de servicio del borde interno (central <-> worker).
    internal_jwt_signing_key: str = Field(default="", alias="INTERNAL_JWT_SIGNING_KEY")

    # Object storage S3/MinIO: SOLO la central firma URLs (plan 02 §7.9 y
    # §11.3). El worker usa URLs prefirmadas, nunca estas credenciales.
    # Sin endpoint/bucket/credenciales no hay firma real: los endpoints de
    # presign responden un ticket de desarrollo documentado (ver storage.py).
    object_storage_endpoint: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT")
    # Host alcanzable por el cliente final para URLs GET prefirmadas. En
    # Compose local difiere del DNS interno usado por los workers al subir.
    object_storage_public_endpoint: str = Field(
        default="", alias="OBJECT_STORAGE_PUBLIC_ENDPOINT"
    )
    object_storage_region: str = Field(default="us-east-1", alias="OBJECT_STORAGE_REGION")
    object_storage_bucket: str = Field(default="", alias="OBJECT_STORAGE_BUCKET")
    object_storage_access_key: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: str = Field(default="", alias="OBJECT_STORAGE_SECRET_KEY")
    object_storage_use_ssl: bool = Field(default=True, alias="OBJECT_STORAGE_USE_SSL")

    # Inventario de workers: SOLO direcciones "ip:port", contactadas por HTTP.
    # El panel de administración puede sumar nodos en caliente (ADMIN_NODES).
    worker_nodes: str = Field(default="", alias="WORKER_NODES")
    # Versión objetivo opcional de la flota. Sin valor, se deriva el máximo
    # observado entre los workers registrados.
    worker_latest_version: str = Field(default="", alias="WORKER_LATEST_VERSION")
    # Firma Ed25519 de asignaciones (la central firma, el worker verifica con
    # la pública que recibe en el registro). Sin clave configurada se genera
    # una efímera por proceso (desarrollo): los workers deben re-registrarse
    # al reiniciar la central.
    assignment_signing_key: str = Field(default="", alias="ASSIGNMENT_SIGNING_KEY")
    # Token de servicio del worker: LEGADO en la relación central→worker
    # (reemplazado por asignaciones firmadas). Se conserva para no romper
    # despliegues previos; los workers nuevos sin entorno no lo usan.
    worker_service_token: str = Field(default="", alias="WORKER_TOKEN")
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # Notificaciones administrativas. El worker nunca recibe estos valores.
    smtp_server: str = Field(default="", alias="SMTP_SERVER")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    smtp_starttls: bool = Field(default=True, alias="SMTP_STARTTLS")

    # Clave privada RSA de custodia: solo la central la lee desde secreto.
    # También descifra ``clave_encriptada`` recibida por clientes V2 y
    # desencripta el ciphertext persistido para el panel administrativo.
    rsa_private_key: str = Field(default="", alias="RSA_PRIVATE_KEY")
    worker_protocol_version: int = 1  # espejo de mrbot_contracts.version.PROTOCOL_VERSION
    worker_heartbeat_interval_seconds: int = 10
    worker_degraded_after_seconds: int = 15
    worker_down_after_seconds: int = 45
    worker_capacity: int = Field(default=10, ge=1, le=10)

    scheduler_enabled: bool = True
    scheduler_poll_interval_ms: int = 1000
    scheduler_batch_size: int = 20
    scheduler_max_empty_rounds: int = 3
    scheduler_dispatch_concurrency: int = 20
    worker_ack_lease_seconds: int = 20
    job_lease_seconds: int = 60
    max_execution_attempts: int = 3

    # Escalado EC2 efímero. Deshabilitado por defecto para preservar el
    # comportamiento de los despliegues existentes.
    autoscale_enabled: bool = Field(default=False, alias="AUTOSCALE_ENABLED")
    autoscale_dry_run: bool = Field(default=False, alias="AUTOSCALE_DRY_RUN")
    autoscale_queue_threshold: int = Field(default=10, alias="AUTOSCALE_QUEUE_THRESHOLD")
    autoscale_up_after_seconds: int = Field(default=20, alias="AUTOSCALE_UP_AFTER_SECONDS")
    autoscale_max_instances: int = Field(default=4, alias="AUTOSCALE_MAX_INSTANCES")
    autoscale_step: int = Field(default=0, ge=0, alias="AUTOSCALE_STEP")  # 0 = sin limite por ciclo
    autoscale_boot_grace_seconds: int = Field(default=240, alias="AUTOSCALE_BOOT_GRACE_SECONDS")
    autoscale_idle_seconds: int = Field(default=45, alias="AUTOSCALE_IDLE_SECONDS")
    autoscale_interval_seconds: int = Field(default=5, alias="AUTOSCALE_INTERVAL_SECONDS")
    autoscale_cooldown_seconds: int = Field(default=60, alias="AUTOSCALE_COOLDOWN_SECONDS")
    autoscale_max_lifetime_seconds: int = Field(default=7200, alias="AUTOSCALE_MAX_LIFETIME_SECONDS")
    autoscale_capacity_per_worker: int = Field(default=0, ge=0, alias="AUTOSCALE_CAPACITY_PER_WORKER")
    autoscale_avg_job_seconds: float = Field(default=75, gt=0, alias="AUTOSCALE_AVG_JOB_SECONDS")
    autoscale_boot_estimate_seconds: float = Field(default=150, gt=0, alias="AUTOSCALE_BOOT_ESTIMATE_SECONDS")
    # Modo por conteo: 1 worker cada N trabajos pendientes (0 = desactivado, usa W/S/R).
    autoscale_jobs_per_worker: int = Field(default=0, ge=0, alias="AUTOSCALE_JOBS_PER_WORKER")
    # Tope de workers totales (fijos + efimeros) en modo por conteo (0 = sin tope extra).
    autoscale_max_total_workers: int = Field(default=0, ge=0, alias="AUTOSCALE_MAX_TOTAL_WORKERS")
    autoscale_target_work_seconds: float = Field(default=300, gt=0, alias="AUTOSCALE_TARGET_WORK_SECONDS")
    autoscale_instance_type_large: str = Field(default="", alias="AUTOSCALE_INSTANCE_TYPE_LARGE")

    # Provisión por asignación: el worker no tiene entorno ni secretos en
    # reposo; la central le envía por sobre sellado el proxy, las claves de
    # captcha y los endpoints de servicio que necesite cada job. Estos
    # secretos viven SOLO aquí (archivos de secretos, nunca en la imagen).
    proxy_enabled: bool = Field(default=False, alias="PROXY_ENABLED")
    proxy_mode: str = Field(default="standard", alias="PROXY_MODE")
    proxy_host: str = Field(default="", alias="PROXY_HOST")
    proxy_username: str = Field(default="", alias="PROXY_USERNAME")
    proxy_password: str = Field(default="", alias="PROXY_PASSWORD")
    proxy_country: str = Field(default="ar", alias="PROXY_COUNTRY")
    capmonster_arca_key: str = Field(default="", alias="CAPMONSTER_ARCA_KEY")
    capmonster_srt_key: str = Field(default="", alias="CAPMONSTER_SRT_KEY")
    cuit_service_base_url: str = Field(default="", alias="CUIT_SERVICE_BASE_URL")
    cuit_service_masiva_url: str = Field(default="", alias="CUIT_SERVICE_MASIVA_URL")
    cuit_service_usuario: str = Field(default="", alias="CUIT_SERVICE_USUARIO")
    cuit_service_api_key: str = Field(default="", alias="CUIT_SERVICE_API_KEY")
    apoc_base_url: str = Field(default="https://servicioscf.afip.gob.ar/facturacion/facturasapocrifas/DownloadFile.aspx", alias="APOC_BASE_URL")
    apoc_base_path: str = Field(default="/tmp/central-api/FacturasApocrifas.txt", alias="APOC_BASE_PATH")
    apoc_refresh_interval_days: int = Field(default=7, alias="APOC_REFRESH_INTERVAL_DAYS")

    # Panel /admin (plan 05): solo campos nuevos con defecto, nada existente cambia.
    admin_panel_title: str = "MrBot Admin"
    admin_audit_page_size: int = 50
    admin_audit_retention_days: int = 365
    admin_report_preview_rows: int = 200
    admin_export_max_rows: int = 10000
    fleet_alert_stale_seconds: int = 30
    fleet_down_seconds: int = 60
    fleet_alert_resumen_minutes: int = 30
    fleet_silence_max_hours: int = 4

    @model_validator(mode="before")
    @classmethod
    def _secret_files(cls, data: object) -> object:
        """Activa el soporte ``*_FILE`` del plan de infra §6.3.

        Para cada campo con alias cuyo valor venga vacío, si existe
        ``<ALIAS>_FILE`` apuntando a un archivo legible, el contenido del
        archivo (sin espacios borde) es el valor. Solo rellena vacíos:
        nunca pisa un valor explícito.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            alias = field.alias or name
            if data.get(name) or data.get(alias):
                continue
            content = _secret_file_content(alias)
            if content:
                data[alias] = content
        return data

    @field_validator("worker_nodes", mode="before")
    @classmethod
    def _keep_raw(cls, v: object) -> object:
        return v or ""

    @property
    def object_storage_configurado(self) -> bool:
        """Indica si hay firma real S3/MinIO (endpoint+bucket+credenciales)."""
        return bool(
            self.object_storage_endpoint
            and self.object_storage_bucket
            and self.object_storage_access_key
            and self.object_storage_secret_key
        )

    @property
    def worker_node_list(self) -> list[str]:
        return parse_worker_nodes(self.worker_nodes)


@lru_cache
def get_settings() -> Settings:
    return Settings()
