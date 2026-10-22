"""Errores públicos seguros para bots y fronteras HTTP.

Los detalles técnicos se registran internamente; este módulo sólo genera o
sanitiza valores destinados a respuestas públicas.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence
import re


class StrEnum(str, Enum):
    """Compatibilidad con ``enum.StrEnum`` para Python 3.9 y anteriores."""

    def __str__(self) -> str:
        return self.value


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    ADDITIONAL_VALIDATION = "additional_validation"
    SERVICE_NOT_ENABLED = "service_not_enabled"
    EXTERNAL_TIMEOUT = "external_timeout"
    NAVIGATION = "navigation"
    QUERY = "query"
    DOWNLOAD = "download"
    PROCESSING = "processing"
    STORAGE = "storage"
    UNEXPECTED = "unexpected"


PublicErrorCategory = ErrorCategory

STAGE_LABELS: dict[str, str] = {
    "login": "el inicio de sesión", "autenticación": "el inicio de sesión", "authentication": "el inicio de sesión",
    "consulta": "la consulta", "query": "la consulta", "navegación": "la navegación", "navigation": "la navegación",
    "descarga": "la descarga", "download": "la descarga", "procesamiento": "el procesamiento", "processing": "el procesamiento",
    "almacenamiento": "el almacenamiento", "storage": "el almacenamiento", "captcha": "la validación adicional",
    "validación": "la validación de los datos", "validacion": "la validación de los datos",
}

PUBLIC_MESSAGES: dict[ErrorCategory, str] = {
    ErrorCategory.AUTHENTICATION: "No fue posible iniciar sesión. Verifique el CUIT y la clave ingresados.",
    ErrorCategory.ADDITIONAL_VALIDATION: "Se requiere completar la validación adicional solicitada por el organismo.",
    ErrorCategory.SERVICE_NOT_ENABLED: "El servicio solicitado no está habilitado para este usuario. Verifique la habilitación e intente nuevamente.",
    ErrorCategory.EXTERNAL_TIMEOUT: "El organismo no respondió a tiempo durante {stage}. Intente nuevamente.",
    ErrorCategory.NAVIGATION: "No se pudo completar {stage} en el organismo consultado. Intente nuevamente.",
    ErrorCategory.DOWNLOAD: "No se pudo descargar {resource}. Intente nuevamente.",
    ErrorCategory.PROCESSING: "No se pudo procesar {resource}. Intente nuevamente.",
    ErrorCategory.STORAGE: "La consulta terminó, pero no se pudo guardar {resource}.",
    ErrorCategory.VALIDATION: "Los datos ingresados no son válidos.",
    ErrorCategory.QUERY: "No se pudo completar la consulta. Intente nuevamente.",
    ErrorCategory.UNEXPECTED: "No se pudo completar {stage}. Intente nuevamente o contacte a soporte.",
}

BUSINESS_MESSAGE_ALLOWLIST = frozenset({
    "Credenciales inválidas.", "Credenciales inválidas", "CUIT inválido.", "El período es obligatorio.", "El periodo es obligatorio.",
    "El período ingresado no es válido.", "Usuario no encontrado", "API key faltante", "Email faltante",
    "Usuario deshabilitado", "Captcha detectado", "Clave Incorrecta", "Se requiere cambiar la clave fiscal",
    "No se pudo iniciar sesión", "No se pudo iniciar sesion en ARCA",
    "Faltan parametros requeridos: CUIT representante, clave y CUIT representado.",
    "Faltan parametros requeridos para la consulta Declaracion en linea.",
    "Faltan parametros requeridos para la consulta SIFERE.",
    "Facturómetro: datos no disponibles en esta página", "Rango de periodos invalido",
    "No se pudo seleccionar el representado en el combo", "No se pudo obtener el QR del VEP.",
    "El Job ya comenzó y no puede cancelarse", "Job ya terminado", "Job no encontrado",
    "Job no puede cancelarse en estado actual", "el archivo fue eliminado por política de privacidad",
    # Mensajes de negocio ya redactados por los bots (paridad con V1). Son
    # literales fijos, sin interpolación, así que no pueden transportar datos
    # del cliente ni detalles de infraestructura.
    "Faltan parametros requeridos.", "Faltan parametros requeridos",
    "Periodo invalido.", "Periodo desde y hasta requeridos.",
    "Periodo desde y hasta son requeridos.",
    "Periodo desde y hasta son requeridos para la consulta Declaracion en linea.",
    "Periodo desde/hasta invalido. Use formato AAAAMM.",
    "No se encontraron DDJJ en el rango solicitado.",
    "No se encontraron filas de DDJJ para seleccionar.",
})

# Mensajes de negocio con una parte variable acotada (periodo, importe de
# texto). Se evalúan después de los filtros técnicos, de modo que una variante
# con detalles de infraestructura sigue siendo descartada. Portado de V1.
BUSINESS_MESSAGE_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"^No se encontró el CUIT representado \d+ en el selector de representados\. .+$"),
    re.compile(r"^No se pudo construir (?:la )?URL de DDJJ para \d{6}\.$"),
    re.compile(r"^No se encontraron (?:DDJJ|VEP|comprobantes)[^.]*\.$"),
)

PUBLIC_ARTIFACT_MESSAGES = frozenset({
    "el archivo fue eliminado por política de privacidad",
})

_SENSITIVE = re.compile(
    r"playwright|locator|page\.(?:goto|click|fill)|stack\s*trace|traceback|https?://|s3://|presigned|x-amz-|"
    r"file://|(?:/|\\)(?:home|tmp|var|mnt|app|workspace)(?:/|\\)|selector|chrom(?:e|ium)|openssl|minio|driver|csrf|"
    r"(?:token|password|clave)=|password|secret(?:[_ -]?key)?|api[_ -]?key|access[_ -]?key|bearer\b|"
    r"sql[_ ]?alchemy|database|target\s+closed|strict\s+mode|timeout\s+\d+ms|connection\s+(?:refused|reset)", re.I,
)
_TECHNICAL = re.compile(
    r"exception|traceback|stack|debug|locator|selector|driver|browser|chrom(?:e|ium)|minio|s3|openssl|sql[_ ]?alchemy|"
    r"database|connection|errno|attributeerror|runtimeerror|assertionerror|typeerror|keyerror|indexerror|"
    r"\berror\b|\bfailed\b|\bfailure\b|\btimeout\b", re.I,
)
_CATALOG_PREFIXES = (
    "No fue posible iniciar sesión.", "Se requiere completar la validación adicional", "El servicio solicitado no está habilitado",
    "El organismo no respondió a tiempo", "No se pudo completar ", "No se pudo descargar ", "No se pudo procesar ",
    "La consulta terminó, pero no se pudo guardar ", "Los datos ingresados no son válidos.", "No se pudo completar la consulta.",
)

# These keys are implementation details of validation libraries, HTTP clients,
# and exception objects.  They must not survive a public error boundary.  Keep
# this list key-based instead of value-based so an attacker cannot smuggle a
# raw body or traceback under an otherwise harmless value.
_DROP_PUBLIC_ERROR_KEYS = frozenset({
    "input", "ctx",
    "body", "raw", "raw_body", "raw_content", "raw_payload", "raw_request", "raw_response",
    "request", "request_body", "response", "response_body", "payload", "content",
    "original_error", "original_exception", "original_message", "raw_error", "raw_message",
    "trace", "traces", "traceback", "stack", "stack_trace", "stacktrace",
    "exception", "exceptions", "cause", "causes", "__cause__", "__context__",
    "debug", "debug_info", "debug_details", "technical_details", "internal_details",
})

_ERROR_KEYS = frozenset({
    "error", "errors", "errores", "error_message", "error_messages", "error_detail", "error_details",
    "error_log", "error_logs", "despachos_error", "periodos_error", "errores_por_seccion",
    "despachos_con_error", "exception", "exceptions", "traceback", "stack_trace", "detail",
    "message", "msg", "messages", "sub_error", "sub_errors",
})

_PUBLIC_METADATA_KEYS = frozenset({
    "error_code", "code", "status_code", "error_status", "success", "invalid_fields",
    "periodo", "período", "tipo", "name", "id", "resource", "operation", "bot", "status", "result",
})

_PUBLIC_CODE = re.compile(
    r"(?:arca|afip|internal|validation|service|query|download|processing|storage|auth)[a-z0-9_.-]*",
    re.I,
)
_VALIDATION_ERROR_KEYS = frozenset({"type", "loc", "msg", "input", "ctx"})


def _safe_label(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text and len(text) <= 80 and "\n" not in text and not _SENSITIVE.search(text) else default


def _stage(value: Any) -> str:
    text = str(value or "").strip()
    return STAGE_LABELS.get(text.lower(), _safe_label(text, "la etapa indicada"))


# --- Redacción de credenciales conocidas --------------------------------------
# Un mensaje puede ser de negocio y aun así interpolar la clave fiscal (por
# ejemplo el prefijo del login de ARCA). Estos ayudantes tapan ese dato sin
# descartar el mensaje. El marcador es el mismo literal que
# ``app.utils.persistence.REDACTED_MARKER`` para que el valor sea único.
REDACTED_MARKER = "[REDACTED]"

# Los secretos muy cortos reemplazarían fragmentos de palabras comunes.
MIN_SECRET_LENGTH = 5

_WHITESPACE_RE = re.compile(r"\s+")
_MULTILINE_RE = re.compile(r"[\r\n\t]+")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return _WHITESPACE_RE.sub(" ", str(value)).strip()


def redact_secrets(value: Any, secrets: Optional[Iterable[Any]] = None) -> str:
    """Reemplaza credenciales conocidas por un marcador neutro.

    El reemplazo es literal (nunca por expresión regular) para no reinterpretar
    los caracteres especiales del secreto, y sólo aplica a secretos de 5+
    caracteres para no romper palabras comunes.
    """
    text = _clean_text(value)
    if not text or not secrets:
        return text
    for secret in secrets:
        candidate = _clean_text(secret)
        if len(candidate) < MIN_SECRET_LENGTH:
            continue
        if candidate in text:
            text = text.replace(candidate, REDACTED_MARKER)
    return text


def contains_technical_details(value: Any) -> bool:
    """True si el texto expone internals de automatización o infraestructura."""
    text = _clean_text(value)
    return bool(text and _SENSITIVE.search(text))


def scrub_technical_details(value: Any, *, secrets: Optional[Iterable[Any]] = None) -> str:
    """Última barrera: elimina las líneas y fragmentos con detalles técnicos."""
    text = _clean_text(value)
    if not text:
        return ""
    if not contains_technical_details(text):
        return redact_secrets(text, secrets)

    kept_lines = [
        line
        for line in _MULTILINE_RE.split(str(value))
        if line.strip() and not _SENSITIVE.search(line)
    ]
    joined = " ".join(kept_lines)
    if not joined.strip():
        # Texto en una sola línea: se conserva el prefijo previo a lo técnico.
        first_match = _SENSITIVE.search(str(value))
        joined = str(value)[: first_match.start()] if first_match else ""
    return redact_secrets(_clean_text(_SENSITIVE.sub(" ", joined)), secrets)


def classify_exception(exc: BaseException | None = None, *, context: str | None = None) -> ErrorCategory:
    """Clasifica por clase/contexto, sin copiar ``str(exc)``."""
    names = " ".join(cls.__name__.lower() for cls in type(exc).mro()) if exc else ""
    ctx = (context or "").lower()
    if "timeout" in names or "timeout" in ctx: return ErrorCategory.EXTERNAL_TIMEOUT
    if any(x in names for x in ("serviceunavailable", "servicenotenabled", "notenabled")) or any(x in ctx for x in ("service_not_enabled", "servicio no habilitado", "servicio no disponible")): return ErrorCategory.SERVICE_NOT_ENABLED
    if any(x in names for x in ("authentication", "login", "credential")) or any(x in ctx for x in ("login", "auth", "autenticación")): return ErrorCategory.AUTHENTICATION
    if any(x in names for x in ("captcha", "passwordchange", "additionalvalidation")) or "captcha" in ctx: return ErrorCategory.ADDITIONAL_VALIDATION
    if any(x in names for x in ("navigation", "playwright", "targetclosed", "browser")) or any(x in ctx for x in ("navigation", "navegación")): return ErrorCategory.NAVIGATION
    if any(x in names for x in ("download", "connection", "http")) or any(x in ctx for x in ("download", "descarga")): return ErrorCategory.DOWNLOAD
    if any(x in names for x in ("storage", "s3", "minio", "oserror")) or any(x in ctx for x in ("storage", "almacenamiento")): return ErrorCategory.STORAGE
    if any(x in names for x in ("process", "parse")) or "process" in ctx or "procesamiento" in ctx: return ErrorCategory.PROCESSING
    if any(x in names for x in ("validation", "valueerror")) or any(x in ctx for x in ("validation", "validación", "validacion")): return ErrorCategory.VALIDATION
    if any(x in names for x in ("notfound", "not_found")): return ErrorCategory.QUERY
    if "query" in ctx or "consulta" in ctx: return ErrorCategory.QUERY
    return ErrorCategory.UNEXPECTED


def public_error(category: ErrorCategory | str, *, stage: str = "la etapa indicada", resource: str = "el archivo") -> str:
    category = ErrorCategory(category)
    return PUBLIC_MESSAGES[category].format(stage=_stage(stage), resource=_safe_label(resource, "el recurso solicitado"))


_BATCH_ITEM_CODES = frozenset({"not_found", "internal"})


def _safe_business_fallback(value: Any) -> str | None:
    if not isinstance(value, str): return None
    text = value.strip()
    if not text: return None
    if text in _BATCH_ITEM_CODES: return text
    if text in BUSINESS_MESSAGE_ALLOWLIST: return text
    if _SENSITIVE.search(text): return None
    # Un mensaje ya saneado por nosotros puede llevar el marcador de redacción.
    # El marcador no debe invalidarlo: se evalúa como si fuera una letra, así
    # sigue pasando por las mismas reglas acotadas y nunca se publica un texto
    # que no las cumpla (por ejemplo "[REDACTED] <script>").
    malla = text.replace(REDACTED_MARKER, "X") if REDACTED_MARKER in text else text
    # Un resultado por contribuyente puede incluir una descripción funcional
    # breve (paridad V1). Se evalúa antes del denylist de "error" porque el
    # sufijo permitido está estrictamente acotado a texto sin infraestructura.
    if re.fullmatch(r"CUIT\s+[0-9 .-]{8,20}:\s*[\w áéíóúüñÁÉÍÓÚÜÑ.,()/-]{1,100}", malla): return text
    if _TECHNICAL.search(text): return None
    if any(pattern.match(malla) for pattern in BUSINESS_MESSAGE_PATTERNS): return text
    if malla.startswith(_CATALOG_PREFIXES) and ":" not in malla and len(malla) <= 220: return text
    # Sólo se aceptan otras variantes con formato funcional acotado. No se
    # permite la antigua regla genérica por primera palabra, porque convertía
    # cualquier texto histórico que comenzara por "No", "El" o "Debe" en un
    # mensaje público aunque no perteneciera al catálogo.
    if re.fullmatch(r"No se (?:encontr[oó]|encontraron) (?:el|la|los|las) [\w áéíóúüñÁÉÍÓÚÜÑ0-9_./-]{1,140}", malla):
        return text
    if re.fullmatch(r"No se pudo iniciar sesi[oó]n(?: en [\w áéíóúüñÁÉÍÓÚÜÑ0-9_./-]{1,80})?", malla):
        return text
    return None


def safe_public_message(exc: BaseException | None = None, *, category: ErrorCategory | str | None = None,
                        context: str | None = None, stage: str = "la etapa indicada", resource: str = "el archivo",
                        fallback: Any = None, secrets: Optional[Iterable[Any]] = None) -> str:
    """Convierte una excepción a un mensaje controlado sin usar su representación.

    ``secrets`` tapa las credenciales conocidas que el propio flujo haya
    interpolado dentro de un mensaje de negocio (por ejemplo la clave fiscal);
    sin ``secrets`` el mensaje se devuelve sin cambios.
    """
    candidate = _safe_business_fallback(fallback)
    message = candidate if candidate is not None else public_error(category or classify_exception(exc, context=context), stage=stage, resource=resource)
    return redact_secrets(message, secrets) if secrets else message


def _is_informative_after_scrub(text: str) -> bool:
    """True si el texto saneado sigue siendo un mensaje útil."""
    return len(text) >= 15 and text.endswith((".", "!", ")"))


def safe_public_text(value: Any, *, secrets: Optional[Iterable[Any]] = None, **kwargs: Any) -> str:
    """Sanea un texto ya redactado sin descartar su contenido de negocio.

    Sólo elimina los fragmentos técnicos y las credenciales conocidas; si tras
    la limpieza no queda un mensaje informativo se devuelve el mensaje genérico
    del catálogo.
    """
    text = redact_secrets(value, secrets)
    if not text:
        return ""
    if not contains_technical_details(text):
        return text
    scrubbed = scrub_technical_details(text, secrets=secrets)
    if _is_informative_after_scrub(scrubbed):
        return scrubbed
    return safe_public_message(fallback=text, secrets=secrets, **kwargs)


def _is_dropped_error_key(key: str) -> bool:
    """Recognize raw/diagnostic field-name variants, including nested ones."""
    lowered = key.casefold().replace("-", "_")
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", key).casefold().replace("-", "_")
    for candidate in {lowered, snake_case}:
        if candidate in _DROP_PUBLIC_ERROR_KEYS:
            return True
        if candidate.endswith(("_body", "_trace", "_traceback", "_stack_trace", "_input", "_ctx")):
            return True
        if candidate.startswith(("raw_", "original_", "traceback_", "trace_", "stack", "debug_", "technical_", "internal_", "exception_", "cause_")):
            return True
    return False


def _is_public_code(value: Any) -> bool:
    """Allow only the established public error-code vocabulary."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(
        text
        and len(text) <= 80
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", text)
        and _PUBLIC_CODE.fullmatch(text)
    )


def _sanitize_metadata(key: str, value: Any) -> Any:
    """Preserve intentionally public metadata without copying technical data."""
    lowered = key.casefold()
    if lowered in {"error_code", "code", "error_status"}:
        return value if _is_public_code(value) else None
    if lowered == "status_code":
        return value if isinstance(value, (bool, int, float)) else None
    if lowered == "success":
        return value if isinstance(value, bool) else None
    if lowered == "invalid_fields":
        if not isinstance(value, (list, tuple)):
            return []
        return [label for item in value if (label := _safe_label(item, ""))]
    if lowered in {"periodo", "período", "tipo", "name", "id", "resource", "operation", "bot", "status", "result"}:
        if isinstance(value, str):
            return _safe_label(value, "")
        return value if value is None or isinstance(value, (bool, int, float)) else None
    return None


def _looks_like_validation_error(value: Mapping[Any, Any]) -> bool:
    """Detect a Pydantic-style error item before normal response traversal."""
    keys = {str(key).casefold() for key in value}
    return len(keys & _VALIDATION_ERROR_KEYS) >= 3 and bool(keys & {"input", "ctx", "loc", "type"})


def sanitize_error_value(value: Any, **kwargs: Any) -> Any:
    """Sanea un valor ubicado en ``error``, ``errors``, ``detail`` o similar."""
    if isinstance(value, BaseException):
        return safe_public_message(exc=value, **kwargs)
    if isinstance(value, str):
        candidate = _safe_business_fallback(value)
        if candidate is not None:
            secrets = kwargs.get("secrets")
            return redact_secrets(candidate, secrets) if secrets else candidate
        return safe_public_message(**kwargs)
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.casefold()
            if _is_dropped_error_key(key_text) or lowered in {"type", "loc"}:
                continue
            safe_key = key
            if lowered not in _PUBLIC_METADATA_KEYS and lowered not in _ERROR_KEYS and _SENSITIVE.search(str(key)):
                safe_key = "detalle"
                while safe_key in result:
                    safe_key = f"detalle_{len(result) + 1}"
            if lowered in _PUBLIC_METADATA_KEYS:
                cleaned = _sanitize_metadata(lowered, item)
                if isinstance(cleaned, str) and kwargs.get("secrets"):
                    cleaned = redact_secrets(cleaned, kwargs["secrets"])
                if cleaned is not None:
                    result[safe_key] = cleaned
                elif lowered in {"status", "result"} and item is None:
                    result[safe_key] = None
            elif lowered in _ERROR_KEYS:
                result[safe_key] = sanitize_error_value(item, **kwargs)
            elif isinstance(item, (Mapping, list, tuple)):
                result[safe_key] = sanitize_error_value(item, **kwargs)
            elif isinstance(item, BaseException):
                result[safe_key] = safe_public_message(exc=item, **kwargs)
            elif isinstance(item, str):
                result[safe_key] = safe_public_message(**kwargs)
            else:
                result[safe_key] = item
        return result
    if isinstance(value, list): return [sanitize_error_value(item, **kwargs) for item in value]
    if isinstance(value, tuple): return tuple(sanitize_error_value(item, **kwargs) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if value is not None:
        return safe_public_message(**kwargs)
    return value


def sanitize_response_payload(value: Any, **kwargs: Any) -> Any:
    """Sanea sólo campos de error y preserva los demás datos funcionales."""
    def is_error_key(key: str) -> bool:
        """Reconoce variantes históricas sin convertir códigos en mensajes."""
        if key in _ERROR_KEYS or key in {"error_code", "error_status"}:
            return key in _ERROR_KEYS
        return (
            key.startswith("error_")
            or key.startswith("errores_")
            or key.endswith("_error")
            or key.endswith("_errors")
        )

    def sanitize_artifact_reference(key: str, item: Any) -> Any:
        """No deja rutas locales ni placeholders de almacenamiento en JSON público."""
        if not isinstance(item, str):
            return item
        lowered = key.lower()
        if item in PUBLIC_ARTIFACT_MESSAGES:
            return item
        if lowered in {"ruta_archivo", "ruta_qr", "response_ruta_archivo", "response_ruta_qr", "local_path"}:
            return None
        if item.lower().startswith("presigned://"):
            return ""
        if (
            lowered in {"url", "url_minio", "url_minio_qr", "minio_url", "minio_urls"}
            or lowered.endswith(("_url", "_url_minio", "_minio_url"))
        ) and item and not item.lower().startswith(("http://", "https://")):
            return ""
        return item

    if isinstance(value, Mapping):
        if _looks_like_validation_error(value):
            return sanitize_error_value(value, **kwargs)
        failed = value.get("success") is False or str(value.get("status", "")).lower() in {"error", "failed"} or str(value.get("result", "")).upper() == "ERROR"
        result: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.casefold()
            # Apply the key barrier at every response depth, not only inside
            # ``detail``.  This covers raw validation envelopes nested below
            # data/result/error collections as well as failed responses.
            if _is_dropped_error_key(key_text):
                continue
            # ``message`` is also the normal business-message field in V1
            # response envelopes.  Treat it as an error only when the
            # envelope explicitly reports failure; otherwise preserve the
            # successful message for V2 parity (e.g. MC ``success=True``).
            message_is_error = lowered in {"message", "msg", "messages"} and failed
            if (
                is_error_key(lowered)
                and lowered not in {"message", "msg", "messages"}
            ) or message_is_error:
                result[key] = sanitize_error_value(item, **kwargs)
            elif isinstance(item, str):
                cleaned = sanitize_artifact_reference(lowered, item)
                secrets = kwargs.get("secrets")
                result[key] = redact_secrets(cleaned, secrets) if (secrets and isinstance(cleaned, str)) else cleaned
            else:
                result[key] = sanitize_response_payload(item, **kwargs)
        return result
    if isinstance(value, list): return [sanitize_response_payload(item, **kwargs) for item in value]
    if isinstance(value, tuple): return tuple(sanitize_response_payload(item, **kwargs) for item in value)
    if isinstance(value, BaseException):
        return safe_public_message(exc=value, **kwargs)
    if isinstance(value, str) and value.lower().startswith("presigned://"):
        return ""
    return value


def safe_error_messages(value: Any, **kwargs: Any) -> list[str]:
    """Normalize a bot error collection for persistence or a response field."""
    cleaned = sanitize_error_value(value, **kwargs)
    if isinstance(cleaned, (list, tuple)):
        messages = []
        for item in cleaned:
            if isinstance(item, str) and item:
                messages.append(item)
            elif item is not None:
                messages.append(safe_public_message(**kwargs))
        return messages
    if isinstance(cleaned, str) and cleaned:
        return [cleaned]
    return [safe_public_message(**kwargs)] if cleaned is not None else []


sanitize_public = sanitize_error_value
sanitize_public_error = sanitize_error_value
sanitize_errors = sanitize_error_value

__all__ = ["ErrorCategory", "PublicErrorCategory", "PUBLIC_MESSAGES", "BUSINESS_MESSAGE_ALLOWLIST", "BUSINESS_MESSAGE_PATTERNS", "PUBLIC_ARTIFACT_MESSAGES", "REDACTED_MARKER", "MIN_SECRET_LENGTH", "classify_exception", "public_error", "safe_public_message", "safe_public_text", "safe_error_messages", "sanitize_error_value", "sanitize_response_payload", "sanitize_public", "sanitize_public_error", "sanitize_errors", "redact_secrets", "contains_technical_details", "scrub_technical_details"]
