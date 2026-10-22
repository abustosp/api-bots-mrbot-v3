# Auditoría interna: `certificacion_primaria_granos`

## Contrato y ruta

Una operación, `consultar` (costo 2 créditos, `central-api/api/bots.py`). Alias `/api/v3/certificacion_primaria_granos/consulta` (`bot_compat.py`) y logs en `/certificacion_primaria_granos/logs` (`bot_logs.py`). Tabla de detalle `bot_jobs_certificacion_primaria_granos` (migración `0029`; el bot no tiene vista legacy V1/V2). `denominacion` se normaliza a `representado_nombre` y `minio_upload` a `subir_archivos` (`security/credentials.py`).

## Flujo

1. **Entrada** (`bots/certificacion_primaria_granos/schema.py`): mismo contrato que `liquidacion_granos` sin las banderas de sección. Rango `dd/mm/aaaa` con `desde <= hasta`.
2. **Plugin** (`plugin.py`): login ARCA, `open_service("LIQUIDACIÓN PRIMARIA DE GRANOS", portal="certificacion_primaria_granos")`, `seleccionar_representado(cuit, nombre)` y `descargar_certificados_depositante`. Sube la planilla (`planilla_xlsx`) y un PDF por certificado (`certificado_pdf`) salvo `subir_archivos=false`. Los slots se derivan del manifiesto, no del dispatcher.
3. **Portal** (`runtime/portals/certificacion_primaria_granos.py`): hereda de `LiquidacionGranosPortal`. Abre Certificación Electrónica de Granos > Consulta de Certificados de Depósito, elige la opción de `#tipoConsulta` cuyo texto coincide con `como depositante` (hoy `D`), completa `#fechaDesde`/`#fechaHasta`, consulta, guarda la grilla (`jig_table`) como XLSX y baja cada PDF por `generarReporteCEG.do` (click como respaldo).

## Hallazgos de la exploración (2026-10-22)

- Opciones de `#tipoConsulta`: `D` Depositante, `P` Depositario, `E` Emisor del Certificado, `C` Corredor. `liquidacion_granos` recorre las cuatro; este bot solo `D`.
- Los botones de empresa de "Seleccione la Empresa" son `<input type="button" value="APELLIDO NOMBRE">`: no tienen `inner_text`. El helper de `bots_dev/liquidacion_granos` leía solo `inner_text` y por eso no encontraba candidatos; ahora usa `value` como respaldo. El portal del worker ya leía `value`.
- La columna *Tipo de Certificado* de la grilla vale `Certificación Primaria`; el atributo `data-tipocertificado` del link (`P`) es el código del PDF, no el tipo de consulta.
- Sin certificados el portal devuelve la grilla vacía: el bot responde `OK` con `cantidad_certificados: 0`.

## Desarrollo local y modo logger

Variantes en `bots_dev/certificacion_primaria_granos/` (variables `CERTIFICACION_PRIMARIA_GRANOS_*`, ver `.env.example`): `_bot_logger.py` (evidencias en `temp/certificacion_primaria_granos_<timestamp>/`), `_bot.py` (limpia, autocontenida) y `_bot_mrbot.py` (réplica de producción con `tempfile`, `limit_browser` y `app.utils.*`). Se generaron a partir de las funciones de `liquidacion_granos` que usa el flujo; no importan de ese bot.

- `logger: true` en el cuerpo: funciona como en cualquier bot (ver [API §10](../api.md)); ejecuta el plugin instrumentado y deja `{job_id}.tar.gz` en el bucket `logger`.
- `logger: true` + `logger_script: true`: ejecuta `_bot_logger.py` como subproceso (`runtime/logger_script.py`). El script y `_bot.py` se copian al worker con `tools/sync_bots_dev_loggers.py`.
