"""Zonas de lanzamiento del autoscaler EC2 (Local Zone Buenos Aires vs São Paulo).

Cubre la regla de sizing por RAM (1,5 a 2 GB por worker), ``for_zone``, el
catálogo por zona con su sizing, la validación del semaphore en el alta manual,
el alta en una zona elegida con flota falsa, la baja agrupada por zona y la
persistencia de la zona activa. Sin AWS ni base: todo con fakes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from math import floor
from pathlib import Path
from unittest.mock import Mock

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.autoscaler import controller  # noqa: E402
from central_api.autoscaler import state as zone_state  # noqa: E402
from central_api.autoscaler.ec2 import (  # noqa: E402
    GB_PER_WORKER_MAX,
    GB_PER_WORKER_MIN,
    MAX_WORKER_SEMAPHORE,
    ZONES,
    Ec2Fleet,
    Ec2Settings,
    concurrency_for_ram_gib,
    worker_sizing,
)
from central_api.settings import get_settings  # noqa: E402
from central_api.store import (  # noqa: E402
    ADMIN_NODES,
    EPHEMERAL_WORKERS,
    WORKERS,
    utcnow,
)


@dataclass
class FakeWorker:
    instance_id: str
    state: str
    public_ip: str | None
    private_ip: str | None = "10.0.0.9"
    launched_at: datetime | None = None
    az: str | None = "us-east-1-bue-1a"
    worker_port: int = 8080
    instance_type: str = "c5.xlarge"
    ram_gib: float = 8.0
    concurrency: int = 5

    def __post_init__(self):
        self.launched_at = self.launched_at or utcnow()

    @property
    def node(self):
        return f"{self.public_ip}:{self.worker_port}" if self.public_ip else None


class FakeFleet:
    """Flota falsa de una zona: registra llamadas y no toca AWS."""

    def __init__(self, rows: list[dict] | None = None):
        self.workers: list[FakeWorker] = []
        self.launch_calls: list[tuple[int, str | None, int | None]] = []
        self.terminate_calls: list[list[str]] = []
        self.types = rows if rows is not None else []
        self.settings = type("Settings", (), {
            "region": "us-east-1",
            "instance_type": "c5.xlarge",
            "worker_port": 8080,
            "worker_concurrency": 5,
            "max_lifetime_minutes": 120,
            "max_instances": 4,
            "project_tag": "unit-test-project",
        })()

    def launch(self, count: int, instance_type: str | None = None, concurrency: int | None = None):
        self.launch_calls.append((count, instance_type, concurrency))
        created = [
            FakeWorker(
                f"i-new-{len(self.workers) + index}",
                "pending",
                f"198.51.100.{20 + index}",
                instance_type=instance_type or "c5.xlarge",
            )
            for index in range(count)
        ]
        self.workers.extend(created)
        return created

    def wait_until_running(self, workers, timeout):
        for worker in workers:
            worker.state = "running"
        return workers

    def list_managed(self, states=("pending", "running")):
        if states is None:
            return list(self.workers)
        return [worker for worker in self.workers if worker.state in states]

    def list_instance_types(self):
        return list(self.types)

    def terminate(self, instance_ids):
        ids = list(instance_ids)
        self.terminate_calls.append(ids)
        for worker in self.workers:
            if worker.instance_id in ids:
                worker.state = "terminated"
        return ids

    def estimate_cost(self, workers, now):
        return 0.0


def _catalogo(*rows: dict) -> list[dict]:
    """Catálogo mínimo con el sizing real de cada tipo."""
    catalogo = []
    for ram, vcpu, name in rows:
        catalogo.append({
            "instance_type": name,
            "vcpu": vcpu,
            "ram_gib": ram,
            "suggested_concurrency": concurrency_for_ram_gib(ram),
            **worker_sizing(ram),
        })
    return catalogo


@pytest.fixture(autouse=True)
def estado_aislado(monkeypatch):
    """Aísla registros, zona activa y flota inyectada entre tests."""
    guardado = {
        "ephemeral": dict(EPHEMERAL_WORKERS),
        "admin": set(ADMIN_NODES),
        "workers": dict(WORKERS),
        "zone": zone_state.ACTIVE_ZONE,
        "manual": set(zone_state.MANUAL_IDS),
        "fleet": controller.FLEET,
        "fleets": dict(controller.FLEETS),
    }
    EPHEMERAL_WORKERS.clear()
    ADMIN_NODES.clear()
    WORKERS.clear()
    controller.set_fleet_for_testing(None)
    controller.EVENTS.clear()
    controller.DRAIN_TASK = None
    zone_state.ACTIVE_ZONE = zone_state.DEFAULT_ZONE
    zone_state.MANUAL_IDS.clear()
    for nombre, valor in {
        "DATABASE_URL": "",
        "AUTOSCALE_ENABLED": "false",
        "AUTOSCALE_DRY_RUN": "false",
        "AUTOSCALE_MAX_INSTANCES": "4",
        "AUTOSCALE_INTERVAL_SECONDS": "1",
    }.items():
        monkeypatch.setenv(nombre, valor)
    get_settings.cache_clear()
    yield
    try:
        asyncio.run(controller.shutdown_tasks())
    except RuntimeError:
        pass
    controller.set_fleet_for_testing(None)
    controller.DRAIN_TASK = None
    controller.EVENTS.clear()
    EPHEMERAL_WORKERS.clear()
    EPHEMERAL_WORKERS.update(guardado["ephemeral"])
    ADMIN_NODES.clear()
    ADMIN_NODES.update(guardado["admin"])
    WORKERS.clear()
    WORKERS.update(guardado["workers"])
    zone_state.ACTIVE_ZONE = guardado["zone"]
    zone_state.MANUAL_IDS.clear()
    zone_state.MANUAL_IDS.update(guardado["manual"])
    controller.FLEET = guardado["fleet"]
    controller.FLEETS.clear()
    controller.FLEETS.update(guardado["fleets"])
    get_settings.cache_clear()


def _flota_por_zona(monkeypatch, flotas: dict[str, FakeFleet]) -> list[str]:
    """Inyecta una flota por zona y devuelve la lista de zonas pedidas."""
    pedidas: list[str] = []

    def make_fleet(zone=None):
        key = controller.resolve_zone(zone)
        pedidas.append(key)
        return flotas[key]

    monkeypatch.setattr(controller, "_make_fleet", make_fleet)
    return pedidas


# --- Regla de sizing: 1,5 a 2 GB por worker ------------------------------------


@pytest.mark.parametrize(
    ("ram_gib", "recomendado_max", "max_concurrency"),
    [(4.0, 2, 4), (8.0, 5, 8), (16.0, 10, 10), (32.0, 10, 10), (64.0, 10, 10)],
)
def test_worker_sizing_sigue_la_regla_de_1_5_2_gb(ram_gib, recomendado_max, max_concurrency):
    sizing = worker_sizing(ram_gib)
    assert sizing["recommended_max"] == recomendado_max
    assert sizing["recommended_max"] == min(MAX_WORKER_SEMAPHORE, floor(ram_gib / 1.5))
    # El máximo permitido es la RAM en GB, con el tope duro 10 del worker (W-2).
    assert sizing["max_concurrency"] == max_concurrency == min(ram_gib, MAX_WORKER_SEMAPHORE)
    assert sizing["recommended_min"] == max(1, min(recomendado_max, floor(ram_gib / 2.0)))
    assert sizing["gb_per_worker_min"] == GB_PER_WORKER_MIN == 1.5
    assert sizing["gb_per_worker_max"] == GB_PER_WORKER_MAX == 2.0
    # El semaphore sugerido por defecto es el recomendado (RAM / 1,5).
    assert concurrency_for_ram_gib(ram_gib) == recomendado_max


def test_settings_deriva_el_semaphore_del_tipo_con_la_misma_regla():
    assert Ec2Settings(instance_type="c5.xlarge").effective_worker_concurrency == 5
    assert Ec2Settings(instance_type="c5.2xlarge").effective_worker_concurrency == 10
    assert Ec2Settings(instance_type="t3.medium").effective_worker_concurrency == 2
    # Un valor explícito del entorno manda sobre la regla.
    assert Ec2Settings(instance_type="t3.medium", worker_concurrency=7).effective_worker_concurrency == 7


# --- for_zone -----------------------------------------------------------------


def test_for_zone_cambia_region_az_volumen_y_no_muta_el_base():
    base = Ec2Settings(region="sa-east-1", subnet_ids=("subnet-sp-1",))

    bue = base.for_zone("bue")
    assert bue.region == "us-east-1"
    assert bue.availability_zones == ("us-east-1-bue-1a",)
    assert bue.volume_type == "gp2"  # Local Zone: solo gp2
    # Subnets de otra región no sirven en la zona elegida.
    assert bue.subnet_ids == ()
    assert bue.instance_type == base.instance_type and bue.worker_port == base.worker_port
    assert base.region == "sa-east-1" and base.volume_type == "gp3"

    sp = base.for_zone("sp")
    assert (sp.region, sp.volume_type, sp.availability_zones) == ("sa-east-1", "gp3", ())
    assert sp.subnet_ids == ("subnet-sp-1",)  # misma región: se heredan


def test_for_zone_usa_las_subnets_de_la_zona_cuando_las_define(monkeypatch):
    monkeypatch.setitem(ZONES, "bue", {**ZONES["bue"], "subnet_ids": ("subnet-bue-1",)})
    base = Ec2Settings(region="us-east-1", subnet_ids=("subnet-otra",))
    assert base.for_zone("bue").subnet_ids == ("subnet-bue-1",)


def test_for_zone_rechaza_zonas_desconocidas():
    with pytest.raises(ValueError):
        Ec2Settings().for_zone("marte")
    assert set(ZONES) == {"bue", "sp"}


# --- Catálogo por zona --------------------------------------------------------


def _cliente_catalogo(zona: str = "us-east-1-bue-1a") -> Mock:
    client = Mock()
    client.describe_instance_type_offerings.return_value = {"InstanceTypeOfferings": [
        {"InstanceType": "c5.xlarge"}, {"InstanceType": "c5.2xlarge"}]}
    client.describe_instance_types.return_value = {"InstanceTypes": [
        {"InstanceType": "c5.2xlarge", "VCpuInfo": {"DefaultVCpus": 8},
         "MemoryInfo": {"SizeInMiB": 16384}},
        {"InstanceType": "c5.xlarge", "VCpuInfo": {"DefaultVCpus": 4},
         "MemoryInfo": {"SizeInMiB": 8192}},
    ]}
    client.describe_subnets.return_value = {
        "Subnets": [{"SubnetId": "s-1", "VpcId": "vpc-default",
                     "AvailabilityZone": zona, "State": "available"}]
    }
    return client


def test_catalogo_de_la_zona_incluye_sizing_y_filtra_por_az():
    client = _cliente_catalogo()
    fleet = Ec2Fleet(Ec2Settings().for_zone("bue"), client=client)

    rows = fleet.list_instance_types()

    # Orden por vCPU y RAM, con el sizing completo de cada fila.
    assert [row["instance_type"] for row in rows] == ["c5.xlarge", "c5.2xlarge"]
    assert rows[0] == {
        "instance_type": "c5.xlarge", "vcpu": 4, "ram_gib": 8.0,
        "suggested_concurrency": 5,
        "recommended_min": 4, "recommended_max": 5, "max_concurrency": 8,
        "gb_per_worker_min": 1.5, "gb_per_worker_max": 2.0,
    }
    assert rows[1]["recommended_max"] == 10 and rows[1]["max_concurrency"] == 10
    filtros = client.describe_instance_type_offerings.call_args.kwargs["Filters"]
    assert {"Name": "location", "Values": ["us-east-1-bue-1a"]} in filtros


def test_worker_subnets_solo_usa_la_az_de_la_zona():
    client = Mock()
    client.describe_subnets.return_value = {"Subnets": [
        {"SubnetId": "subnet-bue", "VpcId": "vpc-default",
         "AvailabilityZone": "us-east-1-bue-1a", "State": "available"},
        {"SubnetId": "subnet-otra-az", "VpcId": "vpc-default",
         "AvailabilityZone": "us-east-1a", "State": "available"},
    ]}
    fleet = Ec2Fleet(Ec2Settings().for_zone("bue"), client=client)

    subnets = fleet._worker_subnets("vpc-default")

    assert [subnet["SubnetId"] for subnet in subnets] == ["subnet-bue"]
    filtros = client.describe_subnets.call_args.kwargs["Filters"]
    assert {"Name": "availability-zone", "Values": ["us-east-1-bue-1a"]} in filtros


# --- Zona activa persistida ---------------------------------------------------


class _SesionFalsa:
    def __init__(self, rows):
        self._rows = rows
        self.escrito: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.escrito.append((str(statement), params))
        return _ResultadoFalso(self._rows)

    async def commit(self):
        return None


class _ResultadoFalso:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _SesionesFalsas:
    def __init__(self, rows):
        self.session = _SesionFalsa(rows)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


def test_zona_activa_por_defecto_es_bue_y_una_invalida_cae_al_default():
    assert zone_state.active_zone() == "bue"
    zone_state.ACTIVE_ZONE = "marte"
    assert zone_state.active_zone() == "bue"


def test_set_zone_persiste_en_autoscale_state(monkeypatch):
    guardado: list[tuple[str, object]] = []

    async def fake_save(key, value):
        guardado.append((key, value))

    monkeypatch.setattr(zone_state, "_save", fake_save)
    assert asyncio.run(zone_state.set_zone("sp")) == "sp"
    assert guardado == [("zone", "sp")]
    assert zone_state.active_zone() == "sp"
    with pytest.raises(ValueError):
        asyncio.run(zone_state.set_zone("marte"))
    assert zone_state.active_zone() == "sp"  # la inválida no se aplica


def test_set_zone_escribe_la_clave_zone_en_autoscale_state(monkeypatch):
    sesiones = _SesionesFalsas([])
    monkeypatch.setattr(zone_state, "db_configurado", lambda: True)
    monkeypatch.setattr(zone_state, "nueva_sesion", sesiones)

    asyncio.run(zone_state.set_zone("sp"))

    (statement, params), = sesiones.session.escrito
    assert "autoscale_state" in statement and "INSERT" in statement.upper()
    assert params == {"k": "zone", "v": json.dumps("sp")}


def test_load_state_restaura_la_zona_persistida(monkeypatch):
    sesiones = _SesionesFalsas([("zone", json.dumps("sp"))])
    monkeypatch.setattr(zone_state, "db_configurado", lambda: True)
    monkeypatch.setattr(zone_state, "nueva_sesion", sesiones)
    zone_state.ACTIVE_ZONE = "bue"

    asyncio.run(zone_state.load_state())

    assert zone_state.active_zone() == "sp"
    assert "autoscale_state" in sesiones.session.escrito[0][0]


@pytest.mark.parametrize("persistida", [json.dumps("marte"), json.dumps(None), json.dumps(7)])
def test_load_state_usa_bue_si_la_zona_persistida_no_es_valida(monkeypatch, persistida):
    monkeypatch.setattr(zone_state, "db_configurado", lambda: True)
    monkeypatch.setattr(zone_state, "nueva_sesion", _SesionesFalsas([("zone", persistida)]))
    zone_state.ACTIVE_ZONE = "sp"

    asyncio.run(zone_state.load_state())

    assert zone_state.active_zone() == "bue"


# --- Alta por zona ------------------------------------------------------------


def test_alta_en_zona_sp_usa_esa_flota_y_registra_la_zona(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    pedidas = _flota_por_zona(monkeypatch, flotas)

    records = asyncio.run(
        controller.launch_workers(1, reason="alta manual en São Paulo", zone="sp", dry_run=False)
    )

    assert pedidas == ["sp"]
    assert flotas["sp"].launch_calls == [(1, None, None)]
    assert records[0]["zone"] == "sp"
    assert EPHEMERAL_WORKERS[records[0]["instance_id"]]["zone"] == "sp"


def test_alta_sin_zona_usa_la_zona_activa(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    pedidas = _flota_por_zona(monkeypatch, flotas)
    asyncio.run(zone_state.set_zone("sp"))

    records = asyncio.run(controller.launch_workers(1, reason="alta en la zona activa", dry_run=False))

    assert pedidas == ["sp"]
    assert records[0]["zone"] == "sp"


def test_el_maximo_de_instancias_se_aplica_por_zona(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    _flota_por_zona(monkeypatch, flotas)
    for index in range(4):  # tope del entorno y de la flota falsa
        EPHEMERAL_WORKERS[f"i-bue-{index}"] = {
            "instance_id": f"i-bue-{index}", "zone": "bue", "state": "running",
        }

    lleno = asyncio.run(controller.launch_workers(1, reason="zona llena", zone="bue", dry_run=False))
    libre = asyncio.run(controller.launch_workers(1, reason="otra zona", zone="sp", dry_run=False))

    assert lleno == [] and flotas["bue"].launch_calls == []
    assert len(libre) == 1 and libre[0]["zone"] == "sp"
    assert controller.instances_in_zone("sp") == 1


# --- Baja agrupada por zona ---------------------------------------------------


def _registrar(worker: FakeWorker, zone: str) -> None:
    controller._register_instance(worker, zone=zone)
    ADMIN_NODES.add(worker.node)


def test_terminate_agrupa_por_zona_y_opera_con_la_flota_de_cada_una(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    _flota_por_zona(monkeypatch, flotas)
    en_bue = FakeWorker("i-bue", "running", "198.51.100.31")
    en_sp = FakeWorker("i-sp", "running", "198.51.100.32")
    for worker, zone in ((en_bue, "bue"), (en_sp, "sp")):
        flotas[zone].workers.append(worker)
        _registrar(worker, zone)

    result = asyncio.run(
        controller.terminate_workers(["i-bue", "i-sp"], reason="baja por zona de prueba")
    )

    assert result["terminated"] == ["i-bue", "i-sp"]
    assert result["draining"] == [] and result["pending"] == [] and result["ignored"] == []
    assert flotas["bue"].terminate_calls == [["i-bue"]]
    assert flotas["sp"].terminate_calls == [["i-sp"]]
    assert "error" not in result


def test_terminate_no_se_frena_si_una_zona_falla(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    _flota_por_zona(monkeypatch, flotas)

    def explota(instance_ids):
        raise RuntimeError("AccessDenied en la Local Zone")

    flotas["bue"].terminate = explota
    en_bue = FakeWorker("i-bue", "running", "198.51.100.41")
    en_sp = FakeWorker("i-sp", "running", "198.51.100.42")
    for worker, zone in ((en_bue, "bue"), (en_sp, "sp")):
        flotas[zone].workers.append(worker)
        _registrar(worker, zone)

    result = asyncio.run(
        controller.terminate_workers(["i-bue", "i-sp"], reason="baja con zona caída")
    )

    # La zona sana se termina igual; la caída queda pendiente y con su error.
    assert result["terminated"] == ["i-sp"]
    assert result["error"]["bue"] == "RuntimeError"
    assert "i-bue" in EPHEMERAL_WORKERS
    assert flotas["bue"].terminate_calls == []


def test_reconcile_adopta_por_zona_y_no_duplica(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}
    _flota_por_zona(monkeypatch, flotas)
    flotas["bue"].workers.append(FakeWorker("i-adopt-bue", "running", "198.51.100.51"))
    flotas["sp"].workers.append(FakeWorker("i-adopt-sp", "running", "198.51.100.52"))
    zone_state.MANUAL_IDS.add("i-adopt-sp")

    adoptadas = asyncio.run(controller.reconcile_managed())

    assert sorted(adoptadas) == ["i-adopt-bue", "i-adopt-sp"]
    assert EPHEMERAL_WORKERS["i-adopt-bue"]["zone"] == "bue"
    assert EPHEMERAL_WORKERS["i-adopt-sp"]["zone"] == "sp"
    assert EPHEMERAL_WORKERS["i-adopt-sp"]["manual"] is True


def test_reconcile_sigue_con_las_demas_zonas_si_una_falla(monkeypatch):
    flotas = {"bue": FakeFleet(), "sp": FakeFleet()}

    class ZonaRota(FakeFleet):
        def list_managed(self, states=("pending", "running")):
            raise RuntimeError("zona sin permisos")

    flotas["bue"] = ZonaRota()
    _flota_por_zona(monkeypatch, flotas)
    flotas["sp"].workers.append(FakeWorker("i-sp-vivo", "running", "198.51.100.61"))

    adoptadas = asyncio.run(controller.reconcile_managed())

    assert adoptadas == ["i-sp-vivo"]
    eventos = controller.event_list()
    assert any(
        evento["action"] == "reconcile_failed" and evento.get("zone") == "bue"
        for evento in eventos
    )


# --- Endpoints administrativos ------------------------------------------------


@pytest.fixture
def admin_sin_efectos(monkeypatch):
    from central_api.admin import autoscale as admin_autoscale

    monkeypatch.setattr(admin_autoscale, "require_admin", lambda _a: "admin")
    monkeypatch.setattr(admin_autoscale, "log_event", lambda *a, **k: None)
    return admin_autoscale


def test_scale_up_rechaza_semaphore_por_encima_del_maximo_del_tipo(monkeypatch, admin_sin_efectos):
    from fastapi import HTTPException

    admin_autoscale = admin_sin_efectos
    flota = FakeFleet(_catalogo((8.0, 4, "c5.xlarge")))
    monkeypatch.setattr(controller, "_make_fleet", lambda zone=None: flota)

    # El máximo del tipo con 8 GiB es 8 (recomendado 5).
    alto = admin_autoscale.ScaleUpBody(
        count=1, motivo="alta con semaphore excesivo", instance_type="c5.xlarge", concurrency=9,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_autoscale.autoscale_scale_up(alto))
    assert exc.value.status_code == 400
    assert "1,5 y 2 GB" in exc.value.detail and "máximo 8" in exc.value.detail
    assert flota.launch_calls == []

    # Hasta el máximo del tipo (8) sí se acepta.
    limite = admin_autoscale.ScaleUpBody(
        count=1, motivo="alta con semaphore al límite", instance_type="c5.xlarge", concurrency=8,
    )
    resultado = asyncio.run(admin_autoscale.autoscale_scale_up(limite))
    assert resultado["success"] is True
    assert resultado["zone"] == "bue"
    assert flota.launch_calls == [(1, "c5.xlarge", 8)]


def test_scale_up_valida_el_tipo_contra_el_catalogo_de_la_zona_pedida(
    monkeypatch, admin_sin_efectos
):
    from fastapi import HTTPException

    admin_autoscale = admin_sin_efectos
    flotas = {
        "bue": FakeFleet(_catalogo((8.0, 4, "c5.xlarge"))),
        "sp": FakeFleet(_catalogo((16.0, 4, "t3.xlarge"))),
    }
    pedidas = _flota_por_zona(monkeypatch, flotas)

    ok = admin_autoscale.ScaleUpBody(
        count=1, motivo="alta en São Paulo", zone="sp",
        instance_type="t3.xlarge", concurrency=10,
    )
    resultado = asyncio.run(admin_autoscale.autoscale_scale_up(ok))
    assert resultado["zone"] == "sp"
    assert flotas["sp"].launch_calls == [(1, "t3.xlarge", 10)]

    # c5.xlarge no existe en el catálogo de sp.
    malo = admin_autoscale.ScaleUpBody(
        count=1, motivo="tipo fuera de la zona", zone="sp", instance_type="c5.xlarge",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_autoscale.autoscale_scale_up(malo))
    assert exc.value.status_code == 400 and "sp" in exc.value.detail
    assert pedidas.count("sp") >= 1


def test_endpoint_zones_lista_zonas_y_el_put_cambia_la_activa(monkeypatch, admin_sin_efectos):
    admin_autoscale = admin_sin_efectos
    flotas = {
        "bue": FakeFleet(_catalogo((8.0, 4, "c5.xlarge"))),
        "sp": FakeFleet(_catalogo((16.0, 4, "t3.xlarge"))),
    }
    _flota_por_zona(monkeypatch, flotas)
    EPHEMERAL_WORKERS["i-bue-0"] = {"instance_id": "i-bue-0", "zone": "bue"}

    listado = asyncio.run(admin_autoscale.get_zones())

    assert listado["success"] is True and listado["active"] == "bue"
    assert [zona["key"] for zona in listado["zones"]] == ["bue", "sp"]
    por_clave = {zona["key"]: zona for zona in listado["zones"]}
    assert por_clave["bue"]["region"] == "us-east-1"
    assert por_clave["bue"]["volume_type"] == "gp2"
    assert por_clave["bue"]["availability_zones"] == ["us-east-1-bue-1a"]
    assert por_clave["bue"]["instances"] == 1 and por_clave["sp"]["instances"] == 0
    assert por_clave["sp"]["types"][0]["instance_type"] == "t3.xlarge"
    assert all(zona["error"] is None for zona in listado["zones"])

    cuerpo = admin_autoscale.ZoneBody(zone="sp", motivo="cambio de zona de lanzamiento")
    cambiado = asyncio.run(admin_autoscale.put_autoscale_zone(cuerpo))

    assert cambiado["success"] is True and cambiado["active"] == "sp"
    assert cambiado["zone"]["region"] == "sa-east-1"
    assert zone_state.active_zone() == "sp"
    assert controller.resolve_zone() == "sp"  # el alta automática pasa a sp


def test_endpoint_zones_informa_el_error_de_la_zona_que_falla(monkeypatch, admin_sin_efectos):
    admin_autoscale = admin_sin_efectos
    flotas = {"bue": FakeFleet(_catalogo((8.0, 4, "c5.xlarge"))), "sp": FakeFleet()}

    def explota():
        raise RuntimeError("AccessDenied")

    flotas["sp"].list_instance_types = explota
    _flota_por_zona(monkeypatch, flotas)

    listado = asyncio.run(admin_autoscale.get_zones())

    por_clave = {zona["key"]: zona for zona in listado["zones"]}
    assert por_clave["sp"]["error"] == "RuntimeError" and por_clave["sp"]["types"] == []
    assert por_clave["bue"]["error"] is None
    assert [t["instance_type"] for t in por_clave["bue"]["types"]] == ["c5.xlarge"]


def test_instance_types_acepta_zone_y_rechaza_zonas_invalidas(monkeypatch, admin_sin_efectos):
    from fastapi import HTTPException

    admin_autoscale = admin_sin_efectos
    flotas = {
        "bue": FakeFleet(_catalogo((8.0, 4, "c5.xlarge"))),
        "sp": FakeFleet(_catalogo((16.0, 4, "t3.xlarge"))),
    }
    _flota_por_zona(monkeypatch, flotas)

    sp = asyncio.run(admin_autoscale.get_instance_types(zone="sp"))
    activo = asyncio.run(admin_autoscale.get_instance_types())

    assert sp["zone"] == "sp" and sp["active"] == "bue"
    assert [t["instance_type"] for t in sp["types"]] == ["t3.xlarge"]
    assert activo["zone"] == "bue"
    assert activo["types"][0]["recommended_max"] == 5

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_autoscale.get_instance_types(zone="marte"))
    assert exc.value.status_code == 400


def test_autoscale_informa_la_zona_activa_y_la_de_cada_efimera(monkeypatch, admin_sin_efectos):
    admin_autoscale = admin_sin_efectos
    monkeypatch.setattr(
        controller, "build_snapshot", lambda: asyncio.sleep(0, result={"efimeros": []})
    )
    monkeypatch.setattr(controller, "_make_fleet", lambda zone=None: _SinAws())
    EPHEMERAL_WORKERS["i-sin-zona"] = {"instance_id": "i-sin-zona", "state": "running"}
    EPHEMERAL_WORKERS["i-en-sp"] = {"instance_id": "i-en-sp", "state": "running", "zone": "sp"}

    data = asyncio.run(admin_autoscale.get_autoscale())

    assert data["zone"]["key"] == "bue"
    assert data["zone"]["label"] == "N. Virginia - Buenos Aires"
    assert data["config"]["ec2"] is None and data["aws_error"] == "RuntimeError"
    zonas = {item["instance_id"]: item["zone"] for item in data["efimeros"]}
    assert zonas == {"i-sin-zona": "bue", "i-en-sp": "sp"}


class _SinAws:
    """Flota que simula AWS caído: el estado del panel debe seguir disponible."""

    def list_managed(self):
        raise RuntimeError("AWS no disponible")

    def estimate_cost(self, workers, now):
        return 0.0

    def instance_runtime(self):
        raise RuntimeError("AWS no disponible")

    def list_instance_types(self):
        raise RuntimeError("AWS no disponible")


def test_catalogo_descarta_tipos_que_no_corren_un_worker():
    """t2.nano/t1.micro no pueden con Chromium: el catálogo los deja afuera."""
    from unittest.mock import Mock

    from central_api.autoscaler.ec2 import Ec2Fleet, Ec2Settings

    client = Mock()
    client.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s-1", "AvailabilityZone": "sa-east-1a"}]}
    client.describe_instance_type_offerings.return_value = {"InstanceTypeOfferings": [
        {"InstanceType": "t2.nano"}, {"InstanceType": "c5.large"}, {"InstanceType": "c5.xlarge"}]}
    client.describe_instance_types.return_value = {"InstanceTypes": [
        {"InstanceType": "t2.nano", "VCpuInfo": {"DefaultVCpus": 1}, "MemoryInfo": {"SizeInMiB": 512}},
        {"InstanceType": "c5.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 4096}},
        {"InstanceType": "c5.xlarge", "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 8192}},
    ]}
    rows = Ec2Fleet(Ec2Settings(subnet_ids=("s-1",)), client=client).list_instance_types()
    assert [r["instance_type"] for r in rows] == ["c5.large", "c5.xlarge"]
    assert rows[0]["max_concurrency"] == 4 and rows[1]["recommended_max"] == 5
