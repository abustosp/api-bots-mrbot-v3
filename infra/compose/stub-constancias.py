#!/usr/bin/env python3
"""Stub local del servicio de constancias (SOLO desarrollo).

Emula ``api-constancias-de-inscripcion.mrbot.com.ar`` para el ciclo E2E
local, donde no hay salida a internet. Responde constancias ficticias con
forma de dict (lo que el plugin espera) sin salir de la red ``control``.

Uso (desde ``infra/compose``):
    docker compose --profile local-storage --profile stub up -d
El worker apunta aquí con ``CONSULTA_CUIT_BASE_URL`` /
``CONSULTA_CUIT_MASIVA_URL`` (ver ``docker-compose.yml``). Nunca en
producción: el plugin usa el endpoint público por defecto.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PUERTO = 8081


def _constancia(cuit: str) -> dict:
    return {
        "cuit": cuit,
        "denominacion": f"CONTRIBUYENTE STUB {cuit}",
        "estado": "ACTIVO",
        "domicilio": "Calle Ficticia 123",
        "impuestos": ["IVA", "GANANCIAS"],
        "origen": "stub-local",
    }


class Manejador(BaseHTTPRequestHandler):
    server_version = "StubConstancias/1.0"

    def _responder(self, cuerpo: object, codigo: int = 200) -> None:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self) -> None:  # noqa: N802 - firma de BaseHTTPRequestHandler
        ruta = urlparse(self.path)
        if not ruta.path.rstrip("/").endswith("consulta_constancia"):
            self._responder({"error": "ruta desconocida"}, 404)
            return
        cuit = (parse_qs(ruta.query).get("cuit") or [""])[0]
        digitos = "".join(c for c in cuit if c.isdigit())
        if len(digitos) != 11:
            self._responder({"error": "cuit debe tener 11 digitos"}, 422)
            return
        self._responder(_constancia(digitos))

    def do_POST(self) -> None:  # noqa: N802 - firma de BaseHTTPRequestHandler
        ruta = urlparse(self.path)
        if not ruta.path.rstrip("/").endswith("consulta_constancia_masiva"):
            self._responder({"error": "ruta desconocida"}, 404)
            return
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            cuerpo = json.loads(self.rfile.read(largo).decode("utf-8") or "{}")
        except ValueError:
            self._responder({"error": "JSON invalido"}, 422)
            return
        cuits = cuerpo.get("cuits") or []
        self._responder(
            {"resultados": [
                _constancia("".join(c for c in str(c) if c.isdigit()))
                for c in cuits[:100]
            ]}
        )

    def log_message(self, formato: str, *args: object) -> None:
        pass  # salud por /listo, sin ruido


if __name__ == "__main__":
    servidor = HTTPServer(("0.0.0.0", PUERTO), Manejador)
    print(f"stub-constancias en :{PUERTO} (solo desarrollo)", flush=True)
    servidor.serve_forever()
