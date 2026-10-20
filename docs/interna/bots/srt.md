# Auditoría interna: `srt`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/srt/schema.py:18-87` normaliza CUITs individuales/lote, define flags y publica la entrada. La lista debe contener 1–50 CUIT, sin duplicados tras normalización.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/srt/plugin.py:158-219` valida, requiere credenciales y abre el servicio; configura captcha SRT y consulta cada CUIT; `:221-292` acumula registros y errores, serializa un JSON consolidado y opcionalmente publica el artefacto.
3. **Portal real.** `services/bot-worker/src/bot_worker/runtime/portals/srt.py:15-20` fija rutas; `:23-194` navega el servicio autenticado, consulta cada CUIT y extrae estado/mensaje/datos. La entrada del servicio se hace por navegación interna después de la autenticación, no por URL pública de consulta directa (`:81-127`).

## Catálogo y normalización

El catálogo publica `srt/consultar_alicuotas` en `services/central-api/src/central_api/api/bots.py:418`, clasificado `CONSULTA` por `:428-438`. Alias V3 `/api/v3/srt/alicuotas/consulta`: `services/central-api/src/central_api/api/bot_compat.py:104`.

La familia de payloads del catálogo es `services/central-api/src/central_api/api/bot_payloads.py:294-297`; `credentials.py:74-103,227-263` normaliza aliases globales CUIT/subida antes del schema. La forma antigua `cuit` puede ser movida a lote durante normalización del esquema. El campo de operación se agrega al payload validado por el handler central; el esquema del bot requiere `cuits_consulta`.

## Salida y límites reales

- `data` contiene `operacion="consultar_alicuotas"`, `cantidad`, `consultas_ok`, `consultas_error`; `consultas` se incluye con `incluir_json=true`. Cada fila contiene `cuit`, `status`, `message`, `data`; una fila fallida queda con `status="ERROR"`, mensaje diagnóstico y `data=null`. `SIN_AFILIACION_VIGENTE` se cuenta como éxito (`plugin.py:235-270`).
- Se escribe un consolidado `{"consultas": [...]}` en JSON y se trunca la serialización a 1.048.576 caracteres antes de publicar. Si `subir_json=true`, se agrega referencia de archivo/huella/tamaño (`plugin.py:272-292`). La truncación puede cortar JSON en mitad de estructura.
- Un CUIT puede fallar y el resto continúa. Cualquier fila error produce `PARCIAL` con errores por CUIT (`plugin.py:205-219,235-270`). Sin `incluir_json`, se omite lista en `data`, pero el archivo aún puede incluir el lote completo truncado.

## Discrepancias observadas

1. **Manifest y modelo difieren en campos requeridos.** El manifiesto requiere solo operación aunque el modelo exige lista de CUIT (`schema.py:18-87`). La familia central publica lista de entrada y flags, pero no sustituye la validación (`bot_payloads.py:294-297`).
2. **Truncamiento no conserva JSON válido.** Se corta una cadena JSON serializada por longitud (`plugin.py:272-275`), dejando archivo potencialmente inválido.
3. **Semántica de estados.** `SIN_AFILIACION_VIGENTE` cuenta como consulta correcta (`plugin.py:266-270`), mientras estados de error pueden provenir de portal no disponible.
4. **Credenciales humanas/captcha son dependencias del servicio.** La sesión ARCA y captcha de formulario SRT son pasos separados (`plugin.py:187-203`).

## Pruebas consultadas

`services/bot-worker/tests/test_portal_srt.py:78-120,122-203` cubre formas de CUIT, resultados y recorrido por lote. No se ejecutaron portales ni credenciales reales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/srt/schema.py:18-87`
- `services/bot-worker/src/bot_worker/bots/srt/plugin.py:158-292`
- `services/bot-worker/src/bot_worker/runtime/portals/srt.py:15-194`
- `services/central-api/src/central_api/api/bots.py:418-438`
- `services/central-api/src/central_api/api/bot_compat.py:104`
- `services/central-api/src/central_api/security/credentials.py:74-103,227-263`
- `services/central-api/src/central_api/api/bot_payloads.py:294-297`
- `services/bot-worker/tests/test_portal_srt.py:78-120,122-203`
