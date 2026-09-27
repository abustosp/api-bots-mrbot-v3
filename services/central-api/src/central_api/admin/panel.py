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
table { width: 100%; min-width: 760px; border-collapse: collapse; }
th, td { padding: 12px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 13px; }
th { color: var(--muted); font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; }
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
      <button type="button" data-view="keys">Claves API</button>
      <button type="button" data-view="jobs">Jobs</button>
      <button type="button" data-view="executions">Tablas / registros</button>
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
        <div class="kicker">Identidades y acceso</div><h2>Usuarios</h2><p class="subtle">Alta, estado, plan y claves API de los clientes V3. Para depuración se admite un nombre sin mail (p. ej. abp). La tabla muestra prefijos; la clave completa solo se revela una vez al emitirla, porque no se guarda en texto plano.</p>
        <form id="users-filter" class="toolbar"><div class="field wide"><label for="users-email">Buscar por email o usuario</label><input id="users-email" name="email" type="search" placeholder="cliente@example.com o abp"></div><div class="field"><label for="users-state">Estado</label><select id="users-state"><option value="">Todos</option><option value="habilitado">Activo</option><option value="deshabilitado">Desactivado</option></select></div><button class="button primary" type="submit">Buscar</button></form>
        <details><summary>Crear usuario</summary><div class="details-body"><form id="create-user-form"><div class="form-grid"><div class="field"><label for="new-email">Email o usuario debug</label><input id="new-email" type="text" placeholder="cliente@example.com o abp" required></div><div class="field"><label for="new-display">Nombre</label><input id="new-display" type="text"></div><div class="field"><label for="new-plan">Plan</label><select id="new-plan"><option>free</option><option>basico</option><option>pro</option><option>empresa</option></select></div><div class="field"><label for="new-api-key">API key (mínimo 3; las claves cortas son débiles)</label><input id="new-api-key" type="password" minlength="3" placeholder="Vacío = generar automáticamente; mínimo 3 caracteres" autocomplete="new-password"></div><div class="field"><label for="new-state">Estado inicial</label><select id="new-state"><option value="habilitado">Activo</option><option value="deshabilitado">Desactivado</option></select></div><div class="field"><label for="new-reason">Motivo</label><input id="new-reason" type="text" value="alta desde panel V3" required></div></div><div class="form-actions"><label class="checkbox-inline"><input id="new-send-credentials" type="checkbox"> Enviar credenciales por email</label><button class="button primary" type="submit">Crear usuario</button></div></form></div></details>
        <div class="card hidden" id="api-key-reveal" role="status" aria-live="polite"><strong id="api-key-reveal-title">API key recién emitida</strong><p class="muted">El valor solo se conserva mientras sigas en Usuarios. Se borra al cambiar de vista o cerrar sesión. Las claves anteriores no se pueden recuperar: emite una nueva si necesitas volver a verla.</p><div class="toolbar"><div class="field wide"><label for="issued-api-key">API key</label><input id="issued-api-key" type="password" readonly autocomplete="off"></div><button class="button secondary" type="button" id="toggle-issued-api-key">Mostrar</button><button class="button secondary" type="button" id="copy-issued-api-key">Copiar</button><button class="button danger" type="button" id="clear-issued-api-key">Ocultar y borrar</button></div></div>
        <div class="table-wrap"><table><thead><tr><th>Email / usuario</th><th>Nombre</th><th>Plan</th><th>API key (prefijo)</th><th>Estado</th><th>Créditos</th><th>Acciones</th></tr></thead><tbody id="users-table"><tr><td colspan="7" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="keys">
        <div class="kicker">Credenciales de clientes</div><h2>Claves API</h2><p class="subtle">Solo metadatos: prefijo, scopes, expiración y estado. El valor se conserva en memoria temporal tras emitirlo y no se puede recuperar después de cerrar o borrar la vista.</p>
        <form id="keys-filter" class="toolbar"><div class="field wide"><label for="keys-q">Buscar</label><input id="keys-q" type="search" placeholder="prefijo, email o scope"></div><div class="field"><label for="keys-state">Estado</label><select id="keys-state"><option value="">Todas</option><option value="activa">Activa</option><option value="revocada">Revocada</option></select></div><button class="button primary" type="submit">Buscar</button></form>
        <div class="table-wrap"><table><thead><tr><th>Prefijo</th><th>Usuario</th><th>Scopes</th><th>Expira</th><th>Estado</th><th>Emitida</th><th>Acciones</th></tr></thead><tbody id="keys-table"><tr><td colspan="7" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="jobs">
        <div class="kicker">Cola y ejecución</div><h2>Jobs</h2><p class="subtle">Observa asignaciones, intentos y acciones durables sin exponer payloads sensibles.</p>
        <form id="jobs-filter" class="toolbar"><div class="field"><label for="jobs-state">Estado</label><select id="jobs-state"><option value="">Todos</option><option>PENDIENTE</option><option>ASIGNADO</option><option>CORRIENDO</option><option>COMPLETO</option><option>FALLIDO</option><option>CANCELADO</option></select></div><div class="field"><label for="jobs-bot">Módulo / bot</label><input id="jobs-bot" list="module-options" type="search" placeholder="Escribir para filtrar módulos" autocomplete="off"></div><div class="field"><label for="jobs-user">Usuario</label><input id="jobs-user" type="text" placeholder="UUID"></div><button class="button primary" type="submit">Actualizar</button></form>
        <div class="grid" id="jobs-metrics"><div class="empty">Cargando métricas...</div></div>
        <div class="table-wrap"><table><thead><tr><th>Job</th><th>Estado</th><th>Bot / operación</th><th>Usuario</th><th>Worker</th><th>Intento</th><th>Acciones</th></tr></thead><tbody id="jobs-table"><tr><td colspan="7" class="empty">Cargando...</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="executions">
        <div class="kicker">Historial consultable</div><h2>Explorador de tablas</h2><p class="subtle">Selecciona una tabla de PostgreSQL o la tabla física de un bot. Solo se carga la selección actual, como en V2, para evitar una página kilométrica.</p>
        <form id="executions-filter" class="toolbar"><div class="field wide"><label for="executions-table-search">Tabla</label><div class="table-combobox" id="executions-table-combobox"><input id="executions-table-search" type="text" role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false" aria-controls="executions-table-listbox" aria-describedby="executions-table-help" placeholder="Buscar tablas..." autocomplete="off" required><span id="executions-table-help" class="visually-hidden">Escribe para filtrar. Usa las flechas para recorrer, Enter para seleccionar y Escape para cerrar.</span><div id="executions-table-listbox" class="table-combobox-listbox" role="listbox" aria-label="Tablas disponibles" hidden></div><select id="executions-table-select" class="visually-hidden" tabindex="-1" aria-hidden="true"><option value="">Cargando catálogo...</option></select></div></div><div class="field wide"><label for="executions-query">Texto</label><input id="executions-query" type="search" placeholder="job, bot, operación o usuario"></div><div class="field wide"><label for="executions-job">Job ID</label><input id="executions-job" type="search" placeholder="UUID del job"></div><div class="field"><label for="executions-bot">Módulo / bot</label><input id="executions-bot" list="module-options" type="search" placeholder="Escribir para filtrar módulos" autocomplete="off"></div><div class="field"><label for="executions-operation">Operación</label><input id="executions-operation" type="text" placeholder="opcional"></div><div class="field"><label for="executions-state">Estado</label><input id="executions-state" type="text" placeholder="COMPLETO"></div><div class="field"><label for="executions-user">Usuario</label><input id="executions-user" type="search" placeholder="email o UUID"></div><div class="field"><label for="executions-limit">Filas</label><select id="executions-limit"><option>25</option><option>50</option><option selected>100</option></select></div><button class="button primary" type="submit">Consultar</button></form>
        <datalist id="module-options"></datalist><p class="muted" id="execution-table-status" aria-live="polite">Selecciona una tabla.</p>
        <div class="bot-sections" id="bot-sections"><div class="empty">Selecciona una tabla para consultar.</div></div>
        <div class="table-wrap"><table><thead id="executions-head"><tr><th>Tabla</th><th>Datos</th></tr></thead><tbody id="executions-table"><tr><td colspan="2" class="empty">Selecciona una tabla.</td></tr></tbody></table></div>
      </section>

      <section class="view hidden" data-panel="fleet">
        <div class="kicker">Operación de workers</div><h2>Flota</h2><p class="subtle">Estado derivado, capacidad, protocolo y alertas del plano de ejecución. Agrega nodos al inventario en caliente sin tocar WORKER_NODES.</p>
        <div class="form-actions"><button class="button primary" type="button" id="fleet-refresh">Actualizar</button><button class="button secondary" type="button" id="fleet-evaluate">Evaluar alertas</button><a class="button secondary" href="/admin/workers/panel" target="_blank" rel="noreferrer">Vista clásica</a></div>
        <form id="add-worker-form" class="toolbar"><div class="field wide"><label for="new-worker-node">Agregar worker (ip:puerto)</label><input id="new-worker-node" type="text" placeholder="10.0.0.13:8080 o bot-worker:8080" required></div><button class="button primary" type="submit">Agregar</button><span class="muted">Se sondea best-effort; igual se registra aunque aún no responda.</span></form>
        <div class="table-wrap"><table><thead><tr><th>Nodo</th><th>Origen</th><th>Estado</th><th>Ejecución</th><th>Protocolo</th><th>Bots</th><th>Error</th><th>Alertas</th><th>Acciones</th></tr></thead><tbody id="fleet-table"><tr><td colspan="9" class="empty">Cargando...</td></tr></tbody></table></div>
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
  const state = { token: sessionStorage.getItem(TOKEN_KEY) || "", view: initialView, tableCatalog: [], tableOptions: [], activeTableOption: -1, moduleOptions: [], moduleOptionsLoaded: false, revealedApiKey: "" };
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
  function clearRevealedApiKey() { state.revealedApiKey = ""; const input = $("#issued-api-key"); if (input) { input.value = ""; input.type = "password"; } const card = $("#api-key-reveal"); if (card) card.classList.add("hidden"); const toggle = $("#toggle-issued-api-key"); if (toggle) toggle.textContent = "Mostrar"; }
  function revealApiKey(value, title) { state.revealedApiKey = String(value || ""); const input = $("#issued-api-key"); input.value = state.revealedApiKey; input.type = "password"; $("#api-key-reveal-title").textContent = title || "API key recién emitida"; $("#toggle-issued-api-key").textContent = "Mostrar"; $("#api-key-reveal").classList.toggle("hidden", !state.revealedApiKey); if (state.revealedApiKey) $("#api-key-reveal").scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  function logout(message = "") { clearRevealedApiKey(); state.token = ""; sessionStorage.removeItem(TOKEN_KEY); showLoggedIn(false); $("#admin-token").value = ""; if (message) loginError(message); }
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
  function setView(view) { if (view !== "users") clearRevealedApiKey(); state.view = view; $$("[data-panel]").forEach((node) => node.classList.toggle("hidden", node.dataset.panel !== view)); $$("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === view)); const loaders = { dashboard: loadDashboard, users: loadUsers, keys: loadKeys, jobs: loadJobs, executions: loadExecutions, fleet: loadFleet, audit: loadAudit }; if (view === "jobs" || view === "executions") loadModuleOptions(); (loaders[view] || loadDashboard)(); }
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
      return `<tr><td><code>${esc(short(job.id, 22))}</code></td><td>${esc(date(job.creado_en))}</td><td>${esc(date(job.asignado_en))}</td><td>${esc(date(job.iniciado_en))}</td><td>${esc(job.operacion || "—")}</td><td>${esc(job.usuario_email || job.usuario || job.user_id || "—")}</td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado || "—")}</span></td>${requestCells}<td>${payloadView(response, "Ver response")}${responseReceived}</td><td>${artifactListView(record.artifacts)}</td><td>${credentials.available ? `<button class="button secondary small" data-action="credential-detail" data-id="${esc(job.id)}">Ver credenciales</button>` : "—"}</td><td>${esc(date(job.finalizado_en))}</td></tr>`;
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
      if (column.key === "job_id" || column.key === "id") return `<td><code>${esc(short(value, 22))}</code></td>`;
      if (column.key === "status" || column.key === "estado") return `<td><span class="status ${statusClass(value)}">${esc(value || "—")}</span></td>`;
      return `<td>${esc(value ?? "—")}</td>`;
    }).join("") + `<td>${record.job_id ? `<button class="button secondary small" data-action="credential-detail" data-id="${esc(record.job_id)}">Ver credenciales</button>` : "—"}</td>`;
    const rows = records.length
      ? records.map((record) => `<tr>${cells(record)}</tr>`).join("")
      : `<tr><td colspan="${Math.max(columns.length + 1, 1)}" class="empty">Sin registros para este bot y filtro.</td></tr>`;
    const operations = (section.operaciones || []).map((operation) => `<span class="pill">${esc(operation)}</span>`).join(" ");
    node.innerHTML = `<details class="bot-section" open><summary><span><strong>${esc(section.bot)}</strong> <span class="bot-section-meta">${operations}</span></span><span class="bot-section-meta"><span class="pill">${esc(section.total || 0)} filas</span><span class="pill">PostgreSQL</span></span></summary><div class="bot-section-body"><div class="table-wrap"><table class="bot-table"><thead><tr>${columns.map((column) => `<th>${esc(column.label)}</th>`).join("")}<th>Credenciales</th></tr></thead><tbody>${rows}</tbody></table></div></div></details>`;
  }
  function renderCanonicalTable(tableName, rows) {
    const head = $("#executions-head");
    const body = $("#executions-table");
    head.closest(".table-wrap").classList.remove("hidden");
    const keys = (state.tableCatalog.find((item) => item.name === tableName) || {}).columns || [];
    const requestColumn = keys.find((key) => ["request_payload", "request"].includes(key));
    const columns = requestColumn ? requestColumns(rows, requestColumn, keys) : keys.map((key) => ({ key, label: key, kind: "field" }));
    head.innerHTML = `<tr>${columns.map((column) => `<th>${esc(column.label)}</th>`).join("")}</tr>`;
    body.innerHTML = rows.length ? rows.map((row) => `<tr>${columns.map((column) => {
      const key = column.key;
      const value = columnValue(row, column);
      if (key === "credentials" && value && value.available) return `<td><button class="button secondary small" data-action="credential-detail" data-id="${esc(row.id || row.job_id || "")}">Ver credenciales</button></td>`;
      if (key === "credentials") return `<td><span class="muted">—</span></td>`;
      if (["estado", "resultado", "result", "status"].includes(key) && typeof value === "string") return `<td><span class="status ${statusClass(value)}">${esc(value)}</span></td>`;
      return `<td>${cellValue(value)}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${Math.max(keys.length, 1)}" class="empty">La tabla no contiene registros para este filtro.</td></tr>`;
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
      $("#dashboard-fleet").innerHTML = fleet.flota?.length ? fleet.flota.map((worker) => `<div style="margin:8px 0"><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span> ${esc(worker.node)} <span class="muted">${esc(worker.en_ejecucion)}/${esc(worker.capacidad)}</span></div>`).join("") : "No hay workers registrados.";
      $("#dashboard-audit").innerHTML = audit.eventos?.length ? audit.eventos.map((event) => `<div style="margin:8px 0"><strong>${esc(event.action)}</strong><br><span class="muted">${esc(date(event.occurred_at))} · ${esc(event.actor_id)}</span></div>`).join("") : "No hay eventos recientes.";
    } catch (error) { flash(error.message, "error"); }
  }
  function userKeyPreview(keys) {
    const items = Array.isArray(keys) ? keys : [];
    return items.length
      ? items.map((key) => {
        const label = key.estado === "activa" ? "Activa" : key.estado === "expirada" ? "Expirada" : "Revocada";
        const copiar = key.estado === "activa" && key.revelable && key.owner_enabled
          ? `<button type="button" class="button secondary small" data-action="copy-user-key" data-id="${esc(key.user_id || "")}" data-key="${esc(key.id)}">Copiar API key</button>`
          : key.estado === "activa" ? `<span class="muted">Emita una nueva para habilitar la copia</span>` : "";
        return `<div class="user-key-item"><code>${esc(key.prefijo || "—")}</code> <span class="status ${key.estado === "activa" ? "status-good" : "status-bad"}">${label}</span>${copiar}</div>`;
      }).join("")
      : `<span class="muted">Sin clave</span>`;
  }
  async function loadUsers(event) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: "100" }); const email = $("#users-email").value.trim(); const estado = $("#users-state").value; if (email) params.set("email", email); if (estado) params.set("estado", estado);
    try {
      const data = await api(`/admin/users?${params}`);
      const rows = data.usuarios || [];
      $("#users-table").innerHTML = rows.length ? rows.map((user) => {
        const enabled = user.estado === "habilitado";
        const userKeys = (user.claves_api || []).map((key) => ({ ...key, user_id: user.id, owner_enabled: enabled }));
        return `<tr><td>${esc(user.email)}</td><td>${esc(user.display_name || "—")}</td><td><span class="pill">${esc(user.plan)}</span></td><td>${userKeyPreview(userKeys)}</td><td><span class="status ${statusClass(user.estado)}">${enabled ? "Activo" : "Desactivado"}</span></td><td>${esc(user.saldo_creditos ?? "—")}</td><td class="actions"><button type="button" class="button secondary small" data-action="user-toggle" data-id="${esc(user.id)}" data-enabled="${enabled}">${enabled ? "Desactivar" : "Activar"}</button><button type="button" class="button secondary small" data-action="issue-key" data-id="${esc(user.id)}">Emitir y revelar clave</button></td></tr>`;
      }).join("") : `<tr><td colspan="7" class="empty">No hay usuarios para ese filtro.</td></tr>`;
    } catch (error) { flash(error.message, "error"); }
  }
  async function createUser(event) {
    event.preventDefault();
    const payload = {
      email: $("#new-email").value.trim(),
      display_name: $("#new-display").value.trim(),
      plan: $("#new-plan").value,
      api_key: $("#new-api-key").value.trim(),
      estado: $("#new-state").value,
      enviar_credenciales: $("#new-send-credentials").checked,
      motivo: $("#new-reason").value.trim(),
    };
    try {
      const data = await api("/admin/users", { method: "POST", body: JSON.stringify(payload) });
      const credenciales = data.credenciales || {};
      $("#create-user-form").reset();
      flash(`Usuario creado: ${data.usuario?.email || payload.email}`);
      if (credenciales.valor_unica_vez) {
        revealApiKey(credenciales.valor_unica_vez, "API key del usuario recién creado");
      } else if (credenciales.solicitado && credenciales.enviadas) {
        flash(`Usuario creado y credenciales enviadas a ${credenciales.destino}.`, "ok");
      } else if (credenciales.solicitado) {
        flash(`Usuario creado, pero no se enviaron credenciales: ${credenciales.motivo}.`, "error");
      }
      await loadUsers();
    } catch (error) { flash(error.message, "error"); }
  }
  async function copyApiKeyToClipboard(userId, keyId) {
    const data = await api(`/admin/users/${encodeURIComponent(userId)}/api-keys/${encodeURIComponent(keyId)}/reveal`, { method: "POST" });
    const value = String(data.api_key || "");
    if (!value) throw new Error("La API no devolvió una clave recuperable.");
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      const temporary = document.createElement("textarea");
      temporary.value = value; temporary.setAttribute("readonly", "");
      temporary.style.position = "fixed"; temporary.style.opacity = "0";
      document.body.appendChild(temporary);
      let copied = false;
      try { temporary.select(); copied = document.execCommand("copy"); }
      finally { temporary.remove(); }
      if (!copied) throw new Error("No se pudo acceder al portapapeles.");
    }
    flash("API key copiada al portapapeles.");
  }
  async function userAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return; const userId = button.dataset.id;
    if (button.dataset.action === "copy-user-key") {
      try {
        await copyApiKeyToClipboard(userId, button.dataset.key);
      } catch (error) { flash(error.message, "error"); }
      return;
    }
    if (button.dataset.action === "issue-key") { const motivo = window.prompt("Motivo para emitir la clave:", "emisión desde panel V3"); if (!motivo) return; const valor_fijo = window.prompt("Valor fijo opcional (mínimo 3 caracteres y sin espacios; vacío = aleatoria):", ""); try { const data = await api(`/admin/users/${encodeURIComponent(userId)}/api-keys`, { method: "POST", body: JSON.stringify({ scopes: [], motivo, valor_fijo: (valor_fijo || "").trim() }) }); revealApiKey(data.valor_unica_vez, "API key recién emitida"); flash("Clave emitida. Puedes mostrarla y copiarla mientras esta pestaña permanezca abierta.", "ok"); await loadUsers(); } catch (error) { flash(error.message, "error"); } return; }
    const enabled = button.dataset.enabled === "true"; const motivo = window.prompt(`Motivo para ${enabled ? "desactivar" : "activar"} el usuario:`, "cambio desde panel V3"); if (!motivo) return;
    try { await api(`/admin/users/${encodeURIComponent(userId)}/${enabled ? "disable" : "enable"}`, { method: "POST", body: JSON.stringify({ motivo }) }); flash("Estado de usuario actualizado."); await loadUsers(); } catch (error) { flash(error.message, "error"); }
  }
  async function loadKeys(event) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: "100" }); const q = $("#keys-q").value.trim(); const estado = $("#keys-state").value; if (q) params.set("q", q); if (estado) params.set("estado", estado);
    try {
      const data = await api(`/admin/api-keys?${params}`);
      const rows = data.claves || [];
      $("#keys-table").innerHTML = rows.length ? rows.map((key) => {
        const copia = key.estado === "activa" && key.revelable && key.owner_enabled
          ? `<button class="button secondary small" data-action="key-copy" data-id="${esc(key.id)}" data-user="${esc(key.user_id)}">Copiar API key</button>`
          : key.estado === "activa" && !key.revelable
            ? `<span class="muted">Reemite para habilitar copia</span>`
            : "";
        return `<tr><td><code>${esc(key.prefijo)}</code></td><td>${esc(key.usuario_email || "—")}</td><td>${esc((key.scopes || []).join(", ") || "—")}</td><td>${esc(key.expira_en || "—")}</td><td><span class="status ${key.estado === "activa" ? "status-good" : "status-bad"}">${esc(key.estado)}</span></td><td>${esc(date(key.emitida_en))}</td><td class="actions">${copia}${key.estado === "activa" ? `<button class="button primary small" data-action="key-replace" data-id="${esc(key.id)}" data-user="${esc(key.user_id)}" data-scopes="${esc(JSON.stringify(key.scopes || []))}">Reemplazar</button>` : ""}<button class="button secondary small" data-action="key-edit" data-id="${esc(key.id)}">Editar</button>${key.estado === "activa" ? `<button class="button danger small" data-action="key-revoke" data-id="${esc(key.id)}">Revocar</button>` : `<button class="button secondary small" data-action="key-restore" data-id="${esc(key.id)}">Restaurar</button>`}</td></tr>`;
      }).join("") : `<tr><td colspan="7" class="empty">No hay claves para ese filtro.</td></tr>`;
    } catch (error) { flash(error.message, "error"); }
  }
  async function keyAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button) return; const keyId = button.dataset.id;
    if (button.dataset.action === "key-copy") {
      try { await copyApiKeyToClipboard(button.dataset.user, keyId); }
      catch (error) { flash(error.message, "error"); }
      return;
    }
    if (button.dataset.action === "key-replace") {
      let newSecret = window.prompt("Ingresa una nueva API key (mínimo 3 caracteres). Deja vacío para generar una aleatoria alfanumérica:", "");
      if (newSecret === null) return;
      const replacementSecret = newSecret.trim();
      if (replacementSecret && (replacementSecret.length < 3 || /\s/.test(replacementSecret))) { flash("La API key debe tener al menos 3 caracteres y no contener espacios.", "error"); return; }
      if (!window.confirm("Se emitirá una nueva clave y se revocará la actual. Confirma que tienes autorización y guardaste el valor en un lugar seguro.")) return;
      const motivo = window.prompt("Motivo para reemplazar la clave (10-500 caracteres):", "rotación desde panel V3"); if (!motivo) return;
      let issued = null;
      try {
        issued = await api(`/admin/users/${encodeURIComponent(button.dataset.user)}/api-keys/rotate`, { method: "POST", body: JSON.stringify({ key_id: keyId, periodo_gracia_horas: 0, motivo, valor_fijo: replacementSecret }) });
        newSecret = "";
        revealApiKey(issued.valor_unica_vez, "Nueva API key (se muestra una sola vez)");
        flash("Clave reemplazada. Copia el secreto ahora: no es posible recuperarlo después.", "ok");
        await loadKeys();
      } catch (error) {
        flash(`No se pudo completar la rotación: ${error.message}. La clave anterior continúa activa si la emisión fue rechazada.`, "error");
      }
      return;
    }
    const motivo = window.prompt("Motivo del cambio (10-500 caracteres):", "cambio desde panel V3"); if (!motivo) return;
    try {
      if (button.dataset.action === "key-edit") {
        const scopesRaw = window.prompt("Scopes separados por coma (vacío = sin cambios de scopes):", "");
        if (scopesRaw === null) return;
        const expira = window.prompt("Expiración ISO (vacío = sin cambios):", "");
        if (expira === null) return;
        const body = { motivo };
        if (scopesRaw.trim()) body.scopes = scopesRaw.split(",").map((s) => s.trim()).filter(Boolean);
        if (expira.trim()) body.expira_en = expira.trim();
        await api(`/admin/api-keys/${encodeURIComponent(keyId)}`, { method: "PATCH", body: JSON.stringify(body) });
        flash("Clave actualizada.");
      } else if (button.dataset.action === "key-revoke") {
        await api(`/admin/api-keys/${encodeURIComponent(keyId)}/revoke`, { method: "POST", body: JSON.stringify({ motivo }) });
        flash("Clave revocada.");
      } else if (button.dataset.action === "key-restore") {
        await api(`/admin/api-keys/${encodeURIComponent(keyId)}/restore`, { method: "POST", body: JSON.stringify({ motivo }) });
        flash("Clave restaurada.");
      } else return;
      await loadKeys();
    } catch (error) { flash(error.message, "error"); }
  }
  async function loadJobs(event) {
    if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const values = [["estado", "#jobs-state"], ["bot", "#jobs-bot"], ["usuario", "#jobs-user"]]; values.forEach(([key, selector]) => { const value = $(selector).value.trim(); if (value) params.set(key, value); });
    try { const [data, metrics] = await Promise.all([api(`/admin/jobs?${params}`), api("/admin/jobs/metrics")]); const stateCards = Object.entries(metrics.por_estado || {}).map(([name, count]) => `<div class="card"><div class="metric"><span>${esc(name)}</span><strong>${esc(count)}</strong></div></div>`).join(""); $("#jobs-metrics").innerHTML = stateCards || `<div class="card"><div class="metric"><span>Total</span><strong>${esc(data.total || 0)}</strong></div></div>`; const rows = data.jobs || []; $("#jobs-table").innerHTML = rows.length ? rows.map((job) => `<tr><td><code>${esc(short(job.job_id, 22))}</code></td><td><span class="status ${statusClass(job.estado)}">${esc(job.estado)}</span></td><td>${esc(job.bot)}<br><span class="muted">${esc(job.operacion)}</span></td><td>${esc(short(job.usuario, 18))}</td><td>${esc(job.worker || "—")}</td><td>${esc(job.intento)}</td><td class="actions"><button class="button secondary small" data-action="job-detail" data-id="${esc(job.job_id)}">Detalle</button>${["PENDIENTE","ASIGNADO"].includes(job.estado) ? `<button class="button primary small" data-action="job-force" data-id="${esc(job.job_id)}">Forzar</button>` : ""}${["PENDIENTE","ASIGNADO","CORRIENDO"].includes(job.estado) ? `<button class="button danger small" data-action="job-cancel" data-id="${esc(job.job_id)}">Cancelar</button>` : ""}</td></tr>`).join("") : `<tr><td colspan="7" class="empty">No hay jobs para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); }
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
  async function loadFleet() { try { const data = await api("/admin/fleet"); const rows = data.flota || []; $("#fleet-table").innerHTML = rows.length ? rows.map((worker) => `<tr><td><code>${esc(worker.node)}</code></td><td><span class="pill">${esc(worker.origen || "—")}</span></td><td><span class="status ${statusClass(worker.estado)}">${esc(worker.estado)}</span></td><td>${esc(worker.jobs_activos ?? worker.en_ejecucion ?? 0)}/${esc(worker.capacidad ?? 0)}</td><td>${esc(worker.protocolo || "—")} <span class="pill">${esc(worker.protocolo_estado || "—")}</span></td><td>${esc((worker.bots || []).join(", ") || "—")}</td><td>${esc(((worker.muestra_error || {}).fallidos || 0))}/${esc(((worker.muestra_error || {}).total || 0))}</td><td>${esc((worker.alertas || []).length)}</td><td class="actions"><button class="button danger small" data-action="worker-remove" data-id="${esc(worker.node)}">Dar de baja</button></td></tr>`).join("") : `<tr><td colspan="9" class="empty">No hay workers inventariados. Agrega uno arriba.</td></tr>`; } catch (error) { flash(error.message, "error"); } }
  async function evaluateFleet() { try { const data = await api("/admin/fleet/evaluate", { method: "POST" }); flash(`Evaluación completada: ${data.activas?.length || 0} alertas activas.`); await loadFleet(); } catch (error) { flash(error.message, "error"); } }
  async function addWorker(event) {
    event.preventDefault(); const node = $("#new-worker-node").value.trim(); if (!node) return;
    try { const data = await api("/admin/workers", { method: "POST", body: JSON.stringify({ node }) }); $("#new-worker-node").value = ""; flash(data.alcanzable ? `Worker ${data.node} agregado y alcanzable.` : `Worker ${data.node} agregado (aún no responde: ${data.detalle || "sin sonda"}).`); await loadFleet(); } catch (error) { flash(error.message, "error"); }
  }
  async function fleetAction(event) {
    const button = event.target.closest("button[data-action]"); if (!button || button.dataset.action !== "worker-remove") return;
    if (!window.confirm(`Dar de baja ${button.dataset.id}? El scheduler dejará de asignarle jobs.`)) return;
    try { await api(`/admin/workers/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" }); flash("Worker dado de baja."); await loadFleet(); } catch (error) { flash(error.message, "error"); }
  }
  async function loadAudit(event) { if (event) event.preventDefault(); const params = new URLSearchParams({ limit: "100" }); const action = $("#audit-action").value.trim(); const actor = $("#audit-actor").value.trim(); if (action) params.set("accion", action); if (actor) params.set("actor", actor); try { const data = await api(`/admin/audit?${params}`); const rows = data.eventos || []; $("#audit-table").innerHTML = rows.length ? rows.slice().reverse().map((item) => `<tr><td>${esc(date(item.occurred_at))}</td><td><strong>${esc(item.action)}</strong></td><td>${esc(item.actor_id)}</td><td>${esc(item.target_type)}<br><span class="muted">${esc(short(item.target_id, 22))}</span></td><td><span class="status ${item.result === "success" ? "status-good" : "status-bad"}">${esc(item.result)}</span></td><td>${esc(item.reason || "—")}</td></tr>`).join("") : `<tr><td colspan="6" class="empty">No hay eventos para ese filtro.</td></tr>`; } catch (error) { flash(error.message, "error"); } }
  $("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); state.token = $("#admin-token").value.trim(); if (!state.token) return; try { await api("/admin/users?limit=1"); sessionStorage.setItem(TOKEN_KEY, state.token); loginError(""); showLoggedIn(true); setView(state.view); } catch (_) { state.token = ""; loginError("No se pudo validar el token de administración."); } });
  $("#logout-button").addEventListener("click", () => logout());
  $("#main-nav").addEventListener("click", (event) => { const button = event.target.closest("button[data-view]"); if (button) setView(button.dataset.view); });
  $("#users-filter").addEventListener("submit", loadUsers); $("#create-user-form").addEventListener("submit", createUser); $("#users-table").addEventListener("click", userAction); $("#keys-filter").addEventListener("submit", loadKeys); $("#keys-table").addEventListener("click", keyAction);
  $("#toggle-issued-api-key").addEventListener("click", () => { const input = $("#issued-api-key"); const visible = input.type === "password"; input.type = visible ? "text" : "password"; $("#toggle-issued-api-key").textContent = visible ? "Ocultar" : "Mostrar"; });
  $("#copy-issued-api-key").addEventListener("click", async () => { if (!state.revealedApiKey) return; try { await navigator.clipboard.writeText(state.revealedApiKey); flash("API key copiada al portapapeles."); } catch (_) { $("#issued-api-key").type = "text"; $("#issued-api-key").select(); flash("No se pudo acceder al portapapeles. Selecciona y copia la clave manualmente.", "error"); } });
  $("#clear-issued-api-key").addEventListener("click", () => { clearRevealedApiKey(); flash("API key ocultada y borrada de la pestaña."); });
  $("#jobs-filter").addEventListener("submit", loadJobs); $("#jobs-table").addEventListener("click", jobAction); $("#executions-filter").addEventListener("submit", loadExecutions); $("#executions-table-select").addEventListener("change", () => loadExecutions());
  $("#executions-table-search").addEventListener("focus", () => { $("#executions-table-search").select(); openTableOptions(); });
  $("#executions-table-search").addEventListener("input", (event) => { $("#executions-table-select").value = ""; openTableOptions(event.target.value); });
  $("#executions-table-search").addEventListener("keydown", handleTableComboboxKeydown);
  $("#executions-table-listbox").addEventListener("pointerdown", (event) => { if (event.target.closest("[role=option]")) event.preventDefault(); });
  $("#executions-table-listbox").addEventListener("click", (event) => { const option = event.target.closest("[role=option]"); if (option) chooseTableOption(option.dataset.value); });
  document.addEventListener("click", (event) => { if (!$("#executions-table-combobox").contains(event.target)) closeTableOptions(true); });
  $("#executions-table-search").addEventListener("blur", () => window.setTimeout(() => closeTableOptions(true), 0));
  $("#executions-limit").addEventListener("change", () => loadExecutions()); $("#executions-table").addEventListener("click", executionAction); $("#bot-sections").addEventListener("click", executionAction);
  $("#fleet-refresh").addEventListener("click", loadFleet); $("#fleet-evaluate").addEventListener("click", evaluateFleet); $("#add-worker-form").addEventListener("submit", addWorker); $("#fleet-table").addEventListener("click", fleetAction); $("#audit-filter").addEventListener("submit", loadAudit);
  if (state.token) { showLoggedIn(true); setView(state.view); } else { showLoggedIn(false); }
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
