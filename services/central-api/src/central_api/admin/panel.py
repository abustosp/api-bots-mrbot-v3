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
table { width: 100%; min-width: 760px; border-collapse: collapse; }
th, td { padding: 12px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 13px; }
th { color: var(--muted); font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; }
.code { display: block; padding: 10px; overflow-x: auto; border-radius: 10px; color: #fff; background: #332b28; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; word-break: break-word; }
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
      <button type="button" data-view="executions">Registros</button>
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
        <div class="grid" id="dashboard-metrics"><div class="empty">Cargando métricas...</div></div>
        <div class="grid" style="margin-top:14px"><div class="card"><h3>Flota</h3><div id="dashboard-fleet" class="muted">Cargando...</div></div><div class="card"><h3>Últimas acciones</h3><div id="dashboard-audit" class="muted">Cargando...</div></div></div>
      </section>

      <section class="view hidden" data-panel="users">
        <div class="kicker">Identidades y acceso</div><h2>Usuarios</h2><p class="subtle">Alta, estado, plan y claves API de los clientes V3.</p>
        <form id="users-filter" class="toolbar"><div class="field wide"><label for="users-email">Buscar por email</label><input id="users-email" name="email" type="search" placeholder="cliente@example.com"></div><div class="field"><label for="users-state">Estado</label><select id="users-state"><option value="">Todos</option><option value="habilitado">Habilitado</option><option value="deshabilitado">Deshabilitado</option></select></div><button class="button primary" type="submit">Buscar</button></form>
        <details><summary>Crear usuario</summary><div class="details-body"><form id="create-user-form"><div class="form-grid"><div class="field"><label for="new-email">Email</label><input id="new-email" type="email" required></div><div class="field"><label for="new-display">Nombre</label><input id="new-display" type="text"></div><div class="field"><label for="new-plan">Plan</label><select id="new-plan"><option>free</option><option>basico</option><option>pro</option><option>empresa</option></select></div><div class="field"><label for="new-reason">Motivo</label><input id="new-reason" type="text" value="alta desde panel V3" required></div></div><button class="button primary" type="submit">Crear usuario</button></form></div></details>
        <div class="table-wrap"><table><thead><tr><th>Email</th><th>Nombre</th><th>Plan</th><th>Estado</th><th>Créditos</th><th>Acciones</th></tr></thead><tbody id="users-table"><tr><td colspan="6" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="jobs">
        <div class="kicker">Cola y ejecución</div><h2>Jobs</h2><p class="subtle">Observa asignaciones, intentos y acciones durables sin exponer payloads sensibles.</p>
        <form id="jobs-filter" class="toolbar"><div class="field"><label for="jobs-state">Estado</label><select id="jobs-state"><option value="">Todos</option><option>PENDIENTE</option><option>ASIGNADO</option><option>CORRIENDO</option><option>COMPLETO</option><option>FALLIDO</option><option>CANCELADO</option></select></div><div class="field"><label for="jobs-bot">Bot</label><input id="jobs-bot" type="text" placeholder="consulta_cuit"></div><div class="field"><label for="jobs-user">Usuario</label><input id="jobs-user" type="text" placeholder="UUID"></div><button class="button primary" type="submit">Actualizar</button></form>
        <div class="grid" id="jobs-metrics"><div class="empty">Cargando métricas...</div></div>
        <div class="table-wrap"><table><thead><tr><th>Job</th><th>Estado</th><th>Bot / operación</th><th>Usuario</th><th>Worker</th><th>Intento</th><th>Acciones</th></tr></thead><tbody id="jobs-table"><tr><td colspan="7" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="executions">
        <div class="kicker">Historial unificado</div><h2>Registros de ejecuciones</h2><p class="subtle">Consulta request, resultado, artefactos y eventos de todos los bots desde una sola vista.</p>
        <form id="executions-filter" class="toolbar"><div class="field"><label for="executions-bot">Bot</label><input id="executions-bot" type="text" placeholder="todos"></div><div class="field"><label for="executions-state">Estado</label><input id="executions-state" type="text" placeholder="COMPLETO"></div><button class="button primary" type="submit">Actualizar</button></form>
        <div class="grid" id="execution-table-counts"><div class="empty">Cargando tablas...</div></div>
        <div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Usuario</th><th>Job</th><th>Bot / operación</th><th>Estado</th><th>Credenciales</th><th>Resultado</th><th>Artefactos MinIO</th></tr></thead><tbody id="executions-table"><tr><td colspan="8" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="fleet">
        <div class="kicker">Operación de workers</div><h2>Flota</h2><p class="subtle">Estado derivado, capacidad, protocolo y alertas del plano de ejecución.</p>
        <div class="form-actions"><button class="button primary" type="button" id="fleet-refresh">Actualizar</button><button class="button secondary" type="button" id="fleet-evaluate">Evaluar alertas</button><a class="button secondary" href="/admin/workers/panel" target="_blank" rel="noreferrer">Vista clásica</a></div>
        <div class="table-wrap"><table><thead><tr><th>Nodo</th><th>Estado</th><th>Ejecución</th><th>Protocolo</th><th>Bots</th><th>Error</th><th>Alertas</th></tr></thead><tbody id="fleet-table"><tr><td colspan="7" class="empty">Cargando...</td></tr></tbody></table></div>
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
  const state = { token: sessionStorage.getItem(TOKEN_KEY) || "", view: "dashboard" };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
  const short = (value, size = 18) => { const text = String(value ?? ""); return text.length > size ? text.slice(0, size) + "…" : text; };
  const date = (value) => value ? new Date(value).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "medium" }) : "—";
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
    const response = await fetch(path, { ...options, headers });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { raw: text }; }
    if (response.status === 401 || response.status === 403) { logout("Sesión inválida o vencida. Ingresa nuevamente."); throw new Error("No autorizado"); }
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
  }
  function setView(view) { state.view = view; $$("[data-panel]").forEach((node) => node.classList.toggle("hidden", node.dataset.panel !== view)); $$("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === view)); const loaders = { dashboard: loadDashboard, users: loadUsers, jobs: loadJobs, executions: loadExecutions, fleet: loadFleet, audit: loadAudit }; loaders[view](); }
  function renderEmpty(target, colspan, message = "Sin datos") { $(target).innerHTML = `<tr><td colspan="${colspan}" class="empty">${esc(message)}</td></tr>`; }
  async function loadDashboard() {
    try {
      const [metrics, fleet, audit] = await Promise.all([api("/admin/jobs/metrics"), api("/admin/fleet"), api("/admin/audit?limit=8")]);
      const states = Object.entries(metrics.por_estado || {}).map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join("");
      $("#dashboard-metrics").innerHTML = states || `<div class="card"><div class="metric"><span>Jobs registrados</span><strong>0</strong></div></div>`;
      $("#dashboard-fleet").innerHTML = fleet.flota?.length ? fleet.flota.map((worker) => `<div style="margin:8px 0"><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span> ${esc(worker.node)} <span class="muted">${esc(worker.en_ejecucion)}/${esc(worker.capacidad)}</span></div>`).join("") : "No hay workers registrados.";
      $("#dashboard-audit").innerHTML = audit.eventos?.length ? audit.eventos.map((event) => `<div style="margin:8px 0"><strong>${esc(event.action)}</strong><br><span class="muted">${esc(date(event.occurred_at))} · ${esc(event.actor_id)}</span></div>`).join("") : "No hay eventos recientes.";
    } catch (error) { flash(error.message, "error"); }
  }
  async function loadUsers(event) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: "100" }); const email = $("#users-email").value.trim(); const estado = $("#users-state").value; if (email) params.set("email", email); if (estado) params.set("estado", estado);
    try { const data = await api(`/admin/users?${params}`); const rows = data.usuarios || []; $("#users-table").innerHTML = rows.length ? rows.map((user) => `<tr><td>${esc(user.email)}</td><td>${esc(user.display_name || "—")}</td><td><span class="pill">${esc(user.plan)}</span></td><td><span class="status ${statusClass(user.estado)}">${esc(user.estado)}</span></td><td>${esc(user.saldo_creditos)}</td><td class="actions"><button class="button secondary small" data-action="user-toggle" data-id="${esc(user.id)}" data-enabled="${user.estado === "habilitado"}">${user.estado === "habilitado" ? "Deshabilitar" : "Habilitar"}</button><button class="button secondary small" data-action="issue-key" data-id="${esc(user.id)}">Emitir clave</button></td></tr>`).join("") : `<tr><td colspan="6" class="empty">No hay usuarios para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); }
  }
  async function createUser(event) {
    event.preventDefault(); const payload = { email: $("#new-email").value.trim(), display_name: $("#new-display").value.trim(), plan: $("#new-plan").value, motivo: $("#new-reason").value.trim() };
    try { const data = await api("/admin/users", { method: "POST", body: JSON.stringify(payload) }); $("#create-user-form").reset(); flash(`Usuario creado: ${data.usuario?.email || payload.email}`); await loadUsers(); } catch (error) { flash(error.message, "error"); }
  }
  async function userAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return; const userId = button.dataset.id;
    if (button.dataset.action === "issue-key") { const motivo = window.prompt("Motivo para emitir la clave:", "emisión desde panel V3"); if (!motivo) return; try { const data = await api(`/admin/users/${encodeURIComponent(userId)}/api-keys`, { method: "POST", body: JSON.stringify({ scopes: [], motivo }) }); flash("La clave se muestra una sola vez. Cópiala ahora.", "ok"); window.alert(`Clave API de una sola vez:\n\n${data.valor_unica_vez}`); } catch (error) { flash(error.message, "error"); } return; }
    const enabled = button.dataset.enabled === "true"; const motivo = window.prompt(`Motivo para ${enabled ? "deshabilitar" : "habilitar"} el usuario:`, "cambio desde panel V3"); if (!motivo) return;
    try { await api(`/admin/users/${encodeURIComponent(userId)}/${enabled ? "disable" : "enable"}`, { method: "POST", body: JSON.stringify({ motivo }) }); flash("Estado de usuario actualizado."); await loadUsers(); } catch (error) { flash(error.message, "error"); }
  }
  async function loadJobs(event) {
    if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const values = [["estado", "#jobs-state"], ["bot", "#jobs-bot"], ["usuario", "#jobs-user"]]; values.forEach(([key, selector]) => { const value = $(selector).value.trim(); if (value) params.set(key, value); });
    try { const [data, metrics] = await Promise.all([api(`/admin/jobs?${params}`), api("/admin/jobs/metrics")]); const stateCards = Object.entries(metrics.por_estado || {}).map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join(""); $("#jobs-metrics").innerHTML = stateCards || `<div class="card"><div class="metric"><span>Total</span><strong>${esc(data.total || 0)}</strong></div></div>`; const rows = data.jobs || []; $("#jobs-table").innerHTML = rows.length ? rows.map((job) => `<tr><td><code>${esc(short(job.job_id, 22))}</code></td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado)}</span></td><td>${esc(job.bot)}<br><span class="muted">${esc(job.operacion)}</span></td><td>${esc(short(job.usuario, 18))}</td><td>${esc(job.worker || "—")}</td><td>${esc(job.intento)}</td><td class="actions"><button class="button secondary small" data-action="job-detail" data-id="${esc(job.job_id)}">Detalle</button>${["PENDIENTE","ASIGNADO","CORRIENDO"].includes(job.estado) ? `<button class="button danger small" data-action="job-cancel" data-id="${esc(job.job_id)}">Cancelar</button>` : ""}</td></tr>`).join("") : `<tr><td colspan="7" class="empty">No hay jobs para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); }
  }
  async function loadExecutions(event) {
    if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100", tabla: "all" }); const bot = $("#executions-bot").value.trim(); const estado = $("#executions-state").value.trim(); if (bot) params.set("bot", bot); if (estado) params.set("estado", estado);
    try { const data = await api(`/admin/records?${params}`); const tables = data.tables || {}; $("#execution-table-counts").innerHTML = Object.entries(tables).map(([name, values]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(values.length)}</strong></div></div>`).join(""); const rows = data.records || []; $("#executions-table").innerHTML = rows.length ? rows.map((record) => { const job = record.job || {}; const result = record.result || {}; const credentials = record.credentials || {}; const artifacts = record.artifacts || []; const artifactNames = artifacts.map((artifact) => artifact.name || artifact.filename).filter(Boolean); return `<tr><td>${esc(date(job.creado_en))}</td><td>${esc(job.usuario_email || job.usuario || job.user_id || "—")}</td><td><code>${esc(short(job.id, 22))}</code></td><td>${esc(job.bot)}<br><span class="muted">${esc(job.operacion)}</span></td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado)}</span></td><td>${credentials.available ? `<button class="button secondary small" data-action="credential-detail" data-id="${esc(job.id)}">Ver (${esc((credentials.fields || []).join(", ") || "clave")})</button>` : "—"}</td><td>${esc(result.result || job.resultado || "—")}</td><td>${artifactNames.length ? artifactNames.map((name) => `<span class="pill">${esc(name)}</span>`).join(" ") : "—"}</td></tr>`; }).join("") : `<tr><td colspan="8" class="empty">No hay ejecuciones para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); }
  }
  async function executionAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button || button.dataset.action !== "credential-detail") return;
    try { const data = await api(`/admin/jobs/${encodeURIComponent(button.dataset.id)}/credentials`); window.alert(`Credenciales de la ejecución ${button.dataset.id}:\n\n${json(data.credentials)}\n\nContexto:\n${json(data.credential_metadata || {})}`); } catch (error) { flash(error.message, "error"); }
  }
  async function jobAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return; const jobId = button.dataset.id;
    if (button.dataset.action === "job-detail") { try { const data = await api(`/admin/jobs/${encodeURIComponent(jobId)}`); window.alert(json(data)); } catch (error) { flash(error.message, "error"); } return; }
    const motivo = window.prompt("Motivo de cancelación:", "cancelación desde panel V3"); if (!motivo) return; try { await api(`/admin/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: JSON.stringify({ motivo }) }); flash("Comando de cancelación registrado."); await loadJobs(); } catch (error) { flash(error.message, "error"); }
  }
  async function loadFleet() { try { const data = await api("/admin/fleet"); const rows = data.flota || []; $("#fleet-table").innerHTML = rows.length ? rows.map((worker) => `<tr><td><code>${esc(worker.node)}</code></td><td><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span></td><td>${esc(worker.jobs_activos ?? worker.en_ejecucion ?? 0)}/${esc(worker.capacidad ?? 0)}</td><td>${esc(worker.protocolo || "—")} <span class="pill">${esc(worker.protocolo_estado || "—")}</span></td><td>${esc((worker.bots || []).join(", ") || "—")}</td><td>${esc(((worker.muestra_error || {}).fallidos || 0))}/${esc(((worker.muestra_error || {}).total || 0))}</td><td>${esc((worker.alertas || []).length)}</td></tr>`).join("") : `<tr><td colspan="7" class="empty">No hay workers registrados.</td></tr>`; } catch (error) { flash(error.message, "error"); } }
  async function evaluateFleet() { try { const data = await api("/admin/fleet/evaluate", { method: "POST" }); flash(`Evaluación completada: ${data.activas?.length || 0} alertas activas.`); await loadFleet(); } catch (error) { flash(error.message, "error"); } }
  async function loadAudit(event) { if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const action = $("#audit-action").value.trim(); const actor = $("#audit-actor").value.trim(); if (action) params.set("accion", action); if (actor) params.set("actor", actor); try { const data = await api(`/admin/audit?${params}`); const rows = data.eventos || []; $("#audit-table").innerHTML = rows.length ? rows.slice().reverse().map((item) => `<tr><td>${esc(date(item.occurred_at))}</td><td><strong>${esc(item.action)}</strong></td><td>${esc(item.actor_id)}</td><td>${esc(item.target_type)}<br><span class="muted">${esc(short(item.target_id, 22))}</span></td><td><span class="status ${item.result === "success" ? "status-good" : "status-bad"}">${esc(item.result)}</span></td><td>${esc(item.reason || "—")}</td></tr>`).join("") : `<tr><td colspan="6" class="empty">No hay eventos para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); } }
  $("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); state.token = $("#admin-token").value.trim(); if (!state.token) return; try { await api("/admin/users?limit=1"); sessionStorage.setItem(TOKEN_KEY, state.token); loginError(""); showLoggedIn(true); setView("dashboard"); } catch (_) { state.token = ""; loginError("No se pudo validar el token de administración."); } });
  $("#logout-button").addEventListener("click", () => logout());
  $("#main-nav").addEventListener("click", (event) => { const button = event.target.closest("button[data-view]"); if (button) setView(button.dataset.view); });
  $("#users-filter").addEventListener("submit", loadUsers); $("#create-user-form").addEventListener("submit", createUser); $("#users-table").addEventListener("click", userAction);
  $("#jobs-filter").addEventListener("submit", loadJobs); $("#jobs-table").addEventListener("click", jobAction); $("#executions-filter").addEventListener("submit", loadExecutions); $("#executions-table").addEventListener("click", executionAction);
  $("#fleet-refresh").addEventListener("click", loadFleet); $("#fleet-evaluate").addEventListener("click", evaluateFleet); $("#audit-filter").addEventListener("submit", loadAudit);
  if (state.token) { showLoggedIn(true); setView("dashboard"); } else { showLoggedIn(false); }
})();
</script>
</body>
</html>'''


@router.get("/", response_class=HTMLResponse)
def portada_admin() -> str:
    """Renderiza la consola administrativa navegable de la V3."""
    return ADMIN_PANEL_HTML


@router.get("/login", response_class=HTMLResponse)
def login_admin() -> str:
    """Mantiene una URL de login explícita, compatible con la navegación V2."""
    return ADMIN_PANEL_HTML
