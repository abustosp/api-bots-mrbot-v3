"""Firma de URLs prefirmadas S3/MinIO (plan 02 §7.9 y §11.3).

Diseño: la central firma, el worker sube. SOLO este proceso conoce
``OBJECT_STORAGE_ACCESS_KEY``/``OBJECT_STORAGE_SECRET_KEY``; el worker
recibe URLs ``PUT`` limitadas a object key, content type, tamaño máximo y
expiración corta, nunca las credenciales.

La firma es AWS Signature V4 con estilo de ruta (``/bucket/key``), válida
tanto contra AWS S3 como contra MinIO local (perfil ``local-storage`` del
compose). Este módulo es stdlib-only para probarse sin red ni SDK.

Sin endpoint/bucket/credenciales configurados no hay firma real: se emite
el ticket de desarrollo documentado (host ``storage.example``), que ningún
despliegue real debe usar. El secreto de firma jamás sale en URLs ni logs.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

#: Host del ticket de desarrollo (sin firma real ni bytes transferidos).
DEV_STORAGE_HOST = "https://storage.example"

#: Hash de contenido usado en URLs prefirmadas (sin cuerpo en la firma).
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


def _ahora() -> datetime:
    """Reloj UTC para sellos de firma y expiraciones."""
    return datetime.now(timezone.utc)


def _firmar_clave(
    secreto: str, fecha: str, region: str, servicio: str = "s3"
) -> bytes:
    """Deriva la clave de firma SigV4 (cadena HMAC fecha/región/servicio)."""
    k_fecha = hmac.new(
        ("AWS4" + secreto).encode("utf-8"), fecha.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    k_region = hmac.new(k_fecha, region.encode("utf-8"), hashlib.sha256).digest()
    k_servicio = hmac.new(
        k_region, servicio.encode("utf-8"), hashlib.sha256
    ).digest()
    return hmac.new(k_servicio, b"aws4_request", hashlib.sha256).digest()


def presign_put_url(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    expires_seconds: int = 900,
    content_type: str | None = None,
    ahora: datetime | None = None,
    http_method: str = "PUT",
) -> str:
    """Genera una URL ``PUT`` o ``GET`` prefirmada SigV4 estilo ruta.

    Falla cerrado (``ValueError``) ante endpoint/bucket/credenciales vacíos
    u object key inválido: nunca se emite una URL a medio firmar.
    """
    method = str(http_method).upper()
    if method not in {"GET", "PUT"}:
        raise ValueError("método de storage no permitido")
    base = (endpoint or "").rstrip("/")
    if not base or not bucket or not access_key or not secret_key:
        raise ValueError("firma de storage sin configurar")
    clave = (object_key or "").strip("/")
    if not clave or ".." in clave.split("/"):
        raise ValueError("object key inválido")
    momento = ahora or _ahora()
    amz_fecha = momento.strftime("%Y%m%dT%H%M%SZ")
    dia = momento.strftime("%Y%m%d")
    limite = max(1, min(int(expires_seconds), 7 * 24 * 3600))
    anfitrion = base.split("://", 1)[-1].split("/", 1)[0]
    uri = "/" + bucket + "/" + "/".join(quote(p, safe="-_.~") for p in clave.split("/"))
    alcance = f"{dia}/{region}/s3/aws4_request"
    parametros = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{alcance}",
        "X-Amz-Date": amz_fecha,
        "X-Amz-Expires": str(limite),
        "X-Amz-SignedHeaders": "host",
    }
    if content_type:
        parametros["X-Amz-SignedHeaders"] = "content-type;host"
    canon_query = "&".join(
        # RFC 3986/AWS SigV4: `/` separa segmentos del path, pero en valores
        # de query (en particular X-Amz-Credential) debe codificarse como %2F.
        f"{quote(k, safe='')}={quote(v, safe='-_.~')}"
        for k, v in sorted(parametros.items())
    )
    cabeceras = f"host:{anfitrion}\n"
    if content_type:
        cabeceras = f"content-type:{content_type.strip()}\n" + cabeceras
    firmados = parametros["X-Amz-SignedHeaders"]
    canonica = (
        f"{method}\n{uri}\n{canon_query}\n{cabeceras}\n{firmados}\n"
        f"{_UNSIGNED_PAYLOAD}"
    )
    ambito = (
        f"AWS4-HMAC-SHA256\n{amz_fecha}\n{alcance}\n"
        f"{hashlib.sha256(canonica.encode('utf-8')).hexdigest()}"
    )
    firma = hmac.new(
        _firmar_clave(secret_key, dia, region),
        ambito.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{base}{uri}?{canon_query}&X-Amz-Signature={firma}"


def presign_get_url(
    *, endpoint: str, region: str, bucket: str, access_key: str,
    secret_key: str, object_key: str, expires_seconds: int = 300,
    ahora: datetime | None = None,
) -> str:
    """Genera una URL GET SigV4 temporal para un objeto autorizado."""
    return presign_put_url(
        endpoint=endpoint, region=region, bucket=bucket, access_key=access_key,
        secret_key=secret_key, object_key=object_key,
        expires_seconds=expires_seconds, ahora=ahora, http_method="GET",
    )


def ticket_desarrollo(object_key: str) -> str:
    """URL de desarrollo sin firma (nunca válida contra storage real)."""
    return f"{DEV_STORAGE_HOST}/{object_key}?firma-temporal"


def firmar_subida(
    *,
    object_key: str,
    content_type: str,
    ttl_seconds: int,
    endpoint: str = "",
    region: str = "us-east-1",
    bucket: str = "",
    access_key: str = "",
    secret_key: str = "",
) -> dict:
    """Firma una subida: URL real SigV4 si hay storage, ticket dev si no.

    Devuelve ``{"upload_url", "modo"}`` con modo ``"real"`` o
    ``"desarrollo"``. El secreto jamás aparece en la URL devuelta.
    """
    configurado = bool(endpoint and bucket and access_key and secret_key)
    if configurado:
        url = presign_put_url(
            endpoint=endpoint,
            region=region or "us-east-1",
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            object_key=object_key,
            expires_seconds=ttl_seconds,
            content_type=content_type or None,
        )
        return {"upload_url": url, "modo": "real"}
    return {"upload_url": ticket_desarrollo(object_key), "modo": "desarrollo"}


__all__ = [
    "DEV_STORAGE_HOST",
    "presign_put_url",
    "presign_get_url",
    "ticket_desarrollo",
    "firmar_subida",
]
