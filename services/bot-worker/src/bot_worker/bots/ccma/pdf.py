"""HTML del PDF consolidado de CCMA (port literal de V1 ``_build_ccma_pdf_html``).

Recibe el resumen y los movimientos ya extraidos y devuelve el documento que el
plugin imprime con ``page.pdf``. La fecha de generacion se inyecta para que la
salida sea reproducible en pruebas. El marcado y los estilos son los de V1.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Mapping, Sequence

ETIQUETAS_RESUMEN = (
    ("CUIT", "cuit"),
    ("Periodo", "periodo"),
    ("Deuda capital", "deuda_capital"),
    ("Deuda accesorios", "deuda_accesorios"),
    ("Total deuda", "total_deuda"),
    ("Credito capital", "credito_capital"),
    ("Credito accesorios", "credito_accesorios"),
    ("Total a favor", "total_a_favor"),
)

COLUMNAS_MOVIMIENTOS = (
    ("periodo", False),
    ("impuesto", False),
    ("concepto", False),
    ("subconcepto", False),
    ("descripcion", False),
    ("fecha_movimiento", False),
    ("debe", True),
    ("haber", True),
)

_ESTILOS = """
      @page {
        size: A4 landscape;
        margin: 10mm 8mm;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        font-family: Arial, sans-serif;
        color: #1f2937;
        font-size: 10px;
        background: #ffffff;
      }
      .report {
        width: 100%;
      }
      .header {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 16px;
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 2px solid #0f172a;
      }
      .title {
        font-size: 18px;
        font-weight: 700;
        margin: 0;
      }
      .subtitle {
        margin: 4px 0 0 0;
        color: #475569;
        font-size: 10px;
      }
      .meta {
        text-align: right;
        color: #475569;
        font-size: 9px;
      }
      .section-title {
        margin: 14px 0 8px 0;
        font-size: 12px;
        font-weight: 700;
        color: #0f172a;
      }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
      }
      .summary-card {
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        padding: 8px;
        background: #f8fafc;
      }
      .summary-label {
        color: #475569;
        font-size: 9px;
        text-transform: uppercase;
        margin-bottom: 4px;
      }
      .summary-value {
        color: #0f172a;
        font-size: 12px;
        font-weight: 700;
        word-break: break-word;
      }
      .table-meta {
        margin-bottom: 6px;
        color: #475569;
        font-size: 9px;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
      }
      thead {
        display: table-header-group;
      }
      tbody tr {
        page-break-inside: avoid;
      }
      th, td {
        border: 1px solid #cbd5e1;
        padding: 4px 5px;
        vertical-align: top;
        word-break: break-word;
      }
      th {
        background: #e2e8f0;
        color: #0f172a;
        font-size: 9px;
        text-transform: uppercase;
      }
      td {
        font-size: 9px;
      }
      .numeric {
        text-align: right;
        white-space: nowrap;
      }
      .empty-state {
        padding: 12px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #f8fafc;
        color: #475569;
      }
"""


def _texto(valor: Any) -> str:
    return escape(str(valor or "") or "-")


def _tarjetas_resumen(resumen: Mapping[str, Any]) -> str:
    return "".join(
        "<div class='summary-card'>"
        f"<div class='summary-label'>{escape(etiqueta)}</div>"
        f"<div class='summary-value'>{_texto(resumen.get(clave))}</div>"
        "</div>"
        for etiqueta, clave in ETIQUETAS_RESUMEN
    )


def _tabla_movimientos(movimientos: Sequence[Mapping[str, Any]]) -> str:
    if not movimientos:
        return "<div class='empty-state'>No se encontraron movimientos para consolidar.</div>"
    encabezados = "".join(
        f"<th>{escape(nombre)}</th>"
        for nombre in (
            "Periodo", "Impuesto", "Concepto", "Subconcepto",
            "Descripcion", "Fecha movimiento", "Debe", "Haber",
        )
    )
    filas = "".join(
        "<tr>"
        + "".join(
            f"<td class='numeric'>{_texto(m.get(clave))}</td>"
            if numerica
            else f"<td>{_texto(m.get(clave))}</td>"
            for clave, numerica in COLUMNAS_MOVIMIENTOS
        )
        + "</tr>"
        for m in movimientos
    )
    return (
        '\n        <table class="movimientos-table">\n'
        f"          <thead>\n            <tr>{encabezados}</tr>\n          </thead>\n"
        f"          <tbody>\n            {filas}\n          </tbody>\n"
        "        </table>\n        "
    )


def build_ccma_pdf_html(
    summary_data: Mapping[str, Any],
    movimientos_data: Sequence[Mapping[str, Any]],
    *,
    generado: datetime,
) -> str:
    """Documento HTML del resumen y los movimientos, listo para imprimir a PDF."""
    generated_at = generado.strftime("%d/%m/%Y %H:%M:%S")
    movimientos_count = len(movimientos_data)
    cuit = escape(str(summary_data.get("cuit", "") or ""))
    return f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <title>CCMA {cuit}</title>
    <style>{_ESTILOS}</style>
  </head>
  <body>
    <div class="report">
      <div class="header">
        <div>
          <h1 class="title">CCMA consolidado</h1>
          <p class="subtitle">Cuenta Corriente de Monotributistas y Autonomos</p>
        </div>
        <div class="meta">
          <div>Generado: {escape(generated_at)}</div>
          <div>Movimientos consolidados: {movimientos_count}</div>
        </div>
      </div>

      <div class="section-title">Resumen</div>
      <div class="summary-grid">
        {_tarjetas_resumen(summary_data)}
      </div>

      <div class="section-title">Movimientos</div>
      <div class="table-meta">Se incluyen todos los movimientos recolectados de la paginacion disponible.</div>
      {_tabla_movimientos(movimientos_data)}
    </div>
  </body>
</html>
"""
