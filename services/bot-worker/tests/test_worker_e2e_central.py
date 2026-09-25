"""E2E del worker contra una central local falsa (stdlib, sin red real).

Verifica el cableado completo ya implementado en el worker: subida de
artefactos por PUT a URLs prefirmadas (directa y vía presign cuando el
slot no trae URL), reporte de resultado idempotente con reintento solo
de transporte, acuse cooperativo de cancelación, fábrica de navegador
(real si hay Playwright o stub solo-dev) y drenaje ante SIGTERM sin
perder jobs. Nada aquí toca base de datos (W-1) ni secretos reales.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from bot_worker.config import PROTOCOL_VERSION, WorkerConfig
from bot_worker.main import create_app
from bot_worker.reporting.central import request_presign, send_cancel_ack
from bot_worker.reporting.results import (
    ResultStore,
    idempotency_key,
    send_result,
)
from bot_worker.runtime.arca_login import ArcaLoginError
from bot_worker.runtime.browser import build_browser_factory
from bot_worker.runtime.context import ArtifactSlot, ArtifactStore
from bot_worker.scheduler.supervisor import (
    JobSupervisor,
    LocalJob,
    WorkerDraining,
)

_TOKEN_FICTICIO = "token-ficticio-e2e"


class _CentralFalsaHandler(BaseHTTPRequestHandler):
    """Central mínima: presign, PUT de artefactos, result y cancel-ack."""

    server_version = "CentralFalsa/3.0"

    def log_message(self, *args):  # type: ignore[no-untyped-def]
        """Silencia el log del servidor de prueba."""

    def _base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def _json(self, codigo: int, cuerpo: dict) -> None:
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _cuerpo(self) -> bytes:
        largo = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(largo) if largo else b""

    def do_PUT(self):  # noqa: N802
        """Guarda los bytes subidos a la URL prefirmada."""
        datos = self._cuerpo()
        self.server.pendientes_put.append(
            {"ruta": self.path, "bytes": datos, "tipo": self.headers.get("Content-Type")}
        )
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        """Atiende presign, result (falla una vez) y cancel-ack."""
        datos = self._cuerpo()
        ruta = self.path
        if ruta.endswith("/artifacts/presign"):
            pedido = json.loads(datos or b"{}")
            artifact_id = pedido.get("artifact_id", "desconocido")
            self.server.pendientes_presign.append(pedido)
            self._json(
                200,
                {
                    "upload_id": "up-falsa-1",
                    "object_key": f"jobs/falso/1/{artifact_id}",
                    "upload_url": f"{self._base()}/upload/{artifact_id}",
                    "expires_at": "2026-09-19T00:15:00Z",
                    "required_headers": {"Content-Type": pedido.get("content_type", "")},
                },
            )
        elif ruta.endswith("/result"):
            self.server.pendientes_result.append(json.loads(datos or b"{}"))
            # Primera entrega falla: el worker debe reintentar el mismo
            # reporte (misma clave idempotente) sin reejecutar el bot.
            if len(self.server.pendientes_result) == 1:
                self._json(500, {"code": "ERROR"})
            else:
                self._json(200, {"confirmado": True})
        elif ruta.endswith("/cancel-ack"):
            self.server.pendientes_cancel_ack.append(json.loads(datos or b"{}"))
            self._json(200, {"recibido": True})
        elif "/events" in ruta or "/heartbeat" in ruta:
            self._json(200, {"recibido": True})
        else:
            self._json(404, {"code": "DESCONOCIDO"})


def _central_falsa() -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Levanta la central falsa en un puerto efímero de loopback."""
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _CentralFalsaHandler)
    servidor.pendientes_put = []  # type: ignore[attr-defined]
    servidor.pendientes_presign = []  # type: ignore[attr-defined]
    servidor.pendientes_result = []  # type: ignore[attr-defined]
    servidor.pendientes_cancel_ack = []  # type: ignore[attr-defined]
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, hilo


def _base_url(servidor: ThreadingHTTPServer) -> str:
    """URL base de la central falsa."""
    return f"http://127.0.0.1:{servidor.server_port}"


def _workdir(tmp_path: Path, contenido: bytes = b"dato-fiscal-1") -> Path:
    """Crea un work_dir temporal con un artefacto de prueba."""
    raiz = tmp_path / "work"
    raiz.mkdir()
    (raiz / "resultado.csv").write_bytes(contenido)
    return raiz


async def _flujo_e2e(base: str, raiz: Path, con_url: bool) -> dict:
    """Corre presign (si el slot no trae URL), PUT, result y cancel-ack."""
    artifact_id = str(uuid.uuid4())
    slot = ArtifactSlot(
        artifact_id=artifact_id,
        put_url=f"{base}/upload/{artifact_id}" if con_url else "",
        object_key=f"jobs/falso/1/{artifact_id}",
        max_bytes=52_428_800,
        content_types=("text/csv",),
    )
    async with httpx.AsyncClient() as cliente:

        async def _presign(aid: str, tipo: str, tamano: int) -> dict:
            """Pide la URL prefirmada a la central falsa antes de subir."""
            return await request_presign(
                cliente, base, job_id="falso", artifact_id=aid,
                content_type=tipo, size_bytes=tamano,
            )

        tienda = ArtifactStore(
            raiz, {artifact_id: slot}, presign=_presign, http_client=cliente
        )
        ref = await tienda.upload(artifact_id, "resultado.csv")
        job_id = str(uuid.uuid4())
        carga = {
            "protocol_version": PROTOCOL_VERSION,
            "idempotency_key": idempotency_key(job_id, 1),
            "status": "completado",
            "result": {"result": "OK", "data": {}},
        }
        ok_result = await send_result(cliente, base, job_id, carga)
        ok_ack = await send_cancel_ack(
            cliente, base, job_id=job_id, accepted=True,
            attempt=1, lease_id=str(uuid.uuid4()),
        )
        return {"ref": ref, "ok_result": ok_result, "ok_ack": ok_ack,
                "job_id": job_id}


def test_e2e_put_directo_resultado_idempotente_y_cancel_ack(tmp_path):
    """PUT directo, reintento idempotente del reporte y cancel-ack."""
    servidor, _ = _central_falsa()
    try:
        base = _base_url(servidor)
        raiz = _workdir(tmp_path)
        salida = asyncio.run(_flujo_e2e(base, raiz, con_url=True))
        assert salida["ok_result"] is True
        assert salida["ok_ack"] is True
        # El PUT llegó con los bytes exactos del artefacto.
        assert len(servidor.pendientes_put) == 1  # type: ignore[attr-defined]
        entrega = servidor.pendientes_put[0]  # type: ignore[attr-defined]
        assert entrega["bytes"] == b"dato-fiscal-1"
        assert entrega["tipo"] == "text/csv"
        # La referencia informa clave, tamaño y SHA-256 reales.
        assert salida["ref"]["size_bytes"] == len(b"dato-fiscal-1")
        assert salida["ref"]["sha256"] == hashlib.sha256(b"dato-fiscal-1").hexdigest()
        # El reporte se reintentó tras el 500 con la misma clave
        # idempotente y sin reejecutar nada: dos entregas, una clave.
        reportes = servidor.pendientes_result  # type: ignore[attr-defined]
        assert len(reportes) == 2
        assert reportes[0]["idempotency_key"] == reportes[1]["idempotency_key"]
        assert reportes[0]["idempotency_key"] == idempotency_key(salida["job_id"], 1)
        # El cancel-ack llegó con su correlación.
        acks = servidor.pendientes_cancel_ack  # type: ignore[attr-defined]
        assert len(acks) == 1
        assert acks[0]["accepted"] is True
        assert acks[0]["attempt"] == 1
    finally:
        servidor.shutdown()


def test_e2e_slot_sin_url_pide_presign_antes_del_put(tmp_path):
    """Sin URL en el sobre, el worker pide presign y luego sube."""
    servidor, _ = _central_falsa()
    try:
        base = _base_url(servidor)
        raiz = _workdir(tmp_path, b"otro-contenido")
        salida = asyncio.run(_flujo_e2e(base, raiz, con_url=False))
        assert salida["ok_result"] is True
        presigns = servidor.pendientes_presign  # type: ignore[attr-defined]
        assert len(presigns) == 1
        assert presigns[0]["content_type"] == "text/csv"
        assert presigns[0]["size_bytes"] == len(b"otro-contenido")
        assert len(servidor.pendientes_put) == 1  # type: ignore[attr-defined]
        assert servidor.pendientes_put[0]["bytes"] == b"otro-contenido"  # type: ignore[attr-defined]
    finally:
        servidor.shutdown()


def test_result_store_deduplica_por_job_e_intento():
    """El store no reporta dos veces el mismo (job_id, attempt)."""
    tienda = ResultStore()
    cuerpo = {"idempotency_key": idempotency_key("j1", 1)}

    async def _doble() -> tuple:
        primero = await tienda.already_reported("j1", 1)
        await tienda.mark_reported("j1", 1, cuerpo)
        segundo = await tienda.already_reported("j1", 1)
        await tienda.mark_reported("j1", 1, {"otro": True})
        tercero = await tienda.already_reported("j1", 1)
        return primero, segundo, tercero

    primero, segundo, tercero = asyncio.run(_doble())
    assert primero is None
    assert segundo == cuerpo
    # El segundo marcado no pisa el primero: idempotencia local.
    assert tercero == cuerpo


def test_result_identifica_nodo_para_la_central():
    """El reporte lleva X-Worker-Node cuando hay nodo (la central lo exige)."""
    cabeceras: dict = {}

    class Cliente:
        async def post(self, url, json=None, timeout=None, headers=None):
            cabeceras.update(headers or {})

            class Resp:
                status_code = 200

            return Resp()

    async def _reportar() -> tuple:
        con_nodo = await send_result(
            Cliente(), "https://central", "j1", {"result": "OK"},
            worker_node="192.0.2.9:8080",
        )
        sin_nodo = await send_result(
            Cliente(), "https://central", "j1", {"result": "OK"},
        )
        return con_nodo, sin_nodo

    con_nodo, sin_nodo = asyncio.run(_reportar())
    assert con_nodo is True
    assert sin_nodo is True
    assert cabeceras == {"X-Worker-Node": "192.0.2.9:8080"}


def test_fabrica_navegador_stub_en_dev_y_sesion_falsa(monkeypatch):
    """El modo dev no abre red; la sesión ARCA stub deja servicios indisponibles."""
    import bot_worker.runtime.browser as browser_module

    monkeypatch.setattr(browser_module, "_hay_playwright", lambda: False)
    fabrica = build_browser_factory()
    assert fabrica._headless is True

    async def _navegacion() -> list:
        async with fabrica.new_context() as (navegador, contexto):
            assert fabrica.active_sessions == 1
            pagina = await contexto.new_page()
            await pagina.goto("https://ejemplo.local/")
            return pagina.visitas

    visitas = asyncio.run(_navegacion())
    assert visitas == ["https://ejemplo.local/"]

    async def _apertura() -> None:
        """Abre la sesión falsa y pide un servicio no simulado en modo dev."""
        async with fabrica.arca_session(object()) as sesion:
            await sesion.open_service("SIPER")

    try:
        asyncio.run(_apertura())
        raise AssertionError("open_service no se simula en modo dev")
    except NotImplementedError:
        pass
    assert fabrica.launch_count >= 2
    assert fabrica.active_sessions == 0

    async def _sin_credenciales() -> None:
        async with fabrica.arca_session(None):  # type: ignore[arg-type]
            pass  # pragma: no cover

    try:
        asyncio.run(_sin_credenciales())
        raise AssertionError("sin credenciales debería rechazar")
    except ArcaLoginError:
        pass


def test_drenaje_rechaza_nuevo_conserva_en_vuelo_y_cancela_al_vencer():
    """Drenaje: 409 a lo nuevo, espera a lo en vuelo, cancela al vencer."""
    sup = JobSupervisor(configured=2)

    async def _ciclo() -> None:
        trabajo = LocalJob(
            job_id=str(uuid.uuid4()), attempt=1, lease_id=str(uuid.uuid4()),
            plugin="siper", operation="consultar",
        )
        await sup.accept(trabajo, {"accepted": True})
        sup.begin_drain()
        assert sup.draining is True
        otro = LocalJob(
            job_id=str(uuid.uuid4()), attempt=1, lease_id=str(uuid.uuid4()),
            plugin="siper", operation="consultar",
        )
        try:
            await sup.accept(otro, {"accepted": True})
            raise AssertionError("en drenaje debería rechazar")
        except WorkerDraining:
            pass
        # Con trabajo en vuelo, el plazo corto vence sin perderlo.
        assert await sup.wait_empty(timeout_seconds=0.1) is False
        assert trabajo.local_state != "TERMINADO"
        marcados = sup.cancel_all()
        assert marcados == [trabajo]
        assert trabajo.local_state == "CANCELANDO"
        sup.release(trabajo, "cancelado")
        assert sup.counters["jobs_cancelled"] == 1
        assert await sup.wait_empty(timeout_seconds=1.0) is True

    asyncio.run(_ciclo())


def test_post_jobs_en_drenaje_devuelve_409_worker_drenando():
    """La API rechaza asignaciones nuevas con WORKER_DRENANDO."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from bot_worker.security.assignments import load_verify_key
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        default_expiry,
        public_pem,
        sealed_hash_of,
        sign_assignment,
    )

    ajustes = WorkerConfig(
        central_url="http://central-inaccesible.local",
        advertised_url="http://127.0.0.1:8080",
    )
    app = create_app(ajustes)
    privada = Ed25519PrivateKey.generate()
    app.state.central_verify_key = load_verify_key(public_pem(privada))
    app.state.supervisor.begin_drain()
    cliente = TestClient(app, raise_server_exceptions=True)
    jid, lid = str(uuid.uuid4()), str(uuid.uuid4())
    expira = default_expiry(300)
    sobre = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": jid,
        "attempt": 1,
        "lease_id": lid,
        "lease_expires_at": "2030-01-01T00:00:00Z",
        "plugin": "siper",
        "operation": "consultar",
        "credentials": {"cuit_representante": "20123456789", "clave": "ficticia"},
        "assignment_expires_at": expira,
        "assignment_signature": sign_assignment(
            privada,
            scope=ASSIGNMENT_SCOPE_ASSIGN,
            job_id=jid,
            attempt=1,
            lease_id=lid,
            expires_at=expira,
            sealed_hash=sealed_hash_of(None),
        ),
    }
    respuesta = cliente.post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "WORKER_DRENANDO"


def test_protocolo_version_entero_uno():
    """El wire usa protocolo entero 1, no '1.0'."""
    assert PROTOCOL_VERSION == 1
    assert isinstance(PROTOCOL_VERSION, int)
