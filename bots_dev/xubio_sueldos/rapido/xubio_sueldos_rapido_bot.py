"""
Bot limpio (nuevo, separado): descarga rápida de sueldos Xubio vía APIs de payroll.

Totalmente independiente de xubio_sueldos_bot.py / xubio_sueldos_bot_logger.py:
no los toca ni los importa.

Cómo funciona:
1. Login en Visma Connect (UI) una sola vez.
2. Por cada contribuyente: selección por UI (dispara el Relogin por tenant).
3. Captura el token del iframe app.xubio.com (abortando su request) y llama
   directo a las APIs de payroll con las cookies de sesión:
   - TXT liquidaciones / TXT conceptos
   - Reporte / Control (JSON)
4. Los 4 archivos se generan localmente:
   - TXT: tal cual devuelve la API (liquidaciones utf-8, conceptos cp1252).
   - Excel reporte / control: misma estructura que baja Xubio (openpyxl).

Velocidad: 2 empresas ~35s vs ~2m45s del modo UI.

Variables de entorno (`.env`) — prefijo propio con fallback a los viejos:
- XUBIO_RAPIDO_USUARIO          (fallback XUBIO_SUELDOS_USUARIO)
- XUBIO_RAPIDO_CLAVE            (fallback XUBIO_SUELDOS_CLAVE)
- XUBIO_RAPIDO_CONTRIBUYENTE    -> también acepta varios separados por | ; ,
- XUBIO_RAPIDO_CONTRIBUYENTES   (lista; si se setea, priva sobre CONTRIBUYENTE)
- XUBIO_RAPIDO_PERIODO          (ej. 202607)
- XUBIO_RAPIDO_NRO_LIQUIDACION  (0-10, default 0)
- XUBIO_RAPIDO_HEADLESS         (1/true/si, default true)
- XUBIO_RAPIDO_PROXY            (1/true/si, default false)

Descargas: ./descargas/ (nombres idénticos al bot original).
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from xubio_sueldos_api import load_rapi_config, run_rapido


DOWNLOAD_DIR = Path("./descargas")


async def main() -> bool:
    config = load_rapi_config()
    print("Iniciando Xubio Sueldos Rápido (versión limpia, separada del bot original)")
    print(f"Contribuyentes: {config.contribuyentes}")
    print(f"Período: {config.periodo} | Nro Liquidación: {config.nro_liquidacion} | Headless: {config.headless}")

    DOWNLOAD_DIR.mkdir(exist_ok=True)

    async with async_playwright() as playwright:
        return await run_rapido(playwright, config, DOWNLOAD_DIR)


if __name__ == "__main__":
    try:
        load_dotenv()
    except Exception:
        pass

    ok = asyncio.run(main())
    print("OK" if ok else "ERROR")
    raise SystemExit(0 if ok else 1)