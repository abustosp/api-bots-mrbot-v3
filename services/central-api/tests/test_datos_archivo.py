"""Response de CCMA y RCEL reconstruido desde los CSV guardados en S4."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from central_api import datos_archivo as da
from central_api.settings import get_settings
from mrbot_contracts import datos_csv as d

CUIT = "20326167399"
SALDOS = {
    "cuit": CUIT, "periodo": "04/2000 - 10/2026", "deuda_capital": "885,620.70",
    "deuda_accesorios": "89,450.39", "total_deuda": "975,071.09", "credito_capital": "2,262.05",
    "credito_accesorios": "0.00", "total_a_favor": "2,262.05",
}
MOVS = [
    {"periodo": "10/2026", "impuesto": "AUT", "concepto": "019", "subconcepto": "019",
     "descripcion": "Obligacion | Mensual", "fecha_movimiento": "12/11/2026", "debe": "109,578.51", "haber": ""},
    {"fila": "Detalle | Período | Saldo"},
]
CCMA = {"operacion": "consultar", "representado_cuit": CUIT, "periodo_desde": "04/2000",
        "movimientos_corte": "sin_progreso", "movimientos_completos": False,
        "sha256": "a" * 64, "size_bytes": 10, **SALDOS, "movimientos": MOVS}
FACTURAS = [
    {"archivo": "f1.pdf", "sha256": "b" * 64, "size_bytes": 5,
     "campos": {"CAE": "86406670102479", "Nombre del Receptor": "CUTULI HERNAN\n", "Domicilio": "Peña | PB"}},
    {"archivo": "f2.pdf", "sha256": "c" * 64, "size_bytes": 9, "campos": {"CAE": "1"}},
]
RCEL = {"operacion": "descargar", "representado_cuit": "20263080921", "cantidad": 2, "facturas": FACTURAS}


@pytest.fixture
def ajustes(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://s3.local")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "bucket")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "a")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "b")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _almacen(monkeypatch, archivos: dict[str, str]) -> list[str]:
    leidas: list[str] = []

    def _leer(settings, key, bot=None):
        leidas.append(key)
        return archivos[key].encode("utf-8")

    monkeypatch.setattr(da, "_leer", _leer)
    return leidas


def _artefactos(*ids: str) -> list[dict]:
    return [{"object_key": f"jobs/j1/1/{i}/abc"} for i in ids]


def test_ccma_reconstruye_el_response_original_y_borra_temporales(ajustes, monkeypatch) -> None:
    reducido, archivos = d.separar_ccma(CCMA)
    leidas = _almacen(monkeypatch, {
        "jobs/j1/1/ccma_saldos.csv/abc": archivos["saldos"],
        "jobs/j1/1/ccma_movimientos.csv/abc": archivos["movimientos"],
    })
    salida = da.reconstruir_sync(
        bot="ccma", datos=reducido, artefactos=_artefactos("ccma_saldos.csv", "ccma_movimientos.csv"),
        settings=ajustes,
    )
    assert salida == CCMA
    assert sorted(leidas) == ["jobs/j1/1/ccma_movimientos.csv/abc", "jobs/j1/1/ccma_saldos.csv/abc"]
    assert list(Path(ajustes.backup_dir).iterdir()) == []


def test_rcel_reconstruye_el_response_original(ajustes, monkeypatch) -> None:
    reducido, archivos = d.separar_rcel(RCEL)
    _almacen(monkeypatch, {"jobs/j1/1/rcel_facturas.csv/abc": archivos["facturas"]})
    salida = da.reconstruir_sync(
        bot="rcel", datos=reducido, artefactos=_artefactos("rcel_facturas.csv"), settings=ajustes
    )
    assert salida == RCEL


def test_job_historico_sin_datos_en_archivo_se_devuelve_intacto(ajustes) -> None:
    assert da.aplica("ccma", CCMA) is False and da.aplica("rcel", RCEL) is False
    salida = asyncio.run(da.reconstruir(bot="ccma", datos=CCMA, artefactos=[]))
    assert salida is CCMA


def test_otros_bots_no_se_tocan(ajustes) -> None:
    datos = {"datos_en_archivo": ["x"]}
    assert da.aplica("sifere", datos) is False
    assert asyncio.run(da.reconstruir(bot="sifere", datos=datos, artefactos=[])) is datos


def test_archivo_faltante_informa_y_conserva_el_resto(ajustes) -> None:
    reducido, _ = d.separar_ccma(CCMA)
    salida = da.reconstruir_sync(bot="ccma", datos=reducido, artefactos=[], settings=ajustes)
    assert salida["datos_error"].startswith("archivo_no_disponible:")
    assert salida["representado_cuit"] == CUIT


def test_falla_del_almacenamiento_no_rompe_y_limpia(ajustes, monkeypatch) -> None:
    def _falla(settings, key, bot=None):
        raise RuntimeError("no se pudo leer")

    monkeypatch.setattr(da, "_leer", _falla)
    reducido, _ = d.separar_rcel(RCEL)
    salida = da.reconstruir_sync(
        bot="rcel", datos=reducido, artefactos=_artefactos("rcel_facturas.csv"), settings=ajustes
    )
    assert salida["datos_error"] == "RuntimeError"
    assert list(Path(ajustes.backup_dir).iterdir()) == []


def test_con_reintentos_se_lee_el_ultimo_archivo(ajustes, monkeypatch) -> None:
    reducido, archivos = d.separar_rcel(RCEL)
    leidas = _almacen(monkeypatch, {"jobs/j1/2/rcel_facturas.csv/zzz": archivos["facturas"]})
    artefactos = [
        {"object_key": "jobs/j1/1/rcel_facturas.csv/abc"},
        {"object_key": "jobs/j1/2/rcel_facturas.csv/zzz"},
    ]
    salida = da.reconstruir_sync(bot="rcel", datos=reducido, artefactos=artefactos, settings=ajustes)
    assert leidas == ["jobs/j1/2/rcel_facturas.csv/zzz"] and salida == RCEL


def test_slots_de_subida_ccma_y_rcel_siempre_incluyen_los_csv() -> None:
    from central_api.scheduler.dispatcher import artifact_upload_slots
    from central_api.store import Job, new_job_id

    ccma = artifact_upload_slots(Job(id=new_job_id(), bot="ccma", operation="consultar", payload={}))
    assert [s["artifact_id"] for s in ccma] == ["ccma_saldos.csv", "ccma_movimientos.csv"]
    rcel = artifact_upload_slots(Job(id=new_job_id(), bot="rcel", operation="descargar", payload={}))
    assert [s["artifact_id"] for s in rcel] == ["rcel_facturas.csv"]
    con_pdf = artifact_upload_slots(Job(id=new_job_id(), bot="ccma", operation="consultar",
                                        payload={"incluir_pdf": True, "subir": True}))
    assert [s["artifact_id"] for s in con_pdf][-1] == "ccma_resumen.pdf"


MAPA_ACORDADO = {
    "arba": "retper-iibb-arba",
    "ccma_compensacion": "ccma",
    "carga_portal_iva": "portaliva",
    "libros_portal_iva": "librosiva",
    "vep_archivo": "vep",
    "ccma": "ccma",
    "rcel": "rcel",
}


def test_cada_modulo_usa_el_bucket_acordado(ajustes) -> None:
    for bot, bucket in MAPA_ACORDADO.items():
        assert ajustes.bucket_para_bot(bot) == bucket, bot
    # Con todos sus archivos (CSV, PDF, ZIP) en el mismo bucket del modulo.
    assert ajustes.bucket_para_bot("mis_comprobantes") == "miscomprobantes"


def test_modulos_sin_bucket_definido_usan_el_general(ajustes) -> None:
    for bot in ("apoc", "certificacion_primaria_granos", "compensaciones", "consulta_cuit", "inexistente", None):
        assert ajustes.bucket_para_bot(bot) == "bucket", bot


def test_todo_bot_del_catalogo_tiene_decision(ajustes) -> None:
    """Cada modulo del catalogo esta en el mapa o en la lista explicita de pendientes."""
    from central_api.api.bots import CATALOGUE
    from central_api.buckets import BUCKET_POR_BOT, SIN_BUCKET

    sin_decidir = {str(i["bot"]) for i in CATALOGUE} - set(BUCKET_POR_BOT) - set(SIN_BUCKET)
    assert sin_decidir == set(), sin_decidir


def test_la_lectura_prueba_primero_el_bucket_del_modulo_y_luego_el_general(ajustes) -> None:
    assert ajustes.buckets_de_lectura("ccma") == ["ccma", "bucket"]
    assert ajustes.buckets_de_lectura("apoc") == ["bucket"]


def test_la_lectura_cae_al_general_si_el_objeto_aun_no_se_migro(ajustes, monkeypatch) -> None:
    intentos: list[str] = []

    def _get(**kw):
        intentos.append(kw["bucket"])
        if kw["bucket"] == "ccma":
            raise RuntimeError("no se pudo leer el archivo del almacenamiento")
        return b"periodo|impuesto\n10/2026|AUT\n"

    monkeypatch.setattr(da, "get_object_bytes", _get)
    assert da._leer(ajustes, "jobs/j1/1/ccma_movimientos.csv/abc", "ccma").startswith(b"periodo")
    assert intentos == ["ccma", "bucket"]


def test_si_no_esta_en_ningun_bucket_falla_con_el_ultimo_error(ajustes, monkeypatch) -> None:
    def _get(**kw):
        raise RuntimeError("no se pudo leer el archivo del almacenamiento")

    monkeypatch.setattr(da, "get_object_bytes", _get)
    with pytest.raises(RuntimeError):
        da._leer(ajustes, "jobs/j1/1/x/abc", "rcel")


def test_el_presign_de_subida_usa_el_bucket_del_modulo_del_job(ajustes, monkeypatch) -> None:
    from types import SimpleNamespace

    from central_api.internal import uploads as up

    vistos: list[str] = []
    monkeypatch.setattr(up, "firmar_subida", lambda **kw: (vistos.append(kw["bucket"]), {"upload_url": "u"})[1])
    monkeypatch.setattr(up, "get_settings", lambda: ajustes)

    async def _pedir(bot):
        async def _vivo(job_id, worker):
            return SimpleNamespace(id=job_id, bot=bot, assignment_attempt=1, _origen_db=False, worker_node=worker, status="CORRIENDO")

        monkeypatch.setattr(up, "_job_vivo", _vivo)
        body = up.PresignBody(artifact_id="cualquiera.pdf", content_type="application/pdf", size_bytes=10)
        await up.presign_artifact("j1", body, worker_node="w")

    for bot in ("ccma", "rcel", "arba", "carga_portal_iva", "apoc"):
        asyncio.run(_pedir(bot))
    assert vistos == ["ccma", "rcel", "retper-iibb-arba", "portaliva", "bucket"]
