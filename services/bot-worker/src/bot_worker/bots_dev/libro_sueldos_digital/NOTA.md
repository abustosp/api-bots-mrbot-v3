# libro_sueldos_digital (stub dev)

Port del desarrollo `bots_dev/libro_sueldos_digital/libro_sueldos_digital_bot.py`
al contrato worker V3 (`bots/libro_sueldos_digital/`): operación `descargar`
sobre el servicio Libro de Sueldos Digital, bajando el PDF de cada liquidación
de cada período del rango `AAAAMM`.

## Stub dev

Directorio solo para desarrollo: NO altera `registry.py`.

1. `validate(payload_ejemplo.json)` falla antes del navegador si el rango
   `AAAAMM` es inválido; `generar_rango_periodos(desde, hasta)` es puro y se
   puede probar sin runtime.
2. `configure({"servicio": "..."})` aplica la sección `service` del sobre
   sellado sobre una copia del plugin.
3. `execute` requiere `runtime` real (sesión ARCA + artefacto `lsd_pdf`);
   con `subir_archivos=false` los PDF quedan solo en `work_dir`.

## Notas del port

- Con más de un representado el servicio abre en `IndexContribuyente.aspx`
  (combo `ddlCUIT` + botón `Aceptar`): el portal selecciona el CUIT pedido —o
  el primero, la preselección del sitio, si el sobre no lo explicita— y el
  plugin verifica el empleador resultante antes de descargar. Con un solo
  representado el servicio abre directo en el empleador de la sesión.
- El portal pagina de a 1 período por defecto: el port selecciona 5 por
  página (postback) para recorrer todo el rango; si un período tiene varias
  liquidaciones se descargan todas.
- Si el LS no está generado, primero dispara `GENERAR LIBRO SUELDOS DIGITAL`
  y espera la aparición de `DESCARGAR LIBRO SUELDOS DIGITAL`.
- El nombre del PDF es determinístico: `{cuit}_{AAAAMM}_{nro:05d}.pdf`.
- Sin MinIO ni credenciales de bucket: subida por slots (`lsd_pdf`); la
  central arma la URL desde el `object_key`.
- Errores por categoría, diagnóstico redactado con `sin_secretos` (sin
  selectores ni texto del sitio en mensajes públicos).
