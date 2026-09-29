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


def archive(member="FacturasApocrifas.txt", text="public-table"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(member, "\ufeff" + text)
    return out.getvalue()


@pytest.fixture
def setup_cache(tmp_path, monkeypatch):
    path = tmp_path / "apoc.txt"
    settings = SimpleNamespace(apoc_base_url="https://fake.invalid/data", apoc_base_path=str(path), apoc_refresh_interval_days=7,
                               proxy_enabled=False, proxy_host="", proxy_mode="standard", proxy_username="", proxy_password="", proxy_country="ar",
                               capmonster_arca_key="", capmonster_srt_key="", cuit_service_base_url="", cuit_service_masiva_url="", cuit_service_usuario="", cuit_service_api_key="")
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

    assert asyncio.run(correr()) == "public-table"
    assert setup_cache.read_text() == "public-table"
    assert apoc_base.read_cached_text() == "public-table"


def test_refresh_when_old(setup_cache, monkeypatch):
    setup_cache.write_text("old")
    viejo = time.time() - 8 * 86400
    os.utime(setup_cache, (viejo, viejo))

    async def correr():
        client = await client_for(archive(text="new"))
        original = apoc_base.download_and_cache

        async def descargar():
            return await original(client)

        monkeypatch.setattr(apoc_base, "download_and_cache", descargar)
        return await apoc_base.refresh_if_needed()

    assert asyncio.run(correr()) is True
    assert setup_cache.read_text() == "new"


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


def test_zip_without_expected_member_fails_cleanly():
    with pytest.raises(ValueError, match="missing expected member"):
        apoc_base._extract(archive("other.txt"))


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
