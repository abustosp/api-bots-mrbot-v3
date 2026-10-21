# Auditoría interna: `retper_iibb_agip`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/schema.py:20-47` limpia CUIT; `:50-59` convierte el período tomando los primeros seis dígitos después de quitar no dígitos; `:62-120` define el modelo estricto, aliases/secretos retirados, conversión especial de `pv` y regla de salida. El JSON Schema publicado está en `:130-151` y exige menos campos que el modelo Pydantic.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/plugin.py:156-218` declara operación/artefactos/configuración; `:220-295` valida, exige credenciales fiscales e intenta ejecutar la consulta con recuperación para estados de indisponibilidad/bloqueo; `:297-328` abre la sesión y solicita reportes; `:330-379` reúne nombres, empaqueta/sube ZIP y compone resultado.
3. **Portal AGIP.** La sesión específica está en `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/session.py`: `:39-88,153-170` define familias/nombres y empaquetado; `:174-216` autentica y clasifica bloqueos; `:357-419` selecciona contribuyente/servicio; `:421-484` aplica y verifica rango de períodos; `:486-603` espera y obtiene los archivos disponibles. La variante escogida por `pv` se registra en `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/retper_iibb_agip_bot_xvfb.py:1-36`; es una modalidad de ejecución/presentación, no una consulta diferente.

## Catálogo y normalización

El catálogo central publica `retper_iibb_agip/consultar` en `services/central-api/src/central_api/api/bots.py:413`; la clasificación general de efecto está en `:428-438`. Alias V3 `/api/v3/retenciones_percepciones_iibb/agip/consulta`: `services/central-api/src/central_api/api/bot_compat.py:86-89`.

`services/central-api/src/central_api/security/credentials.py:74-103` canonicaliza nombres globales; `:200-210` resuelve `desde/hasta` a períodos para bots compatibles; `:227-263` traduce aliases de subida a `subir_archivo`; `:303-370` separa claves y CUIT de sesión. AGIP resuelve `desde`/`hasta` en el propio modelo además. Para alias de payload, central puede quitar la grafía antigua si también vino el campo moderno, por lo que la contradicción no siempre alcanza la validación del bot.

## Normalizaciones, portal y salida

- CUIT pierde separadores y debe quedar en 11 dígitos, pero el esquema no comprueba dígito verificador (`schema.py:20-47`).
- Cada período pierde no dígitos y se truncan caracteres posteriores al sexto; eso acepta cadenas con prefijos/sufijos que no son parte de una fecha normal (`schema.py:50-59`). Luego se valida rango `AAAAMM` y `desde <= hasta` (`:92-120`).
- `usuario=null` pasa a ausencia; se usa el CUIT de sesión. `pv=null` pasa a `false`; su conversor acepta valores string seleccionados y convierte otros textos a falso (`schema.py:62-91`). `incluir_json` se comprueba como opción de salida, pero `_consultar` no lo usa para filtrar el `data` (`plugin.py:330-379`).
- El resultado contiene `operacion`, CUIT representado, `periodo_desde`, `periodo_hasta`, `tipo_reporte`, `archivo` y `archivos_internos`; subida agrega huella/tamaño y el ZIP a `files`. El portal puede emitir cinco familias de reportes con hojas de cálculo/texto SIFERE. No hay filas normalizadas por documento en `data` (`plugin.py:330-379`, `session.py:39-88`).
- El plugin no declara un contrato de éxito parcial: fallas de descarga/paquete/subida pueden dejar trabajo fallido aunque el portal ya haya generado los informes (`plugin.py:220-379`).

## Discrepancias observadas

1. **Manifest menos estricto que el modelo.** El JSON Schema solo marca operación como requerida mientras el modelo requiere CUIT representado, denominación y rango (`schema.py:62-120,130-151`). La familia central tampoco publica todos los campos: `services/central-api/src/central_api/api/bot_payloads.py:266-271` omite `usuario` y `pv`, aunque el modelo histórico de operación los incluye en `:503`.
2. **`incluir_json` no controla el JSON prometido.** Se valida y participa en el guard de salida, pero no suprime los datos que se ensamblan (`plugin.py:330-379`).
3. **Período excesivamente permisivo.** Se eliminan todos los caracteres no numéricos y se toman seis dígitos, no se verifica que la representación original sea una fecha prevista (`schema.py:50-59`).
4. **`pv` expresa implementación en un dato de entrada.** Cambia modalidad de presentación/registro de variante, sin cambiar período ni contenido solicitado (`schema.py:62-91`, registro alternativo citado arriba).
5. **No atomicidad ante errores remotos.** La sesión hace login/selección y puede generar archivos antes de fallar el paquete o la publicación (`plugin.py:297-379`, `session.py:174-603`).

## Pruebas consultadas

`services/bot-worker/tests/test_portal_retper_iibb_agip.py:25-69` prueba paquetes y bloqueo; el resto cubre sesión/consulta. `services/bot-worker/tests/test_agip_xvfb.py` prueba la variante alternativa. No se ejecutaron portales ni pruebas que requieran credenciales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/schema.py:20-151`
- `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/plugin.py:156-379`
- `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/session.py:39-88,153-170,174-216,357-484,486-603`
- `services/bot-worker/src/bot_worker/bots/retper_iibb_agip/retper_iibb_agip_bot_xvfb.py:1-36`
- `services/central-api/src/central_api/api/bots.py:413-438`
- `services/central-api/src/central_api/api/bot_compat.py:86-89`
- `services/central-api/src/central_api/security/credentials.py:74-103,200-210,227-263,303-370`
- `services/central-api/src/central_api/api/bot_payloads.py:266-271,503`
- `services/bot-worker/tests/test_portal_retper_iibb_agip.py:25-69`; `services/bot-worker/tests/test_agip_xvfb.py`

## Modo logger

Esta operación admite `logger: true` en la solicitud (ver [API §10](../api.md) y [Workers](../workers.md#modo-logger)): ejecuta el mismo plugin y guarda logs, HAR, capturas, HTML y descargas en `logger/{job_id}/`. No figura en la documentación pública.
