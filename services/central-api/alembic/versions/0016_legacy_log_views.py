"""0016: vistas ``consulta_*_logs`` compatibles con V1/V2 sobre ``bot_jobs_*``.

Repone en PostgreSQL las 28 tablas de log que la V2 exponía en
``/admin/tables`` (una por modelo ``logs_*``), como vistas de solo lectura
sobre las tablas físicas por bot de la revisión 0015. Cada vista proyecta
las columnas de negocio de su modelo V2 (params desde ``request_payload``,
agregados desde ``response_payload``) con los mismos nombres, para que el
explorador del panel vuelva a seleccionar la tabla de log de cada bot.

Diferencias conscientes con V2 (documentadas, no errores):
- Sin columnas de secreto: ``clave``, ``clave_representante`` y
  ``clave_encriptada`` no existen en las vistas. La credencial vive solo
  como ciphertext en ``jobs`` y se revela por
  ``GET /admin/jobs/{job_id}/credentials`` con auditoría.
- Sin columnas de infraestructura: ``request_proxy``,
  ``request_carga_minio``, ``request_excel/csv/pdf`` ni rutas de
  almacenamiento (``archivo_path``, ``response_ruta_archivo``,
  ``object_key``). ``archivos`` expone solo ``[{"name"}]``.
- ``id`` es un número de fila de depuración (``row_number`` sobre
  ``created_at``), no una PK estable; la identidad estable es ``job_id``.
- ``user_id`` es UUID V3 (no el entero V2) y ``status`` conserva el ciclo
  de vida V3 (``COMPLETO``/``FALLIDO``/...) en vez de ``success``/``error``.
- Columnas extra de depuración al final: ``bot``, ``operation``,
  ``resultado``, ``request_payload``, ``response_payload``, ``created_at``.

El mapa es una copia congelada: no se importa código de la aplicación
desde Alembic (criterio de la revisión 0015).
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# (vista, bot V3, columnas V2 en orden del modelo, sin secretos ni infra).
LEGACY_VIEWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("consulta_aportes_en_linea_logs", "aportes_en_linea", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_ccma_logs", "ccma", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "response_ccma", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_certificado_mipyme_logs", "certificado_mipyme", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "opciones_encontradas", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_controladores_fiscales_logs", "controladores_fiscales", ("id", "user_id", "timestamp", "cuit_representante", "cantidad_archivos", "response_data", "resultados", "archivos", "job_id", "status", "error_message")),
    ("consulta_declaracion_en_linea_logs", "declaracion_en_linea", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "representado_nombre", "periodo_desde", "periodo_hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_facturometro_logs", "facturometro", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "monto_facturado", "tope_facturacion", "categoria", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_hacienda_logs", "hacienda", ("id", "user_id", "timestamp", "desde", "hasta", "cuit_representante", "cuit_representado", "nombre_representado", "cbtes", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_libros_iva_logs", "libros_portal_iva", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "periodos_descargados", "periodos_error", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_liquidacion_granos_logs", "liquidacion_granos", ("id", "user_id", "timestamp", "desde", "hasta", "cuit_representante", "cuit_representado", "nombre_representado", "cbtes", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_mc_logs", "mis_comprobantes", ("id", "user_id", "timestamp", "desde", "hasta", "cuit_representante", "cuit_representado", "nombre_representado", "emitidos", "recibidos", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_mis_facilidades_logs", "mis_facilidades", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_mis_retenciones_logs", "mis_retenciones", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_mis_retenciones_iva_simple_logs", "mis_retenciones_iva_simple", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_moa_logs", "moa", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "despachos", "despachos_procesados", "despachos_exitosos", "despachos_con_error", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_pago_devoluciones_logs", "pago_devoluciones", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "archivo_nombre", "errores_por_seccion", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_portal_iva_logs", "portal_iva", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo", "descarga_csv_ventas", "descarga_csv_compras", "importar_txt_ventas", "importar_txt_compras", "archivos", "response_data", "importaciones", "job_id", "status", "error_message")),
    ("consulta_portal_iva_carga_logs", "portal_iva", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo", "operaciones_ng_o_e", "prorrateo_global", "prorrateo_asignacion_directa", "prorrateo_ambos", "resultados_ventas", "resultados_compras", "resultados_aperturas", "archivos_recibidos", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_rcel_logs", "rcel", ("id", "user_id", "timestamp", "desde", "hasta", "cuit_representante", "cuit_representado", "nombre_representado", "cbtes", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_retenciones_percepciones_iibb_agip_logs", "retper_iibb_agip", ("id", "user_id", "timestamp", "usuario", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_retenciones_percepciones_iibb_arba_logs", "arba", ("id", "user_id", "timestamp", "cuit", "denominacion", "periodo", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_retenciones_percepciones_iibb_misiones_logs", "retper_iibb_misiones", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "denominacion", "periodo_desde", "periodo_hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_sct_logs", "sct", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_sct_compensaciones_logs", "sct", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "desde", "hasta", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_sifere_logs", "sifere", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "periodo", "representado_nombre", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_siper_logs", "siper", ("id", "user_id", "timestamp", "cuit_representante", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_srt_logs", "srt", ("id", "user_id", "timestamp", "cuit_representante", "cuits_consultados", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_vep_logs", "vep_archivo", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "medio_pago", "archivo_nombre", "response_nombre_archivo", "archivos", "response_data", "job_id", "status", "error_message")),
    ("consulta_vep_ccma_logs", "vep_ccma", ("id", "user_id", "timestamp", "cuit_representante", "cuit_representado", "medio_pago", "response_volante_data", "response_total_seleccionado", "response_nombre_archivo", "response_nombre_qr", "archivos", "response_data", "job_id", "status", "error_message")),
)

# Columnas JSON del lado respuesta; el resto de las JSON van del request.
_RESPONSE_JSON = frozenset({
    "response_ccma", "opciones_encontradas", "resultados",
    "resultados_ventas", "resultados_compras", "resultados_aperturas",
    "importaciones", "errores_por_seccion", "periodos_descargados",
    "periodos_error", "response_volante_data", "archivos_recibidos",
})
# Enteras del lado respuesta; el resto de las enteras van del request.
_RESPONSE_INT = frozenset({"response_total_seleccionado"})
# Texto del lado respuesta; el resto del texto va del request.
_RESPONSE_STR = frozenset({"response_nombre_archivo", "response_nombre_qr"})

_EXTRA_COLUMNS = ("bot", "operation", "resultado", "request_payload", "response_payload", "created_at")


def _req(columna: str) -> str:
    return f"t.request_payload->>'{columna}'"


def _bool_expr(fuente: str, columna: str) -> str:
    valor = f"{fuente}->>'{columna}'"
    return (
        "CASE "
        f"WHEN lower({valor}) IN ('true','1','t','yes','y','sí','si','on') THEN TRUE "
        f"WHEN lower({valor}) IN ('false','0','f','no','off') THEN FALSE "
        f"END AS {columna}"
    )


def _int_expr(fuente: str, columna: str) -> str:
    valor = f"{fuente}->>'{columna}'"
    return (
        f"CASE WHEN {valor} ~ '^-?\\d+$' "
        f"THEN ({valor})::INTEGER END AS {columna}"
    )


def _columna_sql(columna: str) -> str:
    """Proyecta una columna V2 desde la fila física ``t`` (sin secretos)."""
    if columna == "id":
        return (
            "ROW_NUMBER() OVER (ORDER BY t.created_at DESC NULLS LAST, "
            "t.job_id DESC)::INTEGER AS id"
        )
    if columna == "user_id":
        return "t.user_id AS user_id"
    if columna == "timestamp":
        return "t.created_at AS timestamp"
    if columna == "job_id":
        return "t.job_id AS job_id"
    if columna == "status":
        return "t.status AS status"
    if columna == "error_message":
        return (
            "COALESCE(NULLIF(t.response_payload->>'error',''), "
            "NULLIF(t.response_payload->>'message',''), "
            "NULLIF(t.response_payload->>'error_message',''), "
            "NULLIF(t.error_code,'')) AS error_message"
        )
    if columna == "archivos":
        return (
            "(SELECT COALESCE(jsonb_agg(jsonb_build_object('name', a) "
            "ORDER BY a), '[]'::jsonb) FROM unnest(t.artifact_names) AS a) "
            "AS archivos"
        )
    if columna == "response_data":
        return "t.response_payload AS response_data"
    if columna == "cuit_representante":
        return (
            "COALESCE(t.credential_metadata->'context'->>'cuit_representante', "
            "t.credential_metadata->'context'->>'cuit_inicio_sesion', "
            "t.credential_metadata->'context'->>'cuit_login') "
            "AS cuit_representante"
        )
    if columna == "cuit_representado":
        return (
            "COALESCE(t.request_payload->>'representado_cuit', "
            "t.request_payload->>'cuit_representado') AS cuit_representado"
        )
    if columna == "usuario":
        return (
            "COALESCE(t.request_payload->>'usuario', "
            "t.credential_metadata->'context'->>'usuario') AS usuario"
        )
    if columna == "desde":
        return (
            "COALESCE(t.request_payload->>'desde', "
            "t.request_payload->>'fecha_desde') AS desde"
        )
    if columna == "hasta":
        return (
            "COALESCE(t.request_payload->>'hasta', "
            "t.request_payload->>'fecha_hasta') AS hasta"
        )
    if columna in (
        "emitidos", "recibidos", "descarga_csv_ventas",
        "descarga_csv_compras", "importar_txt_ventas",
        "importar_txt_compras", "operaciones_ng_o_e", "prorrateo_global",
        "prorrateo_asignacion_directa", "prorrateo_ambos",
    ):
        return _bool_expr("t.request_payload", columna)
    if columna == "response_total_seleccionado":
        return _int_expr("t.response_payload", columna)
    if columna in (
        "cantidad_archivos", "despachos_procesados", "despachos_exitosos",
        "despachos_con_error",
    ):
        return _int_expr("t.request_payload", columna)
    if columna in ("cbtes", "despachos"):
        return f"t.request_payload->'{columna}' AS {columna}"
    if columna in _RESPONSE_JSON:
        return f"t.response_payload->'{columna}' AS {columna}"
    if columna in _RESPONSE_STR:
        return f"t.response_payload->>'{columna}' AS {columna}"
    return f"t.request_payload->>'{columna}' AS {columna}"


def _vista_sql(vista: str, bot: str, columnas: tuple[str, ...]) -> str:
    selectores = ",\n       ".join(_columna_sql(columna) for columna in columnas)
    extras = (
        f"'{bot}'::TEXT AS bot,\n"
        "       t.operation AS operation,\n"
        "       t.result AS resultado,\n"
        "       t.request_payload AS request_payload,\n"
        "       t.response_payload AS response_payload,\n"
        "       t.created_at AS created_at"
    )
    return (
        f"CREATE OR REPLACE VIEW {vista} AS\n"
        f"SELECT {selectores},\n"
        f"       {extras}\n"
        f"FROM bot_jobs_{bot} AS t;"
    )


def upgrade() -> None:
    for vista, bot, columnas in LEGACY_VIEWS:
        op.execute(_vista_sql(vista, bot, columnas))


def downgrade() -> None:
    for vista, _bot, _columnas in LEGACY_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {vista}")
