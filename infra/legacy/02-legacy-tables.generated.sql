-- 02-legacy-tables.generated.sql: GENERADO, no editar a mano.
-- Fuente SQLite: ../api-bots-mrbot-v2/data/sql_app.db
-- Sobre de migracion, version de protocolo: 1.
-- Generado con: python3 infra/legacy/generate_legacy_ddl.py
-- Claves foraneas V2 omitidas a proposito (historial sin restricciones cruzadas); ver docstring del generador.

CREATE TABLE IF NOT EXISTS legacy."admin_fiscal_credential_audits" (
    "id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "admin_username" TEXT,
    "action" TEXT,
    "table_name" TEXT,
    "row_id" INTEGER,
    "affected_rows" INTEGER,
    "remote_addr" TEXT,
    "user_agent" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_admin_fiscal_credential_audits_timestamp" ON legacy."admin_fiscal_credential_audits" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."alembic_version" (
    "version_num" TEXT,
    PRIMARY KEY ("version_num")
);

CREATE TABLE IF NOT EXISTS legacy."consulta_aportes_en_linea_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "archivo_historico_minio_url" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_aportes_en_linea_logs_user_id" ON legacy."consulta_aportes_en_linea_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_aportes_en_linea_logs_job_id" ON legacy."consulta_aportes_en_linea_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_aportes_en_linea_logs_timestamp" ON legacy."consulta_aportes_en_linea_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_ccma_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "response_ccma" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_ccma_logs_user_id" ON legacy."consulta_ccma_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_ccma_logs_job_id" ON legacy."consulta_ccma_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_ccma_logs_timestamp" ON legacy."consulta_ccma_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_certificado_mipyme_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "cuit_representado" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "opciones_encontradas" JSONB,
    "clave_representante" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_certificado_mipyme_logs_user_id" ON legacy."consulta_certificado_mipyme_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_certificado_mipyme_logs_job_id" ON legacy."consulta_certificado_mipyme_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_certificado_mipyme_logs_timestamp" ON legacy."consulta_certificado_mipyme_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_controladores_fiscales_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "archivos" JSONB,
    "resultados" JSONB,
    "constancias" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "constancias_url_minio" JSONB,
    "clave_representante" TEXT,
    "cantidad_archivos" INTEGER,
    "minio_urls" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_controladores_fiscales_logs_user_id" ON legacy."consulta_controladores_fiscales_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_controladores_fiscales_logs_job_id" ON legacy."consulta_controladores_fiscales_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_controladores_fiscales_logs_timestamp" ON legacy."consulta_controladores_fiscales_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_declaracion_en_linea_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "representado_nombre" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_declaracion_en_linea_logs_user_id" ON legacy."consulta_declaracion_en_linea_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_declaracion_en_linea_logs_job_id" ON legacy."consulta_declaracion_en_linea_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_declaracion_en_linea_logs_timestamp" ON legacy."consulta_declaracion_en_linea_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_facturometro_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "cuit_representado" TEXT,
    "clave" TEXT,
    "monto_facturado" TEXT,
    "tope_facturacion" TEXT,
    "categoria" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_facturometro_logs_user_id" ON legacy."consulta_facturometro_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_facturometro_logs_job_id" ON legacy."consulta_facturometro_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_facturometro_logs_timestamp" ON legacy."consulta_facturometro_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_hacienda_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "desde" TEXT,
    "hasta" TEXT,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "nombre_representado" TEXT,
    "cbtes" JSONB,
    "response_minio_url" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_hacienda_logs_user_id" ON legacy."consulta_hacienda_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_hacienda_logs_job_id" ON legacy."consulta_hacienda_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_hacienda_logs_timestamp" ON legacy."consulta_hacienda_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_libros_iva_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "periodos_descargados" JSONB,
    "periodos_error" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_libros_iva_logs_user_id" ON legacy."consulta_libros_iva_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_libros_iva_logs_job_id" ON legacy."consulta_libros_iva_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_libros_iva_logs_timestamp" ON legacy."consulta_libros_iva_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_liquidacion_granos_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "desde" TEXT,
    "hasta" TEXT,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "nombre_representado" TEXT,
    "cbtes" JSONB,
    "response_minio_url" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_liquidacion_granos_logs_user_id" ON legacy."consulta_liquidacion_granos_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_liquidacion_granos_logs_job_id" ON legacy."consulta_liquidacion_granos_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_liquidacion_granos_logs_timestamp" ON legacy."consulta_liquidacion_granos_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_mc_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "desde" TEXT,
    "hasta" TEXT,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "nombre_representado" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "emitidos" BOOLEAN,
    "recibidos" BOOLEAN,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mc_logs_user_id" ON legacy."consulta_mc_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mc_logs_job_id" ON legacy."consulta_mc_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mc_logs_timestamp" ON legacy."consulta_mc_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_mis_facilidades_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_facilidades_logs_user_id" ON legacy."consulta_mis_facilidades_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_facilidades_logs_job_id" ON legacy."consulta_mis_facilidades_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_facilidades_logs_timestamp" ON legacy."consulta_mis_facilidades_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_mis_retenciones_iva_simple_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_iva_simple_logs_user_id" ON legacy."consulta_mis_retenciones_iva_simple_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_iva_simple_logs_job_id" ON legacy."consulta_mis_retenciones_iva_simple_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_iva_simple_logs_timestamp" ON legacy."consulta_mis_retenciones_iva_simple_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_mis_retenciones_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_logs_user_id" ON legacy."consulta_mis_retenciones_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_logs_job_id" ON legacy."consulta_mis_retenciones_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_mis_retenciones_logs_timestamp" ON legacy."consulta_mis_retenciones_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_moa_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "despachos" JSONB,
    "despachos_procesados" INTEGER,
    "despachos_exitosos" INTEGER,
    "despachos_con_error" INTEGER,
    "json_minio_url" TEXT,
    "csv_minio_url" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_moa_logs_user_id" ON legacy."consulta_moa_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_moa_logs_job_id" ON legacy."consulta_moa_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_moa_logs_timestamp" ON legacy."consulta_moa_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_pago_devoluciones_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "request_carga_minio" BOOLEAN,
    "request_proxy" BOOLEAN,
    "archivo_nombre" TEXT,
    "archivo_path" TEXT,
    "archivo_url_minio" TEXT,
    "errores_por_seccion" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_pago_devoluciones_logs_user_id" ON legacy."consulta_pago_devoluciones_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_pago_devoluciones_logs_job_id" ON legacy."consulta_pago_devoluciones_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_pago_devoluciones_logs_timestamp" ON legacy."consulta_pago_devoluciones_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_portal_iva_carga_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo" TEXT,
    "operaciones_ng_o_e" BOOLEAN,
    "prorrateo_global" BOOLEAN,
    "prorrateo_asignacion_directa" BOOLEAN,
    "prorrateo_ambos" BOOLEAN,
    "request_proxy" BOOLEAN,
    "resultados_ventas" JSONB,
    "resultados_compras" JSONB,
    "resultados_aperturas" JSONB,
    "archivos_recibidos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_carga_logs_user_id" ON legacy."consulta_portal_iva_carga_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_carga_logs_job_id" ON legacy."consulta_portal_iva_carga_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_carga_logs_timestamp" ON legacy."consulta_portal_iva_carga_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_portal_iva_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo" TEXT,
    "request_carga_minio" BOOLEAN,
    "request_proxy" BOOLEAN,
    "descarga_csv_ventas" BOOLEAN,
    "descarga_csv_compras" BOOLEAN,
    "importar_txt_ventas" BOOLEAN,
    "importar_txt_compras" BOOLEAN,
    "archivos" JSONB,
    "importaciones" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_logs_user_id" ON legacy."consulta_portal_iva_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_logs_job_id" ON legacy."consulta_portal_iva_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_portal_iva_logs_timestamp" ON legacy."consulta_portal_iva_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_rcel_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "desde" TEXT,
    "hasta" TEXT,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "nombre_representado" TEXT,
    "cbtes" JSONB,
    "response_minio_url" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_rcel_logs_user_id" ON legacy."consulta_rcel_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_rcel_logs_job_id" ON legacy."consulta_rcel_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_rcel_logs_timestamp" ON legacy."consulta_rcel_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_retenciones_percepciones_iibb_agip_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "usuario" TEXT,
    "clave" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "request_carga_minio" BOOLEAN,
    "request_proxy" BOOLEAN,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_agip_user_id_f262b6dc" ON legacy."consulta_retenciones_percepciones_iibb_agip_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_agip__job_id_9244a02d" ON legacy."consulta_retenciones_percepciones_iibb_agip_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_ag_timestamp_55ef5d02" ON legacy."consulta_retenciones_percepciones_iibb_agip_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_retenciones_percepciones_iibb_arba_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit" TEXT,
    "clave" TEXT,
    "denominacion" TEXT,
    "periodo" TEXT,
    "request_carga_minio" BOOLEAN,
    "request_proxy" BOOLEAN,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_arba_user_id_0a80aa00" ON legacy."consulta_retenciones_percepciones_iibb_arba_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_arba__job_id_f41ebe2e" ON legacy."consulta_retenciones_percepciones_iibb_arba_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_ar_timestamp_9526e797" ON legacy."consulta_retenciones_percepciones_iibb_arba_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_retenciones_percepciones_iibb_misiones_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "denominacion" TEXT,
    "periodo_desde" TEXT,
    "periodo_hasta" TEXT,
    "request_carga_minio" BOOLEAN,
    "request_proxy" BOOLEAN,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_misi_user_id_ad41ff7c" ON legacy."consulta_retenciones_percepciones_iibb_misiones_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_misio_job_id_3064bc57" ON legacy."consulta_retenciones_percepciones_iibb_misiones_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_consulta_retenciones_percepciones_iibb_mi_timestamp_c5d164ab" ON legacy."consulta_retenciones_percepciones_iibb_misiones_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_sct_compensaciones_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "desde" TEXT,
    "hasta" TEXT,
    "request_excel" BOOLEAN,
    "request_csv" BOOLEAN,
    "request_pdf" BOOLEAN,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_compensaciones_logs_user_id" ON legacy."consulta_sct_compensaciones_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_compensaciones_logs_job_id" ON legacy."consulta_sct_compensaciones_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_compensaciones_logs_timestamp" ON legacy."consulta_sct_compensaciones_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_sct_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "excel_url_minio" TEXT,
    "csv_url_minio" TEXT,
    "pdf_url_minio" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "vencimientos_excel_url_minio" TEXT,
    "vencimientos_csv_url_minio" TEXT,
    "vencimientos_pdf_url_minio" TEXT,
    "deudas_excel_url_minio" TEXT,
    "deudas_csv_url_minio" TEXT,
    "deudas_pdf_url_minio" TEXT,
    "ddjj_pendientes_excel_url_minio" TEXT,
    "ddjj_pendientes_csv_url_minio" TEXT,
    "ddjj_pendientes_pdf_url_minio" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_logs_user_id" ON legacy."consulta_sct_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_logs_job_id" ON legacy."consulta_sct_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sct_logs_timestamp" ON legacy."consulta_sct_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_sifere_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "periodo" TEXT,
    "representado_nombre" TEXT,
    "archivos" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sifere_logs_user_id" ON legacy."consulta_sifere_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sifere_logs_job_id" ON legacy."consulta_sifere_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_sifere_logs_timestamp" ON legacy."consulta_sifere_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_siper_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "siper_url_minio" TEXT,
    "siper_historial_url_minio" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_siper_logs_user_id" ON legacy."consulta_siper_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_siper_logs_job_id" ON legacy."consulta_siper_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_siper_logs_timestamp" ON legacy."consulta_siper_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_srt_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "cuits_consulta" JSONB,
    "minio_json_urls" JSONB,
    "status" TEXT,
    "error_message" TEXT,
    "cuits_consultados" TEXT,
    "estado_consulta" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_srt_logs_user_id" ON legacy."consulta_srt_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_srt_logs_job_id" ON legacy."consulta_srt_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_srt_logs_timestamp" ON legacy."consulta_srt_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_vep_ccma_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "cuit_representado" TEXT,
    "medio_pago" TEXT,
    "response_volante_data" JSONB,
    "response_total_seleccionado" INTEGER,
    "response_nombre_archivo" TEXT,
    "response_nombre_qr" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_ccma_logs_user_id" ON legacy."consulta_vep_ccma_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_ccma_logs_job_id" ON legacy."consulta_vep_ccma_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_ccma_logs_timestamp" ON legacy."consulta_vep_ccma_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."consulta_vep_logs" (
    "id" INTEGER,
    "user_id" INTEGER,
    "timestamp" TIMESTAMPTZ,
    "cuit_representante" TEXT,
    "clave_representante" TEXT,
    "medio_pago" TEXT,
    "archivo_nombre" TEXT,
    "response_nombre_archivo" TEXT,
    "response_ruta_archivo" TEXT,
    "response_url_minio" TEXT,
    "status" TEXT,
    "error_message" TEXT,
    "cuit_representado" TEXT,
    "archivos" JSONB,
    "response_data" JSONB,
    "job_id" TEXT,
    "clave_encriptada" TEXT,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_logs_user_id" ON legacy."consulta_vep_logs" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_logs_job_id" ON legacy."consulta_vep_logs" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_consulta_vep_logs_timestamp" ON legacy."consulta_vep_logs" ("timestamp");

CREATE TABLE IF NOT EXISTS legacy."playwright_jobs_active" (
    "job_id" TEXT,
    "user_id" INTEGER,
    "bot" TEXT,
    "operation" TEXT,
    "status" TEXT,
    "request_data" JSONB,
    "created_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ,
    "worker_id" TEXT,
    "attempts" INTEGER,
    "idempotency_key" TEXT,
    PRIMARY KEY ("job_id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_active_user_id" ON legacy."playwright_jobs_active" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_active_job_id" ON legacy."playwright_jobs_active" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_active_created_at" ON legacy."playwright_jobs_active" ("created_at");

CREATE TABLE IF NOT EXISTS legacy."playwright_jobs_history" (
    "job_id" TEXT,
    "user_id" INTEGER,
    "bot" TEXT,
    "operation" TEXT,
    "status" TEXT,
    "result" TEXT,
    "created_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "cancel_reason" TEXT,
    "cancelled_by" TEXT,
    "error_message" TEXT,
    PRIMARY KEY ("job_id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_history_user_id" ON legacy."playwright_jobs_history" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_history_job_id" ON legacy."playwright_jobs_history" ("job_id");
CREATE INDEX IF NOT EXISTS "ix_legacy_playwright_jobs_history_created_at" ON legacy."playwright_jobs_history" ("created_at");

CREATE TABLE IF NOT EXISTS legacy."users" (
    "id" INTEGER,
    "mail" TEXT,
    "api_key" TEXT,
    "maximas_consultas_mensuales" INTEGER,
    "consultas_realizadas" INTEGER,
    "habilitado" BOOLEAN,
    "fecha_ultimo_reset" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ,
    "updated_at" TIMESTAMPTZ,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_legacy_users_created_at" ON legacy."users" ("created_at");
