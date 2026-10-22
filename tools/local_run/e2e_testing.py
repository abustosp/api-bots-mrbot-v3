#!/usr/bin/env python3
"""Corrida E2E contra una central V3 (por defecto, la de testing).

Envía un job real, espera el estado terminal y resume el resultado. Sirve para
verificar de punta a punta (central + worker + portal) los módulos que se
corrigieron, después de validarlos localmente con ``run_bot.py``.

Uso:

    export MRBOT_API_KEY='mbk_...'
    tools/local_run/e2e_testing.py --bot sct --operacion consultar \
        --body-json '{"cuit_representante":"20296066916","clave_representante":"...",'\
'"cuit_representado":"33718421549","secciones":["deudas"],"formatos":["pdf"]}' \
        --timeout 600

    # Varias corridas: --body-json acepta un archivo con un JSON por linea.
    tools/local_run/e2e_testing.py --bot sct --operacion consultar --body-file casos.jsonl

Por defecto apunta a https://testing-central.mrbot.com.ar (override con
``--base-url`` o ``MRBOT_BASE_URL``). Nunca apuntar a producción sin querer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://testing-central.mrbot.com.ar"

#: ruta pública por (bot, operacion), tomada de BOT_ROUTE_ALIASES
RUTAS: dict[tuple[str, str], str] = {
    ("mis_comprobantes", "consultar"): "/mis_comprobantes/consulta",
    ("ccma", "consultar"): "/ccma/consulta",
    ("siper", "consultar"): "/siper/consulta",
    ("sct", "consultar"): "/sct/consulta",
    ("compensaciones", "consultar"): "/sct/compensaciones/consulta",
    ("portal_iva", "descargar"): "/portal_iva/consulta",
    ("carga_portal_iva", "cargar"): "/portal_iva/carga",
    ("rcel", "descargar"): "/rcel/consulta",
    ("hacienda", "consultar"): "/hacienda/consulta",
    ("sifere", "consultar"): "/sifere/consulta",
    ("aportes_en_linea", "descargar"): "/aportes-en-linea/consulta",
    ("declaracion_en_linea", "consultar"): "/declaracion-en-linea/consulta",
    ("mis_facilidades", "consultar"): "/mis_facilidades/consulta",
    ("mis_retenciones", "consultar"): "/mis_retenciones/consulta",
    ("pago_devoluciones", "consultar"): "/pago_devoluciones/consulta",
    ("moa", "consultar"): "/moa/consulta",
    ("libros_portal_iva", "descargar"): "/libros_iva/consulta",
    ("libros_portal_iva", "descargar_ddjj"): "/libros_iva/ddjj",
    ("libros_portal_iva", "descargar_libros"): "/libros_iva/consulta",
    ("libro_sueldos_digital", "descargar"): "/libro-sueldos-digital/consulta",
    ("facturometro", "consultar"): "/facturometro/consulta",
    ("controladores_fiscales", "presentar"): "/controladores-fiscales/carga",
    ("certificado_mipyme", "descargar"): "/certificado-mipyme/consulta",
    ("srt", "consultar_alicuotas"): "/srt/alicuotas/consulta",
    ("vep_archivo", "generar"): "/vep/carga",
    ("vep_ccma", "generar"): "/vep-ccma/generar",
    ("consulta_pagos_vep", "consultar"): "/vep/consulta-pagos",
    ("liquidacion_granos", "consultar"): "/liquidacion_granos/consulta",
    ("certificacion_primaria_granos", "consultar"): "/certificacion_primaria_granos/consulta",
}


def _pedir(method: str, url: str, api_key: str, body: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as respuesta:
            texto = respuesta.read().decode()
            try:
                return respuesta.status, json.loads(texto)
            except ValueError:
                return respuesta.status, texto
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode()
        try:
            return exc.code, json.loads(texto)
        except ValueError:
            return exc.code, texto


def _resumen(sobre: dict) -> dict:
    error = sobre.get("error_detail") or sobre.get("error") or {}
    return {
        "status": sobre.get("status"),
        "result": sobre.get("result"),
        "error_code": error.get("error_code") if isinstance(error, dict) else error,
        "cause": error.get("cause") if isinstance(error, dict) else None,
        "message": error.get("message") if isinstance(error, dict) else None,
        "files": [f.get("name") for f in (sobre.get("files") or [])],
        "data_keys": sorted((sobre.get("data") or {}).keys()) if isinstance(sobre.get("data"), dict) else None,
    }


def correr_caso(base: str, api_key: str, bot: str, operacion: str, body: dict, *, timeout: float) -> dict:
    ruta = RUTAS.get((bot, operacion))
    if ruta is None:
        return {"error": f"sin ruta conocida para {bot}/{operacion}"}
    inicio = time.time()
    status, respuesta = _pedir("POST", base + "/api/v3" + ruta, api_key, body)
    if status >= 300 or not isinstance(respuesta, dict):
        return {"http_status": status, "respuesta": str(respuesta)[:500], "elapsed_s": round(time.time() - inicio, 1)}
    job_id = respuesta.get("job_id")
    esperado = "OK"
    if operacion in {"generar", "compensar", "presentar"}:
        esperado = "sincronico"
    while time.time() - inicio < timeout:
        time.sleep(5)
        _, sobre = _pedir("GET", f"{base}/api/v3/jobs/{job_id}", api_key)
        if not isinstance(sobre, dict):
            continue
        if sobre.get("status") in {"COMPLETO", "FALLIDO", "CANCELADO"}:
            return {
                "job_id": job_id,
                "http_status": status,
                "elapsed_s": round(time.time() - inicio, 1),
                **_resumen(sobre),
                "esperado": esperado,
            }
    return {"job_id": job_id, "error": "timeout esperando el job", "elapsed_s": round(time.time() - inicio, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("MRBOT_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--api-key", default=os.getenv("MRBOT_API_KEY", ""))
    parser.add_argument("--bot", required=True)
    parser.add_argument("--operacion", required=True)
    parser.add_argument("--body-json", help="Cuerpo JSON del caso (credenciales incluidas).")
    parser.add_argument("--body-file", help="Archivo con un cuerpo JSON por linea.")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", help="Archivo donde guardar los resultados (JSON).")
    args = parser.parse_args()

    if not args.api_key:
        print("falta MRBOT_API_KEY o --api-key", file=sys.stderr)
        return 2

    cuerpos: list[dict] = []
    if args.body_file:
        for linea in open(args.body_file, encoding="utf-8"):
            linea = linea.strip()
            if linea:
                cuerpos.append(json.loads(linea))
    if args.body_json:
        cuerpos.append(json.loads(args.body_json))
    if not cuerpos:
        print("indique --body-json o --body-file", file=sys.stderr)
        return 2

    resultados = []
    for indice, cuerpo in enumerate(cuerpos, 1):
        resultado = correr_caso(
            args.base_url, args.api_key, args.bot, args.operacion, cuerpo, timeout=args.timeout
        )
        print(json.dumps({"caso": indice, **resultado}, ensure_ascii=False, default=str), flush=True)
        resultados.append(resultado)
    if args.out:
        json.dump(resultados, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0 if all(r.get("status") == "COMPLETO" and r.get("result") in {"OK", "PARCIAL"} for r in resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
