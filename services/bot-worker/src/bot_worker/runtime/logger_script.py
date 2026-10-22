"""Versión ``_logger`` de un bot: ejecuta el script logger de ``bots_dev`` dentro del worker.

Es el comportamiento por defecto de ``logger: true`` en los bots listados en ``SCRIPTS``
(solo con ``logger_script: true``; por defecto ``logger: true`` ejecuta el plugin real
instrumentado y la respuesta del job no cambia; ambas claves son ocultas y solo se aceptan
booleanos). En ese caso el plugin normal NO se ejecuta: corre el script
``bots_dev/<bot>/*_logger.py`` (copia sin cambios del de la raíz ``bots_dev``) como
subproceso, con las credenciales y parámetros del job mapeados a las variables de
entorno que ese script lee. Su carpeta ``temp/{bot}_{ts}/`` (logs, screenshots, html,
har, descargas) se vuelca a la evidencia del tar.gz. Sirve para conciliar el
comportamiento del plugin contra el del script de referencia sin tocar el bot normal.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any

#: bot -> script relativo a ``bot_worker/bots_dev/`` (junto a NOTA.md y payload_ejemplo.json)
SCRIPTS: dict[str, str] = {
    "ccma": "ccma/ccma_bot_logger.py",
    "siper": "siper/siper_bot_logger.py",
    "facturometro": "facturometro/facturometro_bot_logger.py",
    "libro_sueldos_digital": "libro_sueldos_digital/libro_sueldos_digital_bot_logger.py",
    "declaracion_en_linea": "declaracion_en_linea/declaracion_en_linea_bot_logger.py",
    "retper_iibb_agip": "retper_iibb_agip/agip_bot_logger.py",
    "mis_retenciones": "mis_retenciones/mis_retenciones_bot_logger.py",
    "certificacion_primaria_granos": "certificacion_primaria_granos/certificacion_primaria_granos_bot_logger.py",
    "liquidacion_granos": "liquidacion_granos/liquidacion_granos_bot_logger.py",
}

#: esperas (s) previas a cada intento; 0 = primer intento sin espera.
REINTENTOS_BLOQUEO: dict[str, tuple[int, ...]] = {"retper_iibb_agip": (0, 60, 120, 180, 240)}

#: bots cuyo script necesita navegador con interfaz sobre pantalla virtual.
PANTALLA_VIRTUAL = {"retper_iibb_agip"}

CARPETA = Path(__file__).resolve().parent.parent / "bots_dev"


def script_solicitado(payload: dict[str, Any]) -> bool:
    """La versión ``_logger`` solo corre si se pide explícitamente (``logger_script: true``).

    Por defecto ``logger: true`` ejecuta el plugin real instrumentado, de modo que la
    respuesta del job es idéntica a la de ``logger`` ausente o ``false``.
    """
    return payload.get("logger") is True and payload.get("logger_script") is True


def _tf(valor: Any) -> str:
    return "true" if valor else "false"


def entorno(bot: str, payload: dict[str, Any], cuit: str, clave: str) -> dict[str, str]:
    """Variables que lee el ``main`` de cada script logger (mismos nombres que bots_dev)."""
    rep = str(payload.get("representado_cuit") or payload.get("cuit_representado") or cuit)
    if bot == "ccma":
        return {
            "CCMA_CUIT_REPRESENTANTE": cuit, "CCMA_CLAVE_REPRESENTANTE": clave,
            "CCMA_CUIT_REPRESENTADO": rep,
            "CCMA_MOVIMIENTOS": _tf(payload.get("incluir_movimientos")),
            "CCMA_PDF": _tf(payload.get("incluir_pdf")),
        }
    if bot == "siper":
        return {"SIPER_CUIT_REPRESENTANTE": cuit, "SIPER_CLAVE": clave,
                "SIPER_CUIT_REPRESENTADO": rep}
    if bot == "facturometro":
        return {"FACTUROMETRO_CUIT": cuit, "FACTUROMETRO_CLAVE": clave,
                "FACTUROMETRO_REPRESENTADO_CUIT": rep, "FACTUROMETRO_HEADLESS": "true"}
    if bot == "libro_sueldos_digital":
        return {"LSD_CUIT_REPRESENTANTE": cuit, "LSD_CLAVE": clave,
                "LSD_REPRESENTADO_CUIT": rep, "LSD_HEADLESS": "true",
                "PERIODO_DESDE": str(payload.get("periodo_desde", "")),
                "PERIODO_HASTA": str(payload.get("periodo_hasta", ""))}
    if bot == "declaracion_en_linea":
        return {"DECLARACION_CUIT_REPRESENTANTE": cuit, "DECLARACION_CLAVE_REPRESENTANTE": clave,
                "DECLARACION_CUIT_REPRESENTADO": rep, "HEADLESS": "true",
                "DECLARACION_DESDE": str(payload.get("periodo_desde", "")),
                "DECLARACION_HASTA": str(payload.get("periodo_hasta", ""))}
    if bot == "mis_retenciones":
        return {"MIS_RETENCIONES_CUIT_REPRESENTANTE": cuit, "MIS_RETENCIONES_CLAVE_REPRESENTANTE": clave,
                "MIS_RETENCIONES_CUIT_REPRESENTADO": rep,
                "MIS_RETENCIONES_DENOMINACION": str(payload.get("representado_nombre") or payload.get("denominacion", "")),
                "MIS_RETENCIONES_DESDE": str(payload.get("fecha_desde") or payload.get("desde", "")),
                "MIS_RETENCIONES_HASTA": str(payload.get("fecha_hasta") or payload.get("hasta", ""))}
    if bot == "certificacion_primaria_granos":
        return {"CERTIFICACION_PRIMARIA_GRANOS_CUIT_REPRESENTANTE": cuit,
                "CERTIFICACION_PRIMARIA_GRANOS_CLAVE": clave,
                "CERTIFICACION_PRIMARIA_GRANOS_DENOMINACION": str(payload.get("representado_nombre") or payload.get("denominacion", "")),
                "CERTIFICACION_PRIMARIA_GRANOS_DESDE": str(payload.get("fecha_desde") or payload.get("desde", "")),
                "CERTIFICACION_PRIMARIA_GRANOS_HASTA": str(payload.get("fecha_hasta") or payload.get("hasta", "")),
                "CERTIFICACION_PRIMARIA_GRANOS_HEADLESS": "true"}
    if bot == "liquidacion_granos":
        return {"LIQUIDACION_GRANOS_CUIT_REPRESENTANTE": cuit, "LIQUIDACION_GRANOS_CLAVE": clave,
                "LIQUIDACION_GRANOS_DENOMINACION": str(payload.get("representado_nombre") or payload.get("denominacion", "")),
                "LIQUIDACION_GRANOS_DESDE": str(payload.get("fecha_desde") or payload.get("desde", "")),
                "LIQUIDACION_GRANOS_HASTA": str(payload.get("fecha_hasta") or payload.get("hasta", "")),
                "LIQUIDACION_GRANOS_HEADLESS": "true"}
    if bot == "retper_iibb_agip":
        return {"AGIP_USUARIO": str(payload.get("usuario") or cuit), "AGIP_CLAVE": clave,
                "AGIP_CUIT_REPRESENTADO": rep, "AGIP_DENOMINACION": str(payload.get("denominacion", "")),
                "AGIP_DESDE": str(payload.get("periodo_desde") or payload.get("desde", "")),
                "AGIP_HASTA": str(payload.get("periodo_hasta") or payload.get("hasta", "")),
                "AGIP_HEADLESS": "False"}
    return {}


async def ejecutar(
    bot: str, payload: dict[str, Any], cuit: str, clave: str, captcha_key: str,
    destino: Path, timeout: float,
) -> tuple[int, str]:
    """Corre el script; copia ``temp/{bot}_{ts}/*`` a ``destino``. Devuelve (rc, cola de salida)."""
    script = CARPETA / SCRIPTS[bot]
    cwd = destino / "_script"
    cwd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "PLAYWRIGHT_BROWSERS_PATH", "LANG")}
    env.update(entorno(bot, payload, cuit, clave))
    previo = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(CARPETA) + (":" + previo if previo else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # shared.proxy lee estos valores aunque no haya proxy: se deja deshabilitado.
    env.update(SERVER_PROXY="False", PUERTO_PROXY_MINIMO="1", PUERTO_PROXY_MAXIMO="2")
    if captcha_key:
        env["ARCA_CAPMONSTER_API_KEY"] = captcha_key
    if bot in PANTALLA_VIRTUAL:
        # Igual que el plugin: AGIP solo deja entrar con navegador completo (display virtual).
        from bot_worker.runtime.browser import _asegurar_display

        env["DISPLAY"] = _asegurar_display()
    # AGIP bloquea la portada por ventanas (WAF "Web Page Blocked!"): igual que el
    # plugin, se reintenta con espera creciente en lugar de fallar a la primera.
    reintentos = REINTENTOS_BLOQUEO.get(bot, (0,))
    texto, rc = "", -1
    for espera in reintentos:
        if espera:
            await asyncio.sleep(espera)
            shutil.rmtree(cwd / "temp", ignore_errors=True)
        proceso = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(script), cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            salida, _ = await asyncio.wait_for(proceso.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proceso.kill()
            await proceso.wait()
            salida, rc = b"", -1
        else:
            rc = proceso.returncode or 0
        texto = salida.decode("utf-8", "replace")
        if rc == 0 or not any(m in texto for m in ("Web Page Blocked", "Iniciar sesión", "fechaDesdeCo")):
            break
    # La salida estándar del script puede traer la clave: se enmascara antes de guardarla.
    if clave:
        texto = texto.replace(clave, "***")
    # El prefijo de la carpeta de cada script no siempre es el nombre del bot (agip_*).
    for carpeta in sorted(p for p in (cwd / "temp").iterdir() if p.is_dir()) if (cwd / "temp").exists() else []:
        for sub in ("logs", "screenshots", "html", "har", "descargas"):
            origen = carpeta / sub
            if origen.is_dir():
                shutil.copytree(origen, destino / sub, dirs_exist_ok=True)
    (destino / "logs" / f"{bot}_script_stdout.log").write_text(texto, encoding="utf-8")
    shutil.rmtree(cwd, ignore_errors=True)
    return rc, texto[-1500:]
