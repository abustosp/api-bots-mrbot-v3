"""Bucket de S4 de cada módulo (bot) para sus artefactos.

Cada módulo guarda y lee sus archivos en su propio bucket. El bucket se deduce
del módulo (``jobs.bot``) y no se guarda en la base, de modo que no hace falta
cambiar el esquema. Un módulo sin entrada usa el bucket general
(``OBJECT_STORAGE_BUCKET``): conserva el comportamiento anterior.

``apoc`` no guarda archivos. Las entradas marcadas como "inferida" no las
indicó el dueño del sistema: salen del nombre del módulo y conviene confirmarlas.
"""

from __future__ import annotations

#: bot -> bucket de S4 (todos existen hoy en el almacenamiento).
BUCKET_POR_BOT: dict[str, str] = {
    "aportes_en_linea": "aportesenlinea",
    "arba": "retper-iibb-arba",
    "carga_portal_iva": "portaliva",
    "ccma": "ccma",
    "ccma_compensacion": "ccma",
    "certificacion_primaria_granos": "liquidaciongranos",
    "certificado_mipyme": "certificadomipyme",
    "compensaciones": "sct",
    "controladores_fiscales": "controladoresfiscales",
    "declaracion_en_linea": "declaracionenlinea",
    "facturometro": "facturometro",
    "hacienda": "hacienda",
    "libros_portal_iva": "librosiva",
    "liquidacion_granos": "liquidaciongranos",
    "mis_comprobantes": "miscomprobantes",
    "mis_facilidades": "misfacilidades",
    "mis_retenciones": "misretenciones",
    "moa": "moa",
    "pago_devoluciones": "pagodevoluciones",
    "portal_iva": "portaliva",
    "rcel": "rcel",
    "retper_iibb_agip": "retper-iibb-agip",
    "retper_iibb_misiones": "retper-iibb-misiones",
    "sct": "sct",
    "sifere": "sifere",
    "siper": "siper",
    "srt": "srt",
    "vep_archivo": "vep",
    # --- inferidas por nombre (confirmar) ---
    "comprobantes": "miscomprobantes",  # alias deprecado de mis_comprobantes
    "libro_sueldos_digital": "lsd",
    "mis_retenciones_iva_simple": "misretenciones",
    "vep_ccma": "vep",
    "consulta_pagos_vep": "vep",
}

#: Módulos sin bucket definido: siguen en el bucket general hasta que se decida.
SIN_BUCKET = ("apoc", "consulta_cuit")


def bucket_de_bot(bot: str | None, general: str) -> str:
    """Bucket del módulo ``bot``; ``general`` si no tiene uno definido."""
    return BUCKET_POR_BOT.get(str(bot or ""), general)


__all__ = ["BUCKET_POR_BOT", "SIN_BUCKET", "bucket_de_bot"]
