"""Guards de MercadoPago sandbox (plan 04 §§7.6-7.9, invariante B-3).

Sin credenciales reales no se cobra: sin ``MP_ACCESS_TOKEN`` la central
opera en modo ``fake`` documentado (checkout local sin red, sin débito).
Con token, el entorno ``MP_ENVIRONMENT`` elige sandbox o producción, y la
acreditación SIEMPRE exige webhook firmado + reconsulta autenticada con
contraste exacto (ver ``facade.apply_verified_payment``).

Todo es fail-closed: producción sin token, con token de prueba o sin
webhook secret no arranca el cobro (``ConfigMercadoPagoError``). El modo
``live_mode`` del aviso debe coincidir con el entorno configurado.
Este módulo es stdlib-only para probarse sin red ni SDK.
"""

from __future__ import annotations

#: Modos de operación de MercadoPago.
MODO_FAKE = "fake"
MODO_SANDBOX = "sandbox"
MODO_PRODUCCION = "production"

#: Prefijos de credenciales de prueba de MercadoPago (nunca producción).
PREFIJOS_PRUEBA = ("TEST-", "TEST_")


class ConfigMercadoPagoError(ValueError):
    """Configuración de MercadoPago insegura: el cobro no debe arrancar."""


def modo_operacion(mp_environment: str, mp_access_token: str) -> str:
    """Resuelve el modo: sin token siempre ``fake`` (sin cobro real)."""
    if not (mp_access_token or "").strip():
        return MODO_FAKE
    texto = (mp_environment or "sandbox").strip().lower()
    return MODO_PRODUCCION if texto == "production" else MODO_SANDBOX


def es_token_prueba(mp_access_token: str) -> bool:
    """Indica si el token es de prueba (nunca válido en producción)."""
    texto = (mp_access_token or "").strip().upper()
    return texto.startswith(PREFIJOS_PRUEBA)


def validar_configuracion(
    mp_environment: str, mp_access_token: str, mp_webhook_secret: str
) -> str:
    """Valida fail-closed y devuelve el modo de operación.

    Producción exige token real (no ``TEST-``) y webhook secret; sandbox
    exige token (puede ser de prueba); sin token el modo es ``fake`` y no
    hay nada que validar porque nada se cobra.
    """
    modo = modo_operacion(mp_environment, mp_access_token)
    if modo == MODO_FAKE:
        return modo
    if modo == MODO_PRODUCCION:
        if es_token_prueba(mp_access_token):
            raise ConfigMercadoPagoError(
                "producción con credencial de prueba"
            )
        if not (mp_webhook_secret or "").strip():
            raise ConfigMercadoPagoError(
                "producción sin MP_WEBHOOK_SECRET"
            )
        return modo
    if not (mp_access_token or "").strip():  # pragma: no cover - modo fake ya salió
        raise ConfigMercadoPagoError("sandbox sin MP_ACCESS_TOKEN")
    return modo


def coincide_entorno(live_mode: bool, mp_environment: str) -> bool:
    """Indica si el aviso (``live_mode``) coincide con el entorno propio."""
    return bool(live_mode) == ((mp_environment or "sandbox").strip().lower() == "production")


def url_checkout(external_reference: str, modo: str) -> str:
    """URL de pago según modo (fake local, sandbox o producción)."""
    ref = external_reference or ""
    if modo == MODO_PRODUCCION:
        return f"https://www.mercadopago.com/checkout/v1/redirect?preference-id={ref}"
    if modo == MODO_SANDBOX:
        return (
            "https://sandbox.mercadopago.com/checkout/v1/redirect"
            f"?preference-id={ref}"
        )
    return f"https://mercadopago.example/checkout/{ref}"


def nota_modo(modo: str) -> str:
    """Nota pública del checkout según modo (sin secretos ni montos)."""
    if modo == MODO_FAKE:
        return (
            "Modo fake documentado: sin MP_ACCESS_TOKEN no hay cobro real. "
            "La acreditación ocurre solo tras webhook verificado."
        )
    if modo == MODO_SANDBOX:
        return (
            "Entorno sandbox: solo usuarios y tarjetas de prueba. "
            "La acreditación ocurre solo tras webhook verificado."
        )
    return "La acreditación ocurre solo tras webhook verificado."


__all__ = [
    "MODO_FAKE",
    "MODO_SANDBOX",
    "MODO_PRODUCCION",
    "PREFIJOS_PRUEBA",
    "ConfigMercadoPagoError",
    "modo_operacion",
    "es_token_prueba",
    "validar_configuracion",
    "coincide_entorno",
    "url_checkout",
    "nota_modo",
]
