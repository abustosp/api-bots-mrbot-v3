"""
Modo rápido de Xubio Sueldos: descarga vía APIs internas de payroll.

En vez de conducir la UI Angular del iframe (modales, filtros, descargas y
esperas), este módulo:

1. Captura el token del iframe `app.xubio.com` abortando su request al abrir
   la vista NXV (el token se genera server-side y viaja en la URL del iframe).
2. Hace GET directos a `payroll.xubio.com/api/...` con
   `Authorization: Bearer <token>` usando las cookies de sesión del contexto
   (`page.context.request`).
3. Genera los 4 archivos localmente replicando la estructura que arma Xubio:
   - TXT liquidaciones / TXT conceptos (texto que devuelve la API).
   - Excel reporte / Excel control (el frontend arma estos xlsx client-side;
     aquí se recrean con openpyxl usando la misma estructura).

Este módulo es AUTOCONTENIDO: incluye el login Visma Connect y el cambio de
contribuyente por UI, y su propia config con prefijo `XUBIO_RAPIDO_*`
(fallback a `XUBIO_SUELDOS_*`). No importa nada de los bots antiguos.

Bots que lo usan (totalmente separados de los originales):
- xubio_sueldos_rapido_bot_logger.py (con evidencias en ./temp/)
- xubio_sueldos_rapido_bot.py (versión limpia, descargas en ./descargas/)

Validado en vivo (2026-08-16): 2 empresas ~35s vs ~2m45s del modo UI.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from shared.arca_login import launch_arca_browser_context
from shared.proxy import get_proxy_config

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
except ImportError:  # pragma: no cover
    Workbook = None
    Alignment = Border = Font = PatternFill = Side = None


PAYROLL_BASE = "https://payroll.xubio.com/api"
XUBIO_HOME_URL = "https://xubio.com/NXV/home"
XUBIO_VISMA_LOGIN_URL = "https://xubio.com/NXV/vismaConnect/login"
XUBIO_LIBRO_URL = "https://xubio.com/NXV/sueldos/informes/afip-libro-sueldos-digital"
XUBIO_REPORTE_URL = "https://xubio.com/NXV/sueldos/informes/reporte-sueldos"
XUBIO_SBA_MIEMPRESA = "https://xubio.com/sba/api/miempresa/datos/ar"
VISMA_LOGIN_HOST = "connect.visma.com"

MESES_LARGOS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MESES_CORTOS = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]

INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')


def _format_periodo_xubio(periodo: str) -> str:
    """202607 -> 2026-07 ; 2026-07 -> 2026-07."""
    clean = re.sub(r"\D", "", periodo or "")
    if len(clean) == 6:
        return f"{clean[:4]}-{clean[4:]}"
    return (periodo or "").strip()


def _periodo_timestamp(periodo_xubio: str) -> str:
    """2026-07 -> 2026-07-01T00:00:00.000000-03:00 (formato de las APIs)."""
    return f"{periodo_xubio}-01T00:00:00.000000-03:00"


def _mes_nombre_anio(periodo_xubio: str) -> str:
    """2026-07 -> 'julio 2026' (formato del Excel de reporte)."""
    m = re.match(r"(\d{4})-(\d{2})", periodo_xubio or "")
    if not m:
        return (periodo_xubio or "").strip()
    return f"{MESES_LARGOS[int(m.group(2)) - 1]} {m.group(1)}"


def _periodo_corto(iso_periodo: str) -> str:
    """2026-07-01T03:00:00Z -> 'jul-26' (formato de la col Período del control)."""
    m = re.match(r"(\d{4})-(\d{2})", iso_periodo or "")
    if not m:
        return (iso_periodo or "").strip()
    return f"{MESES_CORTOS[int(m.group(2)) - 1]}-{str(int(m.group(1)))[2:]}"


def _normalize_empresa(nombre: str) -> str:
    return re.sub(r"\s+", " ", (nombre or "").strip()).upper()


def _sanitize_empresa(nombre: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", _normalize_empresa(nombre))
    return re.sub(r"\s+", "_", cleaned).strip("_")


def _build_filename(base_dir: Path, tipo: str, empresa: str, periodo: str, ext: str) -> Path:
    empresa_value = _sanitize_empresa(empresa) or "SIN_EMPRESA"
    periodo_value = re.sub(r"\D", "", periodo or "") or "SIN_PERIODO"
    return base_dir / f"{'_'.join([tipo, periodo_value, empresa_value])}{ext}"


# ---------------------------------------------------------------------------
# Config (prefijo XUBIO_RAPIDO_* con fallback a XUBIO_SUELDOS_*)
# ---------------------------------------------------------------------------

def _env(name: str, fallback_name: str = "", default: str = "") -> str:
    value = os.getenv(name)
    if value is None and fallback_name:
        value = os.getenv(fallback_name)
    if value is None:
        return default
    return value.strip()


def _env_flag(name: str, fallback_name: str = "", default: bool = False) -> bool:
    value = _env(name, fallback_name, "")
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "si"}


@dataclass
class RapiConfig:
    usuario: str
    clave: str
    contribuyentes: list[str] = field(default_factory=list)
    periodo: str = ""
    nro_liquidacion: int = 0
    headless: bool = True
    proxy: bool = False


def _nro_liquidacion_from_env() -> int:
    raw = _env("XUBIO_RAPIDO_NRO_LIQUIDACION", "XUBIO_SUELDOS_NRO_LIQUIDACION", "0")
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("XUBIO_RAPIDO_NRO_LIQUIDACION debe ser un entero entre 0 y 10")
    if not 0 <= value <= 10:
        raise ValueError("XUBIO_RAPIDO_NRO_LIQUIDACION debe estar entre 0 y 10")
    return value


def _contribuyentes_from_env() -> list[str]:
    raw_lista = _env("XUBIO_RAPIDO_CONTRIBUYENTES", "XUBIO_SUELDOS_CONTRIBUYENTES", "")
    if raw_lista:
        return [c.strip() for c in re.split(r"[|;,]", raw_lista) if c.strip()]
    single = _env("XUBIO_RAPIDO_CONTRIBUYENTE", "XUBIO_SUELDOS_CONTRIBUYENTE", "")
    if single:
        return [c.strip() for c in re.split(r"[|;,]", single) if c.strip()]
    return [""]


def load_rapi_config() -> RapiConfig:
    """Config del modo rápido: usa XUBIO_RAPIDO_* y cae en XUBIO_SUELDOS_* si faltan."""
    return RapiConfig(
        usuario=_env("XUBIO_RAPIDO_USUARIO", "XUBIO_SUELDOS_USUARIO"),
        clave=_env("XUBIO_RAPIDO_CLAVE", "XUBIO_SUELDOS_CLAVE"),
        contribuyentes=_contribuyentes_from_env(),
        periodo=_env("XUBIO_RAPIDO_PERIODO", "XUBIO_SUELDOS_PERIODO"),
        nro_liquidacion=_nro_liquidacion_from_env(),
        headless=_env_flag("XUBIO_RAPIDO_HEADLESS", "XUBIO_SUELDOS_HEADLESS", default=True),
        proxy=_env_flag("XUBIO_RAPIDO_PROXY", "XUBIO_SUELDOS_PROXY", default=False),
    )


# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------

async def _is_visible(locator: Locator, timeout: int = 3_000) -> bool:
    try:
        if await locator.count() == 0:
            return False
        await locator.first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        try:
            return await locator.first.is_visible()
        except Exception:
            return False


async def _try_click(locator: Locator, timeout: int = 10_000) -> bool:
    try:
        await locator.first.click(timeout=timeout)
        return True
    except Exception:
        return False


def _is_visma_login_page(page: Page) -> bool:
    return VISMA_LOGIN_HOST in (page.url or "")


async def _is_xubio_login_page(page: Page) -> bool:
    if "xubio.com" not in (page.url or ""):
        return False
    try:
        return await page.locator("#userName").count() > 0
    except Exception:
        return False


async def _ensure_xubio_session(page: Page) -> None:
    if _is_visma_login_page(page):
        raise RuntimeError("La sesión de Xubio expiró, se requiere volver a iniciar sesión")
    if await _is_xubio_login_page(page):
        raise RuntimeError("La sesión de Xubio expiró (login clásico detectado)")


async def _wait_back_to_xubio(page: Page, timeout_s: int = 40) -> None:
    for _ in range(timeout_s):
        if not _is_visma_login_page(page):
            return
        await page.wait_for_timeout(1_000)
    raise RuntimeError("Timeout esperando la sesión de Xubio tras el login Visma")


async def _handle_active_session(page: Page) -> bool:
    """Xubio muestra 'Tu cuenta ya tiene una sesión activa' -> click en Continuar inicio."""
    button = page.get_by_role("button", name="Continuar inicio")
    if not await _is_visible(button, timeout=4_000):
        return False
    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await button.click()
    except Exception:
        pass
    await page.wait_for_timeout(2_000)
    return True


async def login_visma(page: Page, config: RapiConfig, logger=None) -> None:
    """Entra por el flujo OAuth de Xubio hacia Visma Connect y completa el login."""
    if logger:
        logger("visma_login_start url=%s", page.url)
    await page.goto(XUBIO_VISMA_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_500)

    if not _is_visma_login_page(page):
        if await _is_xubio_login_page(page):
            raise RuntimeError("Xubio requiere login clásico (no Visma Connect)")
        await _ensure_xubio_session(page)
        return

    await _try_click(page.get_by_role("button", name="Rechazarlas todas"), timeout=5_000)

    email_box = page.get_by_role("textbox", name="Email")
    await email_box.fill(config.usuario)
    await page.get_by_role("button", name="Continuar").click()
    await page.wait_for_timeout(1_000)

    password_box = page.get_by_role("textbox", name="Contraseña")
    await password_box.fill(config.clave)
    await page.get_by_role("button", name="Iniciar sesión").click()

    await _wait_back_to_xubio(page)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1_000)

    await _handle_active_session(page)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1_000)

    if _is_visma_login_page(page):
        raise RuntimeError("No se pudo iniciar sesión en Xubio (Visma Connect)")
    if await _is_xubio_login_page(page):
        raise RuntimeError("El login Visma no completó la sesión en Xubio")
    if logger:
        logger("visma_login_ok url=%s", page.url)


async def _leer_empresa_activa(page: Page) -> str:
    try:
        loc = page.locator("#resaltarCompania")
        if await loc.count() > 0:
            text = (await loc.first.inner_text()).strip()
            if text:
                return text
    except Exception:
        pass
    try:
        loc = page.locator("li.compania a span").last
        if await loc.count() > 0:
            text = (await loc.inner_text()).strip()
            if text:
                return text
    except Exception:
        pass
    return ""


async def seleccionar_contribuyente(page: Page, nombre: str, logger=None) -> str:
    """Selecciona la empresa (si difiere de la activa) y devuelve el nombre efectivo."""
    await page.goto(XUBIO_HOME_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(1_500)
    await _handle_active_session(page)
    await _ensure_xubio_session(page)

    empresa_activa = await _leer_empresa_activa(page)
    if logger:
        logger("empresa_activa=%s", empresa_activa)

    objetivo = _normalize_empresa(nombre) if nombre else ""
    if objetivo and empresa_activa and _normalize_empresa(empresa_activa) == objetivo:
        if logger:
            logger("contribuyente_ya_activo empresa=%s", empresa_activa)
        return _normalize_empresa(empresa_activa)

    if not objetivo:
        if logger:
            logger("contribuyente_sin_variable usa_activa=%s", empresa_activa)
        return _normalize_empresa(empresa_activa)

    compania = page.locator("li.compania")
    if await _is_visible(compania, timeout=3_000):
        try:
            await compania.first.hover()
        except Exception:
            await _try_click(compania.first, timeout=3_000)
        await page.wait_for_timeout(800)

    buscador = page.locator("#buscador-empresa")
    if await _is_visible(buscador, timeout=3_000):
        await buscador.fill(objetivo)
        await page.wait_for_timeout(800)

    link = page.locator("li.licomp a").filter(has_text=objetivo).first
    if await _is_visible(link, timeout=3_000):
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await link.click()
        await page.wait_for_timeout(1_000)
        if logger:
            logger("contribuyente_seleccionado empresa=%s url=%s", objetivo, page.url)
        return objetivo

    raise RuntimeError(f"No se encuentra el contribuyente {nombre}")


# ---------------------------------------------------------------------------
# Token del iframe
# ---------------------------------------------------------------------------

async def capturar_token(page, url_nxv: str, logger=None, timeout_ms: int = 25_000) -> dict:
    """Abre la vista NXV, captura el ?token= del iframe y aborta su carga.

    Devuelve {"token": ..., "url": ...}. No se carga la UI Angular.
    """
    capturado: dict[str, str] = {}

    async def on_request(route):
        req = route.request
        try:
            if "app.xubio.com" in req.url and "token=" in req.url:
                qs = parse_qs(urlparse(req.url).query)
                token = (qs.get("token") or [""])[0].strip()
                if token:
                    capturado["token"] = token
                    capturado["url"] = req.url
                    if logger:
                        logger("token_iframe_capturado url=%s", req.url)
                await route.abort()
                return
            await route.continue_()
        except Exception:
            pass

    await page.route("**/*", on_request)
    try:
        await page.goto(url_nxv, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

    deadline = time.monotonic() + timeout_ms / 1000
    while not capturado.get("token") and time.monotonic() < deadline:
        await page.wait_for_timeout(400)

    try:
        await page.unroute("**/*", on_request)
    except Exception:
        pass

    if not capturado.get("token"):
        raise RuntimeError("No se capturó el token del iframe de Xubio")
    return capturado


# ---------------------------------------------------------------------------
# Llamadas a las APIs de payroll
# ---------------------------------------------------------------------------

async def _api_get(context, token: str, url: str, logger=None) -> str:
    resp = await context.request.get(
        url,
        headers={
            "authorization": f"Bearer {token}",
            "token": token,
            "origin": "https://app.xubio.com",
            "referer": "https://app.xubio.com/",
            "accept": "application/json, text/plain, */*",
            "accept-language": "es-AR",
        },
    )
    body = await resp.text()
    if logger:
        logger("api status=%s len=%s url=%s", resp.status, len(body), url)
    if resp.status != 200:
        raise RuntimeError(f"API {url} -> HTTP {resp.status}: {body[:300]}")
    return body


async def _obtener_token_micro(context, token: str, logger=None, reintentos: int = 4) -> str:
    """El token para xubio.com/sba (miempresa) es el tokenmicroservicios de check-token.

    check-token puede devolver 423 (tenant actualizándose): reintenta.
    """
    url = f"{PAYROLL_BASE}/auth/check-token"
    for intento in range(1, reintentos + 1):
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
        if resp.status == 200:
            try:
                return json.loads(await resp.text()).get("tokenmicroservicios", "")
            except Exception:
                return ""
        if intento < reintentos:
            await asyncio.sleep(2 * intento)
    if logger:
        logger("check-token falló status=%s (se usa token de iframe para miempresa)", resp.status)
    return ""


def _urls_periodo(periodo_xubio: str, nro_liquidacion: str) -> dict[str, str]:
    ts = _periodo_timestamp(periodo_xubio)
    return {
        "liquidaciones": (
            f"{PAYROLL_BASE}/informe/informes/afip-libro-sueldos-digital-exportartxt-liquidacionesperiodo"
            f"?tipoliquidacion=MENSUAL&periodomensual={ts}&numeroliquidacion={nro_liquidacion}"
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
    }


# ---------------------------------------------------------------------------
# Generación de Excel (misma estructura que baja Xubio)
# ---------------------------------------------------------------------------

_THIN = Side(style="thin", color="000000")
_MEDIUM = Side(style="medium", color="000000")
_BORDER_THIN = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_BORDER_MEDIUM = Border(left=_MEDIUM, right=_MEDIUM, top=_MEDIUM, bottom=_MEDIUM)
_FILL_VERDE_CLARO = PatternFill("solid", fgColor="FFD4EAD2")   # labels reporte
_FILL_VERDE = PatternFill("solid", fgColor="FF9FD09A")          # bloques header
_FILL_GRIS = PatternFill("solid", fgColor="FFB2B2B2")           # título control
_FILL_GRIS_CLARO = PatternFill("solid", fgColor="FFF8F8F8")     # filas empleado
_FILL_GRIS_MEDIO = PatternFill("solid", fgColor="FFBFBFBF")     # Neto / Bruto
_CENTER = Alignment(horizontal="center", vertical="center")


def _set_cell(ws, ref, value, fill=None, bold=False, align=None, border=True):
    c = ws[ref]
    c.value = value
    if fill is not None:
        c.fill = fill
    if bold:
        c.font = Font(bold=True)
    if align is not None:
        c.alignment = align
    if border:
        c.border = _BORDER_THIN
    return c


def build_reporte_xlsx(destino: Path, datos_reporte: list, miempresa: dict, periodo_xubio: str) -> Path:
    """Recrea Excel_reporte_sueldos (estructura idéntica a la descarga de Xubio)."""
    if Workbook is None:
        raise RuntimeError("Modo rápido requiere openpyxl (pip install openpyxl)")
    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja 1"
    for col, width in {
        "A": 20.83, "B": 25.83, "C": 11.83, "D": 11.83, "E": 15.83, "F": 15.83,
        "G": 15.83, "H": 20.83, "I": 10.83, "J": 20.83, "K": 8.83, "L": 20.83,
    }.items():
        ws.column_dimensions[col].width = width

    mes = _mes_nombre_anio(periodo_xubio)

    # Encabezado empresa
    _set_cell(ws, "A1", "Empresa", fill=_FILL_VERDE_CLARO, align=_CENTER)
    _set_cell(ws, "B1", miempresa.get("razonsocial", ""))
    _set_cell(ws, "C1", "Domicilio", fill=_FILL_VERDE_CLARO, align=_CENTER)
    _set_cell(ws, "D1", miempresa.get("domicilio", ""))
    _set_cell(ws, "E1", "Actividad", fill=_FILL_VERDE_CLARO, align=_CENTER)
    _set_cell(ws, "F1", miempresa.get("actividadnombre", ""))
    _set_cell(ws, "A2", "CUIT", fill=_FILL_VERDE_CLARO, align=_CENTER)
    _set_cell(ws, "B2", miempresa.get("cuit", ""))

    # Filtros aplicados
    _set_cell(ws, "A4", "Filtros Aplicados", fill=_FILL_VERDE, align=_CENTER)
    for col, label in {
        "A": "Periodo Desde", "B": mes, "C": "Periodo Hasta", "D": mes,
        "E": "Tipo de Liquidación", "F": "", "G": "Convenio Colectivo", "H": "",
        "I": "Obra Social", "J": "", "K": "Legajo", "L": "",
    }.items():
        _set_cell(ws, f"{col}5", label, fill=_FILL_VERDE_CLARO if label else None, align=_CENTER)

    # Tabla
    _set_cell(ws, "A7", "Concepto", fill=_FILL_VERDE, align=_CENTER)
    _set_cell(ws, "D7", "Importe", fill=_FILL_VERDE, align=_CENTER)
    _set_cell(ws, "B7", "", fill=_FILL_VERDE)
    _set_cell(ws, "C7", "", fill=_FILL_VERDE)
    _set_cell(ws, "E7", "", fill=_FILL_VERDE)

    # Secciones en el mismo orden que el archivo de Xubio
    secciones = [
        ("Importes Remunerativos", "Importe Remunerativo"),
        ("Descuentos Remunerativos", None),
        ("Importes No Remunerativos", "Importe No Remunerativo"),
        ("Descuentos No Remunerativos", None),
        ("Retenciones", "Retención"),
    ]

    def items_de(tipo: Optional[str]):
        if tipo is None:
            return []
        return [d for d in datos_reporte if d.get("tipoconcepto") == tipo]

    row = 8
    for nombre, tipo in secciones:
        _set_cell(ws, f"A{row}", nombre, fill=_FILL_VERDE_CLARO, align=_CENTER)
        row += 1
        for item in items_de(tipo):
            _set_cell(ws, f"B{row}", item.get("nombre", ""))
            _set_cell(ws, f"D{row}", item.get("importe"))
            row += 1
        _set_cell(ws, f"C{row}", "Total")
        if tipo is None:
            # Xubio escribe 0 numérico en la col E de los totales de descuentos
            _set_cell(ws, f"E{row}", 0)
        row += 1

    # Total Neto
    _set_cell(ws, f"A{row}", "Total Neto", fill=_FILL_VERDE, align=_CENTER)

    wb.save(destino)
    return destino


def build_control_xlsx(destino: Path, datos_control: list) -> Path:
    """Recrea Excel_control_liquidacion (misma estructura que Xubio)."""
    if Workbook is None:
        raise RuntimeError("Modo rápido requiere openpyxl (pip install openpyxl)")
    wb = Workbook()
    ws = wb.active
    ws.title = "Control de liquidación"
    for col, width in {
        "A": 8.83, "B": 25.83, "C": 12.83, "D": 9.83, "E": 22.83, "F": 26.83,
        "G": 17.83, "H": 10.83, "I": 7.83, "J": 38.83, "K": 10.83, "L": 16.83,
    }.items():
        ws.column_dimensions[col].width = width

    # Título + encabezados
    _set_cell(ws, "A1", "Control Liquidación Sueldos Xubio", fill=_FILL_GRIS, bold=True, align=_CENTER, border=_BORDER_MEDIUM)
    headers = ["Legajo", "Apellido y Nombre", "CUIL", "Período", "Convenio Colectivo",
               "Categoría", "Tipo Liquidación", "Código", "Int.", "Concepto", "Cantidad", "Importe"]
    for idx, h in enumerate(headers, start=1):
        _set_cell(ws, f"{chr(64 + idx)}2", h, fill=_FILL_GRIS, bold=True, align=_CENTER, border=_BORDER_MEDIUM)

    # Agrupar por liquidación preservando orden
    grupos: list[list[dict]] = []
    ultimo_id = None
    for d in datos_control:
        if d.get("liquidacionid") != ultimo_id:
            grupos.append([])
            ultimo_id = d.get("liquidacionid")
        grupos[-1].append(d)

    row = 3
    for filas in grupos:
        cabeza = filas[0]
        # Xubio conserva los espacios finales del apellido (p.ej. "MARCELO ")
        apellido_ui = (cabeza.get("apellidoynombre") or "")
        if not apellido_ui.strip():
            apellido_ui = ""
        for d in filas:
            ws.cell(row, 1, d.get("legajo", "")).fill = _FILL_GRIS_CLARO
            ws.cell(row, 2, apellido_ui).fill = _FILL_GRIS_CLARO
            ws.cell(row, 3, d.get("cuil", "")).fill = _FILL_GRIS_CLARO
            ws.cell(row, 4, _periodo_corto(d.get("periodoliquidacion", ""))).fill = _FILL_GRIS_CLARO
            ws.cell(row, 5, d.get("convenionombre", "")).fill = _FILL_GRIS_CLARO
            ws.cell(row, 6, d.get("categorianombre", "")).fill = _FILL_GRIS_CLARO
            ws.cell(row, 7, d.get("tipoliquidacion", "")).fill = _FILL_GRIS_CLARO
            ws.cell(row, 8, d.get("conceptocodigo", "") or "")
            ws.cell(row, 9, d.get("conceptocodigointerno", "") or "")
            ws.cell(row, 10, d.get("conceptonombre", "") or "")
            ws.cell(row, 11, d.get("cantidad"))
            ws.cell(row, 12, d.get("importe"))
            for col in range(1, 13):
                ws.cell(row, col).border = _BORDER_THIN
            row += 1

        # Resumen por liquidación
        resumen = [
            ("Bruto", cabeza.get("importebruto"), False),
            ("Total remunerativo", cabeza.get("importeremunerativo"), False),
            ("Total no remunerativo", cabeza.get("importenoremunerativo"), False),
            ("Neto", cabeza.get("importeneto"), True),
        ]
        for nombre, valor, ultimo in resumen:
            for col in range(1, 8):
                c = ws.cell(row, col)
                c.fill = _FILL_GRIS_MEDIO if ultimo else _FILL_GRIS
                if ultimo:
                    c.font = Font(bold=True)
                c.border = _BORDER_THIN
            for col in range(8, 11):
                c = ws.cell(row, col)
                c.border = _BORDER_THIN
            c_j = ws.cell(row, 10, nombre)
            c_j.border = _BORDER_THIN
            if ultimo:
                c_j.font = Font(bold=True)
            c_k = ws.cell(row, 11)
            c_k.border = _BORDER_THIN
            c_l = ws.cell(row, 12, valor)
            c_l.border = _BORDER_THIN
            if ultimo:
                c_l.font = Font(bold=True)
            row += 1

    wb.save(destino)
    return destino


# ---------------------------------------------------------------------------
# Orquestación del modo rápido
# ---------------------------------------------------------------------------

async def descargar_rapido(
    page,
    config,
    empresa: str,
    base_dir: Path,
    logger=None,
) -> dict[str, Path]:
    """Descarga los 4 archivos de una empresa vía APIs (la empresa ya está activa).

    Devuelve dict con claves liquidaciones/conceptos/reporte/control.
    """
    periodo_xubio = _format_periodo_xubio(config.periodo)
    nro = f"{int(config.nro_liquidacion):02d}"

    if logger:
        logger("modo_rapido_inicio empresa=%s periodo=%s nro=%s", empresa, periodo_xubio, nro)

    tok = await capturar_token(page, XUBIO_LIBRO_URL, logger=logger)
    token = tok["token"]
    token_micro = await _obtener_token_micro(page.context, token, logger)

    urls = _urls_periodo(periodo_xubio, nro)
    # 4 llamadas de payroll + empresa (miempresa usa tokenmicroservicios)
    tareas = [
        _api_get(page.context, token, urls["liquidaciones"], logger),
        _api_get(page.context, token, urls["conceptos"], logger),
        _api_get(page.context, token, urls["reporte"], logger),
        _api_get(page.context, token, urls["control"], logger),
        _api_get(page.context, token_micro or token, XUBIO_SBA_MIEMPRESA, logger),
    ]
    resultados = await asyncio.gather(*tareas, return_exceptions=True)

    # miempresa no es crítico: si falla, header del reporte sale vacío
    body_miempresa = None
    for idx, r in enumerate(resultados):
        if isinstance(r, Exception):
            if idx == 4:
                if logger:
                    logger("miempresa_datos_error error=%s (header reporte vacío)", r)
                continue
            raise r
    body_liq = resultados[0]
    body_conc = resultados[1]
    body_reporte = resultados[2]
    body_control = resultados[3]
    if body_miempresa is None:
        body_miempresa = resultados[4]
    if body_miempresa is None:
        body_miempresa = "{}"
    data_liq = json.loads(body_liq)["data"]
    data_conc = json.loads(body_conc)["data"]
    datos_reporte = json.loads(body_reporte)
    datos_control = json.loads(body_control)
    miempresa = json.loads(body_miempresa)

    # Archivos
    path_liq = _build_filename(base_dir, "TXT_liquidaciones", empresa, config.periodo, ".txt")
    path_conc = _build_filename(base_dir, "TXT_conceptos", empresa, config.periodo, ".txt")
    path_reporte = _build_filename(base_dir, "Excel_reporte_sueldos", empresa, config.periodo, ".xlsx")
    path_control = _build_filename(base_dir, "Excel_control_liquidacion", empresa, config.periodo, ".xlsx")

    # TXT: liquidaciones se guarda UTF-8; conceptos CP1252 (paridad con la descarga del navegador)
    path_liq.write_bytes(data_liq.encode("utf-8"))
    try:
        path_conc.write_bytes(data_conc.encode("cp1252"))
    except UnicodeEncodeError:
        path_conc.write_bytes(data_conc.encode("utf-8"))

    build_reporte_xlsx(path_reporte, datos_reporte, miempresa, periodo_xubio)
    build_control_xlsx(path_control, datos_control)

    if logger:
        logger(
            "modo_rapido_ok liq=%s conc=%s rep=%s ctrl=%s",
            path_liq, path_conc, path_reporte, path_control,
        )

    return {
        "liquidaciones": path_liq,
        "conceptos": path_conc,
        "reporte": path_reporte,
        "control": path_control,
    }


# ---------------------------------------------------------------------------
# Orquestación completa (usada por ambos bots)
# ---------------------------------------------------------------------------

def _debug_logger_guion():
    """Logger mínimo por consola para modo rápido (sin estructura de evidencias).

    Devuelve un wrapper callable compatible con la interfaz usada en el core:
    logger("msg %s", arg) y logger.error("msg %s", arg).
    """
    logger = logging.getLogger("xubio_rapido")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    class _CallableLogger:
        def __call__(self, message, *args):
            logger.info(message, *args)

        def error(self, message, *args):
            logger.error(message, *args)

    return _CallableLogger()


async def run_rapido(
    playwright: Playwright,
    config: RapiConfig,
    base_dir: Path,
    logger=None,
) -> bool:
    """Login + descarga rápida por cada contribuyente. Devuelve True si todo OK."""
    logger = logger or _debug_logger_guion()

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None

    try:
        proxy = get_proxy_config() if config.proxy else None
        browser, context, page = await launch_arca_browser_context(
            playwright,
            headless=config.headless,
            proxy=proxy,
            context_kwargs={"accept_downloads": True},
        )
        page.set_default_timeout(20_000)
        logger("browser_launched url=%s", page.url)

        await login_visma(page, config, logger)

        lista = config.contribuyentes
        logger("contribuyentes_a_procesar=%s", lista)

        resultados: dict[str, dict[str, Path]] = {}
        for contribuyente in lista:
            logger("contribuyente_procesar=%s", contribuyente or "(empresa activa)")
            empresa = await seleccionar_contribuyente(page, contribuyente, logger)
            logger("empresa_efectiva=%s", empresa)
            descargas = await descargar_rapido(page, config, empresa, base_dir, logger)
            resultados[empresa] = descargas

        success = all(
            p.exists() and p.stat().st_size > 0
            for descargas in resultados.values()
            for p in descargas.values()
        )
        logger("resultado success=%s contribuyentes=%s", success, resultados)
        return success
    except Exception as exc:
        logger.error("run_failed error=%s", exc)
        return False
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception as exc:
                logger.error("context_close_failed error=%s", exc)
        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                logger.error("browser_close_failed error=%s", exc)