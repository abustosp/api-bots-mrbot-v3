# sifere - nota dev

Port V3 de `api-bots-mrbot-v2/app/bot/sifere_bot.py` (`bot_sifere`) y de V1
`api-bots-mrbot/app/bot/sifere_bot.py`. Esta carpeta solo guarda esta nota y
el ejemplo de payload. El código vive en:

- `src/bot_worker/bots/sifere/plugin.py`: operación `consultar`, orquesta el flujo.
- `src/bot_worker/bots/sifere/schema.py`: `SifereConsultarInput`, validación de entrada.
- `src/bot_worker/runtime/portals/sifere.py`: `SiferePortal` (`seleccionar_representado`, `consultar_sircar`, `consultar_consolidados`).
- `src/bot_worker/runtime/arca_login.py`: apertura del servicio ARCA y regla https de COMARB.

## Apertura del servicio

- El servicio se busca por patrón sobre el título recortado. El portal corta el título de la tarjeta con "..." (termina en "SIFERE WEB - Cons..."), por eso el nombre completo nunca coincide.
- `ArcaSession._PATRONES_SERVICIO_RECORTADO["sifere"]` acepta ese prefijo visible y rechaza la tarjeta "SIFERE WEB - DDJJ". `open_service(..., portal="sifere")` lo pasa a `_find_service`.
- La regla https de COMARB (`_asegurar_route_https_comarb`) se instala después de ubicar la tarjeta y antes del clic. Si se instalaba antes, el catálogo de Clave Fiscal quedaba con 0 enlaces en vivo.
- La regla es idempotente por sesión y solo intercepta `**://app1.comarb.gob.ar/**` para pasar `http://` a `https://`.
- Ambos cambios se probaron en vivo contra ARCA el 09/10/2026. Cobertura: `tests/test_arca_login_servicio_sifere.py`.
- Autorrepresentación sin otros representados: COMARB entra directo a `mainMenu.do` con "X (CUIT) representando a X (CUIT)" y no muestra `#selectedCuit`. Si el CUIT del encabezado coincide con el pedido, `seleccionar_representado` lo da por elegido. Probado en vivo el 13/10/2026 (V1, V2 y V3 tienen el mismo fix).

## Operación `consultar`

- `representado_cuit` (alias `cuit`, `cuit_representado`): 11 dígitos. Se quitan guiones, puntos, barras y espacios.
- `periodo`: AAAAMM con mes 01 a 12.
- `representado_nombre` (alias `representado`): opcional. Si falta, el nombre de archivo usa el CUIT.
- `jurisdicciones`: códigos del padrón 901 a 924. Los valores fuera de ese rango se descartan en silencio. `null` consulta las 24. Una lista vacía `[]` no consulta SIRCAR por jurisdicción.
- `consolidados` (por defecto `true`): SIRCREB y SIRTAC del periodo.
- `subir` (alias `carga_minio`, por defecto `true`): sube los archivos como artefactos del sobre.
- `incluir_json` (por defecto `true`): agrega el resumen en `datos`.
- `incluir_json` y `subir` no pueden estar ambos en `false`.
- `operacion` se quita antes de validar, en `SiferePlugin.validate`.
- `cuit_representante`, `clave_representante`, `clave_encriptada` y `proxy_request` son campos del sobre sellado. Se descartan antes de validar y nunca van en un payload de ejemplo.

## Salidas

- SIRCAR: un xlsx y un pdf por método (`retencion`, `percepcion`) y jurisdicción. Artefactos `sifere_sircar_xlsx_<metodo>_<jurisdiccion>` y `sifere_sircar_pdf_<metodo>_<jurisdiccion>`.
- Consolidados: xlsx de SIRCREB y SIRTAC. Las dos descargas de SIRCREB llegan como PDF y TXT (verificado en vivo, periodo 202401). Artefactos `sifere_consolidado_xlsx_<n>`.
- Sin pandas en el worker. El resumen por jurisdicción viaja como JSON plano y los archivos quedan como artefactos.
- Una descarga individual que falla no corta la corrida. El resultado queda `PARCIAL` en lugar de `OK`.

## payload_ejemplo.json

- Valida contra `SiferePlugin.validate` sin navegador ni subida (`subir: false`). Usa periodo 202401 y jurisdicciones 901 y 902.
- No contiene credenciales. Para revalidarlo desde `services/bot-worker`:

```
PYTHONPATH=src ../../.venv/bin/python -c "import asyncio, json; from bot_worker.bots.sifere.plugin import SiferePlugin; print(asyncio.run(SiferePlugin().validate(json.load(open('src/bot_worker/bots_dev/sifere/payload_ejemplo.json')))))"
```

## Pendiente y limitaciones

- `consultar_jurisdiccion` era un nombre obsoleto y ya no existe. Los métodos reales del portal son `seleccionar_representado`, `consultar_sircar` y `consultar_consolidados`.
- `SiferePlugin.configure()` acepta `sifere_servicio` del sobre, pero el patrón fijo de SIFERE tiene prioridad en `_find_service`. Un nombre de servicio distinto no se usa para ubicar la tarjeta. Queda como seguimiento en `arca_login.py`.
