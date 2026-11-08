import asyncio
import copy
import weakref
from types import SimpleNamespace

from bot_worker import main
from bot_worker.config import WorkerConfig
from bot_worker.runtime.context import BotResult
from bot_worker.scheduler.supervisor import LocalJob


def test_runner_reintenta_el_mismo_resultado_sin_repetir_el_bot(tmp_path, monkeypatch):
    async def scenario():
        settings = WorkerConfig(
            central_url="http://central", advertised_url="http://worker:8080",
            work_dir=str(tmp_path), worker_concurrency=1,
        )
        app = main.create_app(settings)
        app.state.http = object()
        env = main.JobEnvelope(
            job_id="job-1", attempt=1, lease_id="lease-1", plugin="bot",
            operation="consultar", credentials={"cuit_representante": "1", "clave": "x"},
            proxy_profile={"host": "proxy", "password": "secret"},
            captcha_profile={"arca_key": "captcha"},
            service_profile={"cuit_api_key": "api"},
        )
        job = LocalJob("job-1", 1, "lease-1", "bot", "consultar")
        await app.state.supervisor.accept(job, {"accepted": True})
        calls = []
        runtime_refs = []
        reports = []
        retry_started = asyncio.Event()
        finish = asyncio.Event()

        class Plugin:
            manifest = SimpleNamespace(timeout_por_defecto_seconds=30, artefactos_produce=())

            async def validate(self, payload):
                return payload

            async def execute(self, payload, runtime):
                calls.append(payload)
                runtime_refs.append(weakref.ref(runtime))
                (runtime.work_dir / "output").write_text("data")
                return BotResult(result="OK", data={"count": 1})

        async def started(*args):
            pass

        async def renewal(*args):
            await asyncio.Event().wait()

        async def send_result(client, base, job_id, payload, **kwargs):
            reports.append(copy.deepcopy(payload))
            if len(reports) == 1:
                return False
            retry_started.set()
            await finish.wait()
            return True

        async def no_delay(_):
            pass

        monkeypatch.setattr(main, "get_plugin", lambda _: Plugin())
        monkeypatch.setattr(main, "get_plugin_para_payload", lambda *_: Plugin())
        monkeypatch.setattr(main, "_report_started", started)
        monkeypatch.setattr(main, "_renovar_lease_periodicamente", renewal)
        monkeypatch.setattr(main, "send_result", send_result)
        monkeypatch.setattr(main.asyncio, "sleep", no_delay)
        task = asyncio.create_task(main._run_job(app, env, job))
        await asyncio.wait_for(retry_started.wait(), timeout=1)
        assert app.state.supervisor.en_ejecucion == 1
        assert job.local_state == "REPORTANDO"
        assert not await app.state.results.already_reported(job.job_id, job.attempt)
        assert not list(tmp_path.rglob("output"))
        assert runtime_refs[0]() is None
        assert env.credentials is env.proxy_profile is env.captcha_profile is env.service_profile is None
        finish.set()
        await asyncio.wait_for(task, timeout=1)
        assert len(calls) == 1
        assert len(reports) == 2 and reports[0] == reports[1]
        assert await app.state.results.already_reported(job.job_id, job.attempt)
        assert app.state.supervisor.en_ejecucion == 0

    asyncio.run(scenario())
