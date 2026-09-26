"""Compact operation-specific field lists copied from the V2 request models.

A leading ``!`` marks a required field. The list order is intentionally kept
for stable OpenAPI rendering. Retired download-retention and output-name options
are omitted here.
"""

_CONTRACTS = """
aportes_en_linea/descargar: clave_encriptada cuit_login! clave! cuit_representado archivo_historico_minio proxy_request
arba/descargar: clave_encriptada cuit! clave! periodo! denominacion! proxy_request carga_minio
carga_portal_iva/cargar: clave_encriptada cuit_representante! clave_representante! cuit_representado denominacion periodo! operaciones_ng_o_e prorrateo_global prorrateo_asignacion_directa prorrateo_ambos importacion_definitiva_bienes importacion_servicios regimen_turiva bienes_usados ninguna_anteriores proxy_request
ccma/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado! proxy_request movimientos pdf
certificado_mipyme/descargar: clave_encriptada cuit_representante! clave! cuit_representado! proxy_request
compensaciones/consultar: clave_encriptada cuit_login! clave! cuit_representado! desde! hasta! excel csv pdf proxy_request
consulta_cuit/consulta: cuit!
consulta_cuit/consultar: cuit!
consulta_cuit/consultar_masivo: cuits!
consulta_pagos_vep/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado! periodo minio_upload proxy_request
controladores_fiscales/presentar: clave_encriptada cuit_representante! clave! proxy_request minio_upload
declaracion_en_linea/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado representado_nombre periodo_desde! periodo_hasta! proxy_request carga_minio
facturometro/consultar: clave_encriptada cuit_login! clave! cuit_representado! proxy_request
hacienda/consultar: clave_encriptada desde! hasta! cuit_representante! denominacion! representado_cuit! clave! minio_upload proxy_request
libros_portal_iva/descargar_ddjj: clave_encriptada cuit_representante! clave! cuit_representado denominacion periodo_desde! periodo_hasta! proxy_request
libros_portal_iva/descargar_libros: clave_encriptada cuit_representante! clave! cuit_representado denominacion periodo_desde! periodo_hasta! proxy_request
liquidacion_granos/consultar: clave_encriptada desde! hasta! cuit_representante! clave! denominacion! cuit_representado minio_upload proxy_request
mis_comprobantes/consulta: clave_encriptada desde! hasta! cuit_inicio_sesion! representado_nombre! representado_cuit! contrasena! descarga_emitidos! descarga_recibidos! puntos_venta_emitidos puntos_venta_recibidos carga_minio carga_json timeout_mc proxy_request
mis_comprobantes/consultar: clave_encriptada desde! hasta! cuit_inicio_sesion! representado_nombre! representado_cuit! contrasena! descarga_emitidos! descarga_recibidos! puntos_venta_emitidos puntos_venta_recibidos carga_minio carga_json timeout_mc proxy_request
mis_comprobantes/solicitar: clave_encriptada desde! hasta! cuit_inicio_sesion! representado_nombre! representado_cuit! contrasena! emitidos! recibidos! puntos_venta_emitidos puntos_venta_recibidos proxy_request
mis_comprobantes/historial: clave_encriptada desde! hasta! cuit_inicio_sesion! representado_nombre! representado_cuit! contrasena! descarga_emitidos! descarga_recibidos! carga_minio carga_json proxy_request
mis_facilidades/consultar: clave_encriptada cuit_login! clave! cuit_representado denominacion lista_exclusion_situacion proxy_request carga_minio
mis_retenciones/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado denominacion! desde! hasta! impuestos exportar_para_aplicativo proxy_request carga_minio
mis_retenciones_iva_simple/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado denominacion! desde! hasta! proxy_request carga_minio
moa/consultar: clave_encriptada cuit_representante! clave! cuit_representado! despachos! tipo_agente rol minio_upload proxy_request
pago_devoluciones/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado proxy_request carga_minio
portal_iva/descargar: clave_encriptada cuit_representante! clave_representante! cuit_representado denominacion periodo! operaciones_ng_o_e prorrateo_global prorrateo_asignacion_directa prorrateo_ambos importacion_definitiva_bienes importacion_servicios regimen_turiva bienes_usados ninguna_anteriores descarga_csv_ventas descarga_csv_compras carga_minio proxy_request
rcel/descargar: clave_encriptada desde! hasta! cuit_representante! nombre_rcel! representado_cuit! clave! minio_upload proxy_request
retper_iibb_agip/consultar: clave_encriptada usuario! clave! cuit_representado! denominacion! desde! hasta! proxy_request carga_minio
retper_iibb_misiones/consultar: clave_encriptada cuit_representante! clave_representante! desde! hasta! denominacion! proxy_request carga_minio
sct/consultar: clave_encriptada cuit_login! clave! cuit_representado! proxy_request vencimientos_excel_minio vencimientos_csv_minio vencimientos_pdf_minio deudas_excel_minio deudas_csv_minio deudas_pdf_minio ddjj_pendientes_excel_minio ddjj_pendientes_csv_minio ddjj_pendientes_pdf_minio
sifere/consultar: clave_encriptada cuit_representante! clave_representante! cuit_representado! periodo! representado_nombre proxy_request jurisdicciones carga_minio
siper/consultar: clave_encriptada cuit_representante! clave! cuit_representado detalle_minio categorias_minio proxy_request
srt/consultar_alicuotas: clave_encriptada cuit_login! clave! cuits_consulta! proxy_request
vep_archivo/generar: clave_encriptada cuit_inicio_sesion! medio_pago! contrasena! minio_upload proxy_request representado_cuit archivo_nombre archivo_b64 incluir_json subir_pdf
vep_ccma/generar: clave_encriptada cuit_representante! clave_representante! cuit_representado! medio_pago filtro_impuestos filtro_intereses seleccionar_impuestos seleccionar_intereses minio_upload proxy_request generar_volante
"""

V2_OPERATION_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {}
V2_REQUIRED_FIELDS: dict[tuple[str, str], frozenset[str]] = {}
for _line in _CONTRACTS.strip().splitlines():
    _key, _names = _line.split(":", 1)
    _bot, _operation = _key.split("/", 1)
    _fields = tuple(_names.split())
    _pair = (_bot, _operation)
    V2_OPERATION_FIELDS[_pair] = tuple(name.rstrip("!") for name in _fields)
    V2_REQUIRED_FIELDS[_pair] = frozenset(name[:-1] for name in _fields if name.endswith("!"))

__all__ = ["V2_OPERATION_FIELDS", "V2_REQUIRED_FIELDS"]
