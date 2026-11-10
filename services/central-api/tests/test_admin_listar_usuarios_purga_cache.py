"""Con PostgreSQL configurado, un usuario borrado de la base no sigue listado desde la caché."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from central_api.admin import users as admin_users  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def test_listado_descarta_usuarios_que_ya_no_estan_en_pg(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    get_settings.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    admin_users.USERS["fantasma"] = admin_users.AdminUser(
        id="fantasma", email="fantasma@x.com", estado="habilitado", plan="prueba"
    )
    admin_users.USERS["vivo"] = admin_users.AdminUser(
        id="vivo", email="vivo@x.com", estado="habilitado", plan="prueba"
    )

    async def filas():
        return [("vivo", "vivo@x.com", True)], []

    async def vacio():
        return {}

    monkeypatch.setattr("central_api.db.db_configurado", lambda: True)
    monkeypatch.setattr(admin_users, "_filas_identidad_pg", filas)
    monkeypatch.setattr(admin_users, "_cuentas_billing_pg", vacio)
    monkeypatch.setattr(admin_users, "_periodos_vigentes_pg", vacio)

    r = TestClient(create_app()).get("/admin/users", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    assert [u["email"] for u in r.json()["usuarios"]] == ["vivo@x.com"]
    assert "fantasma" not in admin_users.USERS
    admin_users.USERS.clear()
    get_settings.cache_clear()
