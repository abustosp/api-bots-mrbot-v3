from __future__ import annotations

import asyncio
import io
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from central_api.services import apoc_base
from central_api.scheduler import dispatcher


TABLA = (
    "# AFIP - Facturas Apocrifas\n"
    "20123456789, 2024-01-01, 2024-12-31\n"
    "20987654321, 2023-06-15, 2023-07-20\n"
)


def archive(member="FacturasApocrifas.txt", text=TABLA):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(member, "\ufeff" + text)
    return out.getvalue()


@pytest.fixture
def setup_cache(tmp_path, monkeypatch):
    path = tmp_path / "apoc.txt"
    settings = SimpleNamespace(apoc_base_url="https://fake.invalid/data", apoc_base_path=str(path), apoc_refresh_interval_days=7,
                               proxy_enabled=False, proxy_host="", proxy_mode="standard", proxy_username="", proxy_password="", proxy_country="ar",
                               capmonster_api_key="", capmonster_arca_key="", capmonster_srt_key="", cuit_service_base_url="", cuit_service_masiva_url="", cuit_service_usuario="", cuit_service_api_key="")
    monkeypatch.setattr(apoc_base, "get_settings", lambda: settings)
    monkeypatch.setattr(dispatcher, "get_settings", lambda: settings)
    monkeypatch.setattr(apoc_base, "_cached_text", None)
    return path


async def client_for(body=None, error=False):
    def handler(request):
        if error:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, content=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_download_when_missing(setup_cache):
    async def correr():
        client = await client_for(archive())
        return await apoc_base.download_and_cache(client)

    assert asyncio.run(correr()) == TABLA
    assert setup_cache.read_text() == TABLA
    assert apoc_base.read_cached_text() == TABLA


def test_refresh_when_old(setup_cache, monkeypatch):
    setup_cache.write_text("old")
    viejo = time.time() - 8 * 86400
    os.utime(setup_cache, (viejo, viejo))
    nuevo = "# AFIP\n20987654321, 2023-06-15, 2023-07-20\n"

    async def correr():
        client = await client_for(archive(text=nuevo))
        original = apoc_base.download_and_cache

        async def descargar():
            return await original(client)

        monkeypatch.setattr(apoc_base, "download_and_cache", descargar)
        return await apoc_base.refresh_if_needed()

    assert asyncio.run(correr()) is True
    assert setup_cache.read_text() == nuevo


def test_does_not_refresh_fresh(setup_cache, monkeypatch):
    setup_cache.write_text("fresh")
    monkeypatch.setattr(
        apoc_base, "download_and_cache", lambda: pytest.fail("unexpected download")
    )
    assert asyncio.run(apoc_base.refresh_if_needed()) is False


def test_network_failure_preserves_stale_cache(setup_cache, monkeypatch):
    setup_cache.write_text("stale")
    viejo = time.time() - 8 * 86400
    os.utime(setup_cache, (viejo, viejo))

    async def correr():
        client = await client_for(error=True)
        original = apoc_base.download_and_cache

        async def descargar():
            return await original(client)

        monkeypatch.setattr(apoc_base, "download_and_cache", descargar)
        return await apoc_base.refresh_if_needed()

    assert asyncio.run(correr()) is False
    assert apoc_base.read_cached_text() == "stale"


def test_extract_exact_member():
    assert apoc_base._extract(archive("FacturasApocrifas.txt")) == TABLA


def test_extract_renamed_single_txt_member():
    # AFIP renombra el miembro pero sigue siendo el único .txt: se acepta.
    assert apoc_base._extract(archive("facturas_apocrifas_20240901.txt")) == TABLA


def test_zip_with_no_txt_member():
    with pytest.raises(ValueError, match="no .txt member"):
        apoc_base._extract(archive("data.csv"))


def test_zip_with_multiple_txt_members_fails_cleanly():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("a.txt", "\ufeff" + TABLA)
        zf.writestr("b.txt", "\ufeff" + TABLA)
    with pytest.raises(ValueError, match="multiple .txt members"):
        apoc_base._extract(out.getvalue())


def test_not_table_like_content_fails_cleanly():
    with pytest.raises(ValueError, match="does not look like the apoc table"):
        apoc_base._extract(archive(text="public-table"))


def test_not_table_like_not_cached(setup_cache):
    async def correr():
        client = await client_for(archive(text="blah 12345 no AFIP ni filas"))
        return await apoc_base.download_and_cache(client)

    with pytest.raises(ValueError, match="does not look like the apoc table"):
        asyncio.run(correr())
    assert not setup_cache.exists()
    assert apoc_base.read_cached_text() is None


def test_provision_includes_cached_text_for_apoc(setup_cache, monkeypatch):
    setup_cache.write_text("cached")
    apoc_base._cached_text = None
    seccion = dispatcher.provisioned_section("apoc")
    # El plugin la lee de ``service`` (configure()), no del nivel superior.
    assert seccion["service"]["apoc_base_text"] == "cached"
    assert "apoc_base_text" not in seccion


def test_provision_omits_base_for_other_bots(setup_cache):
    """La tabla pesa más de un megabyte: no viaja en el sobre de otros bots."""
    setup_cache.write_text("cached")
    apoc_base._cached_text = None
    for bot in (None, "ccma", "mis_comprobantes"):
        seccion = dispatcher.provisioned_section(bot)
        assert "apoc_base_text" not in seccion.get("service", {})


def test_provision_omits_missing_cache(setup_cache):
    assert "apoc_base_text" not in dispatcher.provisioned_section("apoc")["service"]


def test_refresh_loop_reintenta_pronto_si_falta_la_tabla(setup_cache, monkeypatch):
    """Sin caché (AFIP caído al arrancar) reintenta en una hora, no en una semana."""
    dormido: list[float] = []

    async def sin_refresco() -> bool:
        return False

    async def dormir(segundos: float) -> None:
        dormido.append(segundos)
        if len(dormido) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(apoc_base, "refresh_if_needed", sin_refresco)
    monkeypatch.setattr(apoc_base.asyncio, "sleep", dormir)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(apoc_base.refresh_loop())
    assert dormido == [3600, 3600]


def test_refresh_loop_espera_la_semana_con_cache_al_dia(setup_cache, monkeypatch):
    setup_cache.write_text(TABLA)
    dormido: list[float] = []

    async def sin_refresco() -> bool:
        return False

    async def dormir(segundos: float) -> None:
        dormido.append(segundos)
        raise asyncio.CancelledError

    monkeypatch.setattr(apoc_base, "refresh_if_needed", sin_refresco)
    monkeypatch.setattr(apoc_base.asyncio, "sleep", dormir)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(apoc_base.refresh_loop())
    assert dormido == [7 * 86400]


def test_una_sola_clave_de_captcha_alimenta_arca_y_srt(monkeypatch):
    from types import SimpleNamespace

    from central_api.scheduler import dispatcher

    monkeypatch.setattr(
        dispatcher, "get_settings",
        lambda: SimpleNamespace(
            capmonster_api_key="clave-unica", capmonster_arca_key="", capmonster_srt_key="",
            cuit_service_base_url="", cuit_service_masiva_url="",
            cuit_service_usuario="", cuit_service_api_key="",
            proxy_enabled=False, proxy_host="",
        ),
    )
    captcha = dispatcher.provisioned_section("ccma")["captcha"]
    assert captcha["arca_key"] == "clave-unica"
    assert captcha["srt_key"] == "clave-unica"


def test_la_clave_por_portal_tiene_prioridad_si_se_define(monkeypatch):
    from types import SimpleNamespace

    from central_api.scheduler import dispatcher

    monkeypatch.setattr(
        dispatcher, "get_settings",
        lambda: SimpleNamespace(
            capmonster_api_key="comun", capmonster_arca_key="solo-arca", capmonster_srt_key="",
            cuit_service_base_url="", cuit_service_masiva_url="",
            cuit_service_usuario="", cuit_service_api_key="",
            proxy_enabled=False, proxy_host="",
        ),
    )
    captcha = dispatcher.provisioned_section("ccma")["captcha"]
    assert captcha["arca_key"] == "solo-arca" and captcha["srt_key"] == "comun"


def _ajustes(**extra):
    from types import SimpleNamespace

    base = dict(
        capmonster_api_key="", capmonster_arca_key="", capmonster_srt_key="",
        cuit_service_base_url="", cuit_service_masiva_url="",
        cuit_service_usuario="", cuit_service_api_key="",
        proxy_enabled=False, proxy_host="",
        proxy_bots="certificado_mipyme", proxy_bots_host="http://pr.oxylabs.io:7777",
        proxy_bots_username="cliente", proxy_bots_password="secreto", proxy_bots_country="ar",
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_proxy_dedicado_solo_aplica_a_los_bots_listados(monkeypatch):
    from central_api.scheduler import dispatcher

    monkeypatch.setattr(dispatcher, "get_settings", lambda: _ajustes())
    con = dispatcher.provisioned_section("certificado_mipyme")["proxy"]
    assert con["host"] == "http://pr.oxylabs.io:7777"
    assert con["username"] == "customer-cliente-cc-AR"
    assert dispatcher.provisioned_section("ccma")["proxy"] is None


def test_sin_proxy_dedicado_configurado_no_cambia_el_comportamiento(monkeypatch):
    from central_api.scheduler import dispatcher

    monkeypatch.setattr(dispatcher, "get_settings", lambda: _ajustes(proxy_bots_host=""))
    assert dispatcher.provisioned_section("certificado_mipyme")["proxy"] is None


def test_proxy_datacenter_elige_un_puerto_del_rango_y_usa_prefijo_user(monkeypatch):
    from central_api.scheduler import dispatcher

    monkeypatch.setattr(
        dispatcher, "get_settings",
        lambda: _ajustes(
            proxy_bots_host="http://dc.oxylabs.io", proxy_bots_username="cliente",
            proxy_bots_port_min=8001, proxy_bots_port_max=8050,
        ),
    )
    vistos = set()
    for _ in range(40):
        p = dispatcher.provisioned_section("certificado_mipyme")["proxy"]
        assert p["mode"] == "datacenter"
        assert p["username"] == "user-cliente"
        puerto = int(p["host"].rsplit(":", 1)[1])
        assert 8001 <= puerto <= 8050
        assert p["host"].startswith("http://dc.oxylabs.io:")
        vistos.add(puerto)
    assert len(vistos) > 1


# ---------------------------------------------------------------------------
# GET /api/v3/utilidades/apoc/{cuit}: descarga/actualización automática de la
# tabla cuando no hay copia local o quedó vencida (petición del operador).

# La ruta valida el dígito verificador: la tabla de estos casos usa CUITs
# módulo 11 válidos para poder ejercitar la descarga real.
TABLA_UTIL = (
    "# AFIP - Facturas Apocrifas\n"
    "20123456786, 2024-01-01, 2024-12-31\n"
)
CUIT_EN_TABLA = "20123456786"
CUIT_AUSENTE = "20222222223"


def _apoc_test_client(monkeypatch, path, *, provisioned=None):
    from fastapi.testclient import TestClient

    from central_api.main import create_app

    settings = SimpleNamespace(
        apoc_base_url="https://fake.invalid/data",
        apoc_base_path=str(path),
        apoc_refresh_interval_days=7,
    )
    monkeypatch.setattr(apoc_base, "get_settings", lambda: settings)
    monkeypatch.setattr(apoc_base, "_cached_text", None)
    monkeypatch.setattr(
        apoc_base, "_provisioned_paths", lambda: list(provisioned or [])
    )
    return TestClient(create_app())


def _descarga_en(path, descargas):
    async def descargar():
        descargas.append("download")
        path.write_text(TABLA_UTIL, encoding="utf-8")
        apoc_base._cached_text = TABLA_UTIL
        return TABLA_UTIL

    return descargar


def test_utilidad_apoc_descarga_si_falta(tmp_path, monkeypatch):
    path = tmp_path / "apoc.txt"
    client = _apoc_test_client(monkeypatch, path)
    descargas: list[str] = []
    monkeypatch.setattr(apoc_base, "download_and_cache", _descarga_en(path, descargas))

    encontrado = client.get(f"/api/v3/utilidades/apoc/{CUIT_EN_TABLA}")
    assert encontrado.status_code == 200
    assert encontrado.json() == {
        "cuit": CUIT_EN_TABLA,
        "fecha_condicion": "2024-01-01",
        "fecha_publicacion": "2024-12-31",
        "apoc": True,
    }
    ausente = client.get(f"/api/v3/utilidades/apoc/{CUIT_AUSENTE}")
    assert ausente.status_code == 200
    assert ausente.json()["apoc"] is False
    assert descargas == ["download"]


def test_utilidad_apoc_actualiza_si_esta_vencida(tmp_path, monkeypatch):
    path = tmp_path / "apoc.txt"
    path.write_text("# AFIP\n", encoding="utf-8")
    viejo = time.time() - 8 * 86400
    os.utime(path, (viejo, viejo))
    client = _apoc_test_client(monkeypatch, path)
    descargas: list[str] = []
    monkeypatch.setattr(apoc_base, "download_and_cache", _descarga_en(path, descargas))

    assert client.get(f"/api/v3/utilidades/apoc/{CUIT_EN_TABLA}").json()["apoc"] is True
    assert descargas == ["download"]


def test_utilidad_apoc_no_descarga_si_esta_al_dia(tmp_path, monkeypatch):
    path = tmp_path / "apoc.txt"
    path.write_text(TABLA_UTIL, encoding="utf-8")
    client = _apoc_test_client(monkeypatch, path)

    async def fallar():
        pytest.fail("no debe descargar una caché al día")

    monkeypatch.setattr(apoc_base, "download_and_cache", fallar)
    assert client.get(f"/api/v3/utilidades/apoc/{CUIT_EN_TABLA}").json()["apoc"] is True


def test_utilidad_apoc_usa_copia_provisionada_si_no_puede_descargar(tmp_path, monkeypatch):
    provisionada = tmp_path / "provisionada.txt"
    provisionada.write_text(TABLA_UTIL, encoding="utf-8")
    client = _apoc_test_client(monkeypatch, tmp_path / "apoc.txt", provisioned=[provisionada])

    async def fallar():
        raise ValueError("AFIP no responde")

    monkeypatch.setattr(apoc_base, "download_and_cache", fallar)
    assert client.get(f"/api/v3/utilidades/apoc/{CUIT_EN_TABLA}").json()["apoc"] is True


def test_utilidad_apoc_503_sin_base_ni_copia_local(tmp_path, monkeypatch):
    client = _apoc_test_client(monkeypatch, tmp_path / "apoc.txt")

    async def fallar():
        raise ValueError("AFIP no responde")

    monkeypatch.setattr(apoc_base, "download_and_cache", fallar)
    respuesta = client.get(f"/api/v3/utilidades/apoc/{CUIT_EN_TABLA}")
    assert respuesta.status_code == 503
    assert "apócrifos" in respuesta.json()["detail"]
