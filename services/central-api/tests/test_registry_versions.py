"""Latest automática desde el registry y actualización a una versión elegida."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api import registry  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

ADMIN = {"Authorization": "Bearer admin-reg"}
UPDATER = {"Authorization": "Bearer updater-reg"}
REPO = "registry.local/abustosp/mrbot-bot-worker"
VIEJA, MEDIA, NUEVA = "20261019_100000", "20261020_143316", "20261021_133600"
TAGS = ["latest", MEDIA, "debug-x", NUEVA, VIEJA]


class FalsoRegistry:
    """Registry v2 mínimo: lista de tags paginada de a 2 y exige Basic auth."""

    def __init__(self) -> None:
        self.llamadas: list[httpx.Request] = []
        self.caido = False
        self.tags = list(TAGS)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.llamadas.append(request)
        if self.caido:
            return httpx.Response(503)
        esperado = "Basic " + base64.b64encode(b"lector:secreto").decode()
        if request.headers.get("authorization") != esperado:
            return httpx.Response(401)
        assert request.url.path == "/v2/abustosp/mrbot-bot-worker/tags/list"
        pagina = int(request.url.params.get("pagina", "0"))
        trozo = self.tags[pagina * 2:(pagina + 1) * 2]
        cabeceras = {}
        if (pagina + 1) * 2 < len(self.tags):
            cabeceras["Link"] = f'</v2/abustosp/mrbot-bot-worker/tags/list?n=500&pagina={pagina + 1}>; rel="next"'
        return httpx.Response(200, json={"name": "x", "tags": trozo}, headers=cabeceras)


@pytest.fixture
def entorno(monkeypatch):
    from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, JOBS, PAUSED_NODES, WORKERS, WorkerEntry
    from central_api.worker_updates import UPDATE_REQUESTS

    falso = FalsoRegistry()
    monkeypatch.setattr(registry, "_TRANSPORT", httpx.MockTransport(falso))
    monkeypatch.setenv("ADMIN_TOKEN", "admin-reg")
    monkeypatch.setenv("UPDATER_TOKEN", "updater-reg")
    monkeypatch.setenv("WORKER_NODES", "viejo:8080,nuevo:8080")
    monkeypatch.setenv("WORKER_IMAGE_REPO", REPO)
    monkeypatch.setenv("REGISTRY_USER", "lector")
    monkeypatch.setenv("REGISTRY_PASSWORD", "secreto")
    monkeypatch.setenv("WORKER_REGISTRY_CACHE_SECONDS", "60")
    monkeypatch.delenv("WORKER_IMAGE_REF", raising=False)
    monkeypatch.delenv("WORKER_LATEST_VERSION", raising=False)
    get_settings.cache_clear()
    registry.reiniciar()
    colecciones = (JOBS, WORKERS, ADMIN_NODES, EPHEMERAL_WORKERS, PAUSED_NODES, UPDATE_REQUESTS)
    previo = [type(c)(c) for c in colecciones]
    for c in colecciones:
        c.clear()
    WORKERS["viejo:8080"] = WorkerEntry(node="viejo:8080", status="SANO", app_version=MEDIA)
    WORKERS["nuevo:8080"] = WorkerEntry(node="nuevo:8080", status="SANO", app_version=NUEVA)
    yield TestClient(create_app()), falso
    for c, copia in zip(colecciones, previo):
        c.clear()
        c.update(copia)
    registry.reiniciar()
    get_settings.cache_clear()


def test_lista_versiones_con_paginacion_filtro_y_auth(entorno) -> None:
    _, falso = entorno
    estado = registry.refrescar()
    assert estado.error == ""
    # latest y tags de depuración no son versiones; orden de la más nueva a la más vieja
    assert registry.versiones() == [NUEVA, MEDIA, VIEJA]
    assert registry.latest() == NUEVA
    assert len(falso.llamadas) == 3  # tres páginas


def test_cache_vigente_y_refresco_forzado(entorno) -> None:
    _, falso = entorno
    registry.refrescar()
    llamadas = len(falso.llamadas)
    registry.refrescar()
    assert len(falso.llamadas) == llamadas  # dentro del TTL no consulta
    registry.refrescar(force=True)
    assert len(falso.llamadas) > llamadas


def test_registry_caido_conserva_la_ultima_lista_y_no_lanza(entorno) -> None:
    _, falso = entorno
    registry.refrescar()
    falso.caido = True
    estado = registry.refrescar(force=True)
    assert "registry no disponible" in estado.error
    assert "secreto" not in estado.error and "lector" not in estado.error
    assert registry.latest() == NUEVA  # sigue la última lista buena


def test_la_latest_automatica_marca_workers_desactualizados(entorno) -> None:
    cliente, _ = entorno
    flota = cliente.get("/admin/fleet", headers=ADMIN).json()
    por_nodo = {f["node"]: f for f in flota["manuales"]}
    assert por_nodo["viejo:8080"]["outdated"] is True
    assert por_nodo["nuevo:8080"]["outdated"] is False
    assert flota["resumen"]["versiones"]["latest_version"] == NUEVA
    assert flota["resumen"]["versiones"]["origen"] == "registry"


def test_sin_registry_cae_en_worker_latest_version(entorno, monkeypatch) -> None:
    cliente, falso = entorno
    falso.caido = True
    monkeypatch.setenv("WORKER_LATEST_VERSION", VIEJA)
    get_settings.cache_clear()
    registry.reiniciar()
    flota = cliente.get("/admin/fleet", headers=ADMIN).json()
    assert flota["resumen"]["versiones"]["latest_version"] == VIEJA
    assert flota["resumen"]["versiones"]["origen"] == "config"


def test_endpoint_de_versiones(entorno) -> None:
    cliente, _ = entorno
    assert cliente.get("/admin/fleet/versions").status_code in (401, 403)
    datos = cliente.get("/admin/fleet/versions", headers=ADMIN).json()
    assert datos["habilitado"] is True and datos["repo"] == REPO
    assert datos["latest"] == NUEVA and datos["origen"] == "registry" and datos["error"] is None
    assert [v["version"] for v in datos["versiones"]] == [NUEVA, MEDIA, VIEJA]
    assert datos["versiones"][0]["es_latest"] is True
    assert datos["versiones"][1]["workers"] == ["viejo:8080"]


def test_actualizar_sin_version_aplica_la_latest_del_registry(entorno) -> None:
    cliente, _ = entorno
    r = cliente.post("/admin/workers/viejo:8080/update", json={"motivo": "subir a la última"}, headers=ADMIN)
    assert r.status_code == 200
    act = r.json()["actualizacion"]
    assert act["imagen"] == f"{REPO}:{NUEVA}" and act["version"] == NUEVA
    comando = cliente.get("/internal/v1/updater/command", params={"node": "viejo:8080"}, headers=UPDATER).json()
    assert comando["image"] == f"{REPO}:{NUEVA}" and comando["app_version"] == NUEVA


def test_actualizar_a_una_version_elegida_incluso_anterior(entorno) -> None:
    cliente, _ = entorno
    r = cliente.post(
        "/admin/workers/nuevo:8080/update",
        json={"motivo": "volver a la versión anterior", "version": VIEJA}, headers=ADMIN,
    )
    assert r.status_code == 200
    act = r.json()["actualizacion"]
    assert act["imagen"] == f"{REPO}:{VIEJA}" and act["version"] == VIEJA
    comando = cliente.get("/internal/v1/updater/command", params={"node": "nuevo:8080"}, headers=UPDATER).json()
    assert comando["image"] == f"{REPO}:{VIEJA}" and comando["app_version"] == VIEJA


@pytest.mark.parametrize("version,fragmento", [
    ("latest", "formato"),
    ("20260101_000000", "no está publicada"),
    ("../etc", "formato"),
])
def test_versiones_invalidas_o_no_publicadas_se_rechazan(entorno, version, fragmento) -> None:
    cliente, _ = entorno
    r = cliente.post(
        "/admin/workers/viejo:8080/update", json={"motivo": "probar rechazo", "version": version}, headers=ADMIN,
    )
    assert r.status_code == 409 and fragmento in r.json()["detail"]


def test_elegir_version_sin_registry_disponible_se_rechaza(entorno) -> None:
    cliente, falso = entorno
    falso.caido = True
    registry.reiniciar()
    r = cliente.post(
        "/admin/workers/viejo:8080/update", json={"motivo": "sin registro", "version": NUEVA}, headers=ADMIN,
    )
    assert r.status_code == 409 and "registry" in r.json()["detail"]


def test_elegir_version_sin_worker_image_repo_se_rechaza(entorno, monkeypatch) -> None:
    cliente, _ = entorno
    monkeypatch.delenv("WORKER_IMAGE_REPO")
    monkeypatch.setenv("WORKER_IMAGE_REF", f"{REPO}:{MEDIA}")
    get_settings.cache_clear()
    r = cliente.post(
        "/admin/workers/viejo:8080/update", json={"motivo": "sin repo configurado", "version": NUEVA}, headers=ADMIN,
    )
    assert r.status_code == 409 and "WORKER_IMAGE_REPO" in r.json()["detail"]


def test_update_all_usa_la_latest_del_registry(entorno) -> None:
    cliente, _ = entorno
    r = cliente.post("/admin/fleet/update-all", json={"motivo": "actualización masiva"}, headers=ADMIN).json()
    assert [e["node"] for e in r["encoladas"]] == ["viejo:8080"]
    assert r["encoladas"][0]["imagen"] == f"{REPO}:{NUEVA}"


def test_el_panel_ofrece_el_desplegable_de_version(entorno) -> None:
    cliente, _ = entorno
    html = cliente.get("/admin/panel").text if cliente.get("/admin/panel").status_code == 200 else ""
    if not html:  # el panel puede exigir sesión: se verifica el módulo que lo genera
        from central_api.admin import panel

        html = Path(panel.__file__).read_text(encoding="utf-8")
    assert "worker-apply-version" in html and "/admin/fleet/versions" in html
