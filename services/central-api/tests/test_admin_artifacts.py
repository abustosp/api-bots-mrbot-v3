from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from central_api.main import create_app
from central_api.settings import get_settings

H = {"Authorization": "Bearer tok-art"}


class FakeS3:
    def __init__(self) -> None:
        self.objs = {
            "misfacilidades": {
                "viejo.pdf": (datetime(2026, 1, 5, tzinfo=timezone.utc), 10),
                "nuevo.pdf": (datetime(2026, 6, 5, tzinfo=timezone.utc), 20),
            },
            "mrbot-bots-api-bkp-db": {"dump.tgz": (datetime(2026, 1, 5, tzinfo=timezone.utc), 99)},
        }

    def list_objects_v2(self, Bucket, **_):
        return {"Contents": [{"Key": k, "LastModified": m, "Size": s} for k, (m, s) in self.objs[Bucket].items()], "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objs[Bucket].pop(o["Key"], None)
        return {}


def _cliente(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "tok-art")
    get_settings.cache_clear()
    fake = FakeS3()
    monkeypatch.setattr("central_api.admin.backups._cliente_s3", lambda _s: fake)
    return TestClient(create_app()), fake


def test_listado_excluye_backups(monkeypatch) -> None:
    c, _ = _cliente(monkeypatch)
    buckets = c.get("/admin/artifacts/buckets", headers=H).json()["buckets"]
    assert "misfacilidades" in buckets and "mrbot-bots-api-bkp-db" not in buckets


def test_backups_rechazado_y_requiere_confirmacion(monkeypatch) -> None:
    c, fake = _cliente(monkeypatch)
    r = c.post("/admin/artifacts/purge", headers=H, json={"buckets": ["mrbot-bots-api-bkp-db"], "todos": True, "dry_run": False, "motivo": "x" * 12, "confirmation_text": "ELIMINAR"})
    assert r.status_code == 400 and "dump.tgz" in fake.objs["mrbot-bots-api-bkp-db"]
    r = c.post("/admin/artifacts/purge", headers=H, json={"buckets": ["misfacilidades"], "todos": True, "dry_run": False, "motivo": "x" * 12})
    assert r.status_code == 400 and len(fake.objs["misfacilidades"]) == 2


def test_dry_run_y_rango(monkeypatch) -> None:
    c, fake = _cliente(monkeypatch)
    body = {"buckets": ["misfacilidades"], "hasta": "2026-03-01"}
    r = c.post("/admin/artifacts/purge", headers=H, json=body).json()
    assert r["resultados"][0]["objetos"] == 1 and len(fake.objs["misfacilidades"]) == 2
    r = c.post("/admin/artifacts/purge", headers=H, json={**body, "dry_run": False, "motivo": "limpieza test", "confirmation_text": "eliminar"}).json()
    assert r["resultados"][0]["eliminados"] == 1 and list(fake.objs["misfacilidades"]) == ["nuevo.pdf"]


def test_todos_borra_todo_y_exige_rango_o_todos(monkeypatch) -> None:
    c, fake = _cliente(monkeypatch)
    assert c.post("/admin/artifacts/purge", headers=H, json={"buckets": ["misfacilidades"]}).status_code == 400
    c.post("/admin/artifacts/purge", headers=H, json={"buckets": ["misfacilidades"], "todos": True, "dry_run": False, "motivo": "limpieza test", "confirmation_text": "ELIMINAR"})
    assert fake.objs["misfacilidades"] == {}
