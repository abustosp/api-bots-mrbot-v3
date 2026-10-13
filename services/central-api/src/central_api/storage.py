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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

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
    response_content_disposition: str | None = None,
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
    if response_content_disposition and method == "GET":
        parametros["response-content-disposition"] = response_content_disposition
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


def content_disposition(filename: str | None) -> str | None:
    """``attachment`` con nombre ASCII de respaldo y ``filename*`` UTF-8 (RFC 6266)."""
    nombre = (filename or "").replace("\r", " ").replace("\n", " ").strip()
    if not nombre:
        return None
    ascii_fb = nombre.encode("ascii", "ignore").decode().replace('"', "'").replace("\\", "_") or "archivo"
    return f"attachment; filename=\"{ascii_fb}\"; filename*=UTF-8''{quote(nombre, safe='')}"


def presign_get_url(
    *, endpoint: str, region: str, bucket: str, access_key: str,
    secret_key: str, object_key: str, expires_seconds: int = 300,
    ahora: datetime | None = None, filename: str | None = None,
) -> str:
    """Genera una URL GET SigV4 temporal para un objeto autorizado.

    Con ``filename`` el navegador descarga el archivo con ese nombre original
    (``Content-Disposition: attachment`` firmado en la URL).
    """
    return presign_put_url(
        endpoint=endpoint, region=region, bucket=bucket, access_key=access_key,
        secret_key=secret_key, object_key=object_key,
        expires_seconds=expires_seconds, ahora=ahora, http_method="GET",
        response_content_disposition=content_disposition(filename),
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


def put_object_bytes(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    timeout_seconds: float = 30.0,
    ahora: datetime | None = None,
) -> None:
    """Guarda bytes con PUT firmado por la central, sin credenciales externas.

    La central envía el contenido y firma el hash real del cuerpo. Los errores
    de red no exponen URLs ni credenciales y se traducen a 503 en la API.
    """
    if not endpoint or not bucket or not access_key or not secret_key:
        raise ValueError("almacenamiento sin configurar")
    clave = (object_key or "").strip("/")
    if not clave or ".." in clave.split("/"):
        raise ValueError("object key inválido")
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("endpoint de almacenamiento inválido")
    base_path = parsed.path.rstrip("/")
    uri = base_path + "/" + quote(bucket, safe="-_.~") + "/" + "/".join(
        quote(part, safe="-_.~") for part in clave.split("/")
    )
    host = parsed.netloc
    body = bytes(content)
    payload_hash = hashlib.sha256(body).hexdigest()
    moment = ahora or _ahora()
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = moment.strftime("%Y%m%d")
    tipo = (content_type or "application/octet-stream").strip()
    headers = {
        "content-type": tipo,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = (
        f"PUT\n{uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    signing_region = region or "us-east-1"
    scope = f"{day}/{signing_region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signature = hmac.new(
        _firmar_clave(secret_key, day, signing_region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = urlunsplit((parsed.scheme, parsed.netloc, uri, "", ""))
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Content-Type": tipo,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": auth,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError("el almacenamiento rechazó la carga")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("no se pudo guardar el archivo en el almacenamiento") from exc


def object_exists(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    timeout_seconds: float = 8.0,
) -> bool | None:
    """``True``/``False`` si el objeto existe en el bucket; ``None`` si no se pudo saber.

    Usa un HEAD firmado desde la central. Un fallo de red o de credenciales
    devuelve ``None`` (desconocido): nunca se declara "eliminado" por un error
    transitorio. No expone URLs ni credenciales.
    """
    try:
        url = presign_put_url(
            endpoint=endpoint, region=region, bucket=bucket,
            access_key=access_key, secret_key=secret_key, object_key=object_key,
            expires_seconds=30, http_method="GET",
        )
    except ValueError:
        return None
    # GET con Range de 1 byte: HEAD no se puede prefirmar con la misma firma GET.
    request = urllib.request.Request(url, method="GET", headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        if exc.code == 416:  # objeto vacio: existe
            return True
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def get_object_bytes(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    max_bytes: int,
    timeout_seconds: float = 30.0,
    ahora: datetime | None = None,
) -> bytes:
    """Lee desde storage, con GET SigV4 y límite estricto de bytes."""
    if not endpoint or not bucket or not access_key or not secret_key:
        raise ValueError("almacenamiento sin configurar")
    clave = (object_key or "").strip("/")
    if not clave or ".." in clave.split("/"):
        raise ValueError("object key inválido")
    if max_bytes < 1:
        raise ValueError("límite de lectura inválido")
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("endpoint de almacenamiento inválido")
    base_path = parsed.path.rstrip("/")
    uri = base_path + "/" + quote(bucket, safe="-_.~") + "/" + "/".join(
        quote(part, safe="-_.~") for part in clave.split("/")
    )
    host = parsed.netloc
    payload_hash = hashlib.sha256(b"").hexdigest()
    moment = ahora or _ahora()
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = moment.strftime("%Y%m%d")
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = (
        f"GET\n{uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    signing_region = region or "us-east-1"
    scope = f"{day}/{signing_region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signature = hmac.new(
        _firmar_clave(secret_key, day, signing_region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = urlunsplit((parsed.scheme, parsed.netloc, uri, "", ""))
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": auth,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise RuntimeError("el objeto excede el tamaño permitido")
            return bytes(body)
    except RuntimeError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("no se pudo leer el archivo del almacenamiento") from exc


def delete_object_bytes(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    timeout_seconds: float = 30.0,
    ahora: datetime | None = None,
) -> None:
    """Elimina un objeto con DELETE SigV4. Repetir la operación es seguro."""
    if not endpoint or not bucket or not access_key or not secret_key:
        raise ValueError("almacenamiento sin configurar")
    clave = (object_key or "").strip("/")
    if not clave or ".." in clave.split("/"):
        raise ValueError("object key inválido")
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("endpoint de almacenamiento inválido")
    base_path = parsed.path.rstrip("/")
    uri = base_path + "/" + quote(bucket, safe="-_.~") + "/" + "/".join(
        quote(part, safe="-_.~") for part in clave.split("/")
    )
    host = parsed.netloc
    payload_hash = hashlib.sha256(b"").hexdigest()
    moment = ahora or _ahora()
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = moment.strftime("%Y%m%d")
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = (
        f"DELETE\n{uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    signing_region = region or "us-east-1"
    scope = f"{day}/{signing_region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signature = hmac.new(
        _firmar_clave(secret_key, day, signing_region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = urlunsplit((parsed.scheme, parsed.netloc, uri, "", ""))
    request = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": auth,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError("el almacenamiento rechazó la eliminación")
    except urllib.error.HTTPError as exc:
        # DELETE de un objeto inexistente es idempotente para la retención.
        if exc.code == 404:
            return
        raise RuntimeError("no se pudo eliminar el objeto del almacenamiento") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("no se pudo eliminar el objeto del almacenamiento") from exc


__all__ = [
    "DEV_STORAGE_HOST",
    "presign_put_url",
    "presign_get_url",
    "object_exists",
    "put_object_bytes",
    "get_object_bytes",
    "delete_object_bytes",
    "ticket_desarrollo",
    "firmar_subida",
]
