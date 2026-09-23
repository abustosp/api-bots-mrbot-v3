"""Autenticación interna: espejo PG, tokens v1/v2 y sesiones admin.

Cubre los nuevos caminos sin base real: tokens ligados a nodo (v1) y a UUID
pleno (v2), registro idempotente con pubkey, latido con hora de servidor,
rechazo de material privado, DDL sin correlativos (I-1/I-2) y helpers de
sesión del panel. Se ejecuta con ``pytest`` desde ``services/central-api``.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from uuid import UUID

AQUI = Path(__file__).resolve()
SRC = AQUI.parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.models.base import new_uuid7  # noqa: E402
from central_api.models.fleet import Worker  # noqa: E402
from central_api.repositories.base import RepositoryError  # noqa: E402
from central_api.repositories.workers import WorkerRepository  # noqa: E402
from central_api.security import admin_sessions as sesiones  # noqa: E402
from central_api.security import worker_auth as wa  # noqa: E402

PUBKEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7b==\n"
    "-----END PUBLIC KEY-----"
)
PRIVKEY = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n"
    "-----END PRIVATE KEY-----"
)


class ResultadoFalso:
    """Resultado mínimo con ``scalar_one_or_none`` para el repositorio."""

    def __init__(self, fila: object) -> None:
        self._fila = fila

    def scalar_one_or_none(self) -> object:
        return self._fila


class SesionFalsa:
    """Sesión en memoria para probar el repositorio sin PostgreSQL."""

    def __init__(self, existente: Worker | None = None) -> None:
        self.existente = existente
        self.agregados: list = []

    async def execute(self, *args: object, **kwargs: object) -> ResultadoFalso:
        return ResultadoFalso(self.existente)

    def add(self, fila: object) -> None:
        self.agregados.append(fila)

    async def flush(self) -> None:
        return None

    async def get(self, modelo: object, clave: object) -> Worker | None:
        if self.existente is not None and getattr(self.existente, "id", None) == clave:
            return self.existente
        return None


def correr(coro):
    """Ejecuta una corrutina en un bucle nuevo (aislado por prueba)."""
    return asyncio.new_event_loop().run_until_complete(coro)


class TestTokensServicio(unittest.TestCase):
    """Tokens v1 (nodo) y v2 (UUID pleno) del borde interno."""

    def test_v1_acepta_nodo_con_puntos(self):
        token = wa.mint_service_token("clave", "10.0.0.11:8080")
        self.assertTrue(wa.check_service_token("clave", token, "10.0.0.11:8080"))

    def test_v1_rechaza_otro_nodo_y_manipulado(self):
        token = wa.mint_service_token("clave", "10.0.0.11:8080")
        self.assertFalse(wa.check_service_token("clave", token, "10.0.0.12:8080"))
        self.assertFalse(wa.check_service_token("otra", token, "10.0.0.11:8080"))
        self.assertFalse(wa.check_service_token("clave", token + "x", "10.0.0.11:8080"))
        self.assertFalse(wa.check_service_token("clave", "basura", "10.0.0.11:8080"))

    def test_v2_liga_uuid_y_protocolo_entero_1(self):
        wid = new_uuid7()
        self.assertEqual(wa.PROTOCOL_VERSION, 1)
        self.assertIsInstance(wa.PROTOCOL_VERSION, int)
        token = wa.mint_worker_token("clave", wid)
        self.assertTrue(wa.check_worker_token("clave", token, wid))
        self.assertFalse(wa.check_worker_token("clave", token, new_uuid7()))
        self.assertEqual(wa.worker_uuid_from_token(token), wid)

    def test_v2_no_acepta_v1_ni_reves(self):
        wid = new_uuid7()
        v1 = wa.mint_service_token("clave", "10.0.0.11:8080")
        v2 = wa.mint_worker_token("clave", wid)
        self.assertIsNone(wa.worker_uuid_from_token(v1))
        self.assertFalse(wa.check_worker_token("clave", v1, wid))
        self.assertFalse(wa.check_service_token("clave", v2, "10.0.0.11:8080"))

    def test_cualquiera_acepta_ambos_del_mismo_worker(self):
        wid = new_uuid7()
        v1 = wa.mint_service_token("clave", "10.0.0.11:8080")
        v2 = wa.mint_worker_token("clave", wid)
        self.assertTrue(wa.check_any_token("clave", v1, "10.0.0.11:8080", wid))
        self.assertTrue(wa.check_any_token("clave", v2, "10.0.0.11:8080", wid))
        self.assertTrue(wa.check_any_token("clave", v1, "10.0.0.11:8080", None))
        self.assertFalse(wa.check_any_token("clave", v2, "10.0.0.11:8080", None))


class TestRegistroEspejo(unittest.TestCase):
    """Registro idempotente con pubkey y tope 5 (espejo de PostgreSQL)."""

    def test_alta_guarda_pubkey_y_capacidad(self):
        sesion = SesionFalsa(None)
        fila = correr(
            WorkerRepository(sesion).register(  # type: ignore[arg-type]
                name="10.0.0.11:8080",
                endpoint_node="10.0.0.11:8080",
                app_version="3.0.0",
                allowed={"10.0.0.11:8080"},
                capacity=5,
                sealed_pubkey_pem=PUBKEY,
            )
        )
        self.assertIsInstance(fila.id, UUID)
        self.assertEqual(fila.sealed_pubkey_pem, PUBKEY)
        self.assertEqual(fila.capacity, 5)
        self.assertEqual(fila.protocol_version, 1)

    def test_reregistro_conserva_uuid_y_actualiza_pubkey(self):
        previo = Worker(
            id=new_uuid7(),
            name="10.0.0.11:8080",
            status="SANO",
            endpoint="10.0.0.11:8080",
            app_version="3.0.0",
            capacity=5,
            running_jobs=2,
            queued_jobs=0,
        )
        sesion = SesionFalsa(previo)
        fila = correr(
            WorkerRepository(sesion).register(  # type: ignore[arg-type]
                name="10.0.0.11:8080",
                endpoint_node="10.0.0.11:8080",
                app_version="3.0.1",
                allowed={"10.0.0.11:8080"},
                capacity=3,
                sealed_pubkey_pem=PUBKEY,
            )
        )
        self.assertEqual(fila.id, previo.id)
        self.assertEqual(fila.status, "REGISTRANDO")
        self.assertEqual(fila.capacity, 3)
        self.assertEqual(fila.sealed_pubkey_pem, PUBKEY)
        self.assertEqual(fila.running_jobs, 0)

    def test_rechaza_privada_fuera_de_inventario_y_sin_tope(self):
        sesion = SesionFalsa(None)
        with self.assertRaises(RepositoryError):
            correr(
                WorkerRepository(sesion).register(  # type: ignore[arg-type]
                    name="n",
                    endpoint_node="n",
                    app_version="x",
                    allowed={"n"},
                    sealed_pubkey_pem=PRIVKEY,
                )
            )
        with self.assertRaises(RepositoryError):
            correr(
                WorkerRepository(sesion).register(  # type: ignore[arg-type]
                    name="n",
                    endpoint_node="n",
                    app_version="x",
                    allowed=set(),
                )
            )
        with self.assertRaises(RepositoryError):
            correr(
                WorkerRepository(sesion).register(  # type: ignore[arg-type]
                    name="n",
                    endpoint_node="n",
                    app_version="x",
                    allowed={"n"},
                    capacity=6,
                )
            )
        with self.assertRaises(RepositoryError):
            correr(
                WorkerRepository(sesion).register(  # type: ignore[arg-type]
                    name="n",
                    endpoint_node="n",
                    app_version="x",
                    allowed={"n"},
                    protocol_version=99,
                )
            )

    def test_latido_actualiza_hora_y_respeta_tope(self):
        previo = Worker(
            id=new_uuid7(),
            name="10.0.0.11:8080",
            status="REGISTRANDO",
            endpoint="10.0.0.11:8080",
            app_version="3.0.0",
            capacity=5,
            running_jobs=0,
            queued_jobs=0,
        )
        sesion = SesionFalsa(previo)
        correr(
            WorkerRepository(sesion).heartbeat(  # type: ignore[arg-type]
                worker_id=previo.id,
                status="SANO",
                running_jobs=2,
                queued_jobs=0,
                capacity=5,
            )
        )
        self.assertEqual(previo.status, "SANO")
        self.assertIsNotNone(previo.last_heartbeat_at)
        self.assertEqual(len(sesion.agregados), 1)
        with self.assertRaises(RepositoryError):
            correr(
                WorkerRepository(sesion).heartbeat(  # type: ignore[arg-type]
                    worker_id=previo.id,
                    status="SANO",
                    running_jobs=6,
                    queued_jobs=0,
                    capacity=5,
                )
            )


class TestDDLSinCorrelativos(unittest.TestCase):
    """I-1/I-2: UUID de app, sin serial/identity/secuencias en lo nuevo."""

    def test_workers_y_admin_sessions_sin_correlativos(self):
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        from central_api.models.fleet import Worker as W
        from central_api.models.identity import AdminSession

        for modelo in (W, AdminSession):
            ddl = str(CreateTable(modelo.__table__).compile(dialect=postgresql.dialect()))
            for prohibido in ("SERIAL", "IDENTITY", "nextval", "SEQUENCE"):
                self.assertNotIn(prohibido, ddl.upper(), modelo.__tablename__)
        self.assertIn("sealed_pubkey_pem", W.__table__.columns)
        cols = AdminSession.__table__.columns
        self.assertIn("token_hash", cols)
        self.assertTrue(cols["id"].primary_key)


class TestSesionesAdmin(unittest.TestCase):
    """Tokens opacos del panel: solo vive el hash, cookie acotada."""

    def test_emitir_y_verificar(self):
        token = sesiones.mint_session_token()
        self.assertEqual(len(token), 64)
        resumen = sesiones.hash_session_token(token)
        self.assertNotIn(token, resumen)
        self.assertTrue(sesiones.verificar_token(token, resumen))
        self.assertFalse(sesiones.verificar_token(token + "0", resumen))
        self.assertFalse(sesiones.verificar_token("", resumen))

    def test_cookie_acotada_y_expiracion(self):
        params = sesiones.parametros_cookie()
        self.assertEqual(
            (params["key"], params["httponly"], params["secure"],
             params["samesite"], params["path"]),
            ("mrbot_admin_session", True, True, "lax", "/admin"),
        )
        delta = (sesiones.expira_en() - sesiones.utcnow()).total_seconds()
        self.assertTrue(7.9 * 3600 < delta <= 8 * 3600)


if __name__ == "__main__":
    unittest.main()
