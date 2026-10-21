"""Modo logger: captura, redacción y subida al bucket logger."""

import asyncio
import logging

import httpx

from bot_worker.main import validation_payload, JobEnvelope
from bot_worker.runtime.logger_capture import LoggerCapture, logger_solicitado


def test_solo_true_booleano_activa():
    assert logger_solicitado({"logger": True})
    for valor in ("true", 1, "1", None, False):
        assert not logger_solicitado({"logger": valor})


def test_plugin_no_ve_la_clave_logger():
    env = JobEnvelope(operation="descargar", payload={"anio": "2026", "logger": True})
    assert "logger" not in validation_payload(env)


def test_captura_redacta_comprime_sube_y_borra(tmp_path):
    import tarfile

    async def correr():
        cap = LoggerCapture(tmp_path / "ev", "job-1", "arba", "descargar", ["Secreta123"])
        cap.activar_log()
        logging.getLogger("x").warning("clave=Secreta123")
        (tmp_path / "ev" / "har" / "arba_1.har").write_text('{"p":"Secreta123"}')
        (tmp_path / "ev" / "html" / "001.html").write_text("<p>Secreta123</p>")
        trabajo = tmp_path / "w"
        trabajo.mkdir()
        (trabajo / "salida.zip").write_bytes(b"zip")
        cap.cerrar_log()
        cap.copiar_artefactos(trabajo)
        cap.redactar()
        cap.escribir_manifest({"outcome": "completado"})
        tar = cap.comprimir()
        subidos = {}
        intentos = {"n": 0}

        async def presign(path, tipo, tam):
            return {"upload_url": f"https://s/{path}"}

        class Cliente:
            async def put(self, url, content, headers, **_):
                intentos["n"] += 1
                if intentos["n"] == 1:
                    raise httpx.WriteTimeout("lento")
                subidos[url] = content
                return httpx.Response(200, request=httpx.Request("PUT", url))

        real_sleep = asyncio.sleep

        async def sin_espera(_):
            await real_sleep(0)

        asyncio.sleep = sin_espera
        try:
            ok = await cap.subir(Cliente(), presign, tar)
        finally:
            asyncio.sleep = real_sleep
        cap.borrar_rastros(tar)
        return ok, subidos, tar

    ok, subidos, tar = asyncio.run(correr())
    assert ok
    contenido = subidos["https://s/job-1.tar.gz"]
    assert not (tmp_path / "ev").exists() and not tar.exists()
    (tmp_path / "x.tgz").write_bytes(contenido)
    with tarfile.open(tmp_path / "x.tgz") as t:
        nombres = t.getnames()
        assert "job-1/descargas/workdir/salida.zip" in nombres
        assert "job-1/manifest.json" in nombres
        for n in ("job-1/har/arba_1.har", "job-1/html/001.html", "job-1/logs/arba.log"):
            datos = t.extractfile(n).read()
            assert b"Secreta123" not in datos
        assert b"***" in t.extractfile("job-1/logs/arba.log").read()


def test_request_json_con_solicitud_de_la_central(tmp_path):
    import json

    async def correr():
        cap = LoggerCapture(tmp_path / "ev", "job-1", "arba", "descargar")
        cap.guardar_solicitud(
            {"payload": {"anio": "2026"}, "credentials_encrypted_rsa_oaep": {"clave": "ENC"}}, {}
        )
    asyncio.run(correr())
    datos = json.loads((tmp_path / "ev" / "request.json").read_text())
    assert datos["credentials_encrypted_rsa_oaep"]["clave"] == "ENC"


def test_descripcion_de_acciones_no_filtra_valores():
    from bot_worker.runtime.logger_capture import _describir

    class Obj:
        def __repr__(self):
            return "<Locator selector='#clave'>"

    assert "Secreta" not in _describir(Obj(), "fill", ("Secreta123",), {})
    assert "valor omitido" in _describir(Obj(), "fill", ("Secreta123",), {})
    assert _describir(Obj(), "goto", ("https://x.gov.ar/a?token=zz",), {}) == "https://x.gov.ar/a"


def test_resultado_del_bot_queda_como_archivo(tmp_path):
    import json

    async def correr():
        cap = LoggerCapture(tmp_path / "ev", "job-1", "sct", "consultar")
        cap.guardar_resultado({"result": "OK", "data": {"n": 1}})
    asyncio.run(correr())
    datos = json.loads((tmp_path / "ev" / "descargas" / "resultado_bot.json").read_text())
    assert datos["data"]["n"] == 1


def test_no_copia_material_criptografico_al_tar(tmp_path):
    async def correr():
        cap = LoggerCapture(tmp_path / "ev", "job-1", "controladores_fiscales", "presentar")
        trabajo = tmp_path / "w"
        trabajo.mkdir()
        (trabajo / "F8010.001.pem").write_text("-----BEGIN PRIVATE KEY-----")
        (trabajo / "ventas.txt").write_text("datos")
        cap.copiar_artefactos(trabajo)
        return cap
    cap = asyncio.run(correr())
    copiados = {p.name for p in (tmp_path / "ev" / "descargas" / "workdir").glob("*")}
    assert copiados == {"ventas.txt"}


def test_logger_script_exige_ambas_claves_true_y_mapea_entorno():
    from bot_worker.runtime.logger_script import SCRIPTS, entorno, script_solicitado, CARPETA

    assert script_solicitado({"logger": True, "logger_script": True})
    assert not script_solicitado({"logger": True}) and not script_solicitado({"logger": True, "logger_script": False})
    assert not script_solicitado({"logger_script": True}) and not script_solicitado({})
    env = entorno("ccma", {"representado_cuit": "20111", "incluir_pdf": True}, "20222", "k")
    assert env["CCMA_CUIT_REPRESENTADO"] == "20111" and env["CCMA_PDF"] == "true"
    for bot, rel in SCRIPTS.items():
        assert (CARPETA / rel).is_file(), rel
