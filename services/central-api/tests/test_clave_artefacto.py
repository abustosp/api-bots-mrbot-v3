"""Layout de objetos: ``{bucket}/jobs/{job_id}/{archivo}`` (sin subdirectorio de intento)."""

from __future__ import annotations

from central_api import datos_archivo, mc_json
from central_api.internal.uploads import clave_artefacto

J = "01a1311e-5fcb-7e52-822e-01840c992f2e"


def test_la_clave_cuelga_directo_del_job_con_el_nombre_del_archivo() -> None:
    assert clave_artefacto(J, "ccma_saldos.csv", "ccma_saldos_20374730429.csv") == f"jobs/{J}/ccma_saldos_20374730429.csv"
    assert clave_artefacto(J, "ccma_resumen.pdf", "ccma_resumen_20374730429.pdf") == f"jobs/{J}/ccma_resumen_20374730429.pdf"


def test_el_nombre_no_puede_escapar_del_job() -> None:
    k = clave_artefacto(J, "x", "../../otro/../x.csv")
    assert k == f"jobs/{J}/x.csv" and k.count("/") == 2
    assert clave_artefacto(J, "x", "a\\b\\c.csv") == f"jobs/{J}/c.csv"


def test_sin_nombre_se_usa_el_artifact_id_con_sufijo_unico() -> None:
    a, b = clave_artefacto(J, "informe", None), clave_artefacto(J, "informe", None)
    assert a.startswith(f"jobs/{J}/informe-") and a != b and a.count("/") == 2


def test_ccma_lee_por_nombre_en_el_layout_nuevo_y_en_el_historico() -> None:
    nuevo = [{"object_key": f"jobs/{J}/ccma_saldos_20374730429.csv"},
             {"object_key": f"jobs/{J}/ccma_movimientos_20374730429.csv"},
             {"object_key": f"jobs/{J}/ccma_resumen_20374730429.pdf"}]
    assert datos_archivo._clave_de(nuevo, "ccma_saldos.csv").endswith("ccma_saldos_20374730429.csv")
    assert datos_archivo._clave_de(nuevo, "ccma_movimientos.csv").endswith("ccma_movimientos_20374730429.csv")
    viejo = [{"object_key": f"jobs/{J}/1/ccma_saldos.csv/abc"}]
    assert datos_archivo._clave_de(viejo, "ccma_saldos.csv") == f"jobs/{J}/1/ccma_saldos.csv/abc"
    assert datos_archivo._clave_de([], "ccma_saldos.csv") is None


def test_rcel_lee_por_nombre() -> None:
    arts = [{"object_key": f"jobs/{J}/rcel_facturas_20123456789.csv"}]
    assert datos_archivo._clave_de(arts, "rcel_facturas.csv").endswith("rcel_facturas_20123456789.csv")


def test_mc_distingue_emitidos_de_recibidos_por_el_nombre_del_zip() -> None:
    arts = [{"object_key": f"jobs/{J}/6 - MCE - 01012026 - 31012026 - 20320160856 - Empresa.zip"},
            {"object_key": f"jobs/{J}/6 - MCR - 01012026 - 31012026 - 20320160856 - Empresa.zip"},
            {"object_key": f"jobs/{J}/6 - MCE - 01012026 - 31012026 - 20320160856 - Empresa.csv"}]
    assert " - MCE - " in mc_json._zip_de(arts, "emitidos")["object_key"] and mc_json._zip_de(arts, "emitidos")["object_key"].endswith(".zip")
    assert " - MCR - " in mc_json._zip_de(arts, "recibidos")["object_key"]
    assert mc_json._zip_de([{"object_key": f"jobs/{J}/1/emitidos_zip/abc"}], "emitidos")["object_key"].endswith("/abc")
