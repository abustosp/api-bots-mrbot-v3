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


def test_panel_incluye_vista_workers_efimeros() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    assert 'data-view="ephemeral"' in html and 'data-panel="ephemeral"' in html
    for ident in ("eph-policy-form", "eph-launch-form", "eph-terminate-all", "eph-terminate-all-hard"):
        assert f'id="{ident}"' in html
    assert 'data-action="eph-hard"' in html and "mode: hard" in html


def test_panel_formulario_de_alta_con_tipo_y_semaphore() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    for ident in ("eph-launch-type", "eph-launch-concurrency", "eph-type-info"):
        assert f'id="{ident}"' in html
    assert "/admin/autoscale/instance-types" in html and "vCPU" in html
    assert "instance_type, concurrency" in html


def test_panel_selector_de_zona_de_efimeros() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    for ident in ("eph-zone", "eph-zone-info", "eph-zone-help"):
        assert f'id="{ident}"' in html
    # Zona activa visible y endpoint de zonas referenciado.
    assert "Zona activa" in html
    assert "/admin/autoscale/zones" in html
    assert "/admin/autoscale/zone" in html
    assert "loadZones" in html and "changeZone" in html
    # El cambio de zona pide motivo (>= 10) y se envía por PUT con la zona elegida.
    assert 'method: "PUT"' in html
    assert "zone: target, motivo: limpio" in html
    assert "limpio.length < 10" in html
    assert "cambio de zona desde panel V3" in html
    assert '"change", changeZone' in html
    assert " — activa" in html
    # Si el listado de zonas no responde, la vista degrada con la zona del estado.
    assert "No se pudo cargar la lista de zonas" in html
    assert "zoneRowFromStatus" in html


def test_panel_catalogo_de_tipos_se_recarga_por_zona_y_muestra_la_zona() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    # El catálogo se consulta con la zona activa y no se cachea entre zonas.
    assert "/admin/autoscale/instance-types?zone=" in html
    assert "typeCatalog = { rows: [], zone: null, maxPermitido: 10 }" in html
    assert "typeCatalog.zone === clave" in html
    assert "typeCatalog.zone = clave" in html
    assert "loadInstanceTypes(currentZoneKey() || zoneFromStatus" in html
    assert "applyTypeCatalog" in html
    assert "Catálogo de respaldo de la zona" in html
    # La tabla de instancias muestra la zona y el alta manda la zona elegida.
    assert "<th>Zona</th>" in html
    assert 'colspan="9"' in html
    assert "zone: zoneKey" in html


def test_panel_altas_efimeras_muestran_regla_de_ram_y_maximo_por_tipo() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    # Regla de sizing visible: 1,5 a 2 GB por worker, sugerido, mínimo y máximo.
    assert "cada worker consume entre 1,5 y 2 GB de RAM" in html
    assert 'id="eph-launch-help"' in html
    assert 'aria-describedby="eph-launch-help"' in html
    for marca in (
        "gb_per_worker_min", "gb_per_worker_max", "max_concurrency", "suggested_concurrency",
        "recommended_min", "semaphore sugerido", "mínimo recomendado", "máximo permitido",
    ):
        assert marca in html, marca
    # El máximo del tipo se aplica al input y bloquea envíos por encima.
    assert "input.max = String(tope)" in html
    assert "concurrency > tope" in html
    assert "máximo permitido para ese tipo" in html
    assert "input.value = String(Math.max(1, Math.min(Number(row.suggested_concurrency) || 1, tope)))" in html
    # El tope respeta el máximo global que informa el backend.
    assert "function topeDeTipo(row)" in html
    assert "typeCatalog.maxPermitido" in html

    # El confirm de alta menciona tipo, semaphore y zona.
    confirmacion = html.split("¿Levantar ${count} instancia(s)")[1].split("Generan costo")[0]
    for marca in ("${instance_type}", "en la zona", "semaphore", "sugerido:", "máximo por RAM:"):
        assert marca in confirmacion, marca


def test_grupos_de_botones_no_se_pegan_ni_se_solapan() -> None:
    """Los grupos de botones mantienen separación y envuelven en pantallas chicas."""
    import re

    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    reglas = {
        ".nav": r"\.nav\s*\{[^}]*gap:\s*\d+px[^}]*flex-wrap:\s*wrap",
        ".form-actions": r"\.form-actions\s*\{[^}]*gap:\s*\d+px[^}]*flex-wrap:\s*wrap",
        ".toolbar": r"\.toolbar\s*\{[^}]*gap:\s*\d+px[^}]*flex-wrap:\s*wrap",
        ".actions": r"\.actions\s*\{[^}]*gap:\s*\d+px[^}]*flex-wrap:\s*wrap",
        ".actions-inner": r"\.actions-inner\s*\{[^}]*gap:\s*\d+px[^}]*flex-wrap:\s*wrap",
    }
    for nombre, patron in reglas.items():
        assert re.search(patron, css), f"{nombre} debe declarar gap y flex-wrap"
    # Las tablas anchas hacen scroll en vez de comprimir los botones de acciones.
    assert "overflow-x: auto" in css and "min-width: 760px" in css


def test_panel_backups_tiene_nav_vista_y_endpoints() -> None:
    """La vista Backups existe, está en la navegación y usa los endpoints reales."""
    html = TestClient(create_app()).get("/admin/").text

    assert 'data-view="backups"' in html
    assert 'data-panel="backups"' in html
    assert ">Backups</button>" in html
    # Cargador registrado en setView para que la vista se pueble al entrar.
    assert "backups: loadBackups" in html
    for endpoint in (
        '"/admin/backups"',
        '"/admin/backups/dump"',
        '"/admin/backups/restore"',
        "/admin/backups/${String(key",
    ):
        assert endpoint in html, endpoint
    # Acciones de la tabla: crear dump, restaurar y descargar.
    for marca in (
        'id="backups-dump"',
        'id="backups-refresh"',
        'id="backups-restore-run"',
        'id="backups-restore-cancel"',
        'id="backups-restore-dry"',
        'id="backups-restore-confirm"',
        'data-action="backup-restore"',
        'data-action="backup-download"',
        "function loadBackups()",
        "function createDump()",
        "function runRestore()",
        "function renderRestoreResult(data)",
    ):
        assert marca in html, marca
    # El modo elegido y la confirmación explícita viajan al endpoint.
    assert '{ key, modo, motivo, dry_run, confirmacion }' in html
    assert 'confirmacion !== "RESTAURAR"' in html
    assert 'value="overwrite"' in html and 'value="append"' in html
    # La ayuda explica bucket, esquema migrado y semántica de cada modo.
    for texto in (
        "bucket configurado",
        "esquema ya migrado",
        "clave primaria",
        "no pisa nada",
        "sin PK",
    ):
        assert texto in html, texto
    # Nunca se muestran credenciales del bucket ni URLs prefirmadas en el DOM.
    assert "OBJECT_STORAGE_SECRET_KEY" not in html
    assert "secreto" not in html.split('data-panel="backups"')[1].split("</section>")[0]


def test_panel_backups_permite_eliminar_con_checkboxes_y_confirmacion() -> None:
    html = TestClient(create_app()).get("/admin/").text
    for marca in (
        'id="backups-select-all"',
        'id="backups-delete-selected"',
        'id="backups-delete-confirm"',
        'id="backups-delete-run"',
        'class="backup-check"',
        'data-action="backup-delete"',
        '"/admin/backups/delete"',
        "function runDelete()",
        "function openDelete(keys)",
        '{ keys, motivo, confirmacion }',
        'confirmacion.toLowerCase() !== "eliminar"',
        "no se puede deshacer",
    ):
        assert marca in html, marca


def test_panel_backups_javascript_pasa_node_check() -> None:
    """El <script> del panel debe ser JavaScript válido (node --check rc=0)."""
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node no está disponible para --check")

    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "panel.js"
        ruta.write_text(script, encoding="utf-8")
        completado = subprocess.run(
            [node, "--check", str(ruta)], capture_output=True, text=True
        )
    assert completado.returncode == 0, completado.stderr


def test_panel_se_sirve_sin_cache() -> None:
    """Sin Cache-Control el navegador reutiliza un panel viejo tras cada despliegue."""
    from fastapi.testclient import TestClient

    from central_api.main import create_app

    cliente = TestClient(create_app())
    for ruta in ("/admin/", "/admin/login"):
        respuesta = cliente.get(ruta)
        assert respuesta.status_code == 200
        assert respuesta.headers["cache-control"] == "no-store, must-revalidate"
        assert respuesta.headers["x-content-type-options"] == "nosniff"


def test_panel_incluye_seccion_workers_relay() -> None:
    from central_api.admin.panel import ADMIN_PANEL_HTML as html

    for ident in ("fleet-relay-table", "fleet-relay-count", "add-relay-form", "relay-config-toml", "relay-config-env"):
        assert f'id="{ident}"' in html
    for accion in ("relay-token", "worker-pause", "worker-alias", "worker-remove"):
        assert f'data-action="{accion}"' in html or f'"{accion}"' in html
    assert '"/admin/workers/relay"' in html and "/admin/workers/relay/${encodeURIComponent(" in html and "/token`" in html
    assert "N relay" not in html and "/ ${esc(conteoRelay)} relay" in html
