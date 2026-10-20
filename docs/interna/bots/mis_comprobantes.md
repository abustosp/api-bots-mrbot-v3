# Auditoría interna: `mis_comprobantes`

## Catálogo, rutas y discrepancia de operación

El catálogo central anuncia `consulta`, `consultar`, `solicitar` e `historial` (`services/central-api/src/central_api/api/bots.py:L404-L405`). El plugin registra solo `consultar`, `solicitar`, `historial` (`services/bot-worker/src/bot_worker/bots/mis_comprobantes/plugin.py:L133-L165`; `schema.py:L202-L208`). La ruta histórica `/api/v3/mis_comprobantes/consulta` mapea correctamente a `consultar`, y `/solicitar_consulta`/`/historial` mapean a las otras (`central-api/api/bot_compat.py:L58-L60`). En cambio, la ruta genérica `/api/v3/bots/mis_comprobantes/consulta` se registra en catálogo y puede aceptar job, pero el validador de ejecución rechaza `consulta` como operación desconocida (`plugin.py:L193-L205`). Recomendar `consultar` hasta reconciliar catálogo y ejecutor.

## Flujo esquema → plugin → portal

1. **Entrada.** El esquema prohíbe extras (`services/bot-worker/src/bot_worker/bots/mis_comprobantes/schema.py:L65-L67`). `representado_cuit`, `representado_nombre`, fechas y rango se validan en `L83-L133`. El CUIT se limpia de separadores. `emitidos` y `recibidos` default `false`, pero al menos uno debe estar activo (`L136-L166`). Punto de venta se define como lista `int | str | None`, máximo 200, normalizada a texto para preservar ceros (`L41-L62`, `L145-L160`). `timeout_mc` opcional queda en 30 s por helper si es `None` (`schema.py:L37`, `services/bot-worker/src/bot_worker/bots/mis_comprobantes/plugin.py:L104-L107`).
2. **Plugin.** Las tres entradas se separan por operación (`schema.py:L188-L208`). Plugin exige credenciales, abre servicio, selecciona representado, luego ramifica: solicitud nueva, historial existente o descarga directa (`plugin.py:L193-L253`). `solicitar` devuelve IDs por tipo y no publica artifacts (`L255-L281`).
3. **Portal.** `runtime/portals/comprobantes.py:L1-L7`, `L101-L167` especifica que historial solo recorre consultas existentes y descarga su CSV, sin crear una consulta nueva. Las demás interacciones se efectúan desde la pantalla del servicio fiscal. La publicación normaliza/filtra CSV por rango y puede adjuntar cada tipo (`plugin.py:L283-L324`, `L326-L372`, `L374-L412`).

## Normalizaciones, outputs y casos parciales

`consultar` produce `operacion`, representado, rango y objeto por tipo con filas/archivo, hash/tamaño si subido y muestra si `incluir_json=true`. `solicitar` produce `ids_consulta`; historial produce `consultas`, `faltantes` y resúmenes descargados. Historial resulta `ERROR` si faltan todos los tipos pedidos, `PARCIAL` si faltan algunos y `OK` si están todos (`plugin.py:L415-L445`). `cookies_header` existe solo en el modelo historial, pero no se usa para autenticar. `carga_b64`, `proxy_request`, `eliminar_descargas` y `verbose` son incompatibles/deprecados o sin efecto; la API descarta controles de retención y proxy en la frontera (`central-api/api/bots.py:L541-L574`, `security/credentials.py:L277-L280`).

## Discrepancias y propuestas, no implementadas

- El catálogo V3 anuncia operación `consulta`, ausente de `ENTRADAS`; el alias histórico, por contraste, está bien mapeado (`central-api/api/bots.py:L404-L405`; `bot_compat.py:L58-L60`; schema `L202-L208`). **Propuesta:** quitar `consulta` del catálogo o convertirla en alias efectivo antes de encolar, y añadir prueba de integración de la ruta genérica.
- Modelo público central de `mis_comprobantes` hace `representado_nombre` opcional y puntos de venta `list[int]`, mientras validación de ejecución exige nombre y admite texto con ceros (`central-api/api/bot_payloads.py:L204-L212`; worker schema `L117-L123`, `L145-L160`). La representación V2 del catálogo es otra frontera con PV `list[str]` (`central-api/tests/test_bot_schemas.py:L92-L101`). **Propuesta:** unificar el DTO por ruta y declarar exactamente los requisitos de ejecución.
- `timeout_mc` sí es consumido por plugin pero no está en el modelo público compacto V3 (`bot_payloads.py:L204-L212`; worker schema `L145-L147`). **Propuesta:** publicarlo con rango/default real o retirarlo de ejecución pública.
- Normalizador de puntos acepta escalares antes de convertir a lista, pero el campo Pydantic se valida como lista antes del validador `mode="after"` (`schema.py:L41-L62`, `L145-L160`). **Propuesta:** quitar la rama escalar o mover normalización a etapa anterior; no documentar scalar como aceptado.
- Defaults de payload legado marcan `carga_minio=false`, `carga_json=false` para consulta, en tanto worker V3 usa ambos en `true` (`central-api/api/bot_payloads.py:L556-L577`; schema `L177-L185`). **Propuesta:** explicitar diferencias por endpoint y tests sobre body efectivo.
- Campos de credenciales en el catálogo aparecen como objeto genérico sin `required` (`central-api/api/bots.py:L820-L845`), aunque la ejecución exige credenciales (`plugin.py:L207-L214`). **Propuesta:** describir CUIT de inicio y clave requeridos y sus aliases.

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/mis_comprobantes/schema.py:L35-L239`
- `services/bot-worker/src/bot_worker/bots/mis_comprobantes/plugin.py:L133-L253`, `L255-L281`, `L283-L412`, `L415-L445`
- `services/bot-worker/src/bot_worker/runtime/portals/comprobantes.py:L1-L7`, `L101-L167`
- `services/central-api/src/central_api/api/bots.py:L404-L405`, `L541-L574`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L58-L60`
- `services/central-api/src/central_api/api/bot_payloads.py:L204-L212`, `L556-L577`
- Revisados: `services/bot-worker/tests/test_mis_comprobantes_operaciones.py`, `services/central-api/tests/test_bot_schemas.py:L92-L101`.

No se hicieron llamadas al portal ni se usaron cookies/credenciales.