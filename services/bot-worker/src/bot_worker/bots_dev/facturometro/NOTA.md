# facturometro (stub dev)

Port de `api-bots-mrbot-v2/app/bot/facturometro_bot.py` (`bot_facturometro`)
al contrato worker V3 (`bots/facturometro/`): operacion `consultar` de solo
lectura sobre Monotributo (monto, tope, categoria).

## Stub dev

Directorio solo para desarrollo: NO registra el bot ni toca `registry.py`.

1. `FacturometroPlugin().validate(payload_ejemplo.json)` falla antes del
   navegador si el CUIT es invalido.
2. `configure({"servicio": "MONOTRIBUTO"})` aplica la seccion `service`.
3. `execute` requiere `runtime` real con sesion ARCA; no produce artefactos
   (deja `resultado.json` redactado en `work_dir` para trazabilidad).

## Notas del port

- Bot de solo lectura: `idempotency_class="LECTURA"`, sin slots.
- V2 lee `#spanFacturometroMonto`, `#spanFacturometroCategoriaTope` y
  `#spanFacturometroCategoria2` con fallback a `CalcularFacturacion`.
- Errores por categoria, diagnostico redactado con `sin_secretos`.
