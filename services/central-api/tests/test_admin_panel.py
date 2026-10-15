"""Regresión del panel web administrativo V3 inspirado en la V2."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def test_panel_admin_renderiza_navegacion_y_vistas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    cuerpo = respuesta.text
    for vista in ("dashboard", "users", "jobs", "executions", "fleet", "audit"):
        assert f'data-view="{vista}"' in cuerpo
        assert f'data-panel="{vista}"' in cuerpo
    # La gestión de claves vive en Usuarios: ya no hay pestaña ni sección propia.
    assert 'data-view="keys"' not in cuerpo
    assert 'data-panel="keys"' not in cuerpo
    assert 'id="keys-table"' not in cuerpo
    assert "loadKeys" not in cuerpo
    for endpoint in (
        "/admin/users",
        "/api-keys/rotate",
        "/admin/jobs",
        "/admin/records",
        "/admin/jobs/metrics",
        "/admin/fleet",
        "/admin/audit",
    ):
        assert endpoint in cuerpo
    assert "sessionStorage" in cuerpo
    assert "mrbot_admin_token" in cuerpo
    assert "valor_unica_vez" in cuerpo
    # Columna API Key simple como en V1: una línea con campo, Ver, Copiar y Guardar.
    assert "<th>API Key</th>" in cuerpo
    assert "<th>User ID</th>" in cuerpo
    assert 'data-action="copy-user-id"' in cuerpo
    assert "<code>${esc(user.id)}</code>" in cuerpo
    assert "function userKeyCell(user, enabled)" in cuerpo
    assert 'find((key) => key.estado === "activa")' in cuerpo
    assert 'class="key-input" type="password" data-key-input=' in cuerpo
    for accion in ("key-view", "key-copy", "key-save"):
        assert f'data-action="{accion}" ${{ids}}' in cuerpo
    assert ">Ver</button>" in cuerpo and ">Copiar</button>" in cuerpo and ">Guardar</button>" in cuerpo
    assert "/api-keys/${encodeURIComponent(keyId)}/reveal" in cuerpo
    assert "copyApiKeyToClipboard(button.dataset.user, button.dataset.key)" in cuerpo
    assert "key.revelable && enabled" in cuerpo
    assert "/api-keys/rotate`" in cuerpo
    assert "valor_fijo: valor" in cuerpo
    assert "(vacío = aleatoria)" in cuerpo
    # Sin historial ni botones de la versión anterior.
    for viejo in ("userKeyPreview", "key-replace", "key-revoke", "key-restore", "key-edit",
                  "Emitir y revelar clave", "api-key-reveal", "issued-api-key",
                  "revealApiKey", "clearRevealedApiKey", "window.confirm(\"Se emitirá"):
        assert viejo not in cuerpo, viejo
    assert 'data-action="user-toggle"' in cuerpo
    assert "Desactivar" in cuerpo and "Activar" in cuerpo
    assert 'data-action="account-save"' in cuerpo
    assert "function accountEditor(user)" in cuerpo
    for account_type in ("PRUEBA", "TESTING", "NORMAL"):
        assert account_type in cuerpo
    assert "/admin/billing/accounts/${encodeURIComponent(userId)}/account-type" in cuerpo
    assert 'data-account-reason="${esc(user.id)}" type="text" minlength="10" maxlength="500"' in cuerpo
    assert "reason.length < 10 || reason.length > 500" in cuerpo
    assert "entre 10 y 500 caracteres" in cuerpo
    assert "La clave se guarda cifrada, nunca en texto plano" in cuerpo
    assert 'value="habilitado">Activo' in cuerpo
    assert 'value="deshabilitado">Desactivado' in cuerpo
    assert "credential-detail" in cuerpo
    assert "Archivos" in cuerpo
    assert "Asignado" in cuerpo
    assert "Finalizado" in cuerpo
    assert "bot-sections" in cuerpo
    assert "flattenRequest" in cuerpo
    assert "Request · ${field}" in cuerpo
    assert "Response JSON" in cuerpo
    assert "artifactListView" in cuerpo
    assert "executions-table-select" in cuerpo
    assert "/admin/table-catalog" in cuerpo
    assert 'id="new-api-key"' in cuerpo
    assert 'id="new-api-key" type="password" minlength="3"' in cuerpo
    assert 'cache: "no-store"' in cuerpo
    assert "las claves cortas son débiles" in cuerpo
    assert 'id="new-state"' in cuerpo
    assert 'id="new-send-credentials"' in cuerpo
    assert 'list="module-options"' in cuerpo
    assert 'id="module-options"' in cuerpo
    assert "Solo se carga la selección actual" in cuerpo
    assert "renderPhysicalBotSection" in cuerpo
    assert "request_payload" in cuerpo
    assert "response_payload" in cuerpo
    inicio = cuerpo.index('data-panel="executions"')
    fin = cuerpo.index('data-panel="fleet"', inicio)
    explorer = cuerpo[inicio:fin]
    assert 'class="form-actions"' not in explorer
    assert 'id="execution-table-counts"' not in explorer
    assert 'id="table-description"' not in explorer


def test_panel_sin_abrir_tablas_y_con_buscador_de_tablas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Abrir tablas" not in cuerpo
    assert "Tablas / registros" in cuerpo
    assert "executions-table-search" in cuerpo
    assert "filterTableOptions" in cuerpo

    assert cliente.get("/admin/tables").status_code == 404


def test_panel_selector_tablas_es_combobox_accesible_con_teclado() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert 'id="executions-table-search" type="text" role="combobox"' in cuerpo
    assert 'aria-autocomplete="list"' in cuerpo
    assert 'aria-controls="executions-table-listbox"' in cuerpo
    assert 'aria-describedby="executions-table-help"' in cuerpo
    assert 'id="executions-table-listbox"' in cuerpo
    assert 'role="listbox"' in cuerpo
    assert 'role="option"' in cuerpo
    assert 'id="executions-table-select" class="visually-hidden"' in cuerpo
    for tecla in ('"ArrowDown"', '"ArrowUp"', '"Enter"', '"Escape"'):
        assert tecla in cuerpo
    assert 'addEventListener("input"' in cuerpo
    assert 'select.dispatchEvent(new Event("change", { bubbles: true }))' in cuerpo
    assert "No hay tablas que coincidan." in cuerpo

    # La selección continúa en la vista integrada y no introduce ruta alternativa.
    assert 'data-view="executions">Tablas / registros</button>' in cuerpo
    assert "/admin/records?" in cuerpo
    assert 'data-panel="executions"' in cuerpo
    assert cliente.get("/admin/tables").status_code == 404


def test_panel_explorador_conserva_detalle_de_tablas_legacy_y_fisicas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Tablas V1/V2 por bot (consulta_*_logs)" in cuerpo
    assert "renderBotSections" in cuerpo
    assert "renderPhysicalBotSection" in cuerpo
    assert '"response_payload", "response_data", "artifact_metadata"' in cuerpo
    assert '"status"' in cuerpo
    assert "Request · ${field}" in cuerpo
    assert "Response JSON" in cuerpo
    assert "Archivos" in cuerpo


def test_panel_login_es_alias_html_y_admin_queda_fuera_de_openapi() -> None:
    cliente = TestClient(create_app())

    login = cliente.get("/admin/login")
    assert login.status_code == 200
    assert "Administración V3" in login.text

    esquema = cliente.get("/openapi.json").json()
    assert not any(path.startswith("/admin") for path in esquema["paths"])


def test_panel_no_reemplaza_la_autorizacion_json(monkeypatch) -> None:
    """Los endpoints JSON siguen exigiendo Bearer aunque exista el panel."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    get_settings.cache_clear()
    try:
        cliente = TestClient(create_app())

        respuesta = cliente.get("/admin/users")
        assert respuesta.status_code == 401
    finally:
        get_settings.cache_clear()


def test_panel_admin_botones_copiar_request_response_sin_secretos() -> None:
    cuerpo = TestClient(create_app()).get("/admin/").text
    for etiqueta in (
        "Copiar request",
        "Copiar response",
        "Copiar request + response",
        "Copiar como curl",
    ):
        assert f'"{etiqueta}"' in cuerpo
    assert 'aria-label="${label}"' in cuerpo
    for fragmento in (
        "data-copy-kind",
        "function copyRecordAction",
        "function buildCopyText",
        "writeClipboard(text)",
        "navigator.clipboard.writeText",
        'document.execCommand("copy")',
        "Copiado ✓",
        "1500",
        "COPY_LIMIT_BYTES = 200 * 1024",
        "Authorization: Basic <API_KEY>",
        "/api/v3/bots/",
        'aria-live", "polite"',
    ):
        assert fragmento in cuerpo
    # Las credenciales nunca se incrustan: solo el placeholder.
    assert "Basic ${" not in cuerpo
    assert "state.token" not in cuerpo.split("function buildCopyText")[1].split("function copyAnnounce")[0]


def test_las_tablas_del_panel_tienen_filtros_por_columna_en_cascada() -> None:
    html = TestClient(create_app()).get("/admin/").text
    # Menu con casillas (multiseleccion), buscador, Todos/Ninguno, Aplicar/Limpiar.
    for marca in (
        "setupColumnFilters", "cfOpenColumnMenu", "cfApply", "cf-menu", "cf-item",
        'data-cf="apply"', 'data-cf="clear"', 'data-cf="all"', 'data-cf="none"',
    ):
        assert marca in html, marca
    # Cascada: cada columna solo ofrece los valores de las filas que cumplen las demas.
    assert "cfPass(row, st.sel, index)" in html
    assert "cf-off" in html
    # Boton para quitar todos los filtros, con contador y chips.
    assert "Quitar filtros" in html and "Mostrando" in html and "cf-chip" in html
    # Las columnas de acciones no se filtran y no entran textos de formularios.
    assert 'CF_SKIP = new Set(["acciones"' in html
    assert "details > summary" in html


def test_la_celda_de_acciones_no_rompe_la_linea_de_la_tabla() -> None:
    html = TestClient(create_app()).get("/admin/").text
    # La celda <td> nunca es flex (rompia el borde horizontal de la fila);
    # el flex vive en un contenedor interno.
    assert "td.actions { display: table-cell" in html
    assert 'class="actions-inner"' in html
    assert '<td class="actions"><div class="actions-inner">' in html


def test_panel_muestra_job_id_completo_y_todas_las_columnas_del_job() -> None:
    """Los job_id no se recortan y la grilla de jobs expone todo el job."""
    cuerpo = TestClient(create_app()).get("/admin/").text

    # Un UUID tiene 36 caracteres: ninguna tabla de jobs puede truncarlo.
    assert 'short(job.id, 22)' not in cuerpo
    assert 'short(job.job_id, 22)' not in cuerpo
    assert 'short(value, 22)' not in cuerpo
    assert cuerpo.count("idCode(") >= 5
    for columna in (
        "Resultado", "User ID", "Prioridad", "App version", "Asignado", "Iniciado",
        "Finalizado", "Lease expira", "Idempotency key", "Cancelación", "Error",
    ):
        assert f"<th>{columna}</th>" in cuerpo


def test_resumen_y_catalogo_de_jobs_exponen_todas_las_columnas_sin_secretos() -> None:
    from types import SimpleNamespace

    from central_api.admin import jobs as admin_jobs

    fila = SimpleNamespace(
        priority=3, max_attempts=5, app_version="20261014_031956",
        protocol_version=1, idempotency_key="idem-1", lease_expires_at=None,
        cancel_reason=None, cancelled_by=None, error_code="E1",
        error_message="falló", credential_ciphertext="SECRETO",
    )
    extra = admin_jobs._campos_extra_job(fila)
    assert extra["prioridad"] == 3 and extra["max_intentos"] == 5
    assert extra["idempotency_key"] == "idem-1" and extra["error_code"] == "E1"
    assert "SECRETO" not in str(extra) and "credential_ciphertext" not in extra

    job = SimpleNamespace(
        id="0192f3a4-7b1c-7d2e-8f90-123456789abc", status="COMPLETO", bot="rcel",
        operation="consulta", payload={"user_id": "u"}, user_email="a@b.c",
        worker_node="w", assignment_attempt=1, created_at=None, extra=extra,
        user_id="u", worker_id="w", result_status="OK",
    )
    resumen = admin_jobs._resumen_job(job)
    assert resumen["job_id"] == job.id and len(resumen["job_id"]) == 36
    for clave in ("user_id", "worker_id", "resultado", "app_version", "error_code"):
        assert clave in resumen
    for columna in extra:
        if columna in ("lease_expira_en",):
            continue
        assert columna in admin_jobs.TABLE_COLUMNS["jobs"] or columna in (
            "prioridad", "max_intentos", "app_version", "protocol_version",
            "idempotency_key", "cancel_reason", "cancelled_by", "error_code",
            "error_message",
        )
    assert {"error_message", "lease_expira_en", "idempotency_key"} <= set(
        admin_jobs.TABLE_COLUMNS["jobs"]
    )


def test_panel_tiene_vista_de_reportes_de_consumo() -> None:
    cuerpo = TestClient(create_app()).get("/admin/").text
    assert 'data-view="reports"' in cuerpo and 'data-panel="reports"' in cuerpo
    for elemento in ("reports-desde", "reports-hasta", "reports-tipo", "reports-download"):
        assert f'id="{elemento}"' in cuerpo
    assert 'value="desglose_cuit"' in cuerpo
    assert "/admin/reporting?" in cuerpo and "/admin/reporting/download?" in cuerpo
    assert "CUIT representado" in cuerpo


def test_panel_tiene_botones_para_ir_a_reportes() -> None:
    cuerpo = TestClient(create_app()).get("/admin/").text
    # el boton de Resumen se quito a pedido: queda el de Jobs
    assert 'id="goto-reports"' not in cuerpo
    assert cuerpo.count('data-goto="reports"') >= 1
    assert 'document.addEventListener("click"' in cuerpo and "[data-goto]" in cuerpo
    # el destino existe y la pestaña del menu sigue disponible
    assert 'data-panel="reports"' in cuerpo and 'data-view="reports"' in cuerpo
