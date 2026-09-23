# declaracion_en_linea (stub dev)

Port de `api-bots-mrbot-v2/app/bot/declaracion_en_linea_bot.py` al contrato
worker V3 (`bots/declaracion_en_linea/`): operacion `consultar` sobre
Declaración en Línea, descargando DDJJ y VEP por rango `AAAAMM`.

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `validate(payload_ejemplo.json)` falla antes del navegador si el rango
   `AAAAMM` es invalido; `generar_rango_periodos(desde, hasta)` es puro y
   se puede probar sin runtime.
2. `configure({"servicio": "..."})` aplica la seccion `service` del sobre
   sobre una copia del plugin.
3. `execute` requiere `runtime` real (sesion ARCA + slots `ddjj_pdf` /
   `vep_pdf`); con `subir_archivos=false` no se necesitan slots.

## Notas del port

- Sin CUIT representado se usa el CUIT de las credenciales (como en V2).
- Cada PDF vive bajo `work_dir`; subida por slots, nunca MinIO.
- Errores por categoria, diagnostico redactado con `sin_secretos`.
