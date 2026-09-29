"""Cached public AFIP apoc table, refreshed asynchronously."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import zipfile
from pathlib import Path

import httpx

from central_api.settings import get_settings

log = logging.getLogger(__name__)
_MAX_DOWNLOAD_BYTES = 2_000_000
_MAX_TEXT_BYTES = 5_000_000
_MEMBER = "FacturasApocrifas.txt"
_cached_text: str | None = None


def read_cached_text() -> str | None:
    """Read cache into process memory once; missing/unreadable cache is absent."""
    global _cached_text
    if _cached_text is None:
        try:
            _cached_text = Path(get_settings().apoc_base_path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return None
    return _cached_text


def _extract(data: bytes) -> str:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("AFIP apoc response is not a ZIP")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            info = archive.getinfo(_MEMBER)
        except KeyError as exc:
            raise ValueError("AFIP apoc ZIP is missing expected member") from exc
        if info.file_size > _MAX_TEXT_BYTES:
            raise ValueError("AFIP apoc text exceeds size limit")
        raw = archive.read(info)
    text = raw.decode("utf-8-sig")
    if not text:
        raise ValueError("AFIP apoc text is empty")
    return text


async def download_and_cache(client: httpx.AsyncClient | None = None) -> str:
    """Download validated ZIP and atomically replace the configured text cache."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    try:
        async with client.stream("GET", get_settings().apoc_base_url) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_DOWNLOAD_BYTES:
                    raise ValueError("AFIP apoc ZIP exceeds size limit")
                chunks.append(chunk)
        text = _extract(b"".join(chunks))
        path = Path(get_settings().apoc_base_path)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        await asyncio.to_thread(temporary.write_text, text, encoding="utf-8")
        await asyncio.to_thread(os.replace, temporary, path)
        global _cached_text
        _cached_text = text
        return text
    finally:
        if own_client:
            await client.aclose()


async def refresh_if_needed() -> bool:
    """Refresh absent/stale cache; failures preserve and continue serving old data."""
    path = Path(get_settings().apoc_base_path)
    interval = max(1, get_settings().apoc_refresh_interval_days)
    try:
        fresh = path.is_file() and (time.time() - path.stat().st_mtime) < interval * 86400
    except OSError:
        fresh = False
    if fresh:
        read_cached_text()
        return False
    try:
        await download_and_cache()
        log.info("AFIP apoc cache refreshed")
        return True
    except Exception as exc:  # noqa: BLE001 - retain stale cache and keep service running
        read_cached_text()
        log.warning("AFIP apoc refresh failed (%s); retaining existing cache if available", type(exc).__name__)
        return False


async def refresh_loop() -> None:
    """Initial refresh then periodic checks without blocking job dispatch."""
    await refresh_if_needed()
    while True:
        await asyncio.sleep(max(1, get_settings().apoc_refresh_interval_days) * 86400)
        await refresh_if_needed()
