"""Portada HTML mínima del panel /admin con enlaces a cada sección."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def portada_admin() -> str:
    """Índice del panel con navegación a usuarios, jobs, flota y auditoría."""
    return """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin — MrBot central</title>
<style>
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1a1a1a; max-width: 60rem; }
nav a { margin-right: 1rem; }
.muted { color: #555; font-size: 0.85rem; }
</style>
</head>
<body>
<h1>Panel de administración</h1>
<p class="muted">Consola operativa interna. Mutaciones con Bearer [REDACTED] excluidas de OpenAPI pública.</p>
<nav>
<a href="/admin/workers/panel">Workers</a>
<a href="/admin/fleet">Flota (JSON)</a>
<a href="/admin/jobs">Jobs (JSON)</a>
<a href="/admin/users">Usuarios (JSON)</a>
<a href="/admin/audit">Auditoría (JSON)</a>
</nav>
<p class="muted">Las vistas JSON exigen cabecera Authorization: Bearer [REDACTED]</p>
</body>
</html>"""
