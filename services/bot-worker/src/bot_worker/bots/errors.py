"""Errores tipados de los plugins (plan 03-worker S11).

Define las categorias de error del protocolo worker -> central y las
excepciones que los plugins lanzan. El supervisor las normaliza a
``BotError`` (categoria + diagnostico interno + reintentable).

Las categorias se escriben en ingles porque son codigos internos que la
central traduce a mensaje publico; nunca llegan al cliente tal cual.
El diagnostico interno nunca incluye secretos: credenciales, cookies,
tokens ni URL firmadas.
"""

from __future__ import annotations


class Categoria:
    """Categorias de error del protocolo (plan 03-worker, seccion 11)."""

    ENVELOPE_INVALID = "ENVELOPE_INVALID"
    CREDENTIALS_REJECTED = "CREDENTIALS_REJECTED"
    CAPTCHA_UNSOLVABLE = "CAPTCHA_UNSOLVABLE"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CHROMIUM_CRASHED = "CHROMIUM_CRASHED"
    WORKER_OOM = "WORKER_OOM"
    ARTIFACT_UPLOAD_FAILED = "ARTIFACT_UPLOAD_FAILED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    INTERNAL = "INTERNAL"


class ErrorDeBot(Exception):
    """Base de errores esperados de un plugin.

    Lleva categoria de protocolo, diagnostico interno redactado y si el
    intento puede reintentarse. El diagnostico no debe interpolar
    secretos; usar :func:`sin_secretos` antes de construirlo.
    """

    categoria = Categoria.INTERNAL
    reintentable = False

    def __init__(
        self, diagnostico: str, *, diagnostic_code: str | None = None
    ) -> None:
        """``diagnostic_code`` es opcional y debe ser un identificador fijo.

        Nunca se construye con texto libre del portal: el codigo elige el
        mensaje operativo y permite distinguir etapas del mismo flujo
        (por ejemplo, captcha sin resolvedor frente a proveedor caido).
        """
        super().__init__(diagnostico)
        self.diagnostico = diagnostico
        if diagnostic_code is not None:
            self.diagnostic_code = diagnostic_code


class InvalidInputError(ErrorDeBot):
    """Entrada invalida: falla temprano en ``validate`` sin usar navegador."""

    categoria = Categoria.ENVELOPE_INVALID
    reintentable = False


class CredentialsRejectedError(ErrorDeBot):
    """El organismo rechazo las credenciales fiscales o faltan en runtime.

    ``diagnostic_code`` es opcional y debe ser un identificador fijo del
    codigo (nunca texto libre del portal). Permite distinguir, por ejemplo,
    una cuenta inexistente de una clave incorrecta sin exponer mensajes del
    sitio externo.
    """

    categoria = Categoria.CREDENTIALS_REJECTED
    reintentable = False


class CaptchaUnsolvableError(ErrorDeBot):
    """El proveedor de CAPTCHA agoto intentos o el desafio cambio."""

    categoria = Categoria.CAPTCHA_UNSOLVABLE
    reintentable = True


class TargetUnavailableError(ErrorDeBot):
    """El sitio del organismo devolvio 5xx, timeout o red inaccesible."""

    categoria = Categoria.TARGET_UNAVAILABLE
    reintentable = True

    def __init__(
        self,
        diagnostico: str,
        *,
        diagnostic_code: str = "target_unavailable_unclassified",
    ) -> None:
        super().__init__(diagnostico, diagnostic_code=diagnostic_code)
        # Código fijo de diagnóstico interno. Nunca sustituirlo por el texto
        # libre de una excepción, respuesta HTML, URL o credencial.


class BrowserCrashedError(ErrorDeBot):
    """El driver cerro el contexto o el proceso Chromium se perdio."""

    categoria = Categoria.CHROMIUM_CRASHED
    reintentable = True


class ArtifactUploadError(ErrorDeBot):
    """El PUT prefirmado expiro o el checksum no coincide.

    Solo se reintenta el reporte/subida con el mismo intento; nunca se
    vuelve a ejecutar el bot ya completado.
    """

    categoria = Categoria.ARTIFACT_UPLOAD_FAILED
    reintentable = True


class DeadlineExceededError(ErrorDeBot):
    """El presupuesto de deadline se agoto antes de completar el paso."""

    categoria = Categoria.DEADLINE_EXCEEDED
    reintentable = False


def sin_secretos(texto: str, secretos: object = None) -> str:
    """Reemplaza valores sensibles por ``<redacted>`` en un diagnostico.

    Recibe el texto y uno o varios secretos (str o iterable). Los valores
    vacios o nulos se ignoran. Usar siempre antes de guardar un
    diagnostico en ``BotError``, eventos o artefactos.
    """
    limpio = str(texto)
    if secretos is None:
        return limpio
    if isinstance(secretos, str):
        candidatos = [secretos]
    else:
        try:
            candidatos = list(secretos)
        except TypeError:
            candidatos = [secretos]
    for secreto in candidatos:
        if secreto is None:
            continue
        valor = str(secreto)
        if len(valor) >= 4 and valor in limpio:
            limpio = limpio.replace(valor, "<redacted>")
    return limpio
