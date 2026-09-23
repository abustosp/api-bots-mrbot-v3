#!/usr/bin/env python3
"""Valida sintacticamente todos los bloques de codigo de los planes.

Un plan cuyos ejemplos no compilan induce al error a quien lo implemente. Este
script recorre los bloques ```json, ```python, ```yaml, ```toml y ```mermaid de
los planes y READMEs y reporta los que no son parseables.

No valida SQL: eso lo hace infra/postgres/verify-ddl.sh contra PostgreSQL real,
que es una comprobacion mas fuerte que un parser.

Uso: validate-code-blocks.py [raiz]
Salida: 0 si todo parsea, 1 si hay errores.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

SKIP_DIRS = {".research", ".git"}

# Lenguajes que este script sabe validar y como.
JSON_LANGS = {"json"}
PY_LANGS = {"python"}
YAML_LANGS = {"yaml", "yml"}
TOML_LANGS = {"toml"}
MERMAID_LANGS = {"mermaid"}

MERMAID_KINDS = (
    "graph",
    "flowchart",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "classDiagram",
    "gantt",
    "pie",
    "journey",
    "mindmap",
    "timeline",
)


def strip_json_comments(text: str) -> str:
    """Quita comentarios // y /* */ y comas finales.

    Los planes usan JSON anotado para explicar campos del protocolo. Eso no es
    JSON estricto, pero sigue siendo verificable si se normaliza primero.
    """
    out = []
    in_str = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    # Comas finales antes de } o ]
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def check_json(body: str) -> str | None:
    candidate = strip_json_comments(body).strip()
    if not candidate:
        return "bloque vacio"
    # Un bloque puede contener varios documentos separados por lineas en blanco
    # o ser un fragmento de campos sin envolver. Se intenta el caso normal y,
    # si falla, se prueba envolviendolo como objeto.
    for attempt in (candidate, "{" + candidate + "}"):
        try:
            json.loads(attempt)
            return None
        except json.JSONDecodeError as exc:
            last = f"{exc.msg} (linea {exc.lineno}, col {exc.colno})"
    return last


def check_python(body: str) -> str | None:
    try:
        ast.parse(body)
        return None
    except SyntaxError as exc:
        return f"{exc.msg} (linea {exc.lineno})"


def check_yaml(body: str) -> str | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return "SKIP: pyyaml no instalado"
    try:
        list(yaml.safe_load_all(body))
        return None
    except Exception as exc:  # noqa: BLE001 - yaml lanza varios tipos
        return str(exc).replace("\n", " ")[:200]


def check_toml(body: str) -> str | None:
    try:
        import tomllib
    except ImportError:
        return "SKIP: tomllib no disponible"
    try:
        tomllib.loads(body)
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)[:200]


def check_mermaid(body: str) -> str | None:
    """Comprobacion estructural, no un render completo.

    Detecta el error real y frecuente: un diagrama sin tipo declarado, o
    delimitadores desbalanceados que rompen el render en GitHub.
    """
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return "diagrama vacio"
    first = lines[0]
    if not first.startswith(MERMAID_KINDS):
        return f"no declara tipo de diagrama: {first[:60]!r}"

    # El balance de delimitadores solo tiene sentido donde son delimitadores.
    # En erDiagram, `||--o{` y `}o--||` son notacion de cardinalidad, no llaves
    # de apertura y cierre, asi que contarlas da un falso positivo.
    probe = body
    if first.startswith("erDiagram"):
        probe = re.sub(r"[|}{o][|o]?--[o|][|{}o]?", "", probe)

    # Las etiquetas entre comillas pueden contener delimitadores legitimamente.
    probe = re.sub(r'"[^"]*"', "", probe)

    for open_ch, close_ch, name in (
        ("[", "]", "corchetes"),
        ("(", ")", "parentesis"),
        ("{", "}", "llaves"),
    ):
        if probe.count(open_ch) != probe.count(close_ch):
            return (
                f"{name} desbalanceados: "
                f"{probe.count(open_ch)} vs {probe.count(close_ch)}"
            )
    return None


CHECKERS = [
    (JSON_LANGS, check_json),
    (PY_LANGS, check_python),
    (YAML_LANGS, check_yaml),
    (TOML_LANGS, check_toml),
    (MERMAID_LANGS, check_mermaid),
]


def checker_for(lang: str):
    for langs, fn in CHECKERS:
        if lang in langs:
            return fn
    return None


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    checked = 0
    skipped = 0
    failures: list[tuple[str, int, str, str]] = []

    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        src = path.read_text()
        # Numero de linea del bloque, para poder localizarlo.
        for match in re.finditer(r"```(\w+)\n(.*?)```", src, re.S):
            lang, body = match.group(1), match.group(2)
            fn = checker_for(lang)
            if fn is None:
                continue
            line = src[: match.start()].count("\n") + 1
            err = fn(body)
            if err and err.startswith("SKIP:"):
                skipped += 1
                continue
            checked += 1
            if err:
                failures.append((str(path), line, lang, err))

    for path, line, lang, err in failures:
        print(f"FALLA {path}:{line} [{lang}] {err}")

    print()
    print(f"bloques verificados: {checked}")
    if skipped:
        print(f"bloques omitidos por falta de parser: {skipped}")
    if failures:
        print(f"bloques con error: {len(failures)}")
        return 1
    print("todos los bloques parsean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
