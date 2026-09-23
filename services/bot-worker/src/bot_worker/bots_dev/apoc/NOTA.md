# apoc (stub dev)

Port V3 de `api-bots-mrbot-v2/app/bot/apoc.py` al contrato worker S7
(`manifest` + `validate` + `execute` + `configure`).

- Sin navegador, sin credenciales, sin entorno: la base APOC llega como
  texto inline en la sección `service` del sobre sellado
  (`apoc_base_text`, formato `base.txt` de V2). Sin base provisionada,
  `execute` responde `apoc: false` con advertencia, nunca error.
- Solo escribe `resultado.json` bajo `runtime.work_dir`.
- `payload_ejemplo.json`: operación `consultar` con un CUIT de ejemplo.
- Validación offline: `validate` + `buscar_en_base` son puros; el flujo
  completo requiere sobre sellado con `apoc_base_text` (lo arma la central).
