import logging

from mrbot_contracts.log_redaction import (
    OBJETO_ELIMINADO,
    instalar_redaccion_de_urls,
    redactar_urls,
    ubicacion_de_objeto,
)

URL = (
    "https://s3.ca-central-1.s4.mega.io/miscomprobantes/jobs/01a1/1/csv/c0b5"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAXXXX%2F2026"
    "&X-Amz-Signature=deadbeef"
)


def test_la_url_se_reemplaza_por_bucket_y_clave():
    salida = redactar_urls(f'PUT {URL} "HTTP/1.1 200 OK"')
    assert "miscomprobantes/jobs/01a1/1/csv/c0b5" in salida
    for prohibido in ("https://", "s4.mega.io", "X-Amz", "deadbeef", "AKIAXXXX"):
        assert prohibido not in salida


def test_ubicacion_de_objeto_no_incluye_host_ni_query():
    assert ubicacion_de_objeto(URL) == "miscomprobantes/jobs/01a1/1/csv/c0b5"


def test_texto_sin_url_no_cambia():
    assert redactar_urls("sin urls") == "sin urls"


def test_el_filtro_instalado_redacta_los_logs_de_cualquier_logger(caplog):
    instalar_redaccion_de_urls()
    with caplog.at_level(logging.INFO):
        logging.getLogger("otro.modulo").info("subiendo a %s", URL)
        logging.getLogger("httpx").warning("request %s", URL)
    texto = " ".join(r.getMessage() for r in caplog.records)
    assert "X-Amz" not in texto and "https://" not in texto
    assert "miscomprobantes/jobs/01a1/1/csv/c0b5" in texto


def test_mensaje_de_objeto_eliminado():
    assert OBJETO_ELIMINADO == "objeto eliminado del bucket"
