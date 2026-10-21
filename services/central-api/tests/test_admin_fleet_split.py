"""Split de workers manuales y efímeros en el endpoint y panel de flota."""

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


@pytest.fixture
def admin_fleet(monkeypatch):
    from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, JOBS, WORKERS

    monkeypatch.setenv("ADMIN_TOKEN", "admin-fleet-token")
    monkeypatch.setenv("WORKER_NODES", "env-node:8080")
    get_settings.cache_clear()
    saved = (dict(JOBS), dict(WORKERS), set(ADMIN_NODES), dict(EPHEMERAL_WORKERS))
    JOBS.clear()
    WORKERS.clear()
    ADMIN_NODES.clear()
    EPHEMERAL_WORKERS.clear()
    yield
    JOBS.clear()
    JOBS.update(saved[0])
    WORKERS.clear()
    WORKERS.update(saved[1])
    ADMIN_NODES.clear()
    ADMIN_NODES.update(saved[2])
    EPHEMERAL_WORKERS.clear()
    EPHEMERAL_WORKERS.update(saved[3])
    get_settings.cache_clear()


def test_admin_fleet_separa_listas_y_calcula_resumen(admin_fleet) -> None:
    from central_api.store import (
        ADMIN_NODES,
        EPHEMERAL_WORKERS,
        JOBS,
        WORKERS,
        Job,
        WorkerEntry,
        utcnow,
    )

    manual = "env-node:8080"
    admin = "manual-admin:8080"
    ephemeral_node = "ec2-node:8080"
    ADMIN_NODES.update({admin, ephemeral_node})
    WORKERS[manual] = WorkerEntry(
        node=manual, capacity=7, running_jobs=1, status="SANO",
        last_heartbeat_at=utcnow(),
    )
    WORKERS[admin] = WorkerEntry(node=admin, capacity=5)
    WORKERS[ephemeral_node] = WorkerEntry(
        node=ephemeral_node, capacity=10, running_jobs=2, status="SANO",
        last_heartbeat_at=utcnow(),
    )
    for index, node in enumerate((manual, ephemeral_node, ephemeral_node)):
        job = Job(
            id=f"job-{index}", bot="bot", operation="run", payload={},
            status="CORRIENDO", worker_node=node,
        )
        JOBS[job.id] = job

    EPHEMERAL_WORKERS["i-known"] = {
        "instance_id": "i-known",
        "node": ephemeral_node,
        "state": "running",
        "instance_type": "c6i.large",
        "ram_gib": 8.0,
        "concurrency": 10,
        "launched_at": utcnow() - timedelta(minutes=90),
    }
    # Los metadatos de instancias antiguas pueden faltar. El endpoint debe
    # seguir devolviendo la fila con defaults legibles y sin KeyError.
    EPHEMERAL_WORKERS["i-legacy"] = {
        "instance_id": "i-legacy",
        "node": "",
        "state": "pending",
    }

    cliente = TestClient(create_app())
    respuesta = cliente.get(
        "/admin/fleet", headers={"Authorization": "Bearer admin-fleet-token"}
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    # Se mantiene el campo histórico e incluye los nodos inventariados.
    assert {fila["node"] for fila in cuerpo["flota"]} == {
        manual, admin, ephemeral_node,
    }
    assert {fila["node"] for fila in cuerpo["manuales"]} == {manual, admin}
    assert len(cuerpo["efimeros"]) == 2
    efimero = next(fila for fila in cuerpo["efimeros"] if fila["instance_id"] == "i-known")
    assert efimero["instance_type"] == "c6i.large"
    assert efimero["ram_gib"] == 8.0
    assert efimero["concurrency"] == 10
    assert efimero["launched_at"]
    assert efimero["edad"] >= 90 * 60
    assert efimero["estado"] == "SANO"
    legado = next(fila for fila in cuerpo["efimeros"] if fila["instance_id"] == "i-legacy")
    assert legado["estado"] == "REGISTRANDO"
    assert legado["instance_type"] is None
    assert legado["ram_gib"] is None
    assert legado["concurrency"] == 5

    resumen = cuerpo["resumen"]
    assert resumen["manuales"] == {
        "total": 2, "sanos": 1, "capacidad": 12, "en_ejecucion": 1,
    }
    assert resumen["efimeros"]["total"] == 2
    assert resumen["efimeros"]["sanos"] == 1
    assert resumen["efimeros"]["capacidad"] == 15
    assert resumen["efimeros"]["en_ejecucion"] == 2
    assert resumen["efimeros"]["costo_estimado_usd"] > 0


def test_panel_admin_muestra_las_dos_secciones_de_flota() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Workers manuales" in cuerpo
    assert "Workers efímeros (EC2)" in cuerpo
    assert 'id="fleet-manual-table"' in cuerpo
    assert 'id="fleet-ephemeral-table"' in cuerpo
    assert "Semaphore (slots)" in cuerpo
    assert ">Terminar</button>" in cuerpo
    assert 'data-action="worker-remove"' in cuerpo
    assert 'data-action="ephemeral-terminate"' in cuerpo
    assert 'api("/admin/autoscale/terminate"' in cuerpo
    assert "manuales / ${esc(conteoEfimeros)} efimeros" in cuerpo
    assert "${esc(worker.instance_id" in cuerpo
    assert 'id="fleet-version-summary"' in cuerpo
    assert 'id="fleet-outdated-only"' in cuerpo
    assert cuerpo.count("<th>Versión</th>") == 2
    assert "Desactualizado" in cuerpo
    assert "de ${totalVersions} desactualizados" in cuerpo
    assert "manualesVisibles" in cuerpo
    assert "efimerosVisibles" in cuerpo


def test_admin_fleet_expone_latest_y_cuenta_workers_desactualizados(admin_fleet, monkeypatch) -> None:
    from central_api.store import ADMIN_NODES, WORKERS, WorkerEntry

    monkeypatch.setenv("WORKER_NODES", "")
    monkeypatch.delenv("WORKER_LATEST_VERSION", raising=False)
    get_settings.cache_clear()
    versiones = {
        "worker-old:8080": ("3.9.9", ""),
        "worker-latest:8080": ("3.10.0", "sha256:bbb"),
        "worker-same-tag-old-digest:8080": ("3.10.0", "sha256:aaa"),
        "worker-unreported:8080": ("", ""),
    }
    for node, (version, digest) in versiones.items():
        ADMIN_NODES.add(node)
        WORKERS[node] = WorkerEntry(
            node=node, status="SANO", app_version=version, image_digest=digest
        )

    cliente = TestClient(create_app())
    respuesta = cliente.get(
        "/admin/fleet", headers={"Authorization": "Bearer admin-fleet-token"}
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["latest_version"] == "3.10.0"
    filas = {fila["node"]: fila for fila in cuerpo["manuales"]}
    assert filas["worker-latest:8080"]["version"] == "3.10.0"
    assert filas["worker-latest:8080"]["image_digest"] == "sha256:bbb"
    assert filas["worker-latest:8080"]["is_latest"] is True
    assert filas["worker-old:8080"]["outdated"] is True
    assert filas["worker-same-tag-old-digest:8080"]["is_latest"] is False
    assert filas["worker-unreported:8080"]["version"] is None
    assert filas["worker-unreported:8080"]["outdated"] is True
    assert cuerpo["resumen"]["versiones"] == {
        "latest_version": "3.10.0",
        "latest_digest": "sha256:bbb",
        "origen": "flota",
        "total": 4,
        "outdated": 3,
        "unknown": 1,
    }


def test_admin_fleet_expected_version_overrides_reported_high_water(admin_fleet, monkeypatch) -> None:
    from central_api.store import ADMIN_NODES, WORKERS, WorkerEntry

    monkeypatch.setenv("WORKER_NODES", "")
    monkeypatch.setenv("WORKER_LATEST_VERSION", "3.11.0")
    get_settings.cache_clear()
    node = "worker-expected:8080"
    ADMIN_NODES.add(node)
    WORKERS[node] = WorkerEntry(node=node, app_version="3.10.9", status="SANO")

    cliente = TestClient(create_app())
    cuerpo = cliente.get(
        "/admin/fleet", headers={"Authorization": "Bearer admin-fleet-token"}
    ).json()

    fila = next(item for item in cuerpo["manuales"] if item["node"] == node)
    assert fila["latest_version"] == "3.11.0"
    assert fila["is_latest"] is False
    assert cuerpo["resumen"]["versiones"]["outdated"] == 1


def test_worker_registration_and_heartbeat_store_version_metadata(admin_fleet) -> None:
    from central_api.store import WORKERS

    cliente = TestClient(create_app())
    registro = cliente.post(
        "/internal/v1/workers/register",
        json={
            "advertised_url": "http://env-node:8080",
            "image": {"app_version": "3.4.1", "digest": "sha256:old"},
        },
    )
    assert registro.status_code == 200
    assert WORKERS["env-node:8080"].app_version == "3.4.1"
    assert WORKERS["env-node:8080"].image_digest == "sha256:old"

    latido = cliente.post(
        "/internal/v1/workers/env-node:8080/heartbeat",
        json={
            "image": {"version": "3.4.2", "digest": "sha256:new"},
            "capacity": 5,
            "en_ejecucion": 0,
        },
    )
    assert latido.status_code == 200
    assert WORKERS["env-node:8080"].app_version == "3.4.2"
    assert WORKERS["env-node:8080"].image_digest == "sha256:new"


def test_alias_e_ip_de_nodos():
    from central_api.worker_nodes import parse_worker_aliases, resolve_node_ip

    assert parse_worker_aliases("a:1=api2, b:2 = mrbot1,x") == {"a:1": "api2", "b:2": "mrbot1"}
    assert resolve_node_ip("10.1.2.3:8080") == "10.1.2.3"
    assert resolve_node_ip("[::1]:443") == "::1"


def test_alias_de_worker_editable_desde_el_panel(admin_fleet, monkeypatch) -> None:
    from central_api.store import WORKER_ALIAS_OVERRIDES

    monkeypatch.setenv("WORKER_ALIASES", "env-node:8080=desde-env")
    get_settings.cache_clear()
    WORKER_ALIAS_OVERRIDES.clear()
    cabeceras = {"Authorization": "Bearer admin-fleet-token"}
    try:
        with TestClient(create_app()) as client:
            def alias_en_flota() -> str:
                datos = client.get("/admin/fleet", headers=cabeceras).json()
                fila = next(w for w in datos["manuales"] if w["node"] == "env-node:8080")
                return fila["alias"]

            assert alias_en_flota() == "desde-env"
            r = client.put("/admin/workers/env-node:8080/alias", headers=cabeceras, json={"alias": "  api   2 "})
            assert r.status_code == 200 and r.json()["alias"] == "api 2"
            assert alias_en_flota() == "api 2"
            r = client.put("/admin/workers/env-node:8080/alias", headers=cabeceras, json={"alias": ""})
            assert r.json()["alias"] == "desde-env"
            assert client.put("/admin/workers/otro:1/alias", headers=cabeceras, json={"alias": "x"}).status_code == 404
            assert client.put("/admin/workers/env-node:8080/alias", json={"alias": "x"}).status_code in (401, 403)
            assert client.put("/admin/workers/env-node:8080/alias", headers=cabeceras, json={"alias": "x" * 65}).status_code == 422
    finally:
        WORKER_ALIAS_OVERRIDES.clear()


def test_pausar_worker_lo_excluye_del_reparto_y_reanudar_lo_devuelve(admin_fleet) -> None:
    from central_api.scheduler.selector import select_worker
    from central_api.store import PAUSED_NODES, WORKERS, Job, WorkerEntry, utcnow

    PAUSED_NODES.clear()
    cabeceras = {"Authorization": "Bearer admin-fleet-token"}
    WORKERS["env-node:8080"] = WorkerEntry(
        node="env-node:8080", capacity=5, status="SANO", last_heartbeat_at=utcnow()
    )
    WORKERS["otro:8080"] = WorkerEntry(
        node="otro:8080", capacity=5, status="SANO", last_heartbeat_at=utcnow()
    )
    try:
        with TestClient(create_app()) as client:
            r = client.post("/admin/workers/env-node:8080/pause", headers=cabeceras)
            assert r.status_code == 200 and r.json()["pausado"] is True
            assert r.json()["estado"] == "PAUSADO"
            for n in range(30):
                elegido = select_worker(Job(id=f"j{n}", bot="b", operation="o", payload={}), list(WORKERS.values()))
                assert elegido is not None and elegido.node == "otro:8080"
            manuales = client.get("/admin/fleet", headers=cabeceras).json()["manuales"]
            assert next(w for w in manuales if w["node"] == "env-node:8080")["estado"] == "PAUSADO"
            # sin otro worker disponible, el job espera en cola
            assert select_worker(
                Job(id="x", bot="b", operation="o", payload={}), [WORKERS["env-node:8080"]]
            ) is None
            r = client.post("/admin/workers/env-node:8080/resume", headers=cabeceras)
            assert r.json()["pausado"] is False
            nodos = {
                select_worker(Job(id=f"k{n}", bot="b", operation="o", payload={}), list(WORKERS.values())).node
                for n in range(30)
            }
            assert nodos == {"env-node:8080", "otro:8080"}
            assert client.post("/admin/workers/nada:1/pause", headers=cabeceras).status_code == 404
            assert client.post("/admin/workers/env-node:8080/pause").status_code in (401, 403)
    finally:
        PAUSED_NODES.clear()
