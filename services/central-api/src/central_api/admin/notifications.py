"""Notificaciones administrativas de central-api.

El envío es opcional y falla cerrado: crear un usuario nunca se deshace por un
problema SMTP, pero la respuesta informa si las credenciales fueron enviadas.
El valor de la API key solo aparece en el cuerpo del correo o en la respuesta
de alta cuando no se solicitó envío.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from central_api.settings import get_settings


def enviar_credenciales_email(
    destinatario: str, api_key: str, nombre: str = ""
) -> tuple[bool, str]:
    """Envía una API key por SMTP STARTTLS y devuelve resultado sanitizado."""

    settings = get_settings()
    if not settings.smtp_server or not settings.smtp_user or not settings.smtp_password:
        return False, "smtp_no_configurado"
    if not destinatario or "@" not in destinatario:
        return False, "email_no_valido"
    if not settings.smtp_starttls:
        return False, "smtp_tls_requerido"

    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = destinatario
    message["Subject"] = "Credenciales de acceso a Mr Bot"
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    message.set_content(
        f"{saludo}\n\n"
        "Se creó tu acceso a Mr Bot. Conserva esta API key en un lugar seguro:\n\n"
        f"{api_key}\n\n"
        "No la compartas ni la publiques.\n"
    )

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(
            host=settings.smtp_server, port=settings.smtp_port, timeout=10
        ) as server:
            server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        return False, "smtp_error"
    return True, "enviado"
