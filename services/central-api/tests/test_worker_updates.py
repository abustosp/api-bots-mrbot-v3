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
