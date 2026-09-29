from __future__ import annotations

import io
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


@pytest.mark.asyncio
async def test_download_when_missing(setup_cache, monkeypatch):
    client = await client_for(archive())
    assert await apoc_base.download_and_cache(client) == "public-table"
    assert setup_cache.read_text() == "public-table"
    assert apoc_base.read_cached_text() == "public-table"


@pytest.mark.asyncio
async def test_refresh_when_old(setup_cache, monkeypatch):
    setup_cache.write_text("old")
    old = time.time() - 8 * 86400
    import os
    os.utime(setup_cache, (old, old))
    client = await client_for(archive(text="new"))
    original_download = apoc_base.download_and_cache
    async def download():
        return await original_download(client)
    monkeypatch.setattr(apoc_base, "download_and_cache", download)
    assert await apoc_base.refresh_if_needed() is True
    assert setup_cache.read_text() == "new"


@pytest.mark.asyncio
async def test_does_not_refresh_fresh(setup_cache, monkeypatch):
    setup_cache.write_text("fresh")
    monkeypatch.setattr(apoc_base, "download_and_cache", lambda: pytest.fail("unexpected download"))
    assert await apoc_base.refresh_if_needed() is False


@pytest.mark.asyncio
async def test_network_failure_preserves_stale_cache(setup_cache, monkeypatch):
    setup_cache.write_text("stale")
    old = time.time() - 8 * 86400
    import os
    os.utime(setup_cache, (old, old))
    def handler(request):
        raise httpx.ConnectError("offline", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    original_download = apoc_base.download_and_cache
    async def download():
        return await original_download(client)
    monkeypatch.setattr(apoc_base, "download_and_cache", download)
    assert await apoc_base.refresh_if_needed() is False
    assert apoc_base.read_cached_text() == "stale"


def test_zip_without_expected_member_fails_cleanly():
    with pytest.raises(ValueError, match="missing expected member"):
        apoc_base._extract(archive("other.txt"))


def test_provision_includes_cached_text(setup_cache, monkeypatch):
    setup_cache.write_text("cached")
    apoc_base._cached_text = None
    assert dispatcher.provisioned_section()["apoc_base_text"] == "cached"


def test_provision_omits_missing_cache(setup_cache):
    assert "apoc_base_text" not in dispatcher.provisioned_section()
