# facturometro (stub dev)

Port de `api-bots-mrbot-v2/app/bot/facturometro_bot.py` (`bot_facturometro`)
al contrato worker V3 (`bots/facturometro/`): operacion `consultar` de solo
lectura sobre Monotributo (monto, tope, categoria).

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `FacturometroPlugin().validate(payload_ejemplo.json)` falla antes del
   navegador si el CUIT es invalido.
2. `configure({"servicio": "MONOTRIBUTO"})` aplica la seccion `service`.
3. `execute` requiere `runtime` real con sesion ARCA; sube la captura del
   panel (`#divFacturometro`) por el slot `facturometro_captura` y deja
   `resultado.json` redactado en `work_dir` para trazabilidad.

## Notas del port

- Bot de solo lectura: `idempotency_class="LECTURA"`; el unico artefacto es
  la captura PNG del panel (V1 la subia a MinIO y devolvia `screenshot_url`).
- `representado_cuit` es opcional: sin el se usa el CUIT de la credencial,
  como V1; `carga_minio`/`incluir_captura` controlan la captura.
- V2 lee `#spanFacturometroMonto`, `#spanFacturometroCategoriaTope` y
  `#spanFacturometroCategoria2` con fallback a `CalcularFacturacion`.
- Errores por categoria, diagnostico redactado con `sin_secretos`.
