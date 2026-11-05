# plans/

> **Documentación histórica de diseño; el código manda.** La V3 ya tiene implementación (central, worker, contratos, infra). Estos planes conserva el diseño y su evolución; ante cualquier diferencia vale el código y los READMEs de `services/`, `packages/` e `infra/`.

Cada subcarpeta es el plan de una sección (`00-arquitectura` a `10-api-v1-shape`).
Los marcados "plan de diseño", "borrador" o "no crea commits" reflejan el momento
en que se escribieron. `09-ejecucion-v3/` contiene además verificaciones fechadas
y los nombres de archivos de prueba "propuestos" en `08-testing` no son la suite
real: esa vive en `services/*/tests/`, `packages/mrbot-contracts/tests/` y `tests/`.
