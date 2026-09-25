# `bots_dev` del worker V3

Cada plugin productivo registrado en `bot_worker.bots.registry.REGISTRY` tiene
una subcarpeta con `NOTA.md` y `payload_ejemplo.json`. La suite
`tests/test_bots_dev_fixtures.py` comprueba que la cobertura coincida con el
registro y que cada ejemplo pase `plugin.validate` sin abrir navegador, llamar
servicios externos ni ejecutar operaciones fiscales.

Estos ejemplos prueban el contrato de entrada, no la operación remota. Algunas
cargas, como `carga_portal_iva`, usan bytes ilustrativos y no son documentos
fiscales válidos para enviar al organismo.

## Diferencia con `bots_dev` legacy de V1 y V2

V1 y V2 tienen 46 carpetas cada uno y comparten los mismos nombres de primer
nivel. En la comparación de nombres y alias, 32 carpetas se relacionan con un
plugin productivo V3. Quedan 14 sin plugin V3 identificado:

- Herramientas o infraestructura: `arca_login`, `bot_builder`, `proxy_login_tester`, `shared`, `tests`.
- Proyectos no representados como plugin productivo: `beneficios_mipyme`, `carga_931`, `carga_libro_iva`, `cartilla_medica_union_personal`, `efectores_pami`, `habilitar_servicio`, `portal_iva_ddjj_y_libros`, `tucuman`, `xubio_sueldos`.

La coincidencia de nombres no demuestra paridad funcional. Los proyectos no
representados no se copian al registro de V3: requieren migración al contrato
`BotPlugin`, revisión de seguridad y pruebas propias antes de exponerse como
operaciones de la API.
