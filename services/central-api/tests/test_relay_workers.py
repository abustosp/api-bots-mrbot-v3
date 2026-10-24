"""Workers por relay frp: alta, rotación, baja, hook de frps y flota."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

H = {"Authorization": "Bearer relay-admin"}
HOOK = "/internal/v1/relay/frp-hook"


@pytest.fixture
def cliente(monkeypatch):
    from central_api.store import ADMIN_NODES, RELAY_WORKERS, WORKER_ALIAS_OVERRIDES, WORKERS

    monkeypatch.setenv("ADMIN_TOKEN", "relay-admin")
    monkeypatch.setenv("WORKER_NODES", "")
    monkeypatch.setenv("RELAY_PORT_RANGE", "20001-20002")
    monkeypatch.setenv("RELAY_PUBLIC_ADDR", "frp.example.com")
    get_settings.cache_clear()
    for d in (ADMIN_NODES, RELAY_WORKERS, WORKER_ALIAS_OVERRIDES, WORKERS):
        d.clear()
    yield TestClient(create_app())
    for d in (ADMIN_NODES, RELAY_WORKERS, WORKER_ALIAS_OVERRIDES, WORKERS):
        d.clear()
    get_settings.cache_clear()


def _alta(c, alias="casa"):
    return c.post("/admin/workers/relay", headers=H, json={"alias": alias})


def _login(c, user, token):
    return c.post(HOOK, json={"version": "0.1.0", "op": "Login",
                              "content": {"user": user, "metas": {"relay_token": token}}}).json()


def _proxy(c, user, token, tipo="tcp", puerto=20001):
    return c.post(HOOK, json={"version": "0.1.0", "op": "NewProxy", "content": {
        "user": {"user": user, "metas": {"relay_token": token}},
        "proxy_type": tipo, "remote_port": puerto}}).json()


def test_alta_asigna_puertos_secuenciales_y_agota(cliente) -> None:
    from central_api.store import ADMIN_NODES

    a = _alta(cliente)
    assert a.status_code == 201
    d = a.json()
    assert (d["node"], d["port"], d["alias"]) == ("relay:20001", 20001, "casa")
    assert d["env"] == "WORKER_ADVERTISED_URL=http://relay:20001"
    assert f'metadatas.relay_token = "{d["token"]}"' in d["frpc_toml"]
    assert 'user = "relay-20001"' in d["frpc_toml"] and "remotePort = 20001" in d["frpc_toml"]
    assert 'serverAddr = "frp.example.com"\nserverPort = 7000\n' in d["frpc_toml"]
    assert _alta(cliente).json()["node"] == "relay:20002"
    assert {"relay:20001", "relay:20002"} <= ADMIN_NODES
    assert _alta(cliente).status_code == 409
    assert cliente.post("/admin/workers/relay", json={"alias": "x"}).status_code in (401, 403)


def test_rotar_token_invalida_el_anterior(cliente) -> None:
    viejo = _alta(cliente).json()["token"]
    nuevo = cliente.post("/admin/workers/relay/relay:20001/token", headers=H).json()["token"]
    assert nuevo != viejo
    assert _login(cliente, "relay-20001", viejo)["reject"] is True
    assert _login(cliente, "relay-20001", nuevo)["reject"] is False
    assert cliente.post("/admin/workers/relay/otro:1/token", headers=H).status_code == 404


def test_baja_libera_puerto_y_token(cliente) -> None:
    from central_api.store import RELAY_WORKERS

    token = _alta(cliente).json()["token"]
    assert cliente.delete("/admin/workers/relay:20001", headers=H).status_code == 200
    assert "relay:20001" not in RELAY_WORKERS
    assert _login(cliente, "relay-20001", token)["reject"] is True
    assert _alta(cliente).json()["port"] == 20001


def test_hook_login(cliente) -> None:
    token = _alta(cliente).json()["token"]
    assert _login(cliente, "relay-20001", token) == {"reject": False, "unchange": True}
    assert _login(cliente, "relay-20001", "mal")["reject"] is True
    assert _login(cliente, "relay-20002", token)["reject"] is True


def test_hook_new_proxy(cliente) -> None:
    token = _alta(cliente).json()["token"]
    assert _proxy(cliente, "relay-20001", token)["reject"] is False
    assert _proxy(cliente, "relay-20001", token, puerto=20002)["reject"] is True
    assert _proxy(cliente, "relay-20001", token, tipo="http")["reject"] is True
    assert _proxy(cliente, "relay-20001", "mal")["reject"] is True


def test_flota_devuelve_tres_grupos_y_relay_registra_sin_ip_de_origen(cliente) -> None:
    from central_api.store import ADMIN_NODES

    ADMIN_NODES.add("manual:8080")
    _alta(cliente)
    # El worker relay se registra desde una IP pública cualquiera: no se valida origen.
    r = cliente.post("/internal/v1/workers/register",
                     json={"advertised_url": "http://relay:20001", "app_version": "3.0.0"})
    assert r.status_code == 200 and r.json()["accepted"] is True
    cuerpo = cliente.get("/admin/fleet", headers=H).json()
    assert [f["node"] for f in cuerpo["manuales"]] == ["manual:8080"]
    assert [(f["node"], f["origen"], f["relay_port"]) for f in cuerpo["relay"]] == [
        ("relay:20001", "relay", 20001)]
    assert {f["node"] for f in cuerpo["flota"]} == {"manual:8080", "relay:20001"}
    assert cuerpo["resumen"]["relay"]["total"] == 1
    assert cuerpo["resumen"]["versiones"]["total"] == 2


def test_hook_ping_corta_token_rotado_y_baja(cliente) -> None:
    def ping(token):
        return cliente.post(HOOK, json={"version": "0.1.0", "op": "Ping", "content": {
            "user": {"user": "relay-20001", "metas": {"relay_token": token}}}}).json()

    viejo = _alta(cliente).json()["token"]
    assert ping(viejo)["reject"] is False
    nuevo = cliente.post("/admin/workers/relay/relay:20001/token", headers=H).json()["token"]
    assert ping(viejo)["reject"] is True
    assert ping(nuevo)["reject"] is False
    assert cliente.delete("/admin/workers/relay:20001", headers=H).status_code == 200
    assert ping(nuevo)["reject"] is True
    assert cliente.post(HOOK, json={"op": "Ping", "content": {}}).json()["reject"] is True


def test_realta_de_nodo_dado_de_baja_limpia_el_drenaje(cliente) -> None:
    from central_api.store import WORKERS, WorkerEntry

    _alta(cliente)
    WORKERS["relay:20001"] = WorkerEntry(node="relay:20001")
    assert cliente.delete("/admin/workers/relay:20001", headers=H).status_code == 200
    assert WORKERS["relay:20001"].status == "DRENANDO"
    assert _alta(cliente).json()["node"] == "relay:20001"
    assert WORKERS["relay:20001"].status == "REGISTRANDO"
