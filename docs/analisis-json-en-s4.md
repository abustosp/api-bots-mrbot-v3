# Análisis: sacar los JSON de resultado de la base y guardarlos en S4

Fecha: 2026-10-28. Solo análisis, no hay código cambiado.

## Pregunta

¿Se puede evitar que los dumps lleven los JSON de scraping y de resultados, guardarlos como
objetos en S4 (bucket `mrbot-json`, separados por módulo) y reconstruirlos al pedir el
resultado o desde el panel?

## Medición (copia de producción del 2026-10-28, base de 448 MB)

| Concepto | Tamaño |
|---|---|
| Tablas `bot_jobs_*` (32 tablas, heap + TOAST) | 234 MB |
| Índices de `bot_jobs_*` (GIN y otros, no entran al dump) | 78 MB |
| Resto de las tablas (heap + TOAST) | 75 MB |
| `job_results.payload` + `summary` | 24 MB |
| `jobs.request_payload` + `credential_metadata` | 17 MB |
| `job_events.payload` | 0,3 MB |

Por bot, `mis_comprobantes` concentra el peso: 10 MB de `job_results.payload` y 111 MB en total
para `bot_jobs_mis_comprobantes` (31.937 filas, incluidos sus índices; solo sus columnas JSON
suman unos 19 MB). Le siguen `rcel` (53 MB), `mis_facilidades` (38 MB), `sifere` (30 MB) y
`ccma` (17 MB). Estos tamaños por tabla incluyen índices, que no viajan en el dump.

El dump comprimido pesa unos 25 MB y descomprimido unos 200 MB.

## Hallazgos

1. **El JSON está duplicado.** `job_results.payload` se proyecta por trigger
   (`trg_job_results_bot_detail_sync`) a `bot_jobs_<bot>.response_payload`. El mismo dato vive
   dos veces en la base y por lo tanto dos veces en el dump.
2. **El mayor ahorro está en las tablas `bot_jobs_*`**, que son proyecciones derivadas.
   Excluirlas del dump y reconstruirlas desde `jobs` + `job_results` es la vía más barata.
   La migración 0015 trae un backfill que las llena, pero no lo probé como paso de restore.
3. **Los payloads individuales son chicos**: mediana 278 bytes, p99 2 KB y máximo 42 KB en
   `job_results`. Moverlos uno a uno a S4 (un objeto por job) tiene mucha sobrecarga: más de
   59.000 objetos pequeños, una petición HTTP por lectura y listados del panel con N consultas.
4. **Ya existe un patrón de objetos en S4**: `job_artifacts` guarda `object_key` y se firman URLs
   al pedirlos, y hay un bucket `logger` para los archivos del modo logger. No hay bucket
   `mrbot-json` hoy (hay que crearlo y darle permisos a `mrbot_user`).

## Lectores que habría que adaptar

- `api/jobs.py`: devuelve `data` desde `JobResult.payload` (resultado de un job).
- `api/bot_logs.py`: arma las vistas de logs desde `JobResult.payload` y `Job.request_payload`
  (lectura por lotes con `IN (...)`).
- `admin/jobs.py` y el panel: leen las tablas `bot_jobs_*` y las vistas `consulta_*_logs`.
- El constraint `jsonb_typeof(payload) = 'object'` y el índice GIN de `job_results`.

## Opciones

**A. Excluir `bot_jobs_*` del dump y reconstruirlas al restaurar.**
Bajo riesgo, sin cambios de runtime. El ahorro no está medido: hay que comparar el tamaño real
de los `.copy` de esas tablas antes de prometer una cifra. Los
JSON siguen en la base (`job_results`). No cumple "guardar fuera de la base".

**B. Mover `job_results.payload` (y `request_payload`) a S4 con un puntero en la base.**
Cumple el pedido. Cambios: escritura en `persist_result`, lectura en `api/jobs.py`,
`bot_logs.py`, panel y vistas legacy, y migración de los datos existentes. Riesgos: la
latencia y los fallos de S4 pasan a estar en el camino de lectura, se pierde el índice GIN
(búsquedas por contenido), y hay que garantizar atomicidad entre fila y objeto.
Recomendación de formato: un objeto por job en `mrbot-json/<modulo>/<aaaa>/<mm>/<job_id>.json`,
con escritura previa al commit y el puntero (`payload_key`, `payload_sha256`) en la fila.

**C. Híbrida (recomendada).** Guardar en la base solo el resumen y los campos pequeños, y mover
a S4 los payloads grandes por encima de un umbral (por ejemplo 4 KB), más `response_payload`
de `mis_comprobantes`. Es lo que más reduce con menos objetos, pero exige una lógica de
"en línea o en S4" en lectores y escritores.

## Lo que hay que decidir antes de construirlo

1. Si se quiere solo achicar el dump (opción A, rápida) o sacar los JSON de la base (B o C).
2. Si el JSON debe poder consultarse por contenido (índice GIN) o solo por job.
3. Retención de los objetos y quién puede leerlos.
4. Crear el bucket `mrbot-json` y otorgar permisos de lectura, escritura y borrado a `mrbot_user`.
   Hoy ese usuario no tiene `s3:DeleteObject` en el bucket de backups.
5. Qué pasa con los datos históricos (migración en lote o solo hacia adelante).
