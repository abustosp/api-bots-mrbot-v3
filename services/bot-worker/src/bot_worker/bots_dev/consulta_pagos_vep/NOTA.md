# consulta_pagos_vep (stub dev)

Port de `api-bots-mrbot-v2/app/bot/consulta_pagos_vep_bot.py`
(`bot_consulta_pagos_veps`) al contrato worker V3
(`bots/consulta_pagos_vep/`): operacion `consultar` sobre el servicio
Presentación de DDJJ y Pagos, exportando el CSV de pagos VEP.

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `ConsultaPagosVepPlugin().validate(payload_ejemplo.json)` falla antes del
   navegador si el CUIT o el periodo son invalidos.
2. `configure({"servicio": "...", "muestra_json": 5})` aplica la seccion
   `service` del sobre sellado sobre una copia del plugin.
3. `execute` requiere `runtime` real (sesion ARCA + slot `pagos_vep_csv`);
   con `subir_csv=false` no se necesita slot.

## Notas del port

- `periodo` conserva el defecto V2 (`"72"`).
- El CSV vive bajo `work_dir`; la subida usa slots prefirmados, nunca MinIO.
- Errores por categoria, diagnostico redactado con `sin_secretos`.
