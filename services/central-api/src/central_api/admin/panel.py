"""Panel web administrativo V3, inspirado en la navegación visual de V2.

La primera entrega conserva el contrato administrativo actual: el operador
inicia la consola con ``ADMIN_TOKEN`` y el navegador lo mantiene únicamente en
``sessionStorage`` durante la sesión. Las vistas consumen los routers privados
JSON de ``central-api`` y nunca usan la API key de clientes.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


ADMIN_PANEL_HTML = r'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Administración | MrBot V3</title>
<style>
:root {
  --bg: #e7efec;
  --bg-accent: #dbe8e3;
  --panel: #f8f1e9;
  --panel-strong: #fffdf9;
  --ink: #1f1a17;
  --muted: #75675f;
  --accent: #e0522d;
  --accent-strong: #b53a1d;
  --good: #2c9c7c;
  --warn: #a66a16;
  --bad: #c84e43;
  --line: #ddcec0;
  --shadow: 0 18px 40px rgba(34, 24, 20, .08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  color: var(--ink);
  font-family: "Space Grotesk", "Trebuchet MS", system-ui, sans-serif;
  background: radial-gradient(circle at 10% 0%, var(--bg-accent), transparent 56%),
    linear-gradient(120deg, var(--bg), #eef2ef 60%, #e9ece9);
}
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
.shell { width: 100%; min-height: 100vh; padding: 20px 0 48px; }
.topbar {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  margin: 0 1.4% 18px; flex-wrap: wrap;
}
.brand { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.brand h1 { margin: 0; font-family: Georgia, serif; font-size: 28px; }
.brand span { color: var(--muted); font-size: 13px; letter-spacing: 1.5px; text-transform: uppercase; }
.nav { display: flex; gap: 8px; flex-wrap: wrap; }
.nav button, .button {
  border: 1px solid transparent; border-radius: 999px; padding: 10px 16px;
  color: var(--ink); background: #efe4d7; font-weight: 600;
  transition: transform .18s ease, background .18s ease, border-color .18s ease;
}
.nav button:hover, .button:hover { transform: translateY(-1px); background: #fff8f1; }
.nav button.active { background: #fff7ec; border-color: var(--accent); color: var(--accent-strong); }
.button.primary { color: #fff; background: var(--accent); }
.button.primary:hover { color: #fff; background: var(--accent-strong); }
.button.secondary { background: #fff8f1; border-color: var(--line); }
.button.danger { color: #fff; background: var(--bad); }
.button.small { padding: 7px 11px; font-size: 13px; }
.copy-buttons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.user-box { display: flex; align-items: center; gap: 8px; }
.user-box .muted { max-width: 210px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
main { margin: 0 1.4%; }
.panel {
  padding: 24px; border: 1px solid var(--line); border-radius: 24px;
  background: var(--panel); box-shadow: var(--shadow); animation: rise .35s ease;
}
@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.hidden { display: none !important; }
h2 { margin: 0 0 8px; font-family: Georgia, serif; font-size: 24px; }
h3 { margin: 0 0 7px; font-size: 17px; }
.subtle, .muted { color: var(--muted); }
.subtle { margin-top: 0; }
.flash { margin: 0 0 16px; padding: 11px 14px; border-radius: 12px; display: none; }
.flash.show { display: block; }
.flash.ok { color: #1f7f63; border: 1px solid #8fd9c0; background: #e7f8f1; }
.flash.error { color: #9f3e34; border: 1px solid #f0a7a0; background: #fff0ee; }
.auth-wrap { min-height: 72vh; display: grid; place-items: center; }
.auth-card { width: min(100%, 440px); padding: 34px; border: 1px solid var(--line); border-radius: 20px; background: var(--panel); box-shadow: var(--shadow); }
.auth-card h2 { font-size: 32px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; }
.field input, .field select, .field textarea {
  width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 11px;
  color: var(--ink); background: var(--panel-strong);
}
.field textarea { min-height: 74px; resize: vertical; }
.field input:focus, .field select:focus, .field textarea:focus { outline: 3px solid rgba(224,82,45,.16); border-color: var(--accent); }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 13px; margin: 16px 0; }
.form-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
.toolbar { display: flex; align-items: end; gap: 10px; flex-wrap: wrap; margin: 16px 0; }
.toolbar .field { min-width: 150px; flex: 1 1 170px; }
.toolbar .field.wide { flex-basis: 280px; }
.table-combobox { position: relative; }
.table-combobox input { padding-right: 40px; }
.table-combobox input[aria-expanded="true"] { border-color: var(--accent); border-radius: 11px 11px 0 0; }
.table-combobox-listbox {
  position: absolute; z-index: 20; top: 100%; left: 0; right: 0; max-height: 320px; overflow-y: auto;
  border: 1px solid var(--accent); border-top: 0; border-radius: 0 0 11px 11px;
  background: var(--panel-strong); box-shadow: var(--shadow);
}
.table-combobox-listbox[hidden] { display: none; }
.table-combobox-group { padding: 9px 12px 5px; color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .7px; text-transform: uppercase; }
.table-combobox-option { padding: 9px 12px; cursor: pointer; }
.table-combobox-option:hover, .table-combobox-option.active { color: var(--accent-strong); background: #fff1e6; }
.table-combobox-option[aria-selected="true"] { font-weight: 700; }
.table-combobox-empty { padding: 12px; color: var(--muted); }
.visually-hidden { position: absolute !important; width: 1px !important; height: 1px !important; padding: 0 !important; margin: -1px !important; overflow: hidden !important; clip: rect(0, 0, 0, 0) !important; white-space: nowrap !important; border: 0 !important; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }
.card { padding: 17px; border: 1px solid var(--line); border-radius: 16px; background: var(--panel-strong); }
.metric { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.metric strong { font-size: 30px; color: var(--accent-strong); }
.metric span { color: var(--muted); font-size: 13px; }
.status { display: inline-flex; align-items: center; gap: 6px; font-weight: 700; }
.status::before { content: ""; width: 9px; height: 9px; border-radius: 50%; background: currentColor; }
.status-good, .status-SANO, .status-COMPLETO, .status-habilitado { color: var(--good); }
.status-warn, .status-SATURADO, .status-PENDIENTE, .status-ASIGNADO { color: var(--warn); }
.status-bad, .status-CAIDO, .status-FALLIDO, .status-CANCELADO, .status-deshabilitado { color: var(--bad); }
.status-neutral, .status-DESCONOCIDO, .status-REGISTRANDO { color: var(--muted); }
.pill { display: inline-block; padding: 4px 10px; border-radius: 999px; border: 1px solid #e9cfb8; background: #f3e2cf; color: #5b463a; font-size: 12px; }
.table-wrap { overflow-x: auto; margin-top: 14px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel-strong); }

/* Filtros por columna estilo ERP: multiseleccion en cascada */
th.cf-th { position: relative; white-space: nowrap; }
th.cf-th .cf-btn { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 5px; max-width: 100%; }
th.cf-th .cf-btn:hover { color: var(--accent-strong); }
th.cf-th .cf-caret { font-size: 9px; opacity: .55; }
th.cf-th.cf-active { background: #fff3cd; }
th.cf-th.cf-active .cf-caret { opacity: 1; color: var(--accent-strong); }
.cf-badge { display: inline-block; min-width: 16px; padding: 0 5px; border-radius: 999px; background: var(--accent); color: #fff; font-size: 10px; line-height: 16px; text-align: center; letter-spacing: 0; }
.cf-menu { position: fixed; z-index: 1000; width: 260px; max-width: 92vw; padding: 10px; border: 1px solid var(--line); border-radius: 12px; background: #fff; box-shadow: 0 10px 28px rgba(60,40,20,.22); text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--ink); }
.cf-menu input[type=search] { width: 100%; margin-bottom: 6px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 8px; font-size: 12px; }
.cf-menu .cf-toggle { display: flex; gap: 6px; padding: 2px 2px 6px; font-size: 12px; border-bottom: 1px solid #eee; }
.cf-menu .cf-toggle a { cursor: pointer; color: var(--accent-strong); text-decoration: none; }
.cf-menu .cf-toggle a:hover { text-decoration: underline; }
.cf-menu .cf-list { max-height: 240px; overflow-y: auto; margin-top: 4px; }
.cf-menu .cf-item { display: flex; align-items: center; gap: 7px; padding: 4px 3px; font-size: 12.5px; cursor: pointer; border-radius: 6px; }
.cf-menu .cf-item:hover { background: #f6ede3; }
.cf-menu .cf-item.cf-off { opacity: .45; cursor: not-allowed; }
.cf-menu .cf-item.cf-off:hover { background: transparent; }
.cf-menu .cf-item input { margin: 0; }
.cf-menu .cf-item .cf-n { margin-left: auto; color: var(--muted); font-size: 11px; }
.cf-menu .cf-actions { display: flex; gap: 6px; margin-top: 8px; }
.cf-menu .cf-actions .button { flex: 1; padding: 6px 10px; font-size: 12px; }
.cf-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; font-size: 13px; }
.cf-bar .cf-count { color: var(--muted); }
.cf-bar .cf-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 9px; border-radius: 999px; border: 1px solid #e9cfb8; background: #f3e2cf; font-size: 12px; }

table { width: 100%; min-width: 760px; border-collapse: collapse; }
th, td { padding: 12px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 13px; }
th { color: var(--muted); font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
td.actions { display: table-cell; white-space: nowrap; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; }
td.actions > .actions-inner { display: flex; gap: 6px; flex-wrap: wrap; align-items: flex-start; }
.job-id { white-space: nowrap; user-select: all; word-break: normal; }
.user-id { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.user-id code { font-size: 12px; }
.key-cell { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; }
.key-cell .key-input { width: 210px; min-width: 140px; padding: 6px 9px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel-strong); font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; }
.key-cell .key-input:focus { outline: 3px solid rgba(224,82,45,.16); border-color: var(--accent); }
.code { display: block; padding: 10px; overflow-x: auto; border-radius: 10px; color: #fff; background: #332b28; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; word-break: break-word; }
.bot-sections { display: grid; gap: 14px; margin-top: 18px; }
.bot-section { overflow: hidden; border: 1px solid var(--line); border-radius: 16px; background: var(--panel-strong); }
.bot-section > summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; background: #fff8f1; }
.bot-section-meta { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; color: var(--muted); font-size: 12px; }
.bot-section-body { padding: 0 14px 14px; }
.bot-table { min-width: 1100px; }
.payload details { min-width: 220px; max-width: 360px; }
.payload summary { padding: 6px 8px; font-size: 12px; }
.payload .code { max-height: 220px; overflow: auto; }
details { margin-top: 16px; border: 1px solid var(--line); border-radius: 14px; background: rgba(255,253,249,.55); }
summary { padding: 12px 14px; cursor: pointer; font-weight: 700; }
details > .details-body { padding: 0 14px 14px; }
.empty { padding: 30px 15px; color: var(--muted); text-align: center; }
.kicker { color: var(--muted); font-size: 11px; letter-spacing: 1.3px; text-transform: uppercase; }
@media (max-width: 760px) {
  .shell { padding-top: 12px; }
  .topbar { margin-inline: 3%; }
  main { margin-inline: 3%; }
  .panel { padding: 17px; border-radius: 18px; }
  .nav { width: 100%; }
  .nav button { flex: 1 1 120px; }
}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand"><h1>MrBot</h1><span>Administración V3</span></div>
    <nav class="nav hidden" id="main-nav" aria-label="Navegación administrativa">
      <button type="button" data-view="dashboard">Resumen</button>
      <button type="button" data-view="users">Usuarios</button>
      <button type="button" data-view="jobs">Jobs</button>
      <button type="button" data-view="executions">Tablas / registros</button>
      <button type="button" data-view="reports">Reportes</button>
      <button type="button" data-view="fleet">Flota</button>
      <button type="button" data-view="audit">Auditoría</button>
    </nav>
    <div class="user-box hidden" id="user-box"><span class="muted">Sesión ADMIN_TOKEN</span><button class="button secondary small" type="button" id="logout-button">Salir</button></div>
  </header>
  <main>
    <section class="auth-wrap" id="login-screen">
      <div class="auth-card">
        <div class="kicker">Consola privada</div>
        <h2>Administración</h2>
        <p class="subtle">Gestiona usuarios, jobs, claves API y la flota desde el plano de control V3.</p>
        <div class="flash error" id="login-error" role="alert"></div>
        <form id="login-form">
          <div class="field"><label for="admin-token">Token de administración</label><input id="admin-token" name="token" type="password" autocomplete="current-password" required></div>
          <div class="form-actions"><button class="button primary" type="submit">Ingresar</button></div>
        </form>
        <p class="muted">La primera entrega usa el ADMIN_TOKEN compatible con los endpoints privados actuales. El token solo queda en sessionStorage del navegador.</p>
      </div>
    </section>

    <section class="panel hidden" id="panel-screen">
      <div class="flash" id="flash" role="status"></div>
      <section class="view" data-panel="dashboard">
        <div class="kicker">Centro operativo</div><h2>Resumen</h2><p class="subtle">Estado de la cola, flota y actividad administrativa.</p>
        <div class="form-actions"><button type="button" class="button primary" data-goto="reports" id="goto-reports">📊 Ir al panel de reportes</button></div>
        <div class="grid" id="dashboard-metrics"><div class="empty">Cargando métricas...</div></div>
        <div class="grid" style="margin-top:14px"><div class="card"><h3>Flota</h3><div id="dashboard-fleet" class="muted">Cargando...</div></div><div class="card"><h3>Últimas acciones</h3><div id="dashboard-audit" class="muted">Cargando...</div></div></div>
      </section>

      <section class="view hidden" data-panel="users">
        <div class="kicker">Identidades y acceso</div><h2>Usuarios</h2><p class="subtle">Alta, estado, plan y claves API de los clientes V3. Para depuración se admite un nombre sin mail (p. ej. abp). En la columna API Key: «Ver» la muestra, «Copiar» la copia y «Guardar» reemplaza la clave por lo que escribas (vacío = aleatoria). La clave se guarda cifrada, nunca en texto plano.</p>
        <form id="users-filter" class="toolbar"><div class="field wide"><label for="users-email">Buscar por email o usuario</label><input id="users-email" name="email" type="search" placeholder="cliente@example.com o abp"></div><div class="field"><label for="users-state">Estado</label><select id="users-state"><option value="">Todos</option><option value="habilitado">Activo</option><option value="deshabilitado">Desactivado</option></select></div><button class="button primary" type="submit">Buscar</button></form>
        <details><summary>Crear usuario</summary><div class="details-body"><form id="create-user-form"><div class="form-grid"><div class="field"><label for="new-email">Email o usuario debug</label><input id="new-email" type="text" placeholder="cliente@example.com o abp" required></div><div class="field"><label for="new-display">Nombre</label><input id="new-display" type="text"></div><div class="field"><label for="new-account-type">Tipo de cuenta</label><select id="new-account-type"><option value="PRUEBA">Prueba (100 consultas únicas)</option><option value="TESTING">Testing (ilimitada)</option><option value="NORMAL">Normal (cupo mensual)</option></select></div><div class="field hidden" id="new-max-field"><label for="new-max-requests">Cupo mensual NORMAL (requests/mes)</label><input id="new-max-requests" type="number" min="1" max="10000000" step="1" placeholder="Ej.: 1000"></div><div class="field"><label for="new-api-key">API key (mínimo 3; las claves cortas son débiles)</label><input id="new-api-key" type="password" minlength="3" placeholder="Vacío = generar automáticamente; mínimo 3 caracteres" autocomplete="new-password"></div><div class="field"><label for="new-state">Estado inicial</label><select id="new-state"><option value="habilitado">Activo</option><option value="deshabilitado">Desactivado</option></select></div><div class="field"><label for="new-reason">Motivo</label><input id="new-reason" type="text" value="alta desde panel V3" minlength="3" maxlength="500" required></div></div><div class="form-actions"><label class="checkbox-inline"><input id="new-send-credentials" type="checkbox"> Enviar credenciales por email</label><button class="button primary" type="submit">Crear usuario</button></div></form></div></details>
        <div class="table-wrap"><table><thead><tr><th>User ID</th><th>Email / usuario</th><th>Nombre</th><th>Tipo</th><th>Cuenta</th><th>API Key</th><th>Estado</th><th>Cupo</th><th>Acciones</th></tr></thead><tbody id="users-table"><tr><td colspan="9" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="jobs">
        <div class="kicker">Cola y ejecución</div><h2>Jobs <button type="button" class="button secondary small" data-goto="reports">📊 Reportes de consumo</button></h2><p class="subtle">Observa asignaciones, intentos y acciones durables sin exponer payloads sensibles.</p>
        <form id="jobs-filter" class="toolbar"><div class="field"><label for="jobs-state">Estado</label><select id="jobs-state"><option value="">Todos</option><option>PENDIENTE</option><option>ASIGNADO</option><option>CORRIENDO</option><option>COMPLETO</option><option>FALLIDO</option><option>CANCELADO</option></select></div><div class="field"><label for="jobs-bot">Módulo / bot</label><input id="jobs-bot" list="module-options" type="search" placeholder="Escribir para filtrar módulos" autocomplete="off"></div><div class="field"><label for="jobs-user">Usuario</label><input id="jobs-user" type="text" placeholder="UUID"></div><button class="button primary" type="submit">Actualizar</button></form>
        <div class="grid" id="jobs-metrics"><div class="empty">Cargando métricas...</div></div>
        <div class="table-wrap"><table><thead><tr><th>Job</th><th>Estado</th><th>Resultado</th><th>Bot / operación</th><th>Usuario</th><th>User ID</th><th>Worker</th><th>Intento</th><th>Prioridad</th><th>App version</th><th>Creado</th><th>Asignado</th><th>Iniciado</th><th>Finalizado</th><th>Lease expira</th><th>Idempotency key</th><th>Cancelación</th><th>Error</th><th>Acciones</th></tr></thead><tbody id="jobs-table"><tr><td colspan="19" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="executions">
        <div class="kicker">Historial consultable</div><h2>Explorador de tablas</h2><p class="subtle">Selecciona una tabla de PostgreSQL o la tabla física de un bot. Solo se carga la selección actual, como en V2, para evitar una página kilométrica.</p>
        <form id="executions-filter" class="toolbar"><div class="field wide"><label for="executions-table-search">Tabla</label><div class="table-combobox" id="executions-table-combobox"><input id="executions-table-search" type="text" role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false" aria-controls="executions-table-listbox" aria-describedby="executions-table-help" placeholder="Buscar tablas..." autocomplete="off" required><span id="executions-table-help" class="visually-hidden">Escribe para filtrar. Usa las flechas para recorrer, Enter para seleccionar y Escape para cerrar.</span><div id="executions-table-listbox" class="table-combobox-listbox" role="listbox" aria-label="Tablas disponibles" hidden></div><select id="executions-table-select" class="visually-hidden" tabindex="-1" aria-hidden="true"><option value="">Cargando catálogo...</option></select></div></div><div class="field wide"><label for="executions-query">Texto</label><input id="executions-query" type="search" placeholder="job, bot, operación o usuario"></div><div class="field wide"><label for="executions-job">Job ID</label><input id="executions-job" type="search" placeholder="UUID del job"></div><div class="field"><label for="executions-bot">Módulo / bot</label><input id="executions-bot" list="module-options" type="search" placeholder="Escribir para filtrar módulos" autocomplete="off"></div><div class="field"><label for="executions-operation">Operación</label><input id="executions-operation" type="text" placeholder="opcional"></div><div class="field"><label for="executions-state">Estado</label><input id="executions-state" type="text" placeholder="COMPLETO"></div><div class="field"><label for="executions-user">Usuario</label><input id="executions-user" type="search" placeholder="email o UUID"></div><div class="field"><label for="executions-limit">Filas</label><select id="executions-limit"><option>25</option><option>50</option><option selected>100</option></select></div><button class="button primary" type="submit">Consultar</button></form>
        <datalist id="module-options"></datalist><p class="muted" id="execution-table-status" aria-live="polite">Selecciona una tabla.</p>
        <div class="bot-sections" id="bot-sections"><div class="empty">Selecciona una tabla para consultar.</div></div>
        <div class="table-wrap"><table><thead id="executions-head"><tr><th>Tabla</th><th>Datos</th></tr></thead><tbody id="executions-table"><tr><td colspan="2" class="empty">Selecciona una tabla.</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="reports">
        <div class="kicker">Consumos</div><h2>Reporte de consumos</h2><p class="subtle">Requests exitosas y fallidas por usuario y servicio, con desglose opcional por CUIT representado. Exitosa: job COMPLETO con resultado OK o PARCIAL. Fallida: FALLIDO, CANCELADO o resultado ERROR. Las fechas se toman en hora de Argentina.</p>
        <form id="reports-form" class="toolbar"><div class="field"><label for="reports-desde">Desde</label><input id="reports-desde" type="date" required></div><div class="field"><label for="reports-hasta">Hasta</label><input id="reports-hasta" type="date" required></div><div class="field"><label for="reports-tipo">Detalle</label><select id="reports-tipo"><option value="resumen">Por usuario y servicio</option><option value="desglose_cuit">Por usuario, servicio y CUIT representado</option></select></div><div class="field"><label>&nbsp;</label><button class="button primary" type="submit">Generar</button></div><div class="field"><label>&nbsp;</label><button class="button secondary" type="button" id="reports-download" disabled>Descargar CSV</button></div></form>
        <div class="grid" id="reports-totals"></div>
        <p class="muted" id="reports-note" aria-live="polite"></p>
        <div class="table-wrap"><table><thead id="reports-head"><tr><th>Usuario</th><th>Servicio</th><th>Exitosas</th><th>Fallidas</th></tr></thead><tbody id="reports-table"><tr><td colspan="4" class="empty">Elegí un rango de fechas y generá el reporte.</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="fleet">
        <div class="kicker">Operación de workers</div><h2>Flota</h2><p class="subtle">Estado derivado, capacidad, protocolo y alertas del plano de ejecución. Agrega nodos al inventario en caliente sin tocar WORKER_NODES.</p>
        <div class="form-actions"><button class="button primary" type="button" id="fleet-refresh">Actualizar</button><button class="button secondary" type="button" id="fleet-evaluate">Evaluar alertas</button></div>
        <div class="form-actions"><strong id="fleet-version-summary" class="muted" aria-live="polite">Versiones: cargando...</strong><label class="checkbox-inline"><input id="fleet-outdated-only" type="checkbox"> Solo desactualizados</label></div>
        <form id="add-worker-form" class="toolbar"><div class="field wide"><label for="new-worker-node">Agregar worker (ip:puerto)</label><input id="new-worker-node" type="text" placeholder="10.0.0.13:8080 o bot-worker:8080" required></div><button class="button primary" type="submit">Agregar</button><span class="muted">Se sondea best-effort; igual se registra aunque aún no responda.</span></form>
        <h3>Workers manuales <span class="pill" id="fleet-manual-count">0</span></h3>
        <div class="table-wrap"><table><thead><tr><th>Alias</th><th>Nodo</th><th>IP</th><th>Origen</th><th>Estado</th><th>Versión</th><th>Ejecución</th><th>Protocolo</th><th>Bots</th><th>Error</th><th>Alertas</th><th>Acciones</th></tr></thead><tbody id="fleet-manual-table"><tr><td colspan="12" class="empty">Cargando...</td></tr></tbody></table></div>
        <h3 style="margin-top:24px">Workers efímeros (EC2) <span class="pill" id="fleet-ephemeral-count">0</span></h3>
        <div class="table-wrap"><table><thead><tr><th>Instancia</th><th>Tipo</th><th>RAM</th><th>Semaphore (slots)</th><th>Edad</th><th>Estado</th><th>Versión</th><th>Ejecución</th><th>Acciones</th></tr></thead><tbody id="fleet-ephemeral-table"><tr><td colspan="9" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="audit">
        <div class="kicker">Trazabilidad</div><h2>Auditoría</h2><p class="subtle">Eventos append-only con metadatos sensibles redactados.</p>
        <form id="audit-filter" class="toolbar"><div class="field"><label for="audit-action">Acción</label><input id="audit-action" type="text" placeholder="user."></div><div class="field"><label for="audit-actor">Actor</label><input id="audit-actor" type="text" placeholder="admin:"></div><button class="button primary" type="submit">Filtrar</button></form>
        <div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Acción</th><th>Actor</th><th>Objetivo</th><th>Resultado</th><th>Motivo</th></tr></thead><tbody id="audit-table"><tr><td colspan="6" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>
    </section>
  </main>
</div>
<script>
(() => {
  "use strict";
  const TOKEN_KEY = "mrbot_admin_token";
  const initialView = "dashboard";
  const state = { token: sessionStorage.getItem(TOKEN_KEY) || "", view: initialView, tableCatalog: [], tableOptions: [], activeTableOption: -1, moduleOptions: [], moduleOptionsLoaded: false };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
  const short = (value, size = 18) => { const text = String(value ?? ""); return text.length > size ? text.slice(0, size) + "…" : text; };
  const idCode = (value) => value ? `<code class="job-id" title="${esc(value)}">${esc(value)}</code>` : "—";
  const date = (value) => value ? new Date(value).toLocaleString("es-AR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
  const statusClass = (value) => { const text = String(value ?? ""); if (["SANO","COMPLETO","habilitado"].includes(text)) return "status-good"; if (["PENDIENTE","ASIGNADO","SATURADO"].includes(text)) return "status-warn"; if (["FALLIDO","CAIDO","CANCELADO","deshabilitado"].includes(text)) return "status-bad"; return "status-neutral"; };
  const json = (value) => { try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); } };
  function flash(message, kind = "ok") { const node = $("#flash"); node.textContent = message; node.className = `flash show ${kind}`; window.clearTimeout(flash.timer); flash.timer = window.setTimeout(() => { node.className = "flash"; }, 7000); }
  function loginError(message) { const node = $("#login-error"); node.textContent = message; node.className = `flash show error`; }
  function showLoggedIn(logged) { $("#login-screen").classList.toggle("hidden", logged); $("#panel-screen").classList.toggle("hidden", !logged); $("#main-nav").classList.toggle("hidden", !logged); $("#user-box").classList.toggle("hidden", !logged); }
  function logout(message = "") { state.token = ""; sessionStorage.removeItem(TOKEN_KEY); showLoggedIn(false); $("#admin-token").value = ""; if (message) loginError(message); }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${state.token}`);
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { cache: "no-store", ...options, headers });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { raw: text }; }
    if (response.status === 401 || response.status === 403) { logout("Sesión inválida o vencida. Ingresa nuevamente."); throw new Error("No autorizado"); }
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
  }
  const reportState = { last: null };
  function reportParams() { return new URLSearchParams({ desde: $("#reports-desde").value, hasta: $("#reports-hasta").value, tipo: $("#reports-tipo").value }); }
  function initReports() {
    if ($("#reports-desde").value) return;
    const today = new Date(); const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    $("#reports-hasta").value = iso(today); $("#reports-desde").value = iso(new Date(today.getFullYear(), today.getMonth(), 1));
  }
  async function loadReport(event) {
    if (event) event.preventDefault();
    const tipo = $("#reports-tipo").value;
    try {
      const data = await api(`/admin/reporting?${reportParams()}`);
      reportState.last = reportParams().toString();
      const withCuit = tipo === "desglose_cuit";
      $("#reports-head").innerHTML = `<tr><th>Usuario</th><th>Servicio</th>${withCuit ? "<th>CUIT representado</th>" : ""}<th>Exitosas</th><th>Fallidas</th><th>Total</th></tr>`;
      const rows = data.rows || [];
      $("#reports-table").innerHTML = rows.length ? rows.map((row) => `<tr><td>${esc(row.user_email || "—")}<br>${idCode(row.user_id)}</td><td>${esc(row.servicio)}</td>${withCuit ? `<td>${row.cuit_representado ? `<code>${esc(row.cuit_representado)}</code>` : '<span class="muted">sin representado</span>'}</td>` : ""}<td><span class="status status-good">${esc(row.requests_exitosas)}</span></td><td><span class="status ${Number(row.requests_no_exitosas) ? "status-bad" : "status-neutral"}">${esc(row.requests_no_exitosas)}</span></td><td>${esc(Number(row.requests_exitosas) + Number(row.requests_no_exitosas))}</td></tr>`).join("") : `<tr><td colspan="${withCuit ? 6 : 5}" class="empty">No hay requests en ese rango.</td></tr>`;
      const totals = data.totals || {}; const ok = Number(totals.requests_exitosas || 0), bad = Number(totals.requests_no_exitosas || 0);
      $("#reports-totals").innerHTML = [["Filas", data.total_rows || 0], ["Exitosas", ok], ["Fallidas", bad], ["Total requests", ok + bad]].map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join("");
      $("#reports-note").textContent = data.preview_truncated ? `Vista previa de las primeras ${rows.length} de ${data.total_rows} filas. El CSV trae todas.` : "";
      $("#reports-download").disabled = !(data.total_rows > 0);
    } catch (error) { flash(error.message, "error"); }
  }
  async function downloadReport() {
    try {
      const response = await fetch(`/admin/reporting/download?${reportParams()}`, { cache: "no-store", headers: { Authorization: `Bearer ${state.token}` } });
      if (response.status === 401 || response.status === 403) { logout("Sesión inválida o vencida. Ingresa nuevamente."); return; }
      if (!response.ok) { let detail = `HTTP ${response.status}`; try { detail = (await response.json()).detail || detail; } catch (_) {} throw new Error(detail); }
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
      link.href = url; link.download = `reporte_${$("#reports-tipo").value}_${$("#reports-desde").value}_${$("#reports-hasta").value}.csv`; document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch (error) { flash(error.message, "error"); }
  }
  function setView(view) { state.view = view; $$("[data-panel]").forEach((node) => node.classList.toggle("hidden", node.dataset.panel !== view)); $$("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === view)); const loaders = { dashboard: loadDashboard, users: loadUsers, jobs: loadJobs, executions: loadExecutions, reports: initReports, fleet: loadFleet, audit: loadAudit }; if (view === "jobs" || view === "executions") loadModuleOptions(); (loaders[view] || loadDashboard)(); }
  function cellValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return payloadView(value, "Ver datos");
    const text = String(value);
    return text.length > 80
      ? `<details class="payload"><summary><code>${esc(short(text, 60))}</code></summary><pre class="code">${esc(text)}</pre></details>`
      : esc(text);
  }
  function renderEmpty(target, colspan, message = "Sin datos") { $(target).innerHTML = `<tr><td colspan="${colspan}" class="empty">${esc(message)}</td></tr>`; }
  function payloadView(value, label) {
    const empty = value === null || value === undefined || (typeof value === "object" && Object.keys(value).length === 0);
    return empty ? "<span class=\"muted\">—</span>" : `<details class="payload"><summary>${esc(label)}</summary><pre class="code">${esc(json(value))}</pre></details>`;
  }
  function flattenRequest(value, prefix = "", output = {}) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      if (prefix) output[prefix] = value;
      return output;
    }
    const entries = Object.entries(value);
    if (!entries.length && prefix) output[prefix] = value;
    entries.forEach(([key, nested]) => {
      if (/(password|clave|secret|token|credentialciphertext|privatekey)/i.test(key)) return;
      const path = prefix ? `${prefix}.${key}` : key;
      if (nested && typeof nested === "object" && !Array.isArray(nested)) flattenRequest(nested, path, output);
      else output[path] = nested;
    });
    return output;
  }
  function requestFieldNames(records, requestKey = "request") {
    return Array.from(new Set((records || []).flatMap((record) =>
      Object.keys(flattenRequest(record?.[requestKey] || {}))
    ))).sort((left, right) => left.localeCompare(right));
  }
  function requestCell(value) {
    if (value === null || value === undefined || value === "") return `<span class="muted">—</span>`;
    if (typeof value === "object") return payloadView(value, "Ver valor");
    if (typeof value === "boolean") return value ? "Sí" : "No";
    return cellValue(value);
  }
  function artifactListView(artifacts) {
    const items = Array.isArray(artifacts) ? artifacts.filter((item) => item && typeof item === "object") : [];
    if (!items.length) return `<span class="muted">—</span>`;
    const safeItems = items.map((artifact) => ({
      id: artifact.id || artifact.artifact_id,
      nombre: artifact.name || artifact.filename,
      tipo: artifact.content_type,
      bytes: artifact.size_bytes,
      sha256: artifact.sha256,
      creado_en: artifact.created_at,
      expira_en: artifact.expires_at,
    }));
    return `<details class="payload"><summary>${items.length} archivo${items.length === 1 ? "" : "s"}</summary><pre class="code">${esc(json(safeItems))}</pre></details>`;
  }
  function requestColumns(records, requestKey, existingColumns = []) {
    const fields = requestFieldNames(records, requestKey);
    const columns = existingColumns.map((key) => ({ key, label: key, kind: "field" }));
    const index = columns.findIndex((column) => column.key === requestKey);
    if (index < 0) return columns;
    columns.splice(index, 1, ...(fields.length
      ? fields.map((field) => ({ key: `${requestKey}.${field}`, label: `Request · ${field}`, kind: "request", requestKey, field }))
      : [{ key: requestKey, label: "Request", kind: "field" }]));
    return columns;
  }
  function columnValue(record, column) {
    if (column.kind !== "request") return record[column.key];
    return flattenRequest(record[column.requestKey] || {})[column.field];
  }
  function renderBotSections(sections) {
    const node = $("#bot-sections");
    $("#executions-head").closest(".table-wrap").classList.add("hidden");
    if (!Array.isArray(sections) || !sections.length) { node.innerHTML = `<div class="empty">La tabla seleccionada no tiene registros.</div>`; return; }
    const section = sections[0];
    const records = section.records || [];
    const legacy = section.tablas_legacy || [];
    const operations = (section.operaciones || []).map((operation) => `<span class="pill">${esc(operation)}</span>`).join(" ");
    const legacyTables = legacy.length ? legacy.map((table) => `<span class="pill">${esc(table)}</span>`).join(" ") : `<span class="muted">sin tabla histórica directa</span>`;
    const requestFields = requestFieldNames(records, "request");
    const headings = ["Job", "Timestamp", "Asignado", "Iniciado", "Operación", "Usuario", "Estado", ...requestFields.map((field) => `Request · ${field}`), "Response JSON", "Archivos", "Credenciales", "Finalizado"];
    const rows = records.length ? records.map((record) => {
      const job = record.job || {};
      const response = record.response ?? record.result;
      const credentials = record.credentials || {};
      const request = flattenRequest(record.request || {});
      const requestCells = requestFields.map((field) => `<td>${requestCell(request[field])}</td>`).join("");
      const responseReceived = response?.received_at ? `<br><span class="muted">${esc(date(response.received_at))}</span>` : "";
      return `<tr><td>${idCode(job.id)}</td><td>${esc(date(job.creado_en))}</td><td>${esc(date(job.asignado_en))}</td><td>${esc(date(job.iniciado_en))}</td><td>${esc(job.operacion || "—")}</td><td>${esc(job.usuario_email || job.usuario || job.user_id || "—")}</td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado || "—")}</span></td>${requestCells}<td>${payloadView(response, "Ver response")}${responseReceived}${copyButtons(copyContext({ request: record.request, response, credentials: record.credentials }, job))}</td><td>${artifactListView(record.artifacts)}</td><td>${credentials.available ? `<button class="button secondary small" data-action="credential-detail" data-id="${esc(job.id)}">Ver credenciales</button>` : "—"}</td><td>${esc(date(job.finalizado_en))}</td></tr>`;
    }).join("") : `<tr><td colspan="${headings.length}" class="empty">Sin ejecuciones para este bot.</td></tr>`;
    node.innerHTML = `<details class="bot-section" open><summary><span><strong>${esc(section.bot)}</strong> <span class="bot-section-meta">${operations}</span></span><span class="bot-section-meta"><span class="pill">${esc(section.total || 0)} ejecuciones</span></span></summary><div class="bot-section-body"><div class="bot-section-meta"><strong>V1/V2:</strong> ${legacyTables}</div><div class="table-wrap"><table class="bot-table"><thead><tr>${headings.map((heading) => `<th>${esc(heading)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></div></details>`;
  }
  function renderPhysicalBotSection(section) {
    const node = $("#bot-sections");
    $("#executions-head").closest(".table-wrap").classList.add("hidden");
    const rawColumns = Array.isArray(section.display_columns)
      ? section.display_columns
      : (Array.isArray(section.columns) ? section.columns : []);
    const records = Array.isArray(section.records) ? section.records : [];
    if (!rawColumns.length) { node.innerHTML = `<div class="empty">No hay columnas visibles para la tabla física.</div>`; return; }
    const requestColumn = rawColumns.find((column) => ["request_payload", "request"].includes(column));
    const columns = requestColumn
      ? requestColumns(records, requestColumn, rawColumns)
      : rawColumns.map((key) => ({ key, label: key, kind: "field" }));
    const cells = (record) => columns.map((column) => {
      const value = columnValue(record, column);
      if (column.kind === "request") return `<td>${requestCell(value)}</td>`;
      if (["response_payload", "response_data", "artifact_metadata"].includes(column.key)) {
        const label = column.key === "response_payload" || column.key === "response_data" ? "Ver response JSON" : "Ver metadatos";
        return `<td>${payloadView(value, label)}</td>`;
      }
      if (column.key === "archivos" || column.key === "artifact_names") return `<td>${artifactListView(Array.isArray(value) ? value.map((name) => typeof name === "string" ? { name } : name) : [])}</td>`;
      if (value && typeof value === "object") return `<td>${payloadView(value, "Ver datos")}</td>`;
      if (column.key === "job_id" || column.key === "id") return `<td>${idCode(value)}</td>`;
      if (column.key === "status" || column.key === "estado") return `<td><span class="status ${statusClass(value)}">${esc(value || "—")}</span></td>`;
      return `<td>${esc(value ?? "—")}</td>`;
    }).join("") + `<td>${record.job_id ? `<button class="button secondary small" data-action="credential-detail" data-id="${esc(record.job_id)}">Ver credenciales</button>` : "—"}${copyButtons(copyContext(record, {}), record.job_id || "")}</td>`;
    const rows = records.length
      ? records.map((record) => `<tr>${cells(record)}</tr>`).join("")
      : `<tr><td colspan="${Math.max(columns.length + 1, 1)}" class="empty">Sin registros para este bot y filtro.</td></tr>`;
    const operations = (section.operaciones || []).map((operation) => `<span class="pill">${esc(operation)}</span>`).join(" ");
    node.innerHTML = `<details class="bot-section" open><summary><span><strong>${esc(section.bot)}</strong> <span class="bot-section-meta">${operations}</span></span><span class="bot-section-meta"><span class="pill">${esc(section.total || 0)} filas</span><span class="pill">PostgreSQL</span></span></summary><div class="bot-section-body"><div class="table-wrap"><table class="bot-table"><thead><tr>${columns.map((column) => `<th>${esc(column.label)}</th>`).join("")}<th>Credenciales / Copiar</th></tr></thead><tbody>${rows}</tbody></table></div></div></details>`;
  }
  function renderCanonicalTable(tableName, rows) {
    const head = $("#executions-head");
    const body = $("#executions-table");
    head.closest(".table-wrap").classList.remove("hidden");
    const keys = (state.tableCatalog.find((item) => item.name === tableName) || {}).columns || [];
    const requestColumn = keys.find((key) => ["request_payload", "request"].includes(key));
    const columns = requestColumn ? requestColumns(rows, requestColumn, keys) : keys.map((key) => ({ key, label: key, kind: "field" }));
    const withCopy = rows.some((row) => hasPayload(row.request_payload ?? row.request) || hasPayload(row.response_payload ?? row.response ?? row.response_summary));
    head.innerHTML = `<tr>${columns.map((column) => `<th>${esc(column.label)}</th>`).join("")}${withCopy ? "<th>Copiar</th>" : ""}</tr>`;
    body.innerHTML = rows.length ? rows.map((row) => `<tr>${columns.map((column) => {
      const key = column.key;
      const value = columnValue(row, column);
      if (key === "credentials" && value && value.available) return `<td><button class="button secondary small" data-action="credential-detail" data-id="${esc(row.id || row.job_id || "")}">Ver credenciales</button></td>`;
      if (key === "credentials") return `<td><span class="muted">—</span></td>`;
      if (["estado", "resultado", "result", "status"].includes(key) && typeof value === "string") return `<td><span class="status ${statusClass(value)}">${esc(value)}</span></td>`;
      return `<td>${cellValue(value)}</td>`;
    }).join("")}${withCopy ? `<td>${copyButtons(copyContext(row, {})) || "—"}</td>` : ""}</tr>`).join("") : `<tr><td colspan="${Math.max(keys.length, 1)}" class="empty">La tabla no contiene registros para este filtro.</td></tr>`;
    $("#bot-sections").innerHTML = `<div class="empty">Vista de tabla canónica: ${esc(tableName)}.</div>`;
  }
  function filterTableOptions(query) {
    const needle = String(query || "").trim().toLocaleLowerCase();
    const select = $("#executions-table-select");
    const listbox = $("#executions-table-listbox");
    if (!select || !listbox) return;
    state.tableOptions = [];
    const groups = Array.from(select.children).filter((node) => node.tagName === "OPTGROUP");
    groups.forEach((group) => {
      const matches = Array.from(group.querySelectorAll("option"))
        .filter((option) => option.value && option.textContent.toLocaleLowerCase().includes(needle));
      if (!matches.length) return;
      state.tableOptions.push(...matches);
    });
    state.activeTableOption = state.tableOptions.length ? 0 : -1;
    listbox.innerHTML = state.tableOptions.length
      ? groups.map((group) => {
        const options = state.tableOptions.filter((option) => option.parentElement === group);
        if (!options.length) return "";
        const heading = `<div class="table-combobox-group" role="presentation">${esc(group.label)}</div>`;
        const items = options.map((option) => {
          const index = state.tableOptions.indexOf(option);
          const selected = option.value === select.value;
          return `<div id="executions-table-option-${index}" class="table-combobox-option${index === state.activeTableOption ? " active" : ""}" role="option" aria-selected="${selected}" data-option-index="${index}" data-value="${esc(option.value)}">${esc(option.textContent)}</div>`;
        }).join("");
        return heading + items;
      }).join("")
      : `<div class="table-combobox-empty" role="presentation">No hay tablas que coincidan.</div>`;
    listbox.hidden = !$("#executions-table-search").dataset.open;
    $("#executions-table-search").setAttribute("aria-expanded", String(!listbox.hidden));
    if (listbox.hidden || state.activeTableOption < 0) $("#executions-table-search").removeAttribute("aria-activedescendant");
    else $("#executions-table-search").setAttribute("aria-activedescendant", `executions-table-option-${state.activeTableOption}`);
  }
  function openTableOptions(query = "") {
    const input = $("#executions-table-search");
    input.dataset.open = "true";
    filterTableOptions(query);
  }
  function closeTableOptions(restoreSelection = false) {
    const input = $("#executions-table-search");
    input.dataset.open = "";
    $("#executions-table-listbox").hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    if (restoreSelection) {
      const selected = $("#executions-table-select").selectedOptions[0];
      input.value = selected?.value ? selected.textContent : "";
    }
  }
  function activateTableOption(index) {
    if (!state.tableOptions.length) return;
    state.activeTableOption = Math.max(0, Math.min(index, state.tableOptions.length - 1));
    const input = $("#executions-table-search");
    const activeId = `executions-table-option-${state.activeTableOption}`;
    input.setAttribute("aria-activedescendant", activeId);
    $$("#executions-table-listbox [role=option]").forEach((option) => option.classList.toggle("active", option.id === activeId));
    document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }
  function chooseTableOption(value) {
    const select = $("#executions-table-select");
    const option = Array.from(select.options).find((item) => item.value === value);
    if (!option) return;
    select.value = option.value;
    $("#executions-table-search").value = option.textContent;
    closeTableOptions();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function handleTableComboboxKeydown(event) {
    const input = event.currentTarget;
    const isOpen = input.dataset.open === "true";
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      if (!isOpen) {
        openTableOptions(input.value);
        activateTableOption(direction > 0 ? 0 : state.tableOptions.length - 1);
        return;
      }
      const current = state.activeTableOption;
      activateTableOption(current < 0 ? (direction > 0 ? 0 : state.tableOptions.length - 1) : current + direction);
    } else if (event.key === "Enter" && isOpen) {
      event.preventDefault();
      const option = state.tableOptions[state.activeTableOption];
      if (option) chooseTableOption(option.value);
    } else if (event.key === "Escape" && isOpen) {
      event.preventDefault();
      closeTableOptions(true);
    } else if (event.key === "Tab") {
      closeTableOptions(true);
    }
  }
  function renderCatalog(catalog) {
    state.tableCatalog = catalog || [];
    const select = $("#executions-table-select");
    const canonical = state.tableCatalog.filter((item) => item.kind === "canonical");
    const bots = state.tableCatalog.filter((item) => item.kind === "bot");
    const legacy = state.tableCatalog.filter((item) => item.kind === "legacy");
    select.innerHTML = `<option value="">Selecciona una tabla</option><optgroup label="Tablas PostgreSQL">${canonical.map((item) => `<option value="${esc(item.name)}">${esc(item.label)}</option>`).join("")}</optgroup><optgroup label="Tablas detalladas por bot">${bots.map((item) => `<option value="${esc(item.name)}">${esc(item.label)}${item.legacy?.length ? ` · ${esc(item.legacy.join(", "))}` : ""}</option>`).join("")}</optgroup><optgroup label="Tablas V1/V2 por bot (consulta_*_logs)">${legacy.map((item) => `<option value="${esc(item.name)}">${esc(item.label)}</option>`).join("")}</optgroup>`;
    select.value = bots[0]?.name || canonical[0]?.name || "";
    const selected = select.selectedOptions[0];
    $("#executions-table-search").value = selected?.value ? selected.textContent : "";
    closeTableOptions();
    filterTableOptions("");
  }
  function renderModuleOptions(values) {
    const datalist = $("#module-options");
    if (!datalist) return;
    const options = Array.from(new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean)))
      .sort((left, right) => left.localeCompare(right, "es"));
    state.moduleOptions = options;
    datalist.innerHTML = options.map((value) => `<option value="${esc(value)}"></option>`).join("");
  }
  function addModuleOptions(values) {
    renderModuleOptions([...state.moduleOptions, ...(values || [])]);
  }
  async function loadModuleOptions() {
    if (state.moduleOptionsLoaded) return;
    state.moduleOptionsLoaded = true;
    try {
      const [jobs, catalog] = await Promise.all([
        api("/admin/jobs?limit=200"),
        api("/admin/table-catalog"),
      ]);
      const jobModules = (jobs.jobs || []).map((item) => item.bot);
      const catalogModules = (catalog.tables || [])
        .filter((item) => item.kind === "bot" || item.kind === "legacy")
        .flatMap((item) => [item.bot, item.module, item.name]);
      addModuleOptions([...jobModules, ...catalogModules]);
    } catch (error) {
      state.moduleOptionsLoaded = false;
      flash(error.message, "error");
    }
  }
  async function loadTableCatalog() {
    if (state.tableCatalog.length) return;
    const data = await api("/admin/table-catalog");
    renderCatalog(data.tables || []);
    addModuleOptions((data.tables || [])
      .filter((item) => item.kind === "bot" || item.kind === "legacy")
      .flatMap((item) => [item.bot, item.module, item.name]));
  }
  async function loadDashboard() {
    try {
      const [metrics, fleet, audit] = await Promise.all([api("/admin/jobs/metrics"), api("/admin/fleet"), api("/admin/audit?limit=8")]);
      const states = Object.entries(metrics.por_estado || {}).map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join("");
      $("#dashboard-metrics").innerHTML = states || `<div class="card"><div class="metric"><span>Jobs registrados</span><strong>0</strong></div></div>`;
      const manuales = fleet.manuales || (fleet.flota || []).filter((worker) => worker.origen !== "efimero");
      const efimeros = fleet.efimeros || (fleet.flota || []).filter((worker) => worker.origen === "efimero");
      const conteoManuales = fleet.resumen?.manuales?.total ?? manuales.length;
      const conteoEfimeros = fleet.resumen?.efimeros?.total ?? efimeros.length;
      $("#dashboard-fleet").innerHTML = `<div><strong>${esc(conteoManuales)} manuales / ${esc(conteoEfimeros)} efimeros</strong></div>` + (manuales.length || efimeros.length ? [...manuales, ...efimeros].map((worker) => `<div style="margin:8px 0"><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span> ${esc(worker.alias ? `${worker.alias} (${worker.node})` : (worker.node || worker.instance_id || "—"))} <span class="muted">${esc(worker.en_ejecucion ?? worker.jobs_activos ?? 0)}/${esc(worker.capacidad ?? worker.concurrency ?? 0)}</span></div>`).join("") : `<div class="muted">No hay workers registrados.</div>`);
      $("#dashboard-audit").innerHTML = audit.eventos?.length ? audit.eventos.map((event) => `<div style="margin:8px 0"><strong>${esc(event.action)}</strong><br><span class="muted">${esc(date(event.occurred_at))} · ${esc(event.actor_id)}</span></div>`).join("") : "No hay eventos recientes.";
    } catch (error) { flash(error.message, "error"); }
  }
  // Como en V1: una sola clave por usuario (la activa más reciente), con Ver,
  // Copiar y un campo para editarla. El historial revocado no se muestra.
  function activeKey(keys) {
    return (Array.isArray(keys) ? keys : []).find((key) => key.estado === "activa") || null;
  }
  function userKeyCell(user, enabled) {
    const key = activeKey(user.claves_api);
    const ids = `data-user="${esc(user.id)}" data-key="${esc(key ? key.id : "")}"`;
    const canReveal = Boolean(key && key.revelable && enabled);
    const input = `<input class="key-input" type="password" data-key-input="${esc(user.id)}" placeholder="${key ? (canReveal ? "••••••••" : "nueva API key (vacío = aleatoria)") : "sin clave (vacío = aleatoria)"}" autocomplete="off" aria-label="API key de ${esc(user.email)}">`;
    const ver = canReveal ? `<button type="button" class="button secondary small" data-action="key-view" ${ids}>Ver</button><button type="button" class="button secondary small" data-action="key-copy" ${ids}>Copiar</button>` : "";
    return `<div class="key-cell">${input}${ver}<button type="button" class="button primary small" data-action="key-save" ${ids}>Guardar</button></div>`;
  }
  function accountEditor(user) {
    const mode = user.account_type || "PRUEBA";
    const labels = { PRUEBA: "Prueba (100 únicas)", TESTING: "Testing (ilimitada)", NORMAL: "Normal (cupo mensual)" };
    const options = Object.keys(labels).map((value) => `<option value="${value}" ${mode === value ? "selected" : ""}>${labels[value]}</option>`).join("");
    const limit = mode === "NORMAL" ? esc(user.max_requests ?? "") : "";
    return `<details><summary>${esc(mode)} · configurar</summary><div class="details-body"><div class="form-grid"><div class="field"><label>Tipo de cuenta</label><select data-account-mode="${esc(user.id)}">${options}</select></div><div class="field"><label>Cupo mensual NORMAL (requests/mes)</label><input data-account-max="${esc(user.id)}" type="number" min="1" max="10000000" value="${limit}" placeholder="Obligatorio para NORMAL"></div><div class="field wide"><label>Motivo obligatorio</label><input data-account-reason="${esc(user.id)}" type="text" minlength="10" maxlength="500" placeholder="Motivo del cambio (mínimo 10 caracteres)"></div></div><div class="form-actions"><button type="button" class="button primary small" data-action="account-save" data-id="${esc(user.id)}">Guardar tipo de cuenta</button></div></div></details>`;
  }
  async function loadUsers(event) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: "100" }); const email = $("#users-email").value.trim(); const estado = $("#users-state").value; if (email) params.set("email", email); if (estado) params.set("estado", estado);
    try {
      const data = await api(`/admin/users?${params}`);
      const rows = data.usuarios || [];
      $("#users-table").innerHTML = rows.length ? rows.map((user) => {
        const enabled = user.estado === "habilitado";
        return `<tr><td><div class="user-id"><code>${esc(user.id)}</code><button type="button" class="button secondary small" data-action="copy-user-id" data-id="${esc(user.id)}" title="Copiar user ID">Copiar</button></div></td><td>${esc(user.email)}</td><td>${esc(user.display_name || "—")}</td><td><span class="pill">${esc(user.plan)}</span></td><td>${accountEditor(user)}</td><td>${userKeyCell(user, enabled)}</td><td><span class="status ${statusClass(user.estado)}">${enabled ? "Activo" : "Desactivado"}</span></td><td>${esc(user.max_requests ?? "∞")}</td><td class="actions"><div class="actions-inner"><button type="button" class="button secondary small" data-action="user-toggle" data-id="${esc(user.id)}" data-enabled="${enabled}">${enabled ? "Desactivar" : "Activar"}</button></div></td></tr>`;
      }).join("") : `<tr><td colspan="9" class="empty">No hay usuarios para ese filtro.</td></tr>`;
    } catch (error) { flash(error.message, "error"); }
  }


  // ---- Filtros por columna (estilo ERP) ----
  // Clic en el encabezado: menu con casillas (multiseleccion), buscador,
  // Todos/Ninguno y Aplicar/Limpiar. Los filtros son EN CASCADA: cada columna
  // solo ofrece (y deja marcar) los valores que existen en las filas que ya
  // cumplen los filtros de las demas columnas. "Quitar filtros" limpia todo.
  const CF_SKIP = new Set(["acciones", "copiar", "datos"]);
  const CF_MAX_OPTIONS = 500;
  const cfNorm = (text) => String(text == null ? "" : text).replace(/\s+/g, " ").trim();
  const cfKey = (text) => cfNorm(text).toLowerCase();
  const cfStates = new WeakMap();
  let cfOpenMenu = null;
  function cfState(table) {
    let st = cfStates.get(table);
    if (!st) { st = { sel: {}, signature: "", building: false, timer: 0 }; cfStates.set(table, st); }
    return st;
  }
  function cfDataRows(table) {
    return Array.from(table.tBodies[0] ? table.tBodies[0].rows : []).filter((row) => !row.querySelector("td[colspan]"));
  }
  // Valor filtrable de una celda: ``data-filter`` si existe; si la celda es un
  // <details> (editor), solo su <summary>; si no, el texto sin controles
  // (botones, campos, listas) para que no entren textos de formularios.
  function cfCell(row, index) {
    const cell = row.cells[index];
    if (!cell) return "";
    if (cell.dataset.filter != null) return cfNorm(cell.dataset.filter);
    const holder = cell.querySelector("[data-filter]");
    if (holder) return cfNorm(holder.dataset.filter);
    const summary = cell.querySelector("details > summary");
    if (summary) return cfNorm(summary.textContent.split("·")[0]);
    const clone = cell.cloneNode(true);
    clone.querySelectorAll("button, input, select, textarea, details, script, style").forEach((node) => node.remove());
    return cfNorm(clone.textContent);
  }
  function cfPass(row, sel, skipIndex) {
    return Object.entries(sel).every(([index, set]) => {
      if (Number(index) === skipIndex || !set || !set.size) return true;
      return set.has(cfKey(cfCell(row, Number(index))));
    });
  }
  function cfApply(table) {
    const st = cfState(table);
    const rows = cfDataRows(table);
    let shown = 0;
    rows.forEach((row) => { const ok = cfPass(row, st.sel, -1); row.classList.toggle("hidden", !ok); if (ok) shown += 1; });
    const active = Object.values(st.sel).some((set) => set && set.size);
    const headCells = table.tHead ? Array.from(table.tHead.rows[0].cells) : [];
    headCells.forEach((th, index) => {
      const set = st.sel[index]; const on = Boolean(set && set.size);
      th.classList.toggle("cf-active", on);
      const badge = th.querySelector(".cf-badge");
      if (badge) { badge.textContent = on ? String(set.size) : ""; badge.classList.toggle("hidden", !on); }
    });
    const bar = table.closest(".table-wrap") && table.closest(".table-wrap").nextElementSibling;
    if (bar && bar.classList.contains("cf-bar")) {
      bar.querySelector(".cf-count").textContent = active ? `Mostrando ${shown} de ${rows.length}` : `${rows.length} filas`;
      bar.querySelector(".cf-clear").classList.toggle("hidden", !active);
      const chips = bar.querySelector(".cf-chips"); chips.innerHTML = "";
      Object.entries(st.sel).forEach(([index, set]) => {
        if (!set || !set.size) return;
        const th = headCells[Number(index)]; if (!th) return;
        const chip = document.createElement("span"); chip.className = "cf-chip";
        chip.textContent = `${th.dataset.cfLabel}: ${Array.from(set).slice(0, 2).join(", ")}${set.size > 2 ? ` +${set.size - 2}` : ""}`;
        chips.appendChild(chip);
      });
    }
  }
  function cfClearAll(table) {
    const st = cfState(table); st.sel = {};
    cfApply(table); cfCloseMenu();
  }
  function cfCloseMenu() { if (cfOpenMenu) { cfOpenMenu.remove(); cfOpenMenu = null; } }
  function cfOpenColumnMenu(table, index, anchor) {
    cfCloseMenu();
    const st = cfState(table);
    const rows = cfDataRows(table);
    // Cascada: solo filas que cumplen los filtros de las OTRAS columnas.
    const base = rows.filter((row) => cfPass(row, st.sel, index));
    const counts = new Map(); const labels = new Map();
    rows.forEach((row) => { const raw = cfCell(row, index); if (raw) labels.set(cfKey(raw), raw); });
    base.forEach((row) => { const key = cfKey(cfCell(row, index)); if (key) counts.set(key, (counts.get(key) || 0) + 1); });
    const current = new Set(st.sel[index] ? Array.from(st.sel[index]) : []);
    // Siempre se muestran los valores ya marcados (aunque la cascada los deje sin filas).
    const keys = Array.from(labels.keys()).sort((a, b) => labels.get(a).localeCompare(labels.get(b), "es", { numeric: true }));
    const menu = document.createElement("div");
    menu.className = "cf-menu"; menu.setAttribute("role", "dialog"); menu.setAttribute("aria-label", `Filtrar ${anchor.dataset.cfLabel}`);
    menu.innerHTML = `<input type="search" placeholder="Buscar…" aria-label="Buscar valores"><div class="cf-toggle"><a href="#" data-cf="all">Todos</a> · <a href="#" data-cf="none">Ninguno</a></div><div class="cf-list"></div><div class="cf-actions"><button type="button" class="button primary" data-cf="apply">Aplicar</button><button type="button" class="button secondary" data-cf="clear">Limpiar</button></div>`;
    const list = menu.querySelector(".cf-list");
    if (!keys.length) list.innerHTML = `<div class="muted" style="padding:6px">Sin valores</div>`;
    keys.slice(0, CF_MAX_OPTIONS).forEach((key) => {
      const available = counts.has(key); const checked = current.has(key);
      const item = document.createElement("label"); item.className = `cf-item${available || checked ? "" : " cf-off"}`;
      item.innerHTML = `<input type="checkbox" value="${esc(key)}" ${checked ? "checked" : ""} ${available || checked ? "" : "disabled"}><span>${esc(labels.get(key))}</span><span class="cf-n">${counts.get(key) || 0}</span>`;
      list.appendChild(item);
    });
    document.body.appendChild(menu); cfOpenMenu = menu;
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8)}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    const search = menu.querySelector("input[type=search]"); search.focus();
    search.addEventListener("input", () => {
      const q = cfKey(search.value);
      list.querySelectorAll(".cf-item").forEach((item) => { item.classList.toggle("hidden", q && !cfKey(item.textContent).includes(q)); });
    });
    const visibleChecks = () => Array.from(list.querySelectorAll(".cf-item:not(.hidden):not(.cf-off) input:not(:disabled)"));
    menu.addEventListener("click", (event) => {
      const action = event.target.closest("[data-cf]");
      if (!action) return;
      event.preventDefault();
      const kind = action.dataset.cf;
      if (kind === "all") visibleChecks().forEach((c) => { c.checked = true; });
      else if (kind === "none") visibleChecks().forEach((c) => { c.checked = false; });
      else if (kind === "clear") { delete st.sel[index]; cfApply(table); cfCloseMenu(); }
      else if (kind === "apply") {
        const chosen = new Set(Array.from(list.querySelectorAll("input:checked")).map((c) => c.value));
        if (chosen.size) st.sel[index] = chosen; else delete st.sel[index];
        cfApply(table); cfCloseMenu();
      }
    });
  }
  function cfBuild(table) {
    const st = cfState(table);
    if (st.building || !table.tHead || !table.tHead.rows.length || !table.tBodies[0]) return;
    const head = table.tHead.rows[0];
    const cells = Array.from(head.cells);
    if (!cells.length) return;
    // Tabla sin filas de datos y de encabezado placeholder (p. ej. "Tabla / Datos"): no se filtra.
    if (table.closest(".table-wrap").classList.contains("hidden")) return;
    const signature = cells.map((cell) => cell.dataset.cfLabel || cfKey(cell.textContent)).join("|");
    const built = cells.every((cell) => cell.classList.contains("cf-th") || cell.dataset.cfSkip === "1");
    if (built && st.signature === signature) { cfApply(table); return; }
    st.building = true;
    if (st.signature !== signature) st.sel = {};
    st.signature = signature;
    cells.forEach((cell, index) => {
      const label = cell.dataset.cfLabel || cfNorm(cell.textContent);
      cell.dataset.cfLabel = label;
      if (!label || CF_SKIP.has(label.toLowerCase())) { cell.dataset.cfSkip = "1"; return; }
      if (cell.classList.contains("cf-th")) return;
      cell.classList.add("cf-th");
      cell.innerHTML = `<button type="button" class="cf-btn" aria-haspopup="dialog" aria-label="Filtrar por ${esc(label)}"><span>${esc(label)}</span><span class="cf-badge hidden"></span><span class="cf-caret" aria-hidden="true">▼</span></button>`;
      cell.querySelector(".cf-btn").addEventListener("click", (event) => { event.stopPropagation(); cfOpenColumnMenu(table, index, cell); });
    });
    const wrap = table.closest(".table-wrap");
    if (wrap && !(wrap.nextElementSibling && wrap.nextElementSibling.classList.contains("cf-bar"))) {
      const bar = document.createElement("div"); bar.className = "cf-bar";
      bar.innerHTML = `<span class="cf-count" aria-live="polite"></span><span class="cf-chips"></span><button type="button" class="button secondary small cf-clear hidden">Quitar filtros</button>`;
      bar.querySelector(".cf-clear").addEventListener("click", () => cfClearAll(table));
      wrap.after(bar);
    }
    st.building = false;
    cfApply(table);
  }
  function setupColumnFilters() {
    const observer = new MutationObserver((mutations) => {
      const touched = new Set();
      mutations.forEach((mutation) => {
        const node = mutation.target.nodeType === 1 ? mutation.target : mutation.target.parentElement;
        const table = node && node.closest ? node.closest("table") : null;
        if (table && !cfState(table).building) touched.add(table);
      });
      touched.forEach((table) => {
        const st = cfState(table);
        clearTimeout(st.timer);
        st.timer = setTimeout(() => cfBuild(table), 50);
      });
    });
    const watched = new WeakSet();
    const watch = (table) => {
      if (watched.has(table)) return;
      watched.add(table);
      observer.observe(table, { childList: true, subtree: true, characterData: true });
      cfBuild(table);
    };
    document.querySelectorAll(".table-wrap table").forEach(watch);
    // Tablas creadas despues (secciones por bot en "Tablas / registros"): se
    // detectan al aparecer y reciben los mismos filtros.
    const discover = new MutationObserver(() => {
      document.querySelectorAll(".table-wrap table").forEach(watch);
    });
    discover.observe(document.querySelector("main") || document.body, { childList: true, subtree: true });
    document.addEventListener("click", (event) => { if (cfOpenMenu && !cfOpenMenu.contains(event.target) && !event.target.closest(".cf-btn")) cfCloseMenu(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") cfCloseMenu(); });
    window.addEventListener("resize", cfCloseMenu);
    window.addEventListener("scroll", cfCloseMenu, true);
  }
  function syncNewAccountFields() {
    const normal = $("#new-account-type").value === "NORMAL";
    $("#new-max-field").classList.toggle("hidden", !normal);
    $("#new-max-requests").required = normal;
    if (!normal) $("#new-max-requests").value = "";
  }
  async function createUser(event) {
    event.preventDefault();
    const payload = {
      email: $("#new-email").value.trim(),
      display_name: $("#new-display").value.trim(),
      account_type: $("#new-account-type").value,
      api_key: $("#new-api-key").value.trim(),
      estado: $("#new-state").value,
      enviar_credenciales: $("#new-send-credentials").checked,
      motivo: $("#new-reason").value.trim(),
    };
    if (payload.account_type === "NORMAL") {
      const max = Number($("#new-max-requests").value);
      if (!Number.isInteger(max) || max < 1 || max > 10000000) { flash("Indica el cupo mensual de la cuenta NORMAL (1 a 10000000).", "error"); return; }
      payload.max_requests = max;
    }
    try {
      const data = await api("/admin/users", { method: "POST", body: JSON.stringify(payload) });
      const credenciales = data.credenciales || {};
      $("#create-user-form").reset(); syncNewAccountFields();
      flash(`Usuario creado: ${data.usuario?.email || payload.email}`);
      if (credenciales.solicitado && credenciales.enviadas) {
        flash(`Usuario creado y credenciales enviadas a ${credenciales.destino}.`, "ok");
      } else if (credenciales.solicitado) {
        flash(`Usuario creado, pero no se enviaron credenciales: ${credenciales.motivo}.`, "error");
      }
      await loadUsers();
      const nuevo = data.usuario?.id ? keyInput(data.usuario.id) : null;
      if (nuevo && credenciales.valor_unica_vez) { nuevo.value = credenciales.valor_unica_vez; nuevo.type = "text"; }
    } catch (error) { flash(error.message, "error"); }
  }
  async function writeClipboard(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const temporary = document.createElement("textarea");
    temporary.value = value; temporary.setAttribute("readonly", "");
    temporary.style.position = "fixed"; temporary.style.opacity = "0";
    document.body.appendChild(temporary);
    let copied = false;
    try { temporary.select(); copied = document.execCommand("copy"); }
    finally { temporary.remove(); }
    if (!copied) throw new Error("No se pudo acceder al portapapeles.");
  }
  async function copyApiKeyToClipboard(userId, keyId) {
    const data = await api(`/admin/users/${encodeURIComponent(userId)}/api-keys/${encodeURIComponent(keyId)}/reveal`, { method: "POST" });
    const value = String(data.api_key || "");
    if (!value) throw new Error("La API no devolvió una clave recuperable.");
    await writeClipboard(value);
    flash("API key copiada al portapapeles.");
  }
  const COPY_LIMIT_BYTES = 200 * 1024;
  const copyStore = new Map();
  let copySeq = 0;
  const copyKinds = [["request", "Copiar request"], ["response", "Copiar response"], ["both", "Copiar request + response"], ["curl", "Copiar como curl"]];
  function hasPayload(value) { return value !== null && value !== undefined && !(typeof value === "object" && Object.keys(value).length === 0); }
  function copyContext(record, job) {
    const source = record || {};
    const meta = job || {};
    const request = source.request_payload ?? source.request ?? null;
    let response = source.response_payload ?? source.response ?? source.result ?? null;
    if (!hasPayload(response) && hasPayload(source.response_summary)) response = source.response_summary;
    return {
      job_id: meta.id || meta.job_id || source.job_id || source.id || null,
      bot: meta.bot || source.bot || null,
      operation: meta.operacion || meta.operation || source.operation || source.operacion || null,
      status: meta.estado || meta.status || source.estado || source.status || source.resultado || null,
      created_at: meta.creado_en || meta.created_at || source.created_at || source.creado_en || null,
      request, response,
      attempt: meta.intento ?? source.attempt ?? null,
      worker: meta.worker ?? null,
      started_at: meta.iniciado_en ?? null,
      finished_at: meta.finalizado_en ?? null,
      credentials: source.credentials || meta.credentials || null,
    };
  }
  // Request del estado del job: la consulta que hace el cliente para seguir el job.
  function statusRequest(context) {
    return { method: "GET", path: context.job_id ? `/api/v3/jobs/${context.job_id}` : "/api/v3/jobs/<job_id>" };
  }
  // Response del estado del job: lo que devuelve esa consulta (estado + resultado).
  function statusResponse(context) {
    return {
      job_id: context.job_id, bot: context.bot, operation: context.operation, status: context.status,
      created_at: context.created_at, started_at: context.started_at, finished_at: context.finished_at,
      attempt: context.attempt, result: context.response ?? null,
    };
  }
  function copyButtons(context, lazyJobId) {
    if (!lazyJobId && !hasPayload(context.request) && !hasPayload(context.response)) return "";
    const id = `c${++copySeq}`;
    if (copyStore.size > 500) copyStore.delete(copyStore.keys().next().value);
    copyStore.set(id, { context, lazyJobId: lazyJobId || "" });
    return `<div class="copy-buttons" role="group" aria-label="Copiar request y response">${copyKinds.map(([kind, label]) => `<button type="button" class="button secondary small" data-copy-kind="${kind}" data-copy-id="${id}" aria-label="${label}" title="${label}">${label}</button>`).join("")}</div>`;
  }
  function curlPath(context) {
    return context.bot && context.operation ? `/api/v3/bots/${encodeURIComponent(context.bot)}/${encodeURIComponent(context.operation)}` : "/api/v3/bots/<bot>/<operacion>";
  }
  function buildCopyText(kind, context) {
    if (kind === "request") return json({ job_request: context.request ?? {}, status_request: statusRequest(context) });
    if (kind === "response") return json(statusResponse(context));
    if (kind === "both") return json({ job_request: context.request ?? {}, status_request: statusRequest(context), status_response: statusResponse(context) });
    // curl para recrear el job: cuerpo original + campos de credenciales que
    // el panel nunca almacena en claro, como marcadores a completar.
    const body = { ...(context.request || {}) };
    const fields = (context.credentials && context.credentials.fields) || [];
    fields.forEach((field) => { if (!(field in body) || body[field] === "[REDACTED]" || body[field] === "***") body[field] = `<${String(field).toUpperCase()}>`; });
    const text = JSON.stringify(body, null, 2).replace(/'/g, "'\\''");
    return `curl -X POST '${window.location.origin}${curlPath(context)}' -H 'Content-Type: application/json' -H 'Authorization: Basic <API_KEY>' -d '${text}'`;
  }
  function copyAnnounce(message) {
    let node = document.getElementById("copy-live");
    if (!node) {
      node = document.createElement("span"); node.id = "copy-live";
      node.setAttribute("aria-live", "polite"); node.setAttribute("role", "status");
      node.style.cssText = "position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden";
      document.body.appendChild(node);
    }
    node.textContent = message;
  }
  async function copyRecordAction(event) {
    const button = event.target.closest("button[data-copy-kind]"); if (!button) return;
    const entry = copyStore.get(button.dataset.copyId); if (!entry) return;
    const original = button.dataset.label || button.textContent;
    button.dataset.label = original;
    try {
      let context = entry.context;
      if (entry.lazyJobId && !entry.loaded) {
        const data = await api(`/admin/jobs/${encodeURIComponent(entry.lazyJobId)}`);
        const fresh = copyContext({ request: data.request, response: data.response ?? data.result ?? data.resultado, credentials: data.credentials }, data.job || {});
        // Completa solo lo que falta: nunca pisa datos que el registro ya traia.
        context = { ...fresh };
        Object.keys(entry.context).forEach((key) => { if (!hasPayload(context[key])) context[key] = entry.context[key]; });
        entry.context = context; entry.loaded = true;
      }
      const text = buildCopyText(button.dataset.copyKind, context);
      await writeClipboard(text);
      const bytes = new Blob([text]).size;
      const note = bytes > COPY_LIMIT_BYTES ? ` Copiado (${Math.round(bytes / 1024)} KB, contenido muy grande).` : "";
      button.textContent = "Copiado ✓";
      copyAnnounce(`Copiado al portapapeles.${note}`);
      if (note) flash(note.trim());
      window.clearTimeout(button.copyTimer);
      button.copyTimer = window.setTimeout(() => { button.textContent = original; }, 1500);
    } catch (error) { flash(error.message || "No se pudo copiar.", "error"); }
  }
  document.addEventListener("click", copyRecordAction);
  function askReason(message, fallback) {
    const raw = window.prompt(`${message} (3-500 caracteres):`, fallback);
    if (raw === null) return null;
    const motivo = raw.trim();
    if (motivo.length < 3 || motivo.length > 500) { flash("El motivo debe tener entre 3 y 500 caracteres.", "error"); return null; }
    return motivo;
  }
  const keyInput = (userId) => document.querySelector(`input[data-key-input="${CSS.escape(userId)}"]`);
  async function revealKey(userId, keyId) {
    const data = await api(`/admin/users/${encodeURIComponent(userId)}/api-keys/${encodeURIComponent(keyId)}/reveal`, { method: "POST" });
    const value = String(data.api_key || "");
    if (!value) throw new Error("La API no devolvió una clave recuperable.");
    return value;
  }
  // "Ver" alterna mostrar/ocultar la clave dentro del mismo campo, como en V1.
  async function toggleKeyView(button) {
    const input = keyInput(button.dataset.user); if (!input) return;
    if (input.type === "text") { input.type = "password"; input.value = ""; button.textContent = "Ver"; return; }
    input.value = await revealKey(button.dataset.user, button.dataset.key);
    input.type = "text"; button.textContent = "Ocultar";
  }
  // "Guardar" reemplaza la clave activa por el valor del campo (vacío = aleatoria).
  // Sin clave activa, emite una nueva. Como en V1, sin confirmaciones extra.
  // Si la clave cambió desde otra sesión (lista desactualizada), recarga la
  // fila y reintenta una vez contra la clave activa actual.
  async function persistKey(userId, keyId, valor) {
    return keyId
      ? api(`/admin/users/${encodeURIComponent(userId)}/api-keys/rotate`, { method: "POST", body: JSON.stringify({ key_id: keyId, periodo_gracia_horas: 0, motivo: "edición desde panel", valor_fijo: valor }) })
      : api(`/admin/users/${encodeURIComponent(userId)}/api-keys`, { method: "POST", body: JSON.stringify({ scopes: [], motivo: "emisión desde panel", valor_fijo: valor }) });
  }
  const currentKeyId = (userId) => document.querySelector(`button[data-action="key-save"][data-user="${CSS.escape(userId)}"]`)?.dataset.key || "";
  async function saveKey(button) {
    const userId = button.dataset.user;
    const input = keyInput(userId);
    const valor = (input ? input.value : "").trim();
    if (valor && (valor.length < 3 || /\s/.test(valor))) { flash("La API key debe tener al menos 3 caracteres y no contener espacios.", "error"); return; }
    button.disabled = true;
    try {
      let data;
      try {
        data = await persistKey(userId, button.dataset.key, valor);
      } catch (error) {
        if (!/clave anterior no (está activa|encontrada)/.test(error.message)) throw error;
        await loadUsers();
        data = await persistKey(userId, currentKeyId(userId), valor);
      }
      await loadUsers();
      const nuevo = keyInput(userId);
      if (nuevo) { nuevo.value = data.valor_unica_vez || ""; nuevo.type = "text"; }
      const ver = document.querySelector(`button[data-action="key-view"][data-user="${CSS.escape(userId)}"]`);
      if (ver) ver.textContent = "Ocultar";
      flash(valor ? "API key actualizada." : "API key aleatoria generada y guardada.", "ok");
    } catch (error) {
      flash(`No se pudo guardar la API key: ${error.message}`, "error");
    } finally { button.disabled = false; }
  }
  async function userAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return;
    const action = button.dataset.action; const userId = button.dataset.id;
    if (action === "copy-user-id") {
      try { await writeClipboard(userId); flash("User ID copiado al portapapeles."); }
      catch (error) { flash(error.message, "error"); }
      return;
    }
    if (action === "key-copy") {
      try { await copyApiKeyToClipboard(button.dataset.user, button.dataset.key); }
      catch (error) { flash(error.message, "error"); }
      return;
    }
    if (action === "key-view") { try { await toggleKeyView(button); } catch (error) { flash(error.message, "error"); } return; }
    if (action === "key-save") { await saveKey(button); return; }
    if (action === "account-save") {
      const mode = $(`[data-account-mode="${CSS.escape(userId)}"]`).value;
      const reason = $(`[data-account-reason="${CSS.escape(userId)}"]`).value.trim();
      if (reason.length < 10 || reason.length > 500) { flash("El motivo debe tener entre 10 y 500 caracteres.", "error"); return; }
      const payload = { account_type: mode, motivo: reason };
      if (mode === "NORMAL") {
        const max = Number($(`[data-account-max="${CSS.escape(userId)}"]`).value);
        if (!Number.isInteger(max) || max < 1) { flash("Indica el cupo mensual de la cuenta NORMAL.", "error"); return; }
        payload.max_requests = max;
      }
      try {
        await api(`/admin/billing/accounts/${encodeURIComponent(userId)}/account-type`, { method: "POST", body: JSON.stringify(payload) });
        flash("Tipo de cuenta actualizado y auditado.");
        await loadUsers();
      } catch (error) { flash(error.message, "error"); }
      return;
    }
    if (action !== "user-toggle") return;
    const enabled = button.dataset.enabled === "true"; const motivo = askReason(`Motivo para ${enabled ? "desactivar" : "activar"} el usuario`, "cambio desde panel V3"); if (motivo === null) return;
    try { await api(`/admin/users/${encodeURIComponent(userId)}/${enabled ? "disable" : "enable"}`, { method: "POST", body: JSON.stringify({ motivo }) }); flash("Estado de usuario actualizado."); await loadUsers(); } catch (error) { flash(error.message, "error"); }
  }
  async function loadJobs(event) {
    if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const values = [["estado", "#jobs-state"], ["bot", "#jobs-bot"], ["usuario", "#jobs-user"]]; values.forEach(([key, selector]) => { const value = $(selector).value.trim(); if (value) params.set(key, value); });
    try { const [data, metrics] = await Promise.all([api(`/admin/jobs?${params}`), api("/admin/jobs/metrics")]); const stateCards = Object.entries(metrics.por_estado || {}).map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join(""); $("#jobs-metrics").innerHTML = stateCards || `<div class="card"><div class="metric"><span>Total</span><strong>${esc(data.total || 0)}</strong></div></div>`; const rows = data.jobs || []; $("#jobs-table").innerHTML = rows.length ? rows.map((job) => `<tr><td>${idCode(job.job_id)}</td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado)}</span></td><td>${esc(job.resultado || "—")}</td><td>${esc(job.bot)}<br><span class="muted">${esc(job.operacion)}</span></td><td>${esc(job.usuario_email || job.usuario || "—")}</td><td>${idCode(job.user_id)}</td><td>${idCode(job.worker_id || job.worker)}</td><td>${esc(job.intento)}${job.max_intentos != null ? ` / ${esc(job.max_intentos)}` : ""}</td><td>${esc(job.prioridad ?? job.prioridad_admin ?? "—")}</td><td>${esc(job.app_version || "—")}${job.protocol_version != null ? `<br><span class="muted">proto ${esc(job.protocol_version)}</span>` : ""}</td><td>${esc(date(job.creado_en))}</td><td>${esc(date(job.asignado_en))}</td><td>${esc(date(job.iniciado_en))}</td><td>${esc(date(job.finalizado_en))}</td><td>${esc(date(job.lease_expira_en))}</td><td>${job.idempotency_key ? `<code class="job-id">${esc(job.idempotency_key)}</code>` : "—"}</td><td>${job.cancel_reason || job.cancelled_by ? `${esc(job.cancelled_by || "")}<br><span class="muted">${esc(job.cancel_reason || "")}</span>` : "—"}</td><td>${job.error_code || job.error_message ? `<code>${esc(job.error_code || "")}</code><br><span class="muted">${esc(job.error_message || "")}</span>` : "—"}</td><td class="actions"><div class="actions-inner"><button class="button secondary small" data-action="job-detail" data-id="${esc(job.job_id)}">Detalle</button>${copyButtons({ job_id: job.job_id, bot: job.bot, operation: job.operacion, status: job.estado, created_at: job.creado_en || job.created_at || null, request: null, response: null }, job.job_id)}${["PENDIENTE","ASIGNADO"].includes(job.estado) ? `<button class="button primary small" data-action="job-force" data-id="${esc(job.job_id)}">Forzar</button>` : ""}${["PENDIENTE","ASIGNADO","CORRIENDO"].includes(job.estado) ? `<button class="button danger small" data-action="job-cancel" data-id="${esc(job.job_id)}">Cancelar</button>` : ""}</div></td></tr>`).join("") : `<tr><td colspan="19" class="empty">No hay jobs para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); }
  }
  async function loadExecutions(event) {
    if (event) event.preventDefault();
    try {
      await loadTableCatalog();
      const tabla = $("#executions-table-select").value;
      if (!tabla) { flash("Selecciona una tabla para consultar.", "error"); return; }
      const params = new URLSearchParams({ limit: $("#executions-limit").value, offset: "0", tabla });
      const values = [["q", "#executions-query"], ["job_id", "#executions-job"], ["bot", "#executions-bot"], ["operacion", "#executions-operation"], ["estado", "#executions-state"], ["usuario", "#executions-user"]];
      values.forEach(([key, selector]) => { const value = $(selector).value.trim(); if (value) params.set(key, value); });
      const data = await api(`/admin/records?${params}`);
      const total = Number(data.total || 0);
      const shown = Number((data.records || []).length);
      const tablaLabel = data.catalogo?.label || data.tabla_resuelta || tabla;
      const countLabel = data.has_more ? `${shown} de al menos ${total} filas` : `${shown} de ${total} filas`;
      $("#execution-table-status").textContent = `${tablaLabel} · ${data.fuente || "—"} · ${countLabel}`;
      if (data.catalogo?.kind === "bot" || data.catalogo?.kind === "legacy") {
        $("#executions-head").innerHTML = "<tr><th>Tabla detallada por bot</th></tr>";
        $("#executions-table").innerHTML = `<tr><td class="empty">La tabla detallada se muestra arriba.</td></tr>`;
        if (data.physical_table) renderPhysicalBotSection(data.bot_sections?.[0] || {});
        else renderBotSections(data.bot_sections || []);
      } else {
        renderCanonicalTable(data.tabla_resuelta || tabla, data.records || []);
      }
    } catch (error) { flash(error.message, "error"); }
  }
  async function executionAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button || button.dataset.action !== "credential-detail") return;
    try { const data = await api(`/admin/jobs/${encodeURIComponent(button.dataset.id)}/credentials`); window.alert(`Credenciales de la ejecución ${button.dataset.id}:\n\n${json(data.credentials)}\n\nContexto:\n${json(data.credential_metadata || {})}`); } catch (error) { flash(error.message, "error"); }
  }
  async function jobAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return; const jobId = button.dataset.id;
    if (button.dataset.action === "job-detail") { try { const data = await api(`/admin/jobs/${encodeURIComponent(jobId)}`); window.alert(json(data)); } catch (error) { flash(error.message, "error"); } return; }
    if (button.dataset.action === "job-force") {
      if (!window.confirm("Forzar la ejecución inmediata aunque supere el cupo del worker. Es la única vía que puede excederlo. ¿Continuar?")) return;
      const worker = window.prompt("Worker destino (vacío = mejor SANO disponible):", "") || "";
      const motivo = window.prompt("Motivo del forzado (10-500 caracteres):", "forzado desde panel V3"); if (!motivo) return;
      try { const data = await api(`/admin/jobs/${encodeURIComponent(jobId)}/force`, { method: "POST", body: JSON.stringify({ worker: worker.trim(), motivo }) }); flash(data.success ? `Job forzado en ${data.worker}. ${data.advertencia || ""}` : `Forzado no despachado: ${data.estado}`); await loadJobs(); } catch (error) { flash(error.message, "error"); } return;
    }
    const motivo = window.prompt("Motivo de cancelación:", "cancelación desde panel V3"); if (!motivo) return; try { await api(`/admin/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: JSON.stringify({ motivo }) }); flash("Comando de cancelación registrado."); await loadJobs(); } catch (error) { flash(error.message, "error"); }
  }
  const formatAge = (seconds) => { if (seconds == null || !Number.isFinite(Number(seconds))) return "—"; const total = Math.max(0, Math.floor(Number(seconds))); const days = Math.floor(total / 86400); const hours = Math.floor((total % 86400) / 3600); const minutes = Math.floor((total % 3600) / 60); return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`; };
  function fleetVersionBadge(worker) {
    const version = worker.version || worker.app_version || worker.image_digest || "";
    const label = worker.outdated === true ? "Desactualizado" : worker.is_latest === true ? "Actualizado" : version ? "Sin referencia" : "Sin versión";
    const kind = worker.outdated === true ? "status-bad" : worker.is_latest === true ? "status-good" : "status-neutral";
    const latest = worker.latest_version ? `Latest: ${worker.latest_version}` : "Latest no disponible";
    const digest = worker.image_digest ? `<br><span class="muted" title="${esc(worker.image_digest)}">digest ${esc(short(worker.image_digest, 20))}</span>` : "";
    return `<span class="status ${kind}" title="${esc(latest)}">${label}</span><br><code title="${esc(version || "Sin versión reportada")}">${esc(version ? short(version, 24) : "Sin versión")}</code>${digest}`;
  }
  async function loadFleet() {
    try {
      const data = await api("/admin/fleet");
      const legacy = data.flota || [];
      const manuales = data.manuales || legacy.filter((worker) => worker.origen !== "efimero");
      const efimeros = data.efimeros || legacy.filter((worker) => worker.origen === "efimero");
      const versionSummary = data.resumen?.versiones || {};
      const workers = [...manuales, ...efimeros];
      const totalVersions = Number(versionSummary.total ?? workers.length);
      const outdatedCount = Number(versionSummary.outdated ?? workers.filter((worker) => worker.outdated === true).length);
      const latestVersion = versionSummary.latest_version ?? data.latest_version;
      const latestDigest = versionSummary.latest_digest ?? data.latest_digest;
      const unknownCount = Number(versionSummary.unknown ?? workers.filter((worker) => !worker.version).length);
      $("#fleet-version-summary").textContent = `${outdatedCount} de ${totalVersions} desactualizados · Latest: ${latestVersion || "sin versión reportada"}${latestDigest ? ` · digest ${short(latestDigest, 20)}` : ""}${unknownCount ? ` · ${unknownCount} sin versión` : ""}`;
      const outdatedOnly = $("#fleet-outdated-only").checked;
      const manualesVisibles = outdatedOnly ? manuales.filter((worker) => worker.outdated === true) : manuales;
      const efimerosVisibles = outdatedOnly ? efimeros.filter((worker) => worker.outdated === true) : efimeros;
      $("#fleet-manual-count").textContent = String(data.resumen?.manuales?.total ?? manuales.length);
      $("#fleet-ephemeral-count").textContent = String(data.resumen?.efimeros?.total ?? efimeros.length);
      $("#fleet-manual-table").innerHTML = manualesVisibles.length ? manualesVisibles.map((worker) => `<tr><td><strong>${esc(worker.alias || "—")}</strong></td><td><code>${esc(worker.node)}</code></td><td><code>${esc(worker.ip || "—")}</code></td><td><span class="pill">${esc(worker.origen || "—")}</span></td><td><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span></td><td>${fleetVersionBadge(worker)}</td><td>${esc(worker.jobs_activos ?? worker.en_ejecucion ?? 0)}/${esc(worker.capacidad ?? 0)}</td><td>${esc(worker.protocolo || "—")} <span class="pill">${esc(worker.protocolo_estado || "—")}</span></td><td>${esc((worker.bots || []).join(", ") || "—")}</td><td>${esc(((worker.muestra_error || {}).fallidos || 0))}/${esc(((worker.muestra_error || {}).total || 0))}</td><td>${esc((worker.alertas || []).length)}</td><td class="actions"><div class="actions-inner"><button class="button secondary small" data-action="worker-alias" data-id="${esc(worker.node)}" data-alias="${esc(worker.alias || "")}">Editar alias</button><button class="button danger small" data-action="worker-remove" data-id="${esc(worker.node)}">Dar de baja</button></div></td></tr>`).join("") : `<tr><td colspan="12" class="empty">${outdatedOnly ? "No hay workers manuales desactualizados." : "No hay workers manuales inventariados. Agrega uno arriba."}</td></tr>`;
      $("#fleet-ephemeral-table").innerHTML = efimerosVisibles.length ? efimerosVisibles.map((worker) => `<tr><td><code>${esc(worker.instance_id || "—")}</code></td><td>${esc(worker.instance_type || "—")}</td><td>${esc(worker.ram_gib == null ? "—" : `${worker.ram_gib} GiB`)}</td><td>${esc(worker.concurrency ?? worker.capacidad ?? "—")}</td><td>${esc(formatAge(worker.edad))}</td><td><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span></td><td>${fleetVersionBadge(worker)}</td><td>${esc(worker.jobs_activos ?? worker.en_ejecucion ?? 0)}/${esc(worker.concurrency ?? worker.capacidad ?? 0)}</td><td class="actions"><div class="actions-inner"><button class="button danger small" data-action="ephemeral-terminate" data-id="${esc(worker.instance_id || "")}">Terminar</button></div></td></tr>`).join("") : `<tr><td colspan="9" class="empty">${outdatedOnly ? "No hay workers efímeros desactualizados." : "No hay workers efímeros (EC2)."}</td></tr>`;
    } catch (error) { flash(error.message, "error"); }
  }
  async function evaluateFleet() { try { const data = await api("/admin/fleet/evaluate", { method: "POST" }); flash(`Evaluación completada: ${data.activas?.length || 0} alertas activas.`); await loadFleet(); } catch (error) { flash(error.message, "error"); } }
  async function addWorker(event) {
    event.preventDefault(); const node = $("#new-worker-node").value.trim(); if (!node) return;
    try { const data = await api("/admin/workers", { method: "POST", body: JSON.stringify({ node }) }); $("#new-worker-node").value = ""; flash(data.alcanzable ? `Worker ${data.node} agregado y alcanzable.` : `Worker ${data.node} agregado (aún no responde: ${data.detalle || "sin sonda"}).`); await loadFleet(); } catch (error) { flash(error.message, "error"); }
  }
  async function fleetAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return;
    if (button.dataset.action === "worker-alias") {
      const nuevo = window.prompt(`Alias para ${button.dataset.id} (vacío para quitarlo):`, button.dataset.alias || ""); if (nuevo === null) return;
      try { await api(`/admin/workers/${encodeURIComponent(button.dataset.id)}/alias`, { method: "PUT", body: JSON.stringify({ alias: nuevo.trim() }) }); flash("Alias actualizado."); await loadFleet(); } catch (error) { flash(error.message, "error"); }
      return;
    }
    if (button.dataset.action === "worker-remove") {
      if (!window.confirm(`Dar de baja ${button.dataset.id}? El scheduler dejará de asignarle jobs.`)) return;
      try { await api(`/admin/workers/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" }); flash("Worker dado de baja."); await loadFleet(); } catch (error) { flash(error.message, "error"); }
      return;
    }
    if (button.dataset.action === "ephemeral-terminate") {
      if (!window.confirm(`Terminar instancia EC2 ${button.dataset.id}?`)) return;
      const motivo = window.prompt("Motivo de terminación (mínimo 10 caracteres):", "terminación desde panel V3"); if (!motivo) return;
      try { await api("/admin/autoscale/terminate", { method: "POST", body: JSON.stringify({ instance_ids: [button.dataset.id], motivo }) }); flash(`Instancia ${button.dataset.id} enviada a terminación.`); await loadFleet(); } catch (error) { flash(error.message, "error"); }
    }
  }
  async function loadAudit(event) { if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const action = $("#audit-action").value.trim(); const actor = $("#audit-actor").value.trim(); if (action) params.set("accion", action); if (actor) params.set("actor", actor); try { const data = await api(`/admin/audit?${params}`); const rows = data.eventos || []; $("#audit-table").innerHTML = rows.length ? rows.slice().reverse().map((item) => `<tr><td>${esc(date(item.occurred_at))}</td><td><strong>${esc(item.action)}</strong></td><td>${esc(item.actor_id)}</td><td>${esc(item.target_type)}<br><span class="muted">${esc(short(item.target_id, 22))}</span></td><td><span class="status ${item.result === "success" ? "status-good" : "status-bad"}">${esc(item.result)}</span></td><td>${esc(item.reason || "—")}</td></tr>`).join("") : `<tr><td colspan="6" class="empty">No hay eventos para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); } }
  $("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); state.token = $("#admin-token").value.trim(); if (!state.token) return; try { await api("/admin/users?limit=1"); sessionStorage.setItem(TOKEN_KEY, state.token); loginError(""); showLoggedIn(true); setView(state.view); } catch (_) { state.token = ""; loginError("No se pudo validar el token de administración."); } });
  $("#logout-button").addEventListener("click", () => logout());
  document.addEventListener("click", (event) => { const link = event.target.closest("[data-goto]"); if (link) setView(link.dataset.goto); });
  $("#main-nav").addEventListener("click", (event) => { const button = event.target.closest("button[data-view]"); if (button) setView(button.dataset.view); });
  $("#users-filter").addEventListener("submit", loadUsers); $("#create-user-form").addEventListener("submit", createUser); $("#new-account-type").addEventListener("change", syncNewAccountFields); syncNewAccountFields(); setupColumnFilters(); $("#users-table").addEventListener("click", userAction);
  $("#reports-form").addEventListener("submit", loadReport); $("#reports-download").addEventListener("click", downloadReport); $("#jobs-filter").addEventListener("submit", loadJobs); $("#jobs-table").addEventListener("click", jobAction); $("#executions-filter").addEventListener("submit", loadExecutions); $("#executions-table-select").addEventListener("change", () => loadExecutions());
  $("#executions-table-search").addEventListener("focus", () => { $("#executions-table-search").select(); openTableOptions(); });
  $("#executions-table-search").addEventListener("input", (event) => { $("#executions-table-select").value = ""; openTableOptions(event.target.value); });
  $("#executions-table-search").addEventListener("keydown", handleTableComboboxKeydown);
  $("#executions-table-listbox").addEventListener("pointerdown", (event) => { if (event.target.closest("[role=option]")) event.preventDefault(); });
  $("#executions-table-listbox").addEventListener("click", (event) => { const option = event.target.closest("[role=option]"); if (option) chooseTableOption(option.dataset.value); });
  document.addEventListener("click", (event) => { if (!$("#executions-table-combobox").contains(event.target)) closeTableOptions(true); });
  $("#executions-table-search").addEventListener("blur", () => window.setTimeout(() => closeTableOptions(true), 0));
  $("#executions-limit").addEventListener("change", () => loadExecutions()); $("#executions-table").addEventListener("click", executionAction); $("#bot-sections").addEventListener("click", executionAction);
  $("#fleet-refresh").addEventListener("click", loadFleet); $("#fleet-evaluate").addEventListener("click", evaluateFleet); $("#fleet-outdated-only").addEventListener("change", loadFleet); $("#add-worker-form").addEventListener("submit", addWorker); $("#fleet-manual-table").addEventListener("click", fleetAction); $("#fleet-ephemeral-table").addEventListener("click", fleetAction); $("#audit-filter").addEventListener("submit", loadAudit);
  if (state.token) { showLoggedIn(true); setView(state.view); } else { showLoggedIn(false); }
})();
</script>
</body>
</html>'''


@router.get(
    "/",
    response_class=HTMLResponse,
    summary="Abrir el panel de administración",
    description=(
        "Devuelve la página de entrada de la consola administrativa. Desde ella "
        "se puede iniciar una sesión de administración y acceder a las vistas "
        "disponibles según los permisos y la configuración del servicio."
    ),
    responses={
        200: {
            "description": "Página HTML de entrada al panel administrativo.",
            "content": {
                "text/html": {
                    "example": '<!DOCTYPE html><html lang="es"><head><title>Administración | MrBot V3</title></head><body>...</body></html>'
                }
            },
        }
    },
)
def portada_admin() -> str:
    """Devuelve el documento HTML de entrada a la consola administrativa."""
    return ADMIN_PANEL_HTML


@router.get(
    "/login",
    response_class=HTMLResponse,
    summary="Abrir la pantalla de acceso administrativo",
    description=(
        "Entrega la misma consola administrativa en la ruta explícita de acceso, "
        "para clientes que conservan un enlace directo a esta dirección. La "
        "respuesta es una página HTML; no inicia la sesión por sí sola."
    ),
    responses={
        200: {
            "description": "Página HTML con la pantalla de acceso administrativo.",
            "content": {
                "text/html": {
                    "example": '<!DOCTYPE html><html lang="es"><head><title>Administración | MrBot V3</title></head><body>...</body></html>'
                }
            },
        }
    },
)
def login_admin() -> str:
    """Devuelve la página HTML de acceso administrativo."""
    return ADMIN_PANEL_HTML
