"""JSON de Mis Comprobantes reconstruido desde el ZIP guardado en S4."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from central_api import mc_json
from central_api.settings import get_settings
from mrbot_contracts.planillas import (
    MAX_BYTES_DESCOMPRIMIDOS,
    empaquetar_zip,
    reconstruir_filas_desde_zip,
)

CUIT = "20320160856"
# Encabezado real del ZIP de emitidos de ARCA (misma lista que usa el worker).
from mrbot_contracts.planillas import COLUMNAS_MCE  # noqa: E402


def _zip_emitidos(filas: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\n")
    escritor.writerow(COLUMNAS_MCE)
    for datos in filas:
        fila = [""] * len(COLUMNAS_MCE)
        for indice, nombre in enumerate(COLUMNAS_MCE):
            fila[indice] = datos.get(nombre, "0" if "Imp" in nombre else "")
        escritor.writerow(fila)
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as paquete:
        paquete.writestr("Mis Comprobantes Emitidos.csv", buffer.getvalue())
    return salida.getvalue()


def _fila(fecha: str, tipo: str = "11", numero: str = "5") -> dict[str, str]:
    return {
        COLUMNAS_MCE[0]: fecha,
        COLUMNAS_MCE[1]: tipo,
        COLUMNAS_MCE[2]: "1",
        COLUMNAS_MCE[3]: numero,
        COLUMNAS_MCE[4]: numero,
    }


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


def _datos(filas: int = 2) -> dict:
    return {
        "operacion": "consultar",
        "representado_cuit": CUIT,
        "fecha_desde": "01/01/2026",
        "fecha_hasta": "31/01/2026",
        "emitidos": {"filas": filas, "archivo": "x.csv", "zip": {"archivo": "x.zip"}},
    }


ARTEFACTOS = [{"object_key": "jobs/j1/1/emitidos_zip/abc123"}]


def test_pide_json_solo_con_carga_json_o_incluir_json_verdaderos() -> None:
    assert mc_json.pide_json({"carga_json": True}) is True
    assert mc_json.pide_json({"incluir_json": True}) is True
    assert mc_json.pide_json({"carga_json": False}) is False
    assert mc_json.pide_json({}) is False
    assert mc_json.pide_json(None) is False


def test_aplica_solo_a_mis_comprobantes_con_carga_json() -> None:
    assert mc_json.aplica("mis_comprobantes", "consultar", {"carga_json": True})
    assert mc_json.aplica("mis_comprobantes", "historial", {"incluir_json": True})
    assert not mc_json.aplica("mis_comprobantes", "solicitar", {"carga_json": True})
    assert not mc_json.aplica("mis_comprobantes", "consultar", {"carga_json": False})
    assert not mc_json.aplica("rcel", "descargar", {"carga_json": True})


def test_reconstruye_filas_con_auxiliares_y_borra_el_temporal(ajustes, monkeypatch, tmp_path) -> None:
    zip_bytes = _zip_emitidos([_fila("2026-01-10", numero="7"), _fila("2026-01-20", numero="8")])
    leidas: list[str] = []
    monkeypatch.setattr(mc_json, "_leer_zip", lambda s, key: (leidas.append(key), zip_bytes)[1])

    salida = mc_json.reconstruir_sync(
        payload={"carga_json": True}, datos=_datos(), artefactos=ARTEFACTOS, settings=ajustes
    )

    resumen = salida["emitidos"]
    assert leidas == ["jobs/j1/1/emitidos_zip/abc123"]
    assert resumen["filas"] == 2 and resumen["truncado"] is False
    assert [i["MC"] for i in resumen["items"]] == ["MCE", "MCE"]
    assert resumen["items"][0]["CUIT Cliente"] == CUIT
    assert "MC AUX" in resumen["items"][0] and "Factura AUX" in resumen["items"][0]
    assert "json_error" not in resumen
    # El directorio temporal desaparece siempre.
    assert list(Path(ajustes.backup_dir).iterdir()) == []


def test_aplica_el_filtro_de_rango_igual_que_el_worker(ajustes, monkeypatch) -> None:
    zip_bytes = _zip_emitidos([_fila("2026-01-10"), _fila("2026-03-01")])
    monkeypatch.setattr(mc_json, "_leer_zip", lambda s, key: zip_bytes)
    resumen = mc_json.reconstruir_sync(
        payload={}, datos=_datos(), artefactos=ARTEFACTOS, settings=ajustes
    )["emitidos"]
    assert resumen["filas"] == 1


def test_trunca_al_limite_y_lo_informa(ajustes, monkeypatch) -> None:
    zip_bytes = _zip_emitidos([_fila("2026-01-10", numero=str(n)) for n in range(5)])
    monkeypatch.setattr(mc_json, "_leer_zip", lambda s, key: zip_bytes)
    resumen = mc_json.reconstruir_sync(
        payload={}, datos=_datos(), artefactos=ARTEFACTOS, limite=2, settings=ajustes
    )["emitidos"]
    assert len(resumen["items"]) == 2 and resumen["filas"] == 5 and resumen["truncado"] is True


def test_sin_zip_informa_json_error_y_no_rompe(ajustes) -> None:
    salida = mc_json.reconstruir_sync(
        payload={}, datos=_datos(), artefactos=[], settings=ajustes
    )
    assert salida["emitidos"]["json_error"] == "zip_no_disponible"
    assert salida["emitidos"]["filas"] == 2


def test_falla_del_almacenamiento_no_rompe_y_limpia(ajustes, monkeypatch) -> None:
    def _falla(settings, key):
        raise RuntimeError("no se pudo leer el archivo del almacenamiento")

    monkeypatch.setattr(mc_json, "_leer_zip", _falla)
    salida = mc_json.reconstruir_sync(
        payload={}, datos=_datos(), artefactos=ARTEFACTOS, settings=ajustes
    )
    assert salida["emitidos"]["json_error"] == "RuntimeError"
    assert list(Path(ajustes.backup_dir).iterdir()) == []


def test_zip_corrupto_informa_error_y_limpia(ajustes, monkeypatch) -> None:
    monkeypatch.setattr(mc_json, "_leer_zip", lambda s, key: b"no es un zip ni un csv")
    salida = mc_json.reconstruir_sync(
        payload={}, datos=_datos(), artefactos=ARTEFACTOS, settings=ajustes
    )
    assert "json_error" in salida["emitidos"]
    assert list(Path(ajustes.backup_dir).iterdir()) == []


def test_no_modifica_ni_persiste_el_dato_original(ajustes, monkeypatch) -> None:
    zip_bytes = _zip_emitidos([_fila("2026-01-10")])
    monkeypatch.setattr(mc_json, "_leer_zip", lambda s, key: zip_bytes)
    original = _datos()
    copia = {k: (dict(v) if isinstance(v, dict) else v) for k, v in original.items()}
    mc_json.reconstruir_sync(payload={}, datos=original, artefactos=ARTEFACTOS, settings=ajustes)
    assert original == copia and "items" not in original["emitidos"]


def test_reconstruir_async_no_toca_otros_bots(ajustes) -> None:
    import asyncio

    datos = {"x": 1}
    salida = asyncio.run(
        mc_json.reconstruir(
            bot="rcel", operacion="descargar", payload={"carga_json": True},
            datos=datos, artefactos=[],
        )
    )
    assert salida is datos


def test_zip_bomba_se_rechaza(tmp_path) -> None:
    grande = tmp_path / "bomba.zip"
    with zipfile.ZipFile(grande, "w", zipfile.ZIP_DEFLATED) as paquete:
        paquete.writestr("x.csv", "a;b\n" + "0;0\n" * (MAX_BYTES_DESCOMPRIMIDOS // 4 + 10))
    trabajo = tmp_path / "t"
    trabajo.mkdir()
    with pytest.raises(ValueError, match="límite"):
        reconstruir_filas_desde_zip(
            grande, tipo="emitidos", cuit=CUIT, desde="01/01/2026", hasta="31/01/2026", trabajo=trabajo
        )


def test_empaquetar_zip_copia_el_zip_y_envuelve_un_csv(tmp_path) -> None:
    zip_bytes = _zip_emitidos([_fila("2026-01-10")])
    origen = tmp_path / "crudo.csv"
    origen.write_bytes(zip_bytes)
    destino = empaquetar_zip(origen, tmp_path / "salida.zip")
    assert destino.read_bytes() == zip_bytes

    csv_directo = tmp_path / "directo.csv"
    csv_directo.write_text("a;b\n1;2\n", encoding="utf-8")
    envuelto = empaquetar_zip(csv_directo, tmp_path / "envuelto.zip")
    with zipfile.ZipFile(envuelto) as paquete:
        assert paquete.namelist() == ["directo.csv"]
        assert paquete.read("directo.csv") == b"a;b\n1;2\n"


def test_job_historico_con_muestra_y_sin_zip_queda_intacto(ajustes) -> None:
    datos = _datos()
    datos["emitidos"] = {"filas": 1, "archivo": "x.csv", "muestra": [{"MC": "MCE"}]}
    salida = mc_json.reconstruir_sync(
        payload={}, datos=datos, artefactos=[], settings=ajustes
    )
    assert salida["emitidos"] == datos["emitidos"]
    assert "json_error" not in salida["emitidos"]
