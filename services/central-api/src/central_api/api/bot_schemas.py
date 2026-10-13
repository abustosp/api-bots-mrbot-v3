"""Per-operation request schemas and examples for the public bot API.

The public contract intentionally uses a flat body, like V1: fiscal credentials
(such as ``cuit_representante`` and ``clave``) sit beside the bot's query
fields. The route adapter is responsible for moving credentials to the signed
job envelope; this module only describes the public request shape.
"""

from __future__ import annotations

import calendar
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from central_api.api.bot_payloads import (
    CompatBodyBase,
    _apply_field_documentation,
    _example_value,
    public_bot_compat_body_model,
)


def _description(
    field_name: str,
    bot: str,
    operation: str,
    annotation: Any,
    field_info: Any,
) -> str:
    """Return the audited description and metadata for one request field."""
    documented = _apply_field_documentation(
        field_name, bot, operation, annotation, field_info
    )
    return documented.description or ""


def _example_values(bot: str, operation: str, *, alternate: bool = False,
                    encrypted: bool = False) -> dict[str, Any]:
    """Build a complete, safe, useful flat body for OpenAPI documentation."""
    model = public_bot_compat_body_model(bot, operation)
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name == "credentials":
            continue
        value = _example_value(name, bot, operation, field.default)
        if encrypted and name == "clave_encriptada":
            value = "BASE64_RSA_OAEP_CIPHERTEXT_DEMO"
        values[name] = value

    if bot == "mis_comprobantes" and operation in {"consulta", "consultar", "solicitar", "historial"}:
        values.update({
            "clave_encriptada": "BASE64_RSA_OAEP_CIPHERTEXT",
            "desde": "01/01/2024",
            "hasta": "31/12/2024",
            "cuit_inicio_sesion": "20123456786",
            "representado_nombre": "Empresa Ejemplo S.A.",
            "representado_cuit": "30876543210",
            "contrasena": "mi_contraseña_secreta",
            "descarga_emitidos": True,
            "descarga_recibidos": False,
            "emitidos": True,
            "recibidos": False,
            "puntos_venta_emitidos": ["1", "002", "00003"],
            "puntos_venta_recibidos": ["1", "002", "00003"],
            "carga_minio": True,
            "carga_json": False,
            "timeout_mc": 30,
            "proxy_request": False,
        })
        if operation == "solicitar":
            for name in ("descarga_emitidos", "descarga_recibidos", "carga_minio", "carga_json", "timeout_mc"):
                values.pop(name, None)
        else:
            values.pop("emitidos", None)
            values.pop("recibidos", None)
        if operation == "historial":
            values.pop("timeout_mc", None)
            values.pop("puntos_venta_emitidos", None)
            values.pop("puntos_venta_recibidos", None)

    if bot in {"retper_iibb_agip", "retper_iibb_misiones"} and operation == "consultar":
        values.update({"desde": "202601", "hasta": "202608", "denominacion": "Empresa Ejemplo"})
        if bot == "retper_iibb_agip":
            values.update({"usuario": "usuario@ejemplo.com", "clave": "clave_fiscal", "pv": False})

    return values


def _examples(bot: str, operation: str) -> list[dict[str, Any]]:
    return [_example_values(bot, operation)]


@lru_cache(maxsize=None)
def get_request_model(bot: str, operation: str) -> type[BaseModel]:
    """Return the Pydantic request class for a bot operation.

    Every catalogue pair has a dedicated class. Unknown pairs still receive a
    permissive, documented model so callers can keep a generic fallback rather
    than failing during route registration.
    """
    # ``public_bot_compat_body_model`` also provides a documented field-rich
    # fallback for unknown names, while keeping catalogue models precise.
    source_model = public_bot_compat_body_model(bot, operation)
    fields: dict[str, tuple[Any, Any]] = {}
    for name, field in source_model.model_fields.items():
        if name == "credentials":
            continue
        field_info = deepcopy(field)
        field_info.description = _description(
            name, bot, operation, field.annotation, field_info
        )
        fields[name] = (field.annotation, field_info)

    base_examples = _examples(bot, operation)
    base = type(
        f"{bot}_{operation}_RequestBase",
        (CompatBodyBase,),
        {
            "model_config": ConfigDict(
                # V2's request models ignored undeclared properties and did not
                # trim every string globally.  A few specific validators below
                # intentionally strip values where V2 did so.
                extra="ignore" if fields else "allow",
                str_strip_whitespace=False,
                json_schema_extra={
                    "title": f"{bot.replace('_', ' ').title()} {operation.replace('_', ' ').title()} Request",
                    "description": (
                        f"Cuerpo plano de la operación {bot}/{operation}. "
                        "Incluye los campos de consulta y las credenciales fiscales "
                        "del representante. Los archivos temporales se eliminan "
                        "siempre al terminar la operación."
                    ),
                    "examples": base_examples,
                },
            )
        },
    )
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}_request".split("_"))
    return create_model(model_name, __base__=base, **fields)


class V2CuitValidationError(ValueError):
    """Validation error with the historical HTTP 400 response body."""

    def __init__(self, invalid_fields: list[str]) -> None:
        self.invalid_fields = invalid_fields
        label = "CUIT invalido en" if len(invalid_fields) == 1 else "CUIT invalidos en"
        super().__init__(f"{label}: {', '.join(invalid_fields)}.")

    @property
    def detail(self) -> dict[str, Any]:
        label = "CUIT invalido en" if len(self.invalid_fields) == 1 else "CUIT invalidos en"
        return {
            "message": f"{label}: {', '.join(self.invalid_fields)}.",
            "invalid_fields": self.invalid_fields,
            "error_code": "Validation_400",
            "success": False,
        }


_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def _normalize_cuit(value: Any) -> str:
    return re.sub(r"\D", "", "" if value is None else str(value))


def _is_valid_cuit(value: Any) -> bool:
    cuit = _normalize_cuit(value)
    if len(cuit) != 11 or not cuit.isdigit():
        return False
    total = sum(int(digit) * weight for digit, weight in zip(cuit[:10], _CUIT_WEIGHTS))
    remainder = 11 - total % 11
    verifier = 0 if remainder == 11 else 9 if remainder == 10 else remainder
    return verifier == int(cuit[-1])


def normalize_v2_request_body(bot: str, operation: str, body: dict[str, Any]) -> dict[str, Any]:
    """Apply field normalization and validators performed by the matching V2 model.

    Schema failures are surfaced by the route as generic 422 responses so that
    submitted credentials and other sensitive values never enter error text.
    CUIT failures retain V2's structured 400 response.
    """
    normalized = dict(body)

    def strip_required(*names: str) -> None:
        for name in names:
            value = normalized.get(name)
            if isinstance(value, str):
                value = value.strip()
                normalized[name] = value
                if not value:
                    raise ValueError("El valor es obligatorio")

    if bot == "libros_portal_iva" and operation in {
        "descargar", "descargar_libros", "descargar_ddjj"
    }:
        now = datetime.now(timezone.utc)
        current_period = now.year * 100 + now.month
        periods = []
        for name in ("periodo_desde", "periodo_hasta"):
            value = normalized.get(name)
            if isinstance(value, str):
                value = value.strip()
                normalized[name] = value
            if not isinstance(value, str) or not re.fullmatch(r"\d{6}", value):
                raise ValueError("Período inválido. Use AAAAMM, con mes 01-12 y sin períodos futuros.")
            month = int(value[4:6])
            if not 1 <= month <= 12 or int(value) > current_period:
                raise ValueError("Período inválido. Use AAAAMM, con mes 01-12 y sin períodos futuros.")
            periods.append(value)
        if periods[0] > periods[1]:
            raise ValueError("El período inicial no puede ser posterior al período final.")

    if bot == "mis_retenciones_iva_simple" and operation == "consultar":
        try:
            desde = datetime.strptime(str(normalized.get("desde", "")).strip(), "%d/%m/%Y").date()
            hasta = datetime.strptime(str(normalized.get("hasta", "")).strip(), "%d/%m/%Y").date()
        except (TypeError, ValueError) as exc:
            raise ValueError("Las fechas deben tener formato DD/MM/AAAA.") from exc
        normalized["desde"] = desde.strftime("%d/%m/%Y")
        normalized["hasta"] = hasta.strftime("%d/%m/%Y")
        if hasta < desde:
            raise ValueError("La fecha final no puede ser anterior a la inicial.")
        next_year = desde.year + (1 if desde.month == 12 else 0)
        next_month = 1 if desde.month == 12 else desde.month + 1
        max_hasta = date(next_year, next_month, calendar.monthrange(next_year, next_month)[1])
        if hasta > max_hasta:
            raise ValueError("El rango de IVA Simple no puede superar el último día del mes siguiente.")

    if (bot, operation) in {
        ("retper_iibb_agip", "consultar"),
        ("retper_iibb_misiones", "consultar"),
    }:
        period_names = ("desde", "hasta")
        for name in period_names:
            value = normalized.get(name)
            if isinstance(value, str):
                value = value.strip()
                normalized[name] = value
            if not isinstance(value, str) or not re.fullmatch(r"\d{6}", value):
                raise ValueError("Período inválido. Use el formato AAAAMM.")
            if not 1 <= int(value[4:6]) <= 12:
                raise ValueError("Período inválido. Use el formato AAAAMM.")
        if normalized["desde"] > normalized["hasta"]:
            raise ValueError("El período inicial no puede ser posterior al período final.")
        strip_required("denominacion")
        if bot == "retper_iibb_agip":
            strip_required("usuario", "clave")
            if normalized.get("pv") is None:
                normalized["pv"] = False

    if bot == "retper_iibb_arba" and operation == "consultar":
        periodo = normalized.get("periodo")
        if isinstance(periodo, str):
            periodo = periodo.strip()
            normalized["periodo"] = periodo
        if not isinstance(periodo, str) or not re.fullmatch(r"\d{6}", periodo):
            raise ValueError("Período inválido. Use el formato AAAAMM.")
        if not 1 <= int(periodo[4:6]) <= 12:
            raise ValueError("Período inválido. Use el formato AAAAMM.")
        strip_required("clave", "denominacion")

    invalid: list[str] = []
    for name, value in list(normalized.items()):
        if "cuit" not in name.lower():
            continue
        if isinstance(value, str) and value:
            digits = _normalize_cuit(value)
            if not _is_valid_cuit(digits):
                invalid.append(name)
            else:
                normalized[name] = digits
        elif isinstance(value, list) and value:
            has_cuit_value = any(isinstance(item, str) and item.strip() for item in value)
            if not has_cuit_value:
                continue
            clean_values: list[str] = []
            for index, item in enumerate(value):
                digits = _normalize_cuit(item) if isinstance(item, str) else ""
                if not _is_valid_cuit(digits):
                    invalid.append(f"{name}[{index}]")
                else:
                    clean_values.append(digits)
            if not any(field.startswith(f"{name}[") for field in invalid):
                normalized[name] = clean_values
    if invalid:
        raise V2CuitValidationError(invalid)
    return normalized


def get_openapi_examples(bot: str, operation: str) -> dict[str, dict[str, Any]]:
    """Return examples in FastAPI's ``openapi_examples`` format."""
    return {
        "consulta_habitual": {
            "summary": "Ejemplo del esquema histórico",
            "description": "Valores tomados del schema del bot o de sus campos de entrada reales.",
            "value": _examples(bot, operation)[0],
        }
    }


@lru_cache(maxsize=None)
def get_result_model(bot: str, operation: str) -> type[BaseModel]:
    """Return the stable terminal-result envelope for one bot operation.

    Worker output data is intentionally plugin-defined JSON.  This helper gives
    callers a concrete Pydantic model for the common envelope without claiming
    a false static schema for each browser result.
    """
    model_name = "".join(
        part.capitalize() for part in f"{bot}_{operation}_result".split("_")
    )
    return create_model(
        model_name,
        result=(Literal["OK", "PARCIAL", "ERROR"], ...),
        data=(dict[str, Any], Field(default_factory=dict)),
        artifacts=(list[dict[str, Any]], Field(default_factory=list)),
        error=(dict[str, Any] | None, None),
    )


__all__ = ["get_request_model", "get_openapi_examples", "get_result_model"]
