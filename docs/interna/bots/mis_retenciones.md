# Auditoría interna: `mis_retenciones`

## Contrato y ruta

Catálogo central: operación `consultar` (`services/central-api/src/central_api/api/bots.py:L407`). Alias `/api/v3/mis_retenciones/consulta` mapea a esa operación (`central-api/api/bot_compat.py:L73-L74`). Ruta genérica y envelope `payload`/`credentials`: `central-api/api/bots.py:L466-L513`, `L748-L817`.

## Flujo esquema → plugin → portal

1. **Entrada.** Modelo permite claves extra (`services/bot-worker/src/bot_worker/bots/mis_retenciones/schema.py:L42-L44`). Campos requeridos: CUIT/nombre/rango (`L69-L106`); CUIT limpia separadores, fechas se normalizan a `dd/mm/aaaa` y se comprueba orden cronológico (`L46-L66`, `L108-L139`). `impuestos` es lista opcional máx. 6 de códigos 216, 217, 219, 353, 767, 787; normaliza miembros a texto (`L30-L39`, `L88-L106`). `tipos` default `Retencion` y `Percepcion`; al menos uno requerido. 216 y 787 solo permiten `Retencion`; los demás permiten ambas. El cruce se rechaza si `impuestos` se expresa explícitamente y contradice `tipos` (`L118-L139`).
2. **Plugin.** Configuración puede presetear impuestos, tipos y salidas sin sobreescribir campos enviados (`services/bot-worker/src/bot_worker/bots/mis_retenciones/plugin.py:L140-L173`). Login abre servicio y selecciona representado. Si no confirma selección, agrega aviso y continúa con la relación activa (`L175-L217`).
3. **Portal.** Por cada impuesto/tipo permitido consulta y exporta CSV. Con `exportar_para_aplicativo=true` intenta formatos `siap` y `xls`; de lo contrario CSV común (`plugin.py:L219-L298`; `services/bot-worker/src/bot_worker/runtime/portals/mis_retenciones.py:L267-L705`). El portal recorta fecha final futura a hoy (`runtime/portals/mis_retenciones.py:L67-L76`, `L485-L515`).

## Normalizaciones y outputs

Sin impuestos (omitido, `null` o `[]`) el plugin usa los seis códigos, o cinco disponibles en modo aplicativo (`plugin.py:L227-L231`). En ausencia de lista explícita, omite combinaciones no admitidas por cada código dentro del loop, sin error de validación (`L241-L248`). Cada combinación con fallo deja `filas: 0`, mensaje y código diagnóstico, y el loop sigue (`L263-L280`). Resultado `data.impuestos[código][tipo][formato]` resume filas, archivo, opcionalmente `sha256`, `size_bytes` y muestra; `data.errores` acumula fallos. El estado global permanece `OK` salvo error fatal fuera del loop (`L286-L298`, `L213-L217`). `incluir_json=false` quita muestras; `subir_csv=false` evita adjuntos, no la consulta.

## Discrepancias y propuestas, no implementadas

- El modelo público de central declara impuestos/tipos como listas de texto sin enumeraciones ni dependencias entre valores (`services/central-api/src/central_api/api/bot_payloads.py:L220-L228`), mientras la entrada efectiva aplica códigos, etiquetas, rangos y reglas de cruce. **Propuesta:** reflejar enums, máximo, defaults y restricciones condicionales en el contrato publicado.
- `extra="allow"` deja pasar y conservar nombres desconocidos, aunque el flujo solo consume campos conocidos (`schema.py:L42-L44`, `plugin.py:L219-L298`). **Propuesta:** pasar a rechazo de extras o documentar expresamente que los extras se ignoran.
- El esquema V3 de credenciales es genérico (`central-api/api/bots.py:L820-L845`), frente al requisito práctico de acceso fiscal (`plugin.py:L175-L182`). **Propuesta:** publicar campos y requiredness.

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/mis_retenciones/schema.py:L25-L144`
- `services/bot-worker/src/bot_worker/bots/mis_retenciones/plugin.py:L104-L217`, `L219-L298`
- `services/bot-worker/src/bot_worker/runtime/portals/mis_retenciones.py:L67-L76`, `L485-L515`
- `services/central-api/src/central_api/api/bots.py:L407`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L73-L74`
- `services/central-api/src/central_api/api/bot_payloads.py:L220-L228`
- Revisados: `services/bot-worker/tests/test_portal_mis_retenciones.py` y contrato de payload. El test de fechas confirma el recorte a hoy. No se ejecutó el portal.

Propuestas anotadas para seguimiento, no aplicadas.