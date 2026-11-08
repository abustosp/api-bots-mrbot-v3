import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "migrar_a_v3", Path(__file__).resolve().parents[1] / "tools/migracion_legacy/migrar_a_v3.py"
)
migracion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migracion)


def test_token_exigido_antes_de_cualquier_operacion_de_all(monkeypatch):
    monkeypatch.delenv("NEW_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["migrar_a_v3.py", "all", "--yes"])
    monkeypatch.setattr(migracion, "stage", lambda *args: pytest.fail("se inicio la migracion"))
    with pytest.raises(SystemExit, match="NEW_ADMIN_TOKEN"):
        migracion.main()


def test_token_solo_se_envia_por_stdin_y_no_aparece_en_error(monkeypatch):
    token = "secreto+de/prueba=="
    monkeypatch.setenv("NEW_ADMIN_TOKEN", token)
    monkeypatch.setattr("sys.argv", ["migrar_a_v3.py", "token", "--yes"])
    def ejecutar(args, **kwargs):
        assert token not in str(args)
        assert kwargs["input"] == token
        return SimpleNamespace(returncode=1, stdout="", stderr=token)
    monkeypatch.setattr(migracion.subprocess, "run", ejecutar)
    with pytest.raises(SystemExit) as error:
        migracion.main()
    assert token not in str(error.value)


def test_otro_subcomando_no_requiere_token(monkeypatch):
    monkeypatch.delenv("NEW_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["migrar_a_v3.py", "rehearsal"])
    monkeypatch.setattr(migracion, "do_rehearsal", lambda _: None)
    assert migracion.main() == 0


def test_cambio_remoto_conserva_token_y_no_lo_muestra(monkeypatch, tmp_path, capsys):
    import subprocess
    import time

    token = "valor+seguro/123=="
    archivo = tmp_path / ".env"
    archivo.write_text("OTRA=1\nADMIN_TOKEN=anterior\nFIN=2\n")
    monkeypatch.setattr(migracion, "REMOTE_COMPOSE", str(tmp_path))
    ejecutar_real = subprocess.run
    def ejecutar(args, **kwargs):
        assert token not in str(args)
        if "urllib.request" in args[-1]:
            return SimpleNamespace(returncode=0, stdout="200 403\n")
        return ejecutar_real(args[-1], shell=True, **kwargs)
    monkeypatch.setattr(migracion.subprocess, "run", ejecutar)
    monkeypatch.setattr(migracion, "ssh", lambda *args, **kwargs: SimpleNamespace(stdout="200"))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    migracion.set_admin_token(token)
    assert archivo.read_text() == f"OTRA=1\nADMIN_TOKEN={token}\nFIN=2\n"
    assert next(tmp_path.glob(".env.bak-*")).read_text() == "OTRA=1\nADMIN_TOKEN=anterior\nFIN=2\n"
    assert token not in capsys.readouterr().out
