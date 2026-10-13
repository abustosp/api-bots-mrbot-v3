"""El API nunca devuelve archivos como texto Base64: salen por ``files[]``."""
import json
import re

from fastapi.testclient import TestClient

from central_api.main import create_app

CAMPOS_DE_SALIDA_PROHIBIDOS = ("carga_b64", "incluir_base64", "archivo_historico_b64", "incluir_b64")


def _openapi() -> dict:
    return TestClient(create_app()).get("/openapi.json").json()


def test_el_openapi_no_expone_flags_de_salida_en_base64():
    texto = json.dumps(_openapi())
    for campo in CAMPOS_DE_SALIDA_PROHIBIDOS:
        assert campo not in texto, campo


def test_ninguna_respuesta_documentada_ofrece_archivos_en_base64():
    """Ningun schema de respuesta (2xx) declara campos *_b64 de salida."""
    spec = _openapi()
    encontrados = []

    def _buscar(nodo, ruta):
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                if isinstance(clave, str) and re.search(r"(^|_)b64$|base64", clave, re.I):
                    encontrados.append(f"{ruta}/{clave}")
                _buscar(valor, f"{ruta}/{clave}")
        elif isinstance(nodo, list):
            for i, item in enumerate(nodo):
                _buscar(item, f"{ruta}[{i}]")

    for path, metodos in spec["paths"].items():
        for metodo, op in metodos.items():
            for codigo, resp in (op.get("responses") or {}).items():
                if str(codigo).startswith("2"):
                    _buscar(resp, f"{metodo.upper()} {path} {codigo}")
    assert not encontrados, encontrados


def test_un_cliente_v2_que_envia_flags_b64_sigue_siendo_aceptado(monkeypatch):
    from fastapi.responses import JSONResponse

    from central_api.api import bot_routes

    enviados = []

    async def fake_submit_job(**kwargs):
        enviados.append(kwargs)
        return JSONResponse(status_code=202, content={"success": True, "job_id": "j", "status": "PENDIENTE"})

    async def sin_storage(*args, **kwargs):
        return args[2]

    monkeypatch.setattr(bot_routes, "submit_job", fake_submit_job)
    monkeypatch.setattr(bot_routes, "externalizar_archivos", sin_storage)
    cuerpo = {
        "desde": "01/01/2026", "hasta": "31/01/2026", "cuit_inicio_sesion": "20123456786",
        "representado_nombre": "X", "representado_cuit": "20123456786", "contrasena": "c",
        "descarga_emitidos": True, "descarga_recibidos": False, "carga_b64": True,
    }
    r = TestClient(create_app()).post(
        "/api/v3/mis_comprobantes/consulta", headers={"Idempotency-Key": "b64-legacy"}, json=cuerpo
    )
    assert r.status_code == 202, r.text
    assert "carga_b64" not in enviados[0]["payload"]
