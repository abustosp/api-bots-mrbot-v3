# Auditoría interna: `moa`

## Contrato y ruta

Catálogo central: `consultar` (`services/central-api/src/central_api/api/bots.py:L409`). Alias `/api/v3/moa/consulta` mapea a `consultar` (`central-api/api/bot_compat.py:L97`). La ruta genérica recibe sobre con `payload`/`credentials` (`central-api/api/bots.py:L466-L513`, `L748-L817`).

## Flujo esquema → plugin → portal

1. **Entrada.** Modelo estricto frente a extras (`services/bot-worker/src/bot_worker/bots/moa/schema.py:L30-L31`). CUIT obligatorio admite alias `cuit` y limpieza de separadores. `despachos` lista de 1 a 100; miembros se convierten a texto, recortan, limitan a 64 caracteres, rechazan vacío y deduplican preservando orden (`L34-L45`, `L48-L94`). Defaults: `tipo_agente="IMEX-IMEX-IMPORTADOR/EXPORT."`, `rol="IMEX-Rol Importador Exportador"`, `metodo="url"`, `incluir_json=true`, `subir_csv=true`. Se exige salida JSON o CSV (`L51-L94`).
2. **Plugin.** Configuración puede presetear agente, rol, método y salidas, usando solo campos ausentes del payload (`services/bot-worker/src/bot_worker/bots/moa/plugin.py:L154-L181`). Con credenciales inicia sesión, abre MOA y delega consulta (`L183-L217`).
3. **Portal.** `runtime/portals/moa.py:L23-L46` navega el árbol de empresa con CUIT, tipo de agente y rol. `L95-L176` abre cada despacho por URL/form y extrae carátula/estado. El método elegido se pasa por despacho.

## Normalizaciones y outputs

Se genera CSV con una fila por despacho y columnas aplanadas `caratula_*` y `estado_*`; columnas estables incluyen `despacho`, `metodo`, `timestamp`, `error` (`plugin.py:L101-L115`, `L245-L300`, `L302-L316`). Un fallo por despacho se acumula en `errores` y en la fila, y el loop continúa. `incluir_json=false` limpia `data.datos` pero no contadores/errores/CSV. `subir_csv=false` conserva el resumen CSV sin añadir artefacto. El estado global es siempre `OK` al completar el bucle, incluso con `despachos_con_error>0` (`plugin.py:L183-L217`, `L252-L300`).

## Discrepancias observadas

- El modelo central no declara defaults para `tipo_agente` y `rol`, mientras el validador efectivo tiene defaults V2 específicos (`services/central-api/src/central_api/api/bot_payloads.py:L236-L243`; worker `schema.py:L26-L28`, `L51-L65`).
- Errores parciales por despacho no degradan el status `OK` (`plugin.py:L252-L300`).
- El campo `despachos` público solo tipa como lista texto, mientras la validación efectiva stringifica elementos de lista de cualquier tipo antes de validar (`central-api/api/bot_payloads.py:L236-L243`; schema `L67-L86`).
- El modelo central de credenciales es genérico sin propiedades requeridas (`central-api/api/bots.py:L820-L845`), mientras `execute` exige credenciales fiscales (`plugin.py:L183-L190`).

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/moa/schema.py:L30-L124`
- `services/bot-worker/src/bot_worker/bots/moa/plugin.py:L101-L145`, `L154-L217`, `L219-L316`
- `services/bot-worker/src/bot_worker/runtime/portals/moa.py:L23-L46`, `L95-L176`
- `services/central-api/src/central_api/api/bots.py:L409`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L97`
- `services/central-api/src/central_api/api/bot_payloads.py:L236-L243`
- Revisados: `services/bot-worker/tests/test_portal_moa.py` (pruebas sin acceso real) y contrato público.

No se ejecutaron consultas MOA reales ni se usaron credenciales.
