#!/usr/bin/env python3
"""Generador de esqueletos de bots Playwright + ARCA.

Uso:
    python new_bot.py mi_servicio --service "MI SERVICIO" --prefix MI_SERVICIO

Crea dos archivos:
    mi_servicio_bot_logger.py   (variante con evidencias)
    mi_servicio_bot.py          (variante limpia, sin HTML/screenshot/HAR)

Ambos comparten el mismo `flow(ctx)` -- la logica vive una sola vez.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from textwrap import dedent


TEMPLATE = '''"""Bot {bot_title} ({variant}).

{tagline}
"""
from __future__ import annotations

import asyncio
import os
{imports}
from bot_builder import BotConfig, run_bot, step, build_filename, load_env

load_env()


@step("login_y_servicio")
async def _login(ctx):
    # El login ARCA + apertura del servicio ya las hace run_bot().
    return ctx.page


@step("flujo_principal")
async def _flujo(ctx):
    page = ctx.page
    cuit = os.getenv("{prefix}_CUIT_REPRESENTADO", "")
    {placeholder}


async def flow(ctx):
    try:
        await _login(ctx)
        await _flujo(ctx)
        return ctx.results, ctx.error_logs, True
    except Exception as exc:
        ctx.error_logs.append("Error en {bot}: " + str(exc))
        return ctx.results, ctx.error_logs, False


def main():
    cfg = BotConfig(
        bot="{bot}",
        prefix="{prefix}",
        service_name="{service}",
        service_matchers=({matchers}),
        proxy={proxy},
        logger={is_logger},
    )
    print(run_bot(cfg, flow))


if __name__ == "__main__":
    main()
'''


def _title(name: str) -> str:
    return name.replace("_", " ").strip().title() or name


def render(bot: str, prefix: str, service: str, matchers: str,
           proxy: str, is_logger: bool, imports: str = "",
           placeholder: str = "# TODO: implementar flujo") -> str:
    out = TEMPLATE
    out = out.replace("{bot_title}", _title(bot))
    out = out.replace("{variant}", "logger (con HTML/screenshots/HAR)" if is_logger else "clean (produccion)")
    out = out.replace("{tagline}", f"Variante {'logger' if is_logger else 'clean'} generada por new_bot.py.")
    out = out.replace("{bot}", bot)
    out = out.replace("{prefix}", prefix)
    out = out.replace("{service}", service)
    out = out.replace("{matchers}", matchers or f"re.compile(r\"{service}\", re.I)")
    out = out.replace("{proxy}", str(proxy))
    out = out.replace("{is_logger}", str(is_logger))
    out = out.replace("{imports}", imports)
    out = out.replace("{placeholder}", placeholder)
    return out


def build_files(args: argparse.Namespace) -> list[Path]:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    matchers_list = args.matchers or [args.service]
    matchers = ", ".join(f"re.compile(r\"{m}\", re.I)" for m in matchers_list)
    extra_imports = list(args.imports or [])
    if not any("import re" in i for i in extra_imports):
        extra_imports.insert(0, "import re")
    imports = "\n".join(extra_imports) + ("\n" if extra_imports else "")

    files: list[Path] = []
    plan = [
        (f"{args.bot}_bot_logger.py", True),
        (f"{args.bot}_bot.py", False),
    ]
    for filename, is_logger in plan:
        path = out_dir / filename
        path.write_text(render(
            bot=args.bot,
            prefix=args.prefix,
            service=args.service,
            matchers=matchers,
            proxy=str(args.proxy),
            is_logger=is_logger,
            imports=imports,
        ), encoding="utf-8")
        files.append(path)
    return files


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genera esqueleto de bot Playwright + ARCA.")
    p.add_argument("bot", help="nombre base del bot (ej: mis_facilidades)")
    p.add_argument("--prefix", help="prefijo .env (default: BOT en mayusculas)")
    p.add_argument("--service", default="", help="nombre exacto del servicio ARCA")
    p.add_argument("--matchers", nargs="*", help="regex adicionales para localizar el servicio")
    p.add_argument("--out", default=".", help="directorio de salida")
    p.add_argument("--proxy", action="store_true", help="habilitar proxy por default")
    p.add_argument("--imports", nargs="*", default=[],
                   help="imports extra a insertar (uno por linea)")
    return p.parse_args()


def main() -> int:
    args = parse()
    if not args.prefix:
        args.prefix = args.bot.upper()
    if not args.service:
        args.service = _title(args.bot).upper().replace(" ", " ")
    files = build_files(args)
    for f in files:
        print(f"creado: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
