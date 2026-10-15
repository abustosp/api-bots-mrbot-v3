"""
Investigación de las APIs internas de Xubio Sueldos (modo rápido).

Este script:
1. Hace el login Visma Connect (igual que el bot) una sola vez.
2. Por cada contribuyente:
   a. Abre la vista `afip-libro-sueldos-digital` (o `reporte-sueldos`) del NXV.
   b. Aborta la request del iframe (app.xubio.com) y captura el `?token=` y
      `?tenant=` / `?empresaID=` de la URL -> Bearer token para payroll.
   c. Llama las 4 APIs de payroll vía `context.request` (comparte cookies):
      - TXT liquidaciones: `afip-libro-sueldos-digital-exportartxt-liquidacionesperiodo`
      - TXT conceptos:    `afip-libro-sueldos-digital-exportartxt-conceptosafip`
      - Reporte:          `reporte-sueldos`
      - Control:          `reporte-sueldos/control-liquidacion`
   d. Guarda las respuestas JSON crudas en ./temp/xubio_sueldos_api/ para
      análisis posterior.

No genera los Excel: solo valida que las APIs respondan con las mismas
estructuras que el navegador usa (comparar contra los HAR de correrías previas).

USO (desde la carpeta repo, con .env):
    ./.venv/bin/python xubio_sueldos_api_probe.py

Variables de entorno: mismas que el bot (XUBIO_SUELDOS_*).
"""

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from shared.arca_login import launch_arca_browser_context
from shared.proxy import get_proxy_config


BOT_NAME = "xubio_sueldos_api"
OUT_DIR = Path(f"./temp/{BOT_NAME}")
OUT_DIR.mkdir(parents=True, exist_ok=True)

XUBIO_VISMA_LOGIN_URL = "https://xubio.com/NXV/vismaConnect/login"
VISMA_LOGIN_HOST = "connect.visma.com"

# Vistas NXV que contienen el iframe de app.xubio.com
XUBIO_LIBRO_URL = "https://xubio.com/NXV/sueldos/informes/afip-libro-sueldos-digital"
XUBIO_REPORTE_URL = "https://xubio.com/NXV/sueldos/informes/reporte-sueldos"

PAYROLL_BASE = "https://payroll.xubio.com/api"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si"}


def _format_periodo_xubio(periodo: str) -> str:
    clean = re.sub(r"\D", "", periodo or "")
    if len(clean) == 6:
        return f"{clean[:4]}-{clean[4:]}"
    if len(clean) == 4:
        return clean
    return (periodo or "").strip()


def _contribuyentes_from_env() -> list[str]:
    raw_lista = os.getenv("XUBIO_SUELDOS_CONTRIBUYENTES", "").strip()
    if raw_lista:
        return [c.strip() for c in re.split(r"[|;,]", raw_lista) if c.strip()]
    single = os.getenv("XUBIO_SUELDOS_CONTRIBUYENTE", "").strip()
    lista = [c.strip() for c in re.split(r"[|;,]", single) if c.strip()]
    return lista or [""]


def _periodo_timestamp(periodo_xubio: str) -> str:
    """2026-07 -> 2026-07-01T00:00:00.000000-03:00 (según HAR)."""
    return f"{periodo_xubio}-01T00:00:00.000000-03:00"


def is_visma_login_page(page: Page) -> bool:
    return VISMA_LOGIN_HOST in (page.url or "")


async def is_xubio_login_page(page: Page) -> bool:
    try:
        return "xubio.com" in (page.url or "") and await page.locator("#userName").count() > 0
    except Exception:
        return False


async def _wait_back_to_xubio(page: Page, timeout_s: int = 40) -> None:
    for _ in range(timeout_s):
        if not is_visma_login_page(page):
            return
        await page.wait_for_timeout(1_000)
    raise RuntimeError("Timeout esperando la sesión de Xubio tras el login Visma")


async def _handle_active_session(page: Page) -> bool:
    button = page.get_by_role("button", name="Continuar inicio")
    try:
        if await button.count() == 0:
            return False
        await button.first.wait_for(state="visible", timeout=4_000)
    except Exception:
        return False
    print("  [i] Pantalla 'sesión activa' -> Continuar inicio")
    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await button.click()
    except Exception:
        pass
    await page.wait_for_timeout(2_000)
    return True


async def login_visma(page: Page, usuario: str, clave: str) -> None:
    await page.goto(XUBIO_VISMA_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_500)
    if not is_visma_login_page(page):
        return
    try:
        await page.get_by_role("button", name="Rechazarlas todas").click(timeout=5_000)
    except Exception:
        pass
    await page.get_by_role("textbox", name="Email").fill(usuario)
    await page.get_by_role("button", name="Continuar").click()
    await page.wait_for_timeout(1_000)
    await page.get_by_role("textbox", name="Contraseña").fill(clave)
    await page.get_by_role("button", name="Iniciar sesión").click()
    await _wait_back_to_xubio(page)
    await page.wait_for_timeout(1_000)
    await _handle_active_session(page)
    await page.wait_for_timeout(1_000)
    if is_visma_login_page(page):
        raise RuntimeError("No se pudo iniciar sesión en Xubio (Visma Connect)")


async def _leer_empresa_activa(page: Page) -> str:
    try:
        loc = page.locator("#resaltarCompania")
        if await loc.count() > 0:
            return (await loc.first.inner_text()).strip()
    except Exception:
        pass
    return ""


async def _normalize(nombre: str) -> str:
    return re.sub(r"\s+", " ", (nombre or "").strip()).upper()


async def seleccionar_contribuyente(page: Page, contribuyente: str) -> str:
    """Cambia de empresa (si hace falta) y devuelve el nombre efectivo."""
    await page.goto("https://xubio.com/NXV/home", wait_until="domcontentloaded")
    await page.wait_for_timeout(1_500)
    await _handle_active_session(page)

    empresa_activa = await _leer_empresa_activa(page)
    objetivo = await _normalize(contribuyente) if contribuyente else ""
    if objetivo and empresa_activa and await _normalize(empresa_activa) == objetivo:
        return await _normalize(empresa_activa)
    if not objetivo:
        return await _normalize(empresa_activa)

    compania = page.locator("li.compania")
    try:
        if await compania.count():
            await compania.first.hover()
    except Exception:
        try:
            await compania.first.click(timeout=3_000)
        except Exception:
            pass
    await page.wait_for_timeout(800)

    buscador = page.locator("#buscador-empresa")
    try:
        if await buscador.count():
            await buscador.fill(objetivo)
            await page.wait_for_timeout(800)
    except Exception:
        pass

    link = page.locator("li.licomp a").filter(has_text=objetivo).first
    try:
        if await link.count():
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                await link.click()
            await page.wait_for_timeout(1_000)
            return objetivo
    except Exception:
        pass
    raise RuntimeError(f"No se encuentra el contribuyente {contribuyente}")


async def capturar_token(page: Page, url_nxv: str, timeout: int = 25_000) -> dict[str, str]:
    """Abre la vista NXV y captura el token del iframe abortando la request de app.xubio.com.

    Devuelve {token, tenant, empresaID}. El iframe NO se carga (se aborta).
    """
    capturado: dict[str, str] = {}

    async def on_request(route):
        req = route.request
        if "app.xubio.com" in req.url and "token=" in req.url:
            qs = parse_qs(urlparse(req.url).query)
            capturado["token"] = qs.get("token", [""])[0]
            capturado["tenant"] = qs.get("tenant", [""])[0]
            capturado["empresaID"] = qs.get("empresaID", [""])[0]
            await route.abort()
            return
        try:
            await route.continue_()
        except Exception:
            pass

    await page.route("**/*", on_request)
    try:
        await page.goto(url_nxv, wait_until="domcontentloaded", timeout=timeout)
    except Exception:
        pass
    await page.wait_for_timeout(3_000)

    deadline = time.monotonic() + timeout / 1000
    while not capturado.get("token") and time.monotonic() < deadline:
        await page.wait_for_timeout(500)

    await page.unroute("**/*", on_request)
    if not capturado.get("token"):
        raise RuntimeError(f"No se capturó token de iframe para {url_nxv}")
    return capturado


async def llamar_api(
    context: BrowserContext,
    token: str,
    url: str,
) -> dict:
    """GET a payroll con Bearer token vía APIRequestContext (comparte cookies de sesión)."""
    resp = await context.request.get(
        url,
        headers={
            "authorization": f"Bearer {token}",
            "token": token,
            "origin": "https://app.xubio.com",
            "referer": "https://app.xubio.com/",
            "accept": "application/json, text/plain, */*",
        },
    )
    return {
        "status": resp.status,
        "url": url,
        "headers": resp.headers,
        "body": await resp.text(),
    }


async def probe_contribuyente(
    page: Page,
    context: BrowserContext,
    contribuyente: str,
    periodo_xubio: str,
    nro_liquidacion: str,
) -> dict:
    print(f"\n=== Contribuyente: {contribuyente or '(empresa activa)'} ===")

    empresa = await seleccionar_contribuyente(page, contribuyente)
    print(f"  empresa_efectiva: {empresa}")

    tok = await capturar_token(page, XUBIO_LIBRO_URL)
    token = tok["token"]
    print(f"  token: {token[:20]}... tenant={tok.get('tenant')} empresaID={tok.get('empresaID')}")

    ts = _periodo_timestamp(periodo_xubio)
    nro = f"{int(nro_liquidacion):02d}"

    endpoints = {
        "liquidaciones": (
            f"{PAYROLL_BASE}/informe/informes/afip-libro-sueldos-digital-exportartxt-liquidacionesperiodo"
            f"?tipoliquidacion=MENSUAL&periodomensual={ts}&numeroliquidacion={nro}"
        ),
        "conceptos": (
            f"{PAYROLL_BASE}/informe/informes/afip-libro-sueldos-digital-exportartxt-conceptosafip"
        ),
        "reporte": (
            f"{PAYROLL_BASE}/informe/informes/reporte-sueldos"
            f"?periododesde={ts}&periodohasta={ts}&liquidaciontipo=&convenio=&obrasocial=&legajo="
        ),
        "control": (
            f"{PAYROLL_BASE}/informe/informes/reporte-sueldos/control-liquidacion"
            f"?periododesde={ts}&periodohasta={ts}&liquidaciontipo=&convenio=&obrasocial=&legajo="
        ),
        "check_token": f"{PAYROLL_BASE}/auth/check-token",
    }

    resultados: dict[str, dict] = {}
    for nombre, url in endpoints.items():
        try:
            res = await llamar_api(context, token, url)
            resultados[nombre] = res
            preview = res["body"][:120].replace("\n", " ")
            print(f"  [{nombre}] status={res['status']} len={len(res['body'])} body={preview}")
        except Exception as exc:
            resultados[nombre] = {"status": "ERROR", "error": str(exc)}
            print(f"  [{nombre}] ERROR: {exc}")

    # Guardar crudos para análisis
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", empresa or "SIN_EMPRESA")
    info = {
        "empresa": empresa,
        "token": token,
        "tenant": tok.get("tenant"),
        "empresaID": tok.get("empresaID"),
        "periodo": periodo_xubio,
        "nro_liquidacion": nro,
        "resultados": {
            k: ({"status": v["status"], "len": len(v["body"]), "body": v["body"]} if v.get("status") != "ERROR" else v)
            for k, v in resultados.items()
        },
    }
    (OUT_DIR / f"{safe}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultados


async def run(playwright: Playwright) -> int:
    usuario = os.getenv("XUBIO_SUELDOS_USUARIO", "").strip()
    clave = os.getenv("XUBIO_SUELDOS_CLAVE", "").strip()
    periodo = os.getenv("XUBIO_SUELDOS_PERIODO", "").strip()
    nro = os.getenv("XUBIO_SUELDOS_NRO_LIQUIDACION", "0").strip() or "0"
    contribuyentes = _contribuyentes_from_env()
    headless = _env_flag("XUBIO_SUELDOS_HEADLESS", default=False)
    usar_proxy = _env_flag("XUBIO_SUELDOS_PROXY", default=False)

    if not usuario or not clave:
        print("Faltan XUBIO_SUELDOS_USUARIO/CLAVE en el entorno")
        return 2

    periodo_xubio = _format_periodo_xubio(periodo)
    print(f"Periodo: {periodo_xubio} | Nro liquidación: {nro} | Headless: {headless}")
    print(f"Contribuyentes: {contribuyentes}")

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    try:
        proxy = get_proxy_config() if usar_proxy else None
        browser, context, page = await launch_arca_browser_context(
            playwright,
            headless=headless,
            proxy=proxy,
            context_kwargs={"accept_downloads": True},
        )
        page.set_default_timeout(20_000)

        print("\n[login] Visma Connect...")
        await login_visma(page, usuario, clave)
        print("[login] OK")

        for i, contribuyente in enumerate(contribuyentes, start=1):
            try:
                await probe_contribuyente(page, context, contribuyente, periodo_xubio, nro)
            except Exception as exc:
                print(f"  ERROR en contribuyente {contribuyente}: {exc}")
                print(type(exc).__name__, exc)

        return 0
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        print(f"\nResultados crudos en {OUT_DIR}/")


async def main() -> int:
    async with async_playwright() as playwright:
        return await run(playwright)


if __name__ == "__main__":
    try:
        load_dotenv()
    except Exception:
        pass
    code = asyncio.run(main())
    sys.exit(code)