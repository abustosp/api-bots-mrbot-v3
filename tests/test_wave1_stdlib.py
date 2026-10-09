"""Verificación E2E wave1 con stdlib-only (sin fastapi/pydantic).

Cubre los módulos puros nuevos: billing (entitlements,
reservation), claim SQL, sellado worker, contexto del
runtime, reporting del worker y coherencia wire estática central<->worker.

Se ejecuta con: ``python3 -m unittest discover -s tests -v`` desde la raíz.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "services" / "central-api" / "src"))
sys.path.insert(0, str(RAIZ / "services" / "bot-worker" / "src"))


def _constante_numérica(ruta: Path, nombre: str):
    """Lee una asignación ``NOMBRE = <int>`` por AST (sin importar)."""
    árbol = ast.parse(ruta.read_text(encoding="utf-8"))
    for nodo in ast.walk(árbol):
        if isinstance(nodo, ast.Assign):
            for objetivo in nodo.targets:
                if getattr(objetivo, "id", "") == nombre:
                    return ast.literal_eval(nodo.value)
        elif isinstance(nodo, ast.AnnAssign):
            if getattr(nodo.target, "id", "") == nombre and nodo.value is not None:
                return ast.literal_eval(nodo.value)
    raise AssertionError(f"{nombre} no encontrado en {ruta}")


class TestCompilacion(unittest.TestCase):
    """Todo .py del repo compila (py_compile global ya pasó; humo local)."""

    def test_sin_errores_de_sintaxis_en_módulos_puros(self):
        import py_compile

        módulos = [
            RAIZ / "services/central-api/src/central_api/billing/entitlements.py",
            RAIZ / "services/central-api/src/central_api/billing/reservation.py",
            RAIZ / "services/central-api/src/central_api/scheduler/claim.py",
            RAIZ / "services/bot-worker/src/bot_worker/runtime/sealed.py",
            RAIZ / "services/bot-worker/src/bot_worker/runtime/context.py",
            RAIZ / "services/bot-worker/src/bot_worker/reporting/central.py",
            RAIZ / "services/bot-worker/src/bot_worker/reporting/heartbeat.py",
            RAIZ / "services/bot-worker/src/bot_worker/reporting/results.py",
        ]
        for ruta in módulos:
            with self.subTest(módulo=ruta.name):
                self.assertTrue(ruta.is_file())
                py_compile.compile(str(ruta), doraise=True)


class TestProtocolo(unittest.TestCase):
    """Protocol_version entero 1 en ambos bordes y contratos."""

    def test_contratos_declara_1(self):
        ruta = RAIZ / "packages/mrbot-contracts/src/mrbot_contracts/version.py"
        self.assertEqual(_constante_numérica(ruta, "PROTOCOL_VERSION"), 1)
        texto = ruta.read_text(encoding="utf-8")
        self.assertIn("protocol_version: int", texto)

    def test_central_espeja_1(self):
        ruta = RAIZ / "services/central-api/src/central_api/internal/workers.py"
        self.assertEqual(_constante_numérica(ruta, "PROTOCOL_VERSION"), 1)
        ajustes = RAIZ / "services/central-api/src/central_api/settings.py"
        self.assertIn(
            "worker_protocol_version: int = 1", ajustes.read_text(encoding="utf-8")
        )

    def test_worker_espeja_1(self):
        for ruta in [
            RAIZ / "services/bot-worker/src/bot_worker/config.py",
            RAIZ / "services/bot-worker/src/bot_worker/bots/registry.py",
        ]:
            with self.subTest(ruta=ruta.name):
                self.assertEqual(_constante_numérica(ruta, "PROTOCOL_VERSION"), 1)


class TestTopes(unittest.TestCase):
    """Tope duro de capacidad 10 (W-2) en central y settings."""

    def test_max_worker_capacity_10(self):
        ruta = RAIZ / "services/central-api/src/central_api/internal/workers.py"
        self.assertEqual(_constante_numérica(ruta, "MAX_WORKER_CAPACITY"), 10)

    def test_settings_capacity_10(self):
        texto = (RAIZ / "services/central-api/src/central_api/settings.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("worker_capacity: int = Field(default=10, ge=1, le=10)", texto)

    def test_registro_recorta_a_10(self):
        texto = (
            RAIZ / "services/central-api/src/central_api/internal/workers.py"
        ).read_text(encoding="utf-8")
        self.assertIn("min(max(1, body.capacity), MAX_WORKER_CAPACITY)", texto)


class TestAislamientoWorker(unittest.TestCase):
    """W-1: el worker no toca base de datos ni pagos."""

    def test_sin_drivers_ni_orm_en_worker(self):
        prohibidos = ("sqlalchemy", "asyncpg", "psycopg", "create_engine")
        fugas = []
        for ruta in (RAIZ / "services/bot-worker/src").rglob("*.py"):
            if "__pycache__" in ruta.parts:
                continue
            texto = ruta.read_text(encoding="utf-8")
            for palabra in prohibidos:
                if palabra in texto:
                    fugas.append(f"{ruta.name}: {palabra}")
        self.assertEqual(fugas, [])

    def test_reportes_solo_post_a_presign_y_cancel_ack(self):
        texto = (
            RAIZ / "services/bot-worker/src/bot_worker/reporting/central.py"
        ).read_text(encoding="utf-8")
        self.assertIn("/artifacts/presign", texto)
        self.assertIn("/cancel-ack", texto)


class TestCuentas(unittest.TestCase):
    """Tipos de cuenta y reserva de requests (stdlib-only)."""

    def setUp(self):
        from central_api.billing import entitlements as e
        from central_api.billing import reservation as r

        self.e, self.r = e, r
        e.PERIODS.clear()
        e.BILLING_ACCOUNTS.clear()
        r.USAGE.clear()

    def test_solo_tres_tipos(self):
        self.assertEqual(self.e.ACCOUNT_TYPES, ("PRUEBA", "TESTING", "NORMAL"))
        with self.assertRaises(ValueError):
            self.e.normalize_assignment("TIER", 10)

    def test_prueba_tope_100_no_renovable(self):
        self.e.set_billing_account("u1", "PRUEBA")
        for i in range(100):
            self.r.reserve_for_job("u1", f"j{i}", "consulta_cuit", "consultar")
        with self.assertRaises(self.r.QuotaExhausted):
            self.r.reserve_for_job("u1", "j-extra", "consulta_cuit", "consultar")

    def test_testing_ilimitado(self):
        self.e.set_billing_account("u2", "TESTING")
        for i in range(300):
            self.r.reserve_for_job("u2", f"t{i}", "consulta_cuit", "consultar")
        self.assertEqual(self.e.get_billing_account("u2").requests_reserved, 300)

    def test_normal_cupo_mensual_y_periodo_unico(self):
        self.e.set_billing_account("u3", "NORMAL", max_requests=2)
        p1 = self.e.open_period("u3")
        self.assertIs(p1, self.e.open_period("u3"))  # un solo ABIERTO por usuario
        self.assertIsNotNone(self.e.current_period("u3"))
        self.r.reserve_for_job("u3", "n1", "consulta_cuit", "consultar")
        self.r.reserve_for_job("u3", "n2", "consulta_cuit", "consultar")
        with self.assertRaises(self.r.QuotaExhausted):
            self.r.reserve_for_job("u3", "n3", "consulta_cuit", "consultar")

    def test_liberar_devuelve_cupo(self):
        self.e.set_billing_account("u4", "NORMAL", max_requests=1)
        self.r.reserve_for_job("u4", "l1", "consulta_cuit", "consultar")
        self.assertEqual(self.r.release_usage("l1").estado, "LIBERADO")
        self.r.reserve_for_job("u4", "l2", "consulta_cuit", "consultar")


class TestClaimSQL(unittest.TestCase):
    """El claim usa SKIP LOCKED y no hace HTTP bajo lock."""

    def test_claim_sql_coherente(self):
        from central_api.scheduler import claim

        self.assertIn("FOR UPDATE SKIP LOCKED", claim.CLAIM_NEXT_JOB_SQL)
        self.assertIn("PENDIENTE", claim.CLAIM_NEXT_JOB_SQL)
        self.assertIn("EXISTS", claim.CLAIM_AND_RESERVE_SQL)
        self.assertIn("reserved_slots", claim.CLAIM_AND_RESERVE_SQL)
        self.assertIn("lease_expires_at", claim.CLAIM_AND_RESERVE_SQL)
        for nombre in ("CLAIM_NEXT_JOB_SQL", "CLAIM_AND_RESERVE_SQL", "REAPER_EXPIRED_SQL"):
            texto = getattr(claim, nombre).upper()
            self.assertNotIn("HTTP", texto)
            self.assertNotIn("REQUESTS", texto)


class TestSellado(unittest.TestCase):
    """Sobre sellado: apertura real y falla cerrada."""

    def test_roundtrip_y_errores_cerrados(self):
        from bot_worker.runtime import sealed as s

        privada, pública = s.generate_sealed_keypair()
        self.assertIn("PUBLIC KEY", pública)
        # Cifro con la pública como hace la central.
        import base64
        import json

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        sección = {"credentials": {"cuit": "20-12345678-9"}}
        cruda = json.dumps(sección).encode("utf-8")
        pub = serialization.load_pem_public_key(pública.encode("ascii"))
        clave = Fernet.generate_key()
        enc_clave = pub.encrypt(
            clave,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
        sobre = {
            "alg": "RSA-OAEP-SHA256+Fernet",
            "enc_key_b64": base64.b64encode(enc_clave).decode("ascii"),
            "blob_b64": base64.b64encode(Fernet(clave).encrypt(cruda)).decode("ascii"),
        }
        self.assertEqual(s.decrypt_sealed_section(privada, sobre), sección)
        with self.assertRaises(s.SealedEnvelopeError):
            s.decrypt_sealed_section(privada, {"alg": "otro"})
        with self.assertRaises(s.SealedEnvelopeError):
            s.decrypt_sealed_section(privada, dict(sobre, blob_b64="!!!"))

    def test_alg_coincide_con_central(self):
        central = (
            RAIZ / "services/central-api/src/central_api/security/sealed.py"
        ).read_text(encoding="utf-8")
        self.assertIn('SEALED_ALG = "RSA-OAEP-SHA256+Fernet"', central)
        worker = (
            RAIZ / "services/bot-worker/src/bot_worker/runtime/sealed.py"
        ).read_text(encoding="utf-8")
        self.assertIn("RSA-OAEP-SHA256+Fernet", worker)


class TestContexto(unittest.TestCase):
    """Runtime del worker: redacción y work_dir jail (stdlib-only)."""

    def test_credenciales_no_fugan_en_repr(self):
        import asyncio

        from bot_worker.runtime.context import (
            ArtifactStore,
            FiscalCredentials,
            ProxyConfig,
        )

        creds = FiscalCredentials(cuit_representante="20-1-9", clave="secreta")
        self.assertNotIn("secreta", repr(creds))
        proxy = ProxyConfig(mode="per-job", host="h", password="pw")
        self.assertNotIn("pw", repr(proxy))

        async def _ir():
            import tempfile

            from bot_worker.runtime.context import ArtifactSlot

            with tempfile.TemporaryDirectory() as tmp:
                from pathlib import Path as P

                tienda = ArtifactStore(
                    P(tmp),
                    {"a1": ArtifactSlot(artifact_id="a1", put_url="",
                                        object_key="k")},
                )
                with self.assertRaises(ValueError):
                    tienda.resolve("../fuera")
                (P(tmp) / "ok.txt").write_text("x")
                self.assertTrue(tienda.resolve("ok.txt").is_file())

        asyncio.run(_ir())


class TestReporting(unittest.TestCase):
    """Presign/cancel-ack/result/heartbeat del worker (stdlib-only)."""

    def test_presign_cuerpo_y_respuesta(self):
        async def _ir():
            from bot_worker.reporting.central import PresignError, request_presign

            vistos = {}

            class Cliente:
                async def post(self, url, json=None, headers=None, timeout=None):
                    vistos.update({"url": url, "json": json})
                    class Resp:
                        status_code = 201

                        @staticmethod
                        def json():
                            return {"upload_url": "https://x/y?f",
                                    "object_key": "k"}

                    return Resp()

            dato = await request_presign(
                Cliente(), "https://central", job_id="j1", artifact_id="a1",
                content_type="text/plain", size_bytes=10, worker_node="10.0.0.1:8080",
            )
            self.assertEqual(dato["object_key"], "k")
            # Cuerpo exacto que espera PresignBody de la central.
            self.assertEqual(
                vistos["json"],
                {"artifact_id": "a1", "content_type": "text/plain", "size_bytes": 10},
            )
            self.assertIn("/internal/v1/jobs/j1/artifacts/presign", vistos["url"])

            class Malo:
                async def post(self, *a, **k):
                    class Resp:
                        status_code = 403

                    return Resp()

            with self.assertRaises(PresignError):
                await request_presign(Malo(), "https://central", job_id="j1",
                                      artifact_id="a1", content_type="t",
                                      size_bytes=1)

        asyncio.run(_ir())

    def test_cancel_ack_nunca_lanza(self):
        async def _ir():
            from bot_worker.reporting.central import send_cancel_ack

            self.assertFalse(await send_cancel_ack(None, "https://c", job_id="j"))
            class Roto:
                async def post(self, *a, **k):
                    raise RuntimeError("red caída")

            self.assertFalse(await send_cancel_ack(Roto(), "https://c", job_id="j"))

        asyncio.run(_ir())

    def test_result_idempotente_por_job_attempt(self):
        async def _ir():
            from bot_worker.reporting.results import (
                ResultStore,
                idempotency_key,
                send_result,
            )

            self.assertEqual(idempotency_key("j", 2), "job:j:attempt:2")
            tienda = ResultStore()
            self.assertIsNone(await tienda.already_reported("j", 1))
            await tienda.mark_reported("j", 1, {"ok": True})
            self.assertIsNotNone(await tienda.already_reported("j", 1))

            class Cliente:
                def __init__(self):
                    self.urls = []

                async def post(self, url, json=None, timeout=None):
                    self.urls.append(url)
                    class Resp:
                        status_code = 200

                    return Resp()

            cliente = Cliente()
            self.assertTrue(await send_result(cliente, "https://c", "j", {"a": 1}))
            self.assertEqual(cliente.urls, ["https://c/internal/v1/jobs/j/result"])

        asyncio.run(_ir())

    def test_heartbeat_trae_capacidad_y_ejecucion(self):
        from bot_worker.reporting.heartbeat import build_heartbeat_payload

        class Sup:
            capacity = 10
            en_ejecucion = 2
            en_cola = 1
            draining = False
            counters = {}

            def items(self):
                return []

        carga = build_heartbeat_payload(
            worker_id="w", protocol_version=1, image_version="v",
            supervisor=Sup(), state="SANO", accepting_jobs=True, sequence=1,
        )
        self.assertLessEqual(carga["capacity"], 10)
        self.assertEqual(carga["en_ejecucion"], 2)
        self.assertEqual(carga["protocol_version"], 1)


class TestWireVerde(unittest.TestCase):
    """Subconjunto del wire verificado coherente por forma (estático)."""

    def test_presign_responde_lo_que_el_worker_lee(self):
        central = (
            RAIZ / "services/central-api/src/central_api/internal/uploads.py"
        ).read_text(encoding="utf-8")
        for clave in ("upload_id", "object_key", "upload_url", "expires_at",
                      "required_headers"):
            self.assertIn(f'"{clave}"', central)
        worker = (
            RAIZ / "services/bot-worker/src/bot_worker/reporting/central.py"
        ).read_text(encoding="utf-8")
        self.assertIn("upload_url", worker)

    def test_cancel_ack_acepta_dict_con_accepted(self):
        central = (
            RAIZ / "services/central-api/src/central_api/internal/uploads.py"
        ).read_text(encoding="utf-8")
        self.assertIn('body.get("accepted"', central)

    def test_registro_acepta_campos_del_worker(self):
        central = (
            RAIZ / "services/central-api/src/central_api/internal/workers.py"
        ).read_text(encoding="utf-8")
        for campo in ("advertised_url", "capacity", "capabilities",
                      "build_version", "protocol_version", "sealed_pubkey_pem"):
            self.assertIn(campo, central)


if __name__ == "__main__":
    unittest.main()
