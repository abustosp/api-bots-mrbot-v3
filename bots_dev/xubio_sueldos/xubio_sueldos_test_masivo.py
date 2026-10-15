"""
Test masivo: descarga de sueldos Xubio para una lista de contribuyentes.

Itera sobre cada contribuyente (con la misma sesión Visma) descargando los 4
archivos por empresa: TXT liquidaciones, TXT conceptos, Excel reporte y Excel
control de liquidación, para el período configurado.

Lista de contribuyentes:
- env XUBIO_SUELDOS_CONTRIBUYENTES (separados por | ; o ,)
- si no está seteada, default: "MPR COMIDAS S.A.S. | CELI PATRICIA MARIANA"

Uso:
    python xubio_sueldos_test_masivo.py
"""

import asyncio
import os
import re

from dotenv import load_dotenv
from playwright.async_api import async_playwright

import xubio_sueldos_bot_logger as bot

DEFAULT_CONTRIBUYENTES = "MPR COMIDAS S.A.S.|CELI PATRICIA MARIANA"


def _parse_lista(raw: str) -> list[str]:
    return [c.strip() for c in re.split(r"[|;,]", raw or "") if c.strip()]


async def main() -> bool:
    try:
        load_dotenv()
    except Exception:
        pass

    config = bot._load_config_from_env()
    lista_env = os.getenv("XUBIO_SUELDOS_CONTRIBUYENTES", "").strip()
    if lista_env:
        contribuyentes = _parse_lista(lista_env)
    else:
        contribuyentes = _parse_lista(DEFAULT_CONTRIBUYENTES)
    if not contribuyentes:
        contribuyentes = [""]
    config.contribuyentes = contribuyentes

    print("Test masivo Xubio Sueldos")
    print(f"Contribuyentes ({len(contribuyentes)}):")
    for c in contribuyentes:
        print(f"  - {c or '(empresa activa)'}")
    print(f"Período: {config.periodo}")
    print(f"Nro Liquidación: {config.nro_liquidacion}")
    print(f"Headless: {config.headless}")

    async with async_playwright() as playwright:
        ok = await bot.run_with_config(playwright, config)

    print("\nResultado global:", "OK" if ok else "ERROR")
    return ok


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
