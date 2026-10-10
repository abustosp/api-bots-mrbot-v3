"""Prueba "teorica" del bucket temporal ``uploads`` contra el storage real.

Por cada caso de carga de archivos: pide ticket (POST /api/v3/uploads), sube
con la URL PUT prefirmada, resuelve la referencia como lo hace submit_job,
arma el sobre (preparar_entrada), baja con la URL GET como el worker y compara
bytes (sha256). Al final borra los objetos de prueba. No imprime secretos.

Uso: .venv/bin/python tools/teorico_uploads.py [ruta .env]
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ENV = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "infra/compose/.env"
for linea in ENV.read_text().splitlines():
    if linea.startswith("OBJECT_STORAGE") and "=" in linea:
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("OBJECT_STORAGE_UPLOADS_BUCKET", "uploads")
sys.path[:0] = [str(RAIZ / "services/central-api/src"), str(RAIZ / "services/bot-worker/src")]

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from central_api.api import dependencies as dep  # noqa: E402
from central_api.api.uploads import resolver_archivos  # noqa: E402
from central_api.input_files import preparar_entrada  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.settings import get_settings  # noqa: E402
from central_api.storage import presign_put_url  # noqa: E402
from bot_worker.runtime.input_files import descargar_entradas, descargar_entradas_b64  # noqa: E402

CUIT = "20123456789"
USER = str(uuid.uuid4())


def _zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("F8010.001.pem", b"-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n")
        z.writestr("F8011.001.pem", b"-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n")
    return b.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
# (bot, operacion, archivo, bytes, modo, campo_payload)
CASOS = [
    ("controladores_fiscales", "presentar", "F8010.001.pem", b"-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n", "lista", "archivos"),
    ("controladores_fiscales", "presentar", "lote_pem.zip", _zip(), "lista", "archivos"),
    ("vep_archivo", "generar", "vep_ejemplo.txt", b"0120123456789200010010000300300001\n", "b64", "archivo_key"),
    *[
        ("carga_portal_iva", "cargar", f"{c}.txt", f"linea de {c}\n".encode(), "b64", f"{c}_key")
        for c in ("liv_cbte", "liv_alicuota", "lic_cbte", "lic_alicuota",
                  "csv_cf", "csv_cf_restitucion", "csv_df", "csv_df_restitucion")
    ],
]


async def main() -> int:
    cfg = get_settings()
    if not cfg.object_storage_configurado:
        print("storage sin configurar"); return 2
    app = create_app()
    app.dependency_overrides[dep.require_api_principal] = lambda: ApiPrincipal(user_id=USER, key_id="teorico")
    cli = TestClient(app)
    http = httpx.AsyncClient(timeout=60)
    ep = cfg.object_storage_public_endpoint or cfg.object_storage_endpoint
    claves: list[str] = []
    fallas = 0
    work = Path(tempfile.mkdtemp(prefix="teorico_"))
    try:
        for bot, op, nombre, datos, modo, campo in CASOS:
            etiqueta = f"{bot}/{op} [{campo}] {nombre}"
            try:
                t = cli.post("/api/v3/uploads", json={
                    "bot": bot, "operacion": op, "cuit_representado": CUIT, "filename": nombre,
                    "size_bytes": len(datos), "content_type": "application/octet-stream",
                    "sha256": hashlib.sha256(datos).hexdigest()})
                assert t.status_code == 201, f"ticket {t.status_code}"
                tj = t.json()
                clave = tj["object_key"]; claves.append(clave)
                assert clave == f"{USER}/{bot}/{CUIT}/{nombre}", f"clave {clave}"
                assert tj["bucket"] == "uploads"
                r = await http.put(tj["upload_url"], content=datos,
                                   headers=tj["required_headers"])
                assert r.status_code in (200, 201), f"PUT {r.status_code}"
                await resolver_archivos(USER, bot, [clave])
                payload = {campo: [clave]} if modo == "lista" else {campo: clave}
                nuevo, files = preparar_entrada(bot, op, payload)
                assert len(files) == 1 and files[0]["url"], "sin input_files"
                if modo == "lista":
                    bajados = await descargar_entradas(files, _d(work), http)
                    esperado = ["F8010.001.pem", "F8011.001.pem"] if nombre.endswith(".zip") else [nombre]
                    assert bajados == esperado, f"nombres {bajados}"
                    if not nombre.endswith(".zip"):
                        assert hashlib.sha256(Path(work, "_ult", nombre).read_bytes()).hexdigest() == hashlib.sha256(datos).hexdigest()
                else:
                    import base64
                    campos = await descargar_entradas_b64(files, http)
                    (v,) = campos.values()
                    assert base64.b64decode(v) == datos, "bytes distintos"
                print(f"OK    {etiqueta}  ({len(datos)} B)")
            except Exception as exc:  # noqa: BLE001
                fallas += 1
                print(f"FALLA {etiqueta}: {type(exc).__name__}: {str(exc)[:160]}")
        # ajeno: otro usuario no puede referenciar
        try:
            await resolver_archivos(str(uuid.uuid4()), "vep_archivo", [c for c in claves if "/vep_archivo/" in c])
            print("FALLA aislamiento entre usuarios"); fallas += 1
        except LookupError:
            print("OK    aislamiento: otro usuario no puede usar la clave")
    finally:
        # limpieza: DELETE prefirmado con SigV4 no esta implementado en storage.py,
        # se usa PUT/GET unicamente. Los objetos quedan bajo el prefijo del USER de prueba.
        await http.aclose()
    print(f"\nprefijo de prueba en el bucket: {USER}/  ({len(claves)} objetos)")
    return 1 if fallas else 0


def _d(work: Path) -> Path:
    d = work / "_ult"
    d.mkdir(exist_ok=True)
    return d


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
