"""Verificación E2E wave1 con stdlib-only (sin fastapi/pydantic).

Cubre los módulos puros nuevos: billing (entitlements, facade,
reservation, reconciliation), claim SQL, sellado worker, contexto del
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
            RAIZ / "services/central-api/src/central_api/billing/facade.py",
            RAIZ / "services/central-api/src/central_api/billing/reservation.py",
            RAIZ / "services/central-api/src/central_api/billing/reconciliation.py",
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
    """Tope duro de capacidad 5 (W-2) en central y settings."""

    def test_max_worker_capacity_5(self):
        ruta = RAIZ / "services/central-api/src/central_api/internal/workers.py"
        self.assertEqual(_constante_numérica(ruta, "MAX_WORKER_CAPACITY"), 5)

    def test_settings_capacity_5(self):
        texto = (RAIZ / "services/central-api/src/central_api/settings.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("worker_capacity: int = 5", texto)

    def test_registro_recorta_a_5(self):
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


class TestEntitlements(unittest.TestCase):
    """Catálogo de tiers y períodos (stdlib-only)."""

    def setUp(self):
        from central_api.billing import entitlements as e

        self.e = e
        e.PERIODS.clear()

    def test_tiers_y_concurrencia_tope_5(self):
        for código in ("free", "basico", "pro", "empresa"):
            plan = self.e.get_plan(código)
            self.assertIsNotNone(plan)
            self.assertLessEqual(plan.concurrencia_max, 5)
        self.assertEqual(self.e.get_plan("empresa").concurrencia_max, 5)
        self.assertIsNone(self.e.get_plan("inexistente"))

    def test_bot_habilitado_por_plan(self):
        free = self.e.get_plan("free")
        self.assertTrue(self.e.bot_enabled_for_plan(free, "consulta_cuit"))
        self.assertFalse(self.e.bot_enabled_for_plan(free, "mis_comprobantes"))

    def test_periodo_idempotente_y_forzado(self):
        p1 = self.e.open_period("u1", "free")
        p2 = self.e.open_period("u1", "pro")
        self.assertIs(p1, p2)  # un solo ABIERTO por usuario
        self.assertIsNotNone(self.e.current_period("u1"))
        self.assertEqual(self.e.credit_cost("consulta_cuit", "consulta"), 1)
        with self.assertRaises(ValueError):
            self.e.force_period("u1", "pro", "  ")
        nuevo = self.e.force_period("u1", "pro", "prueba")
        self.assertEqual(nuevo.plan_code, "pro")
        self.assertEqual(p1.estado, "CERRADO")


class TestFacade(unittest.TestCase):
    """Montos exactos e idempotencia de pagos (stdlib-only)."""

    def setUp(self):
        from central_api.billing import facade as f

        self.f = f
        f.PAYMENTS.clear()

    def test_montos_sin_float(self):
        self.assertEqual(str(self.f.a_monto_mp(19900)), "199")
        self.assertEqual(self.f.desde_monto_mp("199.00"), 19900)
        self.assertEqual(self.f.desde_monto_mp(self.f.a_monto_mp(19900)), 19900)

    def test_idempotencia_estable(self):
        k1 = self.f.mp_idempotency_key("p1", 2)
        self.assertEqual(k1, self.f.mp_idempotency_key("p1", 2))
        self.assertNotEqual(k1, self.f.mp_idempotency_key("p1", 3))

    def test_webhook_no_acredita_sin_reconsulta(self):
        pago = self.f.create_credit_payment("u1", "pack-10", "llave-1")
        remoto = {
            "status": "approved",
            "external_reference": "llave-1",
            "transaction_amount": "0",
            "currency_id": "ARS",
            "id": "mp-1",
        }
        self.f.apply_verified_payment(pago, remoto, reconsulted=False)
        self.assertEqual(pago.estado, "PENDIENTE")

    def test_reconsulta_con_contraste_y_doble_aplicacion(self):
        from central_api.billing import reservation as r

        r.USAGE.clear()
        r.CREDIT_MOVES.clear()
        r.BALANCES.clear()
        pago = self.f.create_credit_payment("u2", "pack-10", "llave-2")
        # Importe distinto: no acredita.
        remoto_malo = {
            "status": "approved",
            "external_reference": "llave-2",
            "transaction_amount": "9999.99",
            "currency_id": "ARS",
            "id": "mp-9",
        }
        self.f.apply_verified_payment(pago, remoto_malo, reconsulted=True)
        self.assertEqual(pago.estado, "PENDIENTE")
        self.assertEqual(r.get_balance("u2"), 0)
        # Precio real del pack-10 (0 centavos placeholder): acredita 10.
        remoto_ok = dict(remoto_malo, transaction_amount="0")
        self.f.apply_verified_payment(pago, remoto_ok, reconsulted=True)
        self.assertEqual(pago.estado, "APROBADO")
        self.assertEqual(r.get_balance("u2"), 10)
        # Reintento: idempotente, no duplica.
        self.f.apply_verified_payment(pago, remoto_ok, reconsulted=True)
        self.assertEqual(r.get_balance("u2"), 10)

    def test_payload_hash_no_guarda_payload(self):
        h1 = self.f.payload_hash(b"hola")
        self.assertEqual(len(h1), 64)
        self.assertEqual(h1, self.f.payload_hash(b"hola"))
        self.assertNotEqual(h1, self.f.payload_hash(b"chau"))


class TestReservation(unittest.TestCase):
    """Reserva/confirmación/liberación con una sola fuente (stdlib-only)."""

    def setUp(self):
        from central_api.billing import entitlements as e
        from central_api.billing import reservation as r

        self.e = e
        self.r = r
        e.PERIODS.clear()
        r.USAGE.clear()
        r.CREDIT_MOVES.clear()
        r.BALANCES.clear()

    def test_cuota_primero_y_luego_creditos(self):
        entrada = self.r.reserve_for_job("u1", "job-1", "consulta_cuit", "consulta")
        self.assertEqual((entrada.fuente_cobro, entrada.estado), ("CUOTA", "RESERVADO"))
        # Idempotente por job_id.
        self.assertEqual(self.r.reserve_for_job("u1", "job-1", "consulta_cuit", "consulta").id, entrada.id)
        # Confirmar mueve reservada -> consumida exactamente una vez.
        self.r.confirm_usage("job-1")
        periodo = self.e.current_period("u1")
        self.assertEqual((periodo.cuota_reservada, periodo.cuota_consumida), (0, 1))
        # Confirmar de nuevo no duplica.
        self.r.confirm_usage("job-1")
        self.assertEqual(periodo.cuota_consumida, 1)

    def test_creditos_como_overflow_y_liberacion(self):
        periodo = self.e.open_period("u2", "free")
        periodo.cuota_reservada = periodo.cuota_asignada  # sin cuota libre
        self.r.credit_purchase("u2", 5, "pay-1", idempotency_key="k-compra-1")
        # Compra idempotente.
        self.r.credit_purchase("u2", 5, "pay-1", idempotency_key="k-compra-1")
        self.assertEqual(self.r.get_balance("u2"), 5)
        entrada = self.r.reserve_for_job("u2", "job-2", "consulta_cuit", "consulta")
        self.assertEqual(entrada.fuente_cobro, "CREDITOS")
        self.assertEqual(self.r.get_balance("u2"), 4)
        self.r.release_usage("job-2", motivo="cancelado")
        self.assertEqual(self.r.get_balance("u2"), 5)
        self.assertEqual(self.r.get_usage("job-2").estado, "LIBERADO")

    def test_sin_fondos_lanza_quota_exhausted(self):
        periodo = self.e.open_period("u3", "free")
        periodo.cuota_reservada = periodo.cuota_asignada
        with self.assertRaises(self.r.QuotaExhausted):
            self.r.reserve_for_job("u3", "job-3", "consulta_cuit", "consulta")
        self.assertIsNone(self.r.get_usage("job-3"))

    def test_ajuste_exige_motivo_y_es_idempotente(self):
        with self.assertRaises(ValueError):
            self.r.adjust_credits("u4", 3, "  ")
        m1 = self.r.adjust_credits("u4", 3, "cortesía", referencia="ref-1")
        m2 = self.r.adjust_credits("u4", 3, "cortesía", referencia="ref-1")
        self.assertEqual(m1.id, m2.id)
        self.assertEqual(self.r.get_balance("u4"), 3)


class TestReconciliation(unittest.TestCase):
    """Conciliación local vs remoto reconsultado (stdlib-only)."""

    def test_sin_diferencias_concilia(self):
        from central_api.billing import reconciliation as c

        local = {"id": "p1", "estado": "approved", "importe": 19900,
                 "moneda": "ARS", "externo_id": "ref-1"}
        remoto = {"status": "approved", "transaction_amount": "199.00",
                  "currency_id": "ARS"}
        self.assertEqual(c.comparar_pago(local, remoto), [])
        informe = c.conciliar_lote([local], {"ref-1": remoto})
        self.assertEqual(informe["conciliados"], ["p1"])

    def test_detecta_estado_monto_moneda_y_falta_remoto(self):
        from central_api.billing import reconciliation as c

        local = {"id": "p2", "estado": "approved", "importe": 19900,
                 "moneda": "ARS", "externo_id": "ref-2"}
        remoto = {"status": "rejected", "transaction_amount": "10.00",
                  "currency_id": "USD"}
        difs = c.comparar_pago(local, remoto)
        self.assertIn("estado_distinto", difs)
        self.assertIn("monto_distinto", difs)
        self.assertIn("moneda_distinta", difs)
        informe = c.conciliar_lote([local], {})
        self.assertEqual(informe["sin_remoto"], ["p2"])
        self.assertEqual(informe["conciliados"], [])


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
            capacity = 5
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
        self.assertLessEqual(carga["capacity"], 5)
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
