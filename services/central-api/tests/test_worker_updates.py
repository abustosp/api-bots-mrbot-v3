"""Botón "Actualizar" de workers manuales: solicitudes, cupo, drenaje y updater."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

ADMIN = {"Authorization": "Bearer admin-upd"}
UPDATER = {"Authorization": "Bearer updater-secret"}
IMAGEN = "registry.local/mrbot-bot-worker:2.0.0"


@pytest.fixture
def flota(monkeypatch):
    from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, JOBS, PAUSED_NODES, WORKERS, WorkerEntry
    from central_api.worker_updates import UPDATE_REQUESTS

    monkeypatch.setenv("ADMIN_TOKEN", "admin-upd")
    monkeypatch.setenv("UPDATER_TOKEN", "updater-secret")
    monkeypatch.setenv("WORKER_NODES", "viejo-a:8080,viejo-b:8080,nuevo:8080")
    monkeypatch.setenv("WORKER_IMAGE_REF", IMAGEN)
    monkeypatch.setenv("WORKER_LATEST_VERSION", "2.0.0")
    monkeypatch.setenv("WORKER_UPDATE_DRAIN_SECONDS", "180")
    get_settings.cache_clear()
    previo = (dict(JOBS), dict(WORKERS), set(ADMIN_NODES), dict(EPHEMERAL_WORKERS), set(PAUSED_NODES))
    for coleccion in (JOBS, WORKERS, ADMIN_NODES, EPHEMERAL_WORKERS, PAUSED_NODES, UPDATE_REQUESTS):
        coleccion.clear()
    for nodo, version in (("viejo-a:8080", "1.0.0"), ("viejo-b:8080", "1.0.0"), ("nuevo:8080", "2.0.0")):
        WORKERS[nodo] = WorkerEntry(node=nodo, status="SANO", app_version=version)
    yield TestClient(create_app())
    for coleccion in (JOBS, WORKERS, ADMIN_NODES, EPHEMERAL_WORKERS, PAUSED_NODES, UPDATE_REQUESTS):
        coleccion.clear()
    JOBS.update(previo[0])
    WORKERS.update(previo[1])
    ADMIN_NODES.update(previo[2])
    EPHEMERAL_WORKERS.update(previo[3])
    PAUSED_NODES.update(previo[4])
    get_settings.cache_clear()


def _comando(cliente, nodo):
    r = cliente.get("/internal/v1/updater/command", params={"node": nodo}, headers=UPDATER)
    assert r.status_code == 200
    return r.json()


def test_actualizar_un_worker_recorre_el_ciclo_completo(flota) -> None:
    from central_api.store import PAUSED_NODES

    r = flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["actualizacion"]["estado"] == "ENCOLADA"

    comando = _comando(flota, "viejo-a:8080")
    assert comando["action"] == "update"
    assert comando["image"] == IMAGEN and comando["app_version"] == "2.0.0"
    assert "viejo-a:8080" in PAUSED_NODES

    fila = next(f for f in flota.get("/admin/fleet", headers=ADMIN).json()["manuales"] if f["node"] == "viejo-a:8080")
    assert fila["estado"] == "ACTUALIZANDO"

    r = flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "HECHO"},
    )
    assert r.status_code == 200 and r.json()["estado"] == "HECHO"
    assert "viejo-a:8080" not in PAUSED_NODES
    assert _comando(flota, "viejo-a:8080") == {"action": "none"}


def test_espera_que_terminen_los_jobs_en_vuelo(flota) -> None:
    from central_api.store import WORKERS
    from central_api.worker_updates import UPDATE_REQUESTS

    WORKERS["viejo-a:8080"].running_jobs = 2
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    assert _comando(flota, "viejo-a:8080")["action"] == "none"
    assert UPDATE_REQUESTS["viejo-a:8080"].status == "DRENANDO"

    WORKERS["viejo-a:8080"].running_jobs = 0
    assert _comando(flota, "viejo-a:8080")["action"] == "update"


def test_vence_el_drenaje_y_libera_el_comando(flota) -> None:
    from central_api.store import WORKERS
    from central_api.worker_updates import UPDATE_REQUESTS

    WORKERS["viejo-a:8080"].running_jobs = 5
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    assert _comando(flota, "viejo-a:8080")["action"] == "none"
    UPDATE_REQUESTS["viejo-a:8080"].drain_started_at -= timedelta(seconds=181)
    assert _comando(flota, "viejo-a:8080")["action"] == "update"


def test_actualizar_todos_solo_encola_los_desactualizados_y_respeta_el_cupo(flota) -> None:
    r = flota.post("/admin/fleet/update-all", json={"motivo": "actualización masiva"}, headers=ADMIN)
    assert r.status_code == 200
    cuerpo = r.json()
    assert {e["node"] for e in cuerpo["encoladas"]} == {"viejo-a:8080", "viejo-b:8080"}
    assert cuerpo["omitidas"] == [{"node": "nuevo:8080", "motivo": "ya está actualizado"}]

    # WORKER_UPDATE_MAX_PARALLEL=1: el segundo espera a que termine el primero.
    primero = _comando(flota, "viejo-a:8080")
    assert primero["action"] == "update"
    assert _comando(flota, "viejo-b:8080") == {"action": "none", "estado": "ENCOLADA"}
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": primero["request_id"], "estado": "HECHO"},
    )
    assert _comando(flota, "viejo-b:8080")["action"] == "update"

    repetido = flota.post("/admin/fleet/update-all", json={"motivo": "actualización masiva"}, headers=ADMIN).json()
    assert [o["motivo"] for o in repetido["omitidas"] if o["node"] == "viejo-b:8080"] == ["actualización en curso"]


def test_fallo_del_updater_reanuda_el_worker_y_queda_visible(flota) -> None:
    from central_api.store import PAUSED_NODES

    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    comando = _comando(flota, "viejo-a:8080")
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "REVERTIDO", "error": "unhealthy"},
    )
    assert "viejo-a:8080" not in PAUSED_NODES
    fila = next(f for f in flota.get("/admin/fleet", headers=ADMIN).json()["manuales"] if f["node"] == "viejo-a:8080")
    assert fila["actualizacion"]["estado"] == "REVERTIDO" and fila["actualizacion"]["error"] == "unhealthy"


def test_no_reanuda_un_worker_que_el_admin_ya_tenia_pausado(flota) -> None:
    from central_api.store import PAUSED_NODES

    PAUSED_NODES.add("viejo-a:8080")
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    comando = _comando(flota, "viejo-a:8080")
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "HECHO"},
    )
    assert "viejo-a:8080" in PAUSED_NODES


def test_validaciones_y_autenticacion(flota, monkeypatch) -> None:
    assert flota.post("/admin/workers/viejo-a:8080/update").status_code == 401
    assert flota.post("/admin/workers/desconocido:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN).status_code == 404
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    assert flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN).status_code == 409
    assert flota.post("/admin/workers/viejo-b:8080/update", json={"motivo": "corto"}, headers=ADMIN).status_code == 400

    assert flota.get("/internal/v1/updater/command", params={"node": "viejo-a:8080"}).status_code == 401
    mal = {"Authorization": "Bearer otro"}
    assert flota.get("/internal/v1/updater/command", params={"node": "viejo-a:8080"}, headers=mal).status_code == 403
    r = flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": "no-existe", "estado": "HECHO"},
    )
    assert r.status_code == 409

    monkeypatch.setenv("WORKER_IMAGE_REF", "")
    get_settings.cache_clear()
    assert flota.post("/admin/workers/viejo-b:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN).status_code == 409


def test_los_efimeros_no_se_actualizan_desde_este_boton(flota) -> None:
    from central_api.store import EPHEMERAL_WORKERS

    EPHEMERAL_WORKERS["i-123"] = {"instance_id": "i-123", "node": "viejo-b:8080"}
    r = flota.post("/admin/workers/viejo-b:8080/update", json={"motivo": "subir a la 2.0.0"}, headers=ADMIN)
    assert r.status_code == 404
    todos = flota.post("/admin/fleet/update-all", json={"motivo": "actualización masiva"}, headers=ADMIN).json()
    assert {e["node"] for e in todos["encoladas"]} == {"viejo-a:8080"}


def test_el_panel_incluye_los_botones(flota) -> None:
    html = flota.get("/admin/").text
    assert 'id="fleet-update-all"' in html
    assert 'data-action="worker-update"' in html


def _fila(cliente, nodo):
    return next(f for f in cliente.get("/admin/fleet", headers=ADMIN).json()["manuales"] if f["node"] == nodo)


def test_solicitud_sin_updater_vence_y_el_panel_deja_de_mostrar_actualizando(flota) -> None:
    from central_api.store import utcnow
    from central_api.worker_updates import UPDATE_REQUESTS

    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "sin updater en el host"}, headers=ADMIN)
    assert _fila(flota, "viejo-a:8080")["estado"] == "ACTUALIZANDO"
    UPDATE_REQUESTS["viejo-a:8080"].created_at = utcnow() - timedelta(hours=2)
    fila = _fila(flota, "viejo-a:8080")
    assert fila["estado"] != "ACTUALIZANDO"
    assert fila["actualizacion"]["estado"] == "FALLIDO"
    assert "worker-updater" in fila["actualizacion"]["error"]


def test_solicitud_se_cierra_sola_si_el_worker_ya_esta_en_la_version_objetivo(flota) -> None:
    from central_api.store import WORKERS

    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "se actualiza a mano"}, headers=ADMIN)
    WORKERS["viejo-a:8080"].app_version = "2.0.0"  # el operador lo actualizó por compose
    fila = _fila(flota, "viejo-a:8080")
    assert fila["estado"] != "ACTUALIZANDO"
    assert fila["actualizacion"]["estado"] == "HECHO"


def test_cancelar_una_solicitud_encolada(flota) -> None:
    from central_api.store import PAUSED_NODES

    assert flota.delete("/admin/workers/viejo-a:8080/update").status_code in (401, 403)
    assert flota.delete("/admin/workers/viejo-a:8080/update", headers=ADMIN).status_code == 409  # nada que cancelar
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "cancelar después"}, headers=ADMIN)
    r = flota.delete("/admin/workers/viejo-a:8080/update", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["actualizacion"]["estado"] == "FALLIDO" and "cancelada" in r.json()["actualizacion"]["error"]
    assert "viejo-a:8080" not in PAUSED_NODES
    # y se puede pedir otra
    assert flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "reintentar la subida"}, headers=ADMIN).status_code == 200


def test_no_se_puede_cancelar_una_actualizacion_que_el_updater_ya_ejecuta(flota) -> None:
    flota.post("/admin/workers/viejo-a:8080/update", json={"motivo": "ya en ejecución"}, headers=ADMIN)
    assert _comando(flota, "viejo-a:8080")["action"] == "update"  # pasa a ACTUALIZANDO
    assert flota.delete("/admin/workers/viejo-a:8080/update", headers=ADMIN).status_code == 409


def _auto(cliente, nodo):
    r = cliente.get("/internal/v1/updater/command", params={"node": nodo, "auto": "true"}, headers=UPDATER)
    assert r.status_code == 200
    return r.json()


def test_auto_pausa_al_instante_espera_ocioso_y_actualiza(flota) -> None:
    from central_api.store import PAUSED_NODES, WORKERS

    WORKERS["viejo-a:8080"].running_jobs = 1
    assert _auto(flota, "viejo-a:8080") == {"action": "none", "estado": "DRENANDO"}
    assert "viejo-a:8080" in PAUSED_NODES  # desactualizado: sin jobs nuevos
    WORKERS["viejo-a:8080"].running_jobs = 0
    comando = _auto(flota, "viejo-a:8080")
    assert comando["action"] == "update" and comando["app_version"] == "2.0.0"
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "HECHO"},
    )
    assert "viejo-a:8080" not in PAUSED_NODES


def test_auto_no_fuerza_el_drenaje_por_tiempo(flota) -> None:
    from central_api.store import WORKERS
    from central_api.worker_updates import UPDATE_REQUESTS

    WORKERS["viejo-a:8080"].running_jobs = 1
    _auto(flota, "viejo-a:8080")
    UPDATE_REQUESTS["viejo-a:8080"].drain_started_at -= timedelta(hours=2)
    assert _auto(flota, "viejo-a:8080")["action"] == "none"


def test_auto_ignora_dev_y_nodos_al_dia(flota, monkeypatch) -> None:
    from central_api.store import PAUSED_NODES, WORKERS

    assert _auto(flota, "nuevo:8080") == {"action": "none"}  # ya en la última
    WORKERS["viejo-b:8080"].app_version = "3.0.0-dev"
    assert _auto(flota, "viejo-b:8080") == {"action": "none"}  # instalada -dev
    monkeypatch.setenv("WORKER_IMAGE_REF", "registry.local/mrbot-bot-worker:3.0.0-dev")
    monkeypatch.setenv("WORKER_LATEST_VERSION", "3.0.0-dev")
    get_settings.cache_clear()
    assert _auto(flota, "viejo-a:8080") == {"action": "none"}  # objetivo -dev
    assert not PAUSED_NODES


def test_auto_no_reintenta_la_misma_version_tras_fallar(flota) -> None:
    comando = _auto(flota, "viejo-a:8080")
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "REVERTIDO", "error": "x"},
    )
    assert _auto(flota, "viejo-a:8080") == {"action": "none"}


def test_sin_flag_auto_no_se_crea_solicitud(flota) -> None:
    assert _comando(flota, "viejo-a:8080") == {"action": "none"}


def test_auto_respeta_el_cupo_y_no_pausa_toda_la_flota(flota) -> None:
    from central_api.store import PAUSED_NODES, WORKERS

    WORKERS["viejo-a:8080"].running_jobs = 1
    _auto(flota, "viejo-a:8080")  # ocupa el único cupo
    assert _auto(flota, "viejo-b:8080") == {"action": "none", "estado": "ENCOLADA"}
    assert PAUSED_NODES == {"viejo-a:8080"}  # viejo-b sigue recibiendo jobs hasta su turno


def test_auto_abandona_si_el_worker_nunca_queda_ocioso(flota) -> None:
    from central_api.store import PAUSED_NODES, WORKERS
    from central_api.worker_updates import UPDATE_REQUESTS

    WORKERS["viejo-a:8080"].running_jobs = 1
    _auto(flota, "viejo-a:8080")
    UPDATE_REQUESTS["viejo-a:8080"].drain_started_at -= timedelta(hours=3)
    assert _auto(flota, "viejo-a:8080") == {"action": "none"}
    assert "viejo-a:8080" not in PAUSED_NODES
    assert UPDATE_REQUESTS["viejo-a:8080"].status == "FALLIDO"


def test_auto_version_que_fallo_en_un_nodo_no_se_propaga(flota) -> None:
    comando = _auto(flota, "viejo-a:8080")
    flota.post(
        "/internal/v1/updater/report", headers=UPDATER,
        json={"node": "viejo-a:8080", "request_id": comando["request_id"], "estado": "REVERTIDO", "error": "x"},
    )
    assert _auto(flota, "viejo-b:8080") == {"action": "none"}


def test_auto_respeta_la_ventana_horaria(flota, monkeypatch) -> None:
    from datetime import datetime, timezone

    from central_api import worker_updates

    monkeypatch.setenv("WORKER_UPDATE_AUTO_WINDOW", "02:00-05:00")
    get_settings.cache_clear()
    for utc_h, esperado in ((6, True), (12, False)):  # Buenos Aires = UTC-3
        assert worker_updates._en_ventana(datetime(2026, 1, 1, utc_h, tzinfo=timezone.utc)) is esperado
    monkeypatch.setenv("WORKER_UPDATE_AUTO_WINDOW", "23:00-02:00")  # cruza medianoche
    get_settings.cache_clear()
    assert worker_updates._en_ventana(datetime(2026, 1, 1, 3, tzinfo=timezone.utc)) is True  # 00:00 BA
    monkeypatch.setenv("WORKER_UPDATE_AUTO_WINDOW", "basura")
    get_settings.cache_clear()
    assert worker_updates._en_ventana(datetime(2026, 1, 1, 6, tzinfo=timezone.utc)) is False
    assert _auto(flota, "viejo-a:8080") == {"action": "none"}  # fuera de ventana: no se crea nada


def test_auto_con_registry_caido_no_usa_la_referencia_atrasada(flota, monkeypatch) -> None:
    from central_api import registry

    monkeypatch.setattr(registry, "habilitado", lambda: True)
    monkeypatch.setattr(registry, "latest", lambda: None)
    assert _auto(flota, "viejo-a:8080") == {"action": "none"}
