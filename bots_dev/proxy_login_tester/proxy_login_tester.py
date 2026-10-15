import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qsl, urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
DEFAULT_WORKERS = 5
DEFAULT_TIMEOUT_MS = 20_000


@dataclass
class ProxyTestSettings:
    cuit_representante: str
    clave_representante: str
    proxy_host: str
    proxy_username: str
    proxy_password: str
    starting_port: int
    ending_port: int
    workers: int = DEFAULT_WORKERS
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    proxy_scheme: str = "https"
    include_ports: list[int] | None = None
    exclude_ports: list[int] | None = None


@dataclass
class ProxyTestResult:
    port: int
    success: bool
    stage: str
    message: str
    duration_seconds: float
    artifacts_dir: str = ""
    session_har_path: str = ""
    step_artifacts: list[dict] | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _sanitize_filename(value: str, max_length: int = 120) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip())
    sanitized = sanitized.strip("._")
    if not sanitized:
        sanitized = "no_value"
    return sanitized[:max_length]


def _url_to_filename_fragment(url: str) -> str:
    if not url:
        return "no_url"
    parsed = urlparse(url)
    fragment = "_".join(
        part for part in [parsed.netloc, parsed.path.strip("/"), parsed.query] if part
    )
    return _sanitize_filename(fragment or parsed.scheme or "no_url")


def _headers_to_har_list(headers: dict[str, str] | None) -> list[dict[str, str]]:
    if not headers:
        return []
    return [{"name": key, "value": value} for key, value in headers.items()]


class StepNetworkRecorder:
    def __init__(self) -> None:
        self.current_step = "bootstrap"
        self._entries_by_request: dict[int, dict] = {}
        self._entries_by_step: dict[str, list[dict]] = {}
        self._pending_tasks: set[asyncio.Task] = set()

    def bind(self, page: Page) -> None:
        page.on("request", self._on_request)
        page.on("response", lambda response: self._track_task(self._on_response(response)))
        page.on("requestfinished", lambda request: self._track_task(self._on_request_finished(request)))
        page.on("requestfailed", lambda request: self._track_task(self._on_request_failed(request)))

    def set_step(self, step_label: str) -> None:
        self.current_step = step_label

    def _track_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _on_request(self, request) -> None:
        request_id = id(request)
        started_at = datetime.now().astimezone()
        headers = getattr(request, "headers", {}) or {}
        entry = {
            "step": self.current_step,
            "startedDateTime": started_at.isoformat(),
            "started_perf": perf_counter(),
            "request": {
                "method": request.method,
                "url": request.url,
                "headers": headers,
                "queryString": [{"name": key, "value": value} for key, value in parse_qsl(urlparse(request.url).query)],
            },
            "response": None,
            "time_ms": 0.0,
            "status": "pending",
        }
        self._entries_by_request[request_id] = entry
        self._entries_by_step.setdefault(self.current_step, []).append(entry)

    async def _on_response(self, response) -> None:
        request_id = id(response.request)
        entry = self._entries_by_request.get(request_id)
        if entry is None:
            return
        try:
            headers = await response.all_headers()
        except Exception:
            headers = {}
        entry["response"] = {
            "status": response.status,
            "statusText": getattr(response, "status_text", "") or "",
            "headers": headers,
            "contentType": headers.get("content-type", ""),
        }
        entry["status"] = "response_received"

    async def _on_request_finished(self, request) -> None:
        request_id = id(request)
        entry = self._entries_by_request.get(request_id)
        if entry is None:
            return
        entry["time_ms"] = round((perf_counter() - entry["started_perf"]) * 1000, 2)
        entry["status"] = "finished"

    async def _on_request_failed(self, request) -> None:
        request_id = id(request)
        entry = self._entries_by_request.get(request_id)
        if entry is None:
            return
        failure_text = "request_failed"
        try:
            failure = request.failure
            if callable(failure):
                failure = failure()
            if isinstance(failure, dict):
                failure_text = failure.get("errorText", failure_text)
        except Exception:
            pass
        entry["time_ms"] = round((perf_counter() - entry["started_perf"]) * 1000, 2)
        entry["status"] = failure_text

    async def drain(self) -> None:
        if not self._pending_tasks:
            return
        await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)

    def build_step_har(self, step_label: str, page_url: str) -> dict:
        entries = []
        for entry in self._entries_by_step.get(step_label, []):
            response_data = entry.get("response") or {}
            entries.append(
                {
                    "startedDateTime": entry["startedDateTime"],
                    "time": entry.get("time_ms", 0.0),
                    "request": {
                        "method": entry["request"]["method"],
                        "url": entry["request"]["url"],
                        "httpVersion": "HTTP/1.1",
                        "headers": _headers_to_har_list(entry["request"].get("headers")),
                        "queryString": entry["request"].get("queryString", []),
                        "headersSize": -1,
                        "bodySize": -1,
                    },
                    "response": {
                        "status": response_data.get("status", 0),
                        "statusText": response_data.get("statusText", entry.get("status", "")),
                        "httpVersion": "HTTP/1.1",
                        "headers": _headers_to_har_list(response_data.get("headers")),
                        "content": {
                            "size": -1,
                            "mimeType": response_data.get("contentType", ""),
                        },
                        "redirectURL": "",
                        "headersSize": -1,
                        "bodySize": -1,
                    },
                    "cache": {},
                    "timings": {
                        "send": 0,
                        "wait": entry.get("time_ms", 0.0),
                        "receive": 0,
                    },
                }
            )

        return {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "proxy_login_tester",
                    "version": "1.0",
                },
                "pages": [
                    {
                        "startedDateTime": datetime.now().astimezone().isoformat(),
                        "id": step_label,
                        "title": step_label,
                        "pageTimings": {},
                        "comment": page_url,
                    }
                ],
                "entries": entries,
            }
        }


class ArtifactManager:
    def __init__(self, run_output_dir: Path, port: int) -> None:
        self.port = port
        self.port_dir = run_output_dir / f"port_{port}"
        self.screenshots_dir = self.port_dir / "screenshots"
        self.html_dir = self.port_dir / "html"
        self.har_dir = self.port_dir / "har"
        self.step_index = 0
        self.records: list[dict] = []
        self.session_har_path = self.har_dir / f"session_port_{port}_{_url_to_filename_fragment(LOGIN_URL)}.har"

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.har_dir.mkdir(parents=True, exist_ok=True)

    def next_step_label(self, stage: str) -> str:
        self.step_index += 1
        return f"{self.step_index:03d}_{stage}"

    async def capture(self, page: Page | None, stage: str, step_label: str, recorder: StepNetworkRecorder) -> None:
        current_url = page.url if page is not None else ""
        url_fragment = _url_to_filename_fragment(current_url)
        base_name = f"{step_label}_port_{self.port}_{url_fragment}"

        screenshot_path = self.screenshots_dir / f"{base_name}.png"
        html_path = self.html_dir / f"{base_name}.html"
        har_path = self.har_dir / f"{base_name}.har"

        if page is not None:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                screenshot_path = Path("")
            try:
                html_path.write_text(await page.content(), encoding="utf-8")
            except Exception:
                html_path = Path("")

        await recorder.drain()
        har_payload = recorder.build_step_har(step_label, current_url)
        har_path.write_text(json.dumps(har_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        self.records.append(
            {
                "step": stage,
                "label": step_label,
                "url": current_url,
                "screenshot": str(screenshot_path) if str(screenshot_path) else "",
                "html": str(html_path) if str(html_path) else "",
                "har": str(har_path),
            }
        )


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_host(host: str) -> str:
    normalized = (host or "").strip().strip('"').strip("'")
    if normalized.startswith("http://"):
        normalized = normalized[len("http://"):]
    elif normalized.startswith("https://"):
        normalized = normalized[len("https://"):]
    return normalized.strip("/")


def _normalize_datacenter_username(value: str) -> str:
    normalized = (value or "").strip().strip('"').strip("'")
    if not normalized:
        return normalized
    if normalized.startswith("user-"):
        return normalized
    if normalized.startswith("customer-"):
        return f"user-{normalized[len('customer-'):]}"
    return f"user-{normalized}"


def _read_env_value(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value.strip().strip('"').strip("'")
    return default


def _parse_port_list(raw_value: str) -> list[int]:
    parsed_ports: set[int] = set()
    normalized = (raw_value or "").strip()
    if not normalized:
        return []

    for token in normalized.split(","):
        chunk = token.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start_port = int(start_text.strip())
            end_port = int(end_text.strip())
            if start_port <= 0 or end_port <= 0 or start_port > end_port:
                raise ValueError(f"Rango de puertos inválido: {chunk}")
            parsed_ports.update(range(start_port, end_port + 1))
            continue

        port = int(chunk)
        if port <= 0:
            raise ValueError(f"Puerto inválido: {chunk}")
        parsed_ports.add(port)

    return sorted(parsed_ports)


def resolve_ports(settings: ProxyTestSettings) -> list[int]:
    if settings.include_ports:
        selected_ports = list(settings.include_ports)
    else:
        selected_ports = list(range(settings.starting_port, settings.ending_port + 1))

    if settings.exclude_ports:
        excluded_ports = set(settings.exclude_ports)
        selected_ports = [port for port in selected_ports if port not in excluded_ports]

    return sorted(selected_ports)


def load_settings() -> ProxyTestSettings:
    cuit_representante = _read_env_value("CUIT_REPRESENTANTE_PROXY", "CUIT_REPRESENTANTE")
    clave_representante = _read_env_value("CLAVE_REPRESENTANTE_PROXY", "CLAVE_REPRESENTANTE", "CLAVE")

    proxy_host = _normalize_host(
        _read_env_value("PROXY_URL", "ENTRY_POINT_PROXY", "SERVER_PROXY_HOST")
    )

    datacenter_enabled = _as_bool(os.getenv("DATACENTER_PROXY"), default=False)
    if datacenter_enabled:
        proxy_username = _normalize_datacenter_username(
            _read_env_value("DATACENTER_USERNAME", "DATACENTER_PROXY_USERNAME")
        )
        proxy_password = _read_env_value("DATACENTER_PASSWORD", "DATACENTER_PROXY_PASSWORD")
        proxy_scheme = "https"
    else:
        proxy_username = _read_env_value("USERNAME_PROXY")
        proxy_password = _read_env_value("PASSWORD_PROXY")
        proxy_scheme = "http"

    starting_port = int(_read_env_value("STARTING_PORT_PROXY", default="0"))
    ending_port = int(_read_env_value("ENDING_PORT_PROXY", default="0"))
    workers = int(_read_env_value("PROXY_TEST_WORKERS", default=str(DEFAULT_WORKERS)))
    headless = _as_bool(os.getenv("PROXY_TEST_HEADLESS"), default=True)
    timeout_ms = int(_read_env_value("PROXY_TEST_TIMEOUT_MS", default=str(DEFAULT_TIMEOUT_MS)))
    include_ports = _parse_port_list(_read_env_value("PROXY_TEST_INCLUDE_PORTS"))
    exclude_ports = _parse_port_list(_read_env_value("PROXY_TEST_EXCLUDE_PORTS"))

    missing_values = []
    if not cuit_representante:
        missing_values.append("CUIT_REPRESENTANTE_PROXY/CUIT_REPRESENTANTE")
    if not clave_representante:
        missing_values.append("CLAVE_REPRESENTANTE_PROXY/CLAVE")
    if not proxy_host:
        missing_values.append("PROXY_URL/ENTRY_POINT_PROXY/SERVER_PROXY_HOST")
    if not proxy_username:
        missing_values.append("usuario de proxy")
    if not proxy_password:
        missing_values.append("password de proxy")
    if starting_port <= 0 or ending_port <= 0 or starting_port > ending_port:
        missing_values.append("STARTING_PORT_PROXY/ENDING_PORT_PROXY válidos")

    if missing_values:
        missing_text = ", ".join(missing_values)
        raise ValueError(f"Configuración incompleta para testear proxies: {missing_text}")

    return ProxyTestSettings(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        proxy_host=proxy_host,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
        starting_port=starting_port,
        ending_port=ending_port,
        workers=workers,
        headless=headless,
        timeout_ms=timeout_ms,
        proxy_scheme=proxy_scheme,
        include_ports=include_ports or None,
        exclude_ports=exclude_ports or None,
    )


def build_proxy(settings: ProxyTestSettings, port: int) -> dict[str, str]:
    return {
        "server": f"{settings.proxy_scheme}://{settings.proxy_host}:{port}",
        "username": settings.proxy_username,
        "password": settings.proxy_password,
    }


async def wait_page_ready(page: Page, timeout_ms: int) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
    except Exception:
        pass


async def has_visible_locator(page: Page, locators: list, timeout_ms: int = 1_500) -> bool:
    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            first = locator.first
            try:
                await first.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                if await first.is_visible():
                    return True
        except Exception:
            continue
    return False


async def close_session(browser: Browser | None, context: BrowserContext | None) -> None:
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass


def log_step(worker_id: int, port: int, stage: str, message: str) -> None:
    logging.info("worker=%s port=%s stage=%s | %s", worker_id, port, stage, message)


async def click_if_visible(page: Page, locators: list, timeout_ms: int = 3_000) -> bool:
    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            await locator.first.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


async def wait_for_post_login_landing(page: Page, timeout_ms: int) -> dict[str, bool]:
    ver_todos_locators = [
        page.get_by_role("link", name="Ver todos"),
        page.get_by_role("button", name=re.compile(r"Ver\s+todos", re.I)),
        page.locator("a:has-text('Ver todos')"),
        page.locator("button:has-text('Ver todos')"),
    ]
    password_change_locators = [
        page.get_by_role("heading", name=re.compile(r"Cambiar\s+Clave\s+Fiscal", re.I)),
        page.locator("text=/Cambiar\\s+Clave\\s+Fiscal/i"),
    ]

    deadline = perf_counter() + (timeout_ms / 1000)
    while perf_counter() < deadline:
        await wait_page_ready(page, timeout_ms)

        ver_todos_visible = await has_visible_locator(page, ver_todos_locators, timeout_ms=500)
        if ver_todos_visible:
            return {"ver_todos_visible": True, "password_change_required": False}

        password_change_required = await has_visible_locator(page, password_change_locators, timeout_ms=500)
        if password_change_required:
            return {"ver_todos_visible": False, "password_change_required": True}

        await asyncio.sleep(0.5)

    return {"ver_todos_visible": False, "password_change_required": False}


async def wait_for_ver_todos_section_loaded(page: Page, timeout_ms: int) -> None:
    service_section_locators = [
        page.locator("[id*='servicio' i], [class*='servicio' i]"),
        page.locator("button[onclick*='servicio' i], a[onclick*='servicio' i]"),
        page.locator("button, a").filter(has_text=re.compile(r"[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\s]{8,}")),
    ]

    deadline = perf_counter() + (timeout_ms / 1000)
    while perf_counter() < deadline:
        await wait_page_ready(page, timeout_ms)

        for locator in service_section_locators:
            try:
                if await locator.count() == 0:
                    continue
                first = locator.first
                if await first.is_visible():
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

        await asyncio.sleep(0.5)

    await asyncio.sleep(1)


async def execute_login(
    playwright: Playwright,
    settings: ProxyTestSettings,
    port: int,
    worker_id: int,
    run_output_dir: Path,
) -> ProxyTestResult:
    browser = None
    context = None
    page = None
    started_at = perf_counter()
    stage = "launch"
    artifact_manager = ArtifactManager(run_output_dir, port)
    recorder = StepNetworkRecorder()

    try:
        proxy = build_proxy(settings, port)
        log_step(worker_id, port, stage, f"Iniciando browser con proxy {proxy['server']}")
        browser = await playwright.chromium.launch(headless=settings.headless, proxy=proxy)
        context = await browser.new_context(record_har_path=str(artifact_manager.session_har_path))
        page = await context.new_page()
        page.set_default_timeout(settings.timeout_ms)
        recorder.bind(page)

        stage = "goto_login"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Abriendo login de ARCA")
        await page.goto(LOGIN_URL, timeout=45_000)
        await wait_page_ready(page, settings.timeout_ms)
        await artifact_manager.capture(page, stage, step_label, recorder)

        stage = "fill_cuit"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Cargando CUIT")
        cuit_input = page.get_by_role("spinbutton")
        await cuit_input.click()
        await cuit_input.fill(settings.cuit_representante)
        await artifact_manager.capture(page, stage, step_label, recorder)

        stage = "click_siguiente"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Avanzando a ingreso de clave")
        await page.get_by_role("button", name="Siguiente").click()
        await wait_page_ready(page, settings.timeout_ms)
        await artifact_manager.capture(page, stage, step_label, recorder)

        captcha_detected = await has_visible_locator(
            page,
            [
                page.get_by_role("img", name=re.compile(r"Captcha", re.I)),
                page.locator("img[alt*='captcha' i], img[src*='captcha' i]"),
                page.locator("iframe[src*='captcha' i]"),
                page.locator("[id*='captcha' i], [class*='captcha' i]"),
            ],
        )
        if captcha_detected:
            stage = "captcha"
            step_label = artifact_manager.next_step_label(stage)
            recorder.set_step(step_label)
            log_step(worker_id, port, stage, "Captcha detectado")
            await artifact_manager.capture(page, stage, step_label, recorder)
            return ProxyTestResult(
                port=port,
                success=False,
                stage="captcha",
                message="Captcha detectado",
                duration_seconds=round(perf_counter() - started_at, 2),
                artifacts_dir=str(artifact_manager.port_dir),
                session_har_path=str(artifact_manager.session_har_path),
                step_artifacts=artifact_manager.records,
            )

        stage = "fill_password"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Cargando clave fiscal")
        password_input = page.get_by_role("textbox", name="TU CLAVE")
        await password_input.click()
        await password_input.fill(settings.clave_representante)
        await artifact_manager.capture(page, stage, step_label, recorder)

        stage = "click_ingresar"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Enviando login")
        await page.get_by_role("button", name="Ingresar").click()
        landing_state = await wait_for_post_login_landing(page, settings.timeout_ms)
        await artifact_manager.capture(page, stage, step_label, recorder)

        password_change_required = landing_state["password_change_required"]
        if password_change_required:
            stage = "password_change"
            step_label = artifact_manager.next_step_label(stage)
            recorder.set_step(step_label)
            log_step(worker_id, port, stage, "Se requiere cambiar la clave fiscal")
            await artifact_manager.capture(page, stage, step_label, recorder)
            return ProxyTestResult(
                port=port,
                success=False,
                stage="password_change",
                message="Se requiere cambiar la clave fiscal",
                duration_seconds=round(perf_counter() - started_at, 2),
                artifacts_dir=str(artifact_manager.port_dir),
                session_har_path=str(artifact_manager.session_har_path),
                step_artifacts=artifact_manager.records,
            )

        stage = "ver_todos"
        step_label = artifact_manager.next_step_label(stage)
        recorder.set_step(step_label)
        log_step(worker_id, port, stage, "Validando pantalla posterior al login")
        clicked_ver_todos = False
        if landing_state["ver_todos_visible"]:
            clicked_ver_todos = await click_if_visible(
                page,
                [
                    page.get_by_role("link", name="Ver todos"),
                    page.get_by_role("button", name=re.compile(r"Ver\s+todos", re.I)),
                    page.locator("a:has-text('Ver todos')"),
                    page.locator("button:has-text('Ver todos')"),
                ],
            )
            if clicked_ver_todos:
                await wait_for_ver_todos_section_loaded(page, settings.timeout_ms)
        await artifact_manager.capture(page, stage, step_label, recorder)

        if clicked_ver_todos:
            log_step(worker_id, port, stage, "Login validado con 'Ver todos'")
            return ProxyTestResult(
                port=port,
                success=True,
                stage="success",
                message="Login OK y 'Ver todos' disponible",
                duration_seconds=round(perf_counter() - started_at, 2),
                artifacts_dir=str(artifact_manager.port_dir),
                session_har_path=str(artifact_manager.session_har_path),
                step_artifacts=artifact_manager.records,
            )

        page_text = (await page.text_content("body") or "").lower()
        if any(term in page_text for term in ["incorrect", "inválid", "invalida", "error"]):
            stage = "post_login_check"
            step_label = artifact_manager.next_step_label(stage)
            recorder.set_step(step_label)
            log_step(worker_id, port, stage, "Respuesta posterior al login indica error")
            await artifact_manager.capture(page, stage, step_label, recorder)
            return ProxyTestResult(
                port=port,
                success=False,
                stage="post_login_check",
                message="La página devolvió un error posterior al login",
                duration_seconds=round(perf_counter() - started_at, 2),
                artifacts_dir=str(artifact_manager.port_dir),
                session_har_path=str(artifact_manager.session_har_path),
                step_artifacts=artifact_manager.records,
            )

        log_step(worker_id, port, stage, "Login válido sin 'Ver todos' visible")
        return ProxyTestResult(
            port=port,
            success=True,
            stage="success",
            message="Login OK sin botón visible de 'Ver todos'",
            duration_seconds=round(perf_counter() - started_at, 2),
            artifacts_dir=str(artifact_manager.port_dir),
            session_har_path=str(artifact_manager.session_har_path),
            step_artifacts=artifact_manager.records,
        )
    except Exception as exc:
        log_step(worker_id, port, stage, f"Fallo: {exc}")
        if page is not None:
            failure_label = artifact_manager.next_step_label(f"{stage}_error")
            recorder.set_step(failure_label)
            await artifact_manager.capture(page, f"{stage}_error", failure_label, recorder)
        return ProxyTestResult(
            port=port,
            success=False,
            stage=stage,
            message=str(exc),
            duration_seconds=round(perf_counter() - started_at, 2),
            artifacts_dir=str(artifact_manager.port_dir),
            session_har_path=str(artifact_manager.session_har_path),
            step_artifacts=artifact_manager.records,
        )
    finally:
        await close_session(browser, context)


async def worker(
    worker_id: int,
    queue: asyncio.Queue,
    playwright: Playwright,
    settings: ProxyTestSettings,
    run_output_dir: Path,
    results: list[ProxyTestResult],
) -> None:
    while True:
        port = await queue.get()
        try:
            if port is None:
                return
            result = await execute_login(playwright, settings, port, worker_id, run_output_dir)
            results.append(result)
        finally:
            queue.task_done()


def build_report(settings: ProxyTestSettings, results: list[ProxyTestResult], report_path: Path) -> dict:
    ordered_results = sorted(results, key=lambda item: item.port)
    failed_ports = [item.port for item in ordered_results if not item.success]
    successful_ports = [item.port for item in ordered_results if item.success]
    executed_ports = [item.port for item in ordered_results]

    return {
        "generated_at": datetime.now().isoformat(),
        "report_path": str(report_path),
        "workers": settings.workers,
        "proxy_host": settings.proxy_host,
        "proxy_scheme": settings.proxy_scheme,
        "port_range": {
            "starting_port": settings.starting_port,
            "ending_port": settings.ending_port,
        },
        "port_filters": {
            "include_ports": settings.include_ports or [],
            "exclude_ports": settings.exclude_ports or [],
            "executed_ports": executed_ports,
        },
        "summary": {
            "total_ports": len(ordered_results),
            "successful_ports": len(successful_ports),
            "failed_ports": len(failed_ports),
        },
        "failed_ports": failed_ports,
        "successful_ports": successful_ports,
        "results": [asdict(item) for item in ordered_results],
    }


async def run_batch(settings: ProxyTestSettings) -> dict:
    ports = resolve_ports(settings)
    if not ports:
        raise ValueError("No quedaron puertos para ejecutar luego de aplicar include/exclude")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_output_dir = Path("temp") / f"proxy_login_tester_{timestamp}"
    run_output_dir.mkdir(parents=True, exist_ok=True)

    queue: asyncio.Queue = asyncio.Queue()
    results: list[ProxyTestResult] = []

    for port in ports:
        await queue.put(port)
    for _ in range(settings.workers):
        await queue.put(None)

    async with async_playwright() as playwright:
        tasks = [
            asyncio.create_task(worker(index + 1, queue, playwright, settings, run_output_dir, results))
            for index in range(settings.workers)
        ]
        await queue.join()
        await asyncio.gather(*tasks)

    report_path = run_output_dir / "proxy_login_report.json"

    report = build_report(settings, results, report_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


async def main() -> None:
    configure_logging()
    settings = load_settings()
    ports_to_execute = resolve_ports(settings)
    logging.info(
        "Iniciando test de proxies host=%s puertos=%s-%s workers=%s include=%s exclude=%s total_ejecucion=%s",
        settings.proxy_host,
        settings.starting_port,
        settings.ending_port,
        settings.workers,
        settings.include_ports or [],
        settings.exclude_ports or [],
        len(ports_to_execute),
    )

    report = await run_batch(settings)
    logging.info(
        "Proceso finalizado. Correctos=%s Erróneos=%s Reporte=%s",
        report["summary"]["successful_ports"],
        report["summary"]["failed_ports"],
        report["report_path"],
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())