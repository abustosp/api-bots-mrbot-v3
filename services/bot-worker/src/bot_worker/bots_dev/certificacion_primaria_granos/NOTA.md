# certificacion_primaria_granos

Port V3 de `bots_dev/certificacion_primaria_granos/` (logger explorado contra
el portal real). Flujo: servicio "Liquidación Primaria de Granos" > seleccionar
empresa > Certificación Electrónica de Granos > Consulta de Certificados de
Depósito > tipo `Certificados como Depositante` > fechas > Consultar > grilla
(XLSX) + un PDF por certificado (`generarReporteCEG.do`).

El portal (`runtime/portals/certificacion_primaria_granos.py`) hereda de
`liquidacion_granos`; solo fija el tipo de consulta. Los botones de empresa son
`<input type="button" value=...>`.
