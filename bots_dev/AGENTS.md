# AGENTS.md

## Objetivo
Este repositorio debe mantener bots Playwright y python (en algunos procesos se pueden sumar bs4, pandas, Openpyxl y requests) en dos versiones principales por cada proceso, ubicadas en una carpeta plana por bot:

1. `{bot}/{bot}_bot_logger.py` (o `{bot}/bot_{x}_logger.py` si ese patrón ya está definido para ese módulo): versión de diagnóstico y trazabilidad completa.
2. `{bot}/{bot}_bot.py` (o `{bot}/bot_{x}.py`): versión limpia para ejecución normal. **Canónica**: sufijo `_bot.py`. **Es la semilla del bot de producción en `app/bot/`**: incluye ya la integración MinIO/S3 (`subir_archivo_a_minio`, env `MINIO_*`, parámetro `carga_minio`) y `tempfile` para descargas en directorios temporales. Los nombres `_clean.py` / `slim` son aliases históricos y no deben usarse para archivos nuevos.
3. (opcional) `{bot}/{bot}_bot_draft.py`: versión de desarrollo o pruebas, sin necesidad de cumplir con reglas de logging o estructura final. Suele ser el primer paso, utilizando playwright codegen para tener un script básico que luego se refactoriza a `{bot}_bot_logger.py` y posteriormente a `{bot}_bot.py`.

### Flujo de desarrollo draft → logger → clean → mrbot → mrbot (en API)
Todo bot nuevo se desarrolla en `bots_dev/` siguiendo este orden de etapas:

1. **draft**: exploración con playwright codegen, sin reglas.
2. **logger**: misma lógica + evidencias por paso (`temp/{bot}_{timestamp}/` con `descargas/`, `html/`, `screenshots/`, `har/`).
3. **clean**: misma lógica sin instrumentación, **ya con integración MinIO/S3 y tempfile** (paridad directa con producción).
4. **mrbot** (`{bot}_bot_mrbot.py`): **copia fiel de la versión clean con integración MinIO/tempfile intacta** (ver sección siguiente).
5. **mrbot (en API)**: se porta el archivo a `app/bot/{bot}_bot.py`, el executor en `app/jobs/executors/` lo invoca pasando `carga_minio`, y la ruta en `app/api/routes/` encola el job. En este punto ya no queda nada por agregar, solo ajustar imports (`shared.arca_login` → `app.utils.arca_login`).

La migración final a la API debe ser una **copia casi literal** de la variante mrbot, no una reescritura: así el código probado en desarrollo es exactamente el que corre en producción.

### Regla de paridad mrbot ↔ app/bot (incluido MinIO/S3 y temp dirs)
La variante `{bot}_bot_mrbot.py` debe ser **réplica funcional de `app/bot/{bot}_bot.py`**, para que lo que se prueba en desarrollo sea lo mismo que corre en la API:

1. **Mantener la integración MinIO/S3 completa**: mismos env `MINIO_*`, mismo bucket por bot, mismas llamadas a `subir_archivo_a_minio` (o stub local) y parámetro `carga_minio` en la firma.
2. **Mantener el manejo de descargas en directorios temporales** (`tempfile.TemporaryDirectory`) igual que en producción. Si se prefiere bajar a una carpeta fija para pruebas locales, hacerlo con un guard explícito (ej. `TemporaryDirectory(dir="./descargas")` o `eliminar_descargas`), nunca reemplazando el objeto `temp_dir` por un `str`.
3. Al cambiar lógica de negocio, hacer el cambio primero en logger, luego en clean y luego en mrbot; verificar con `diff` contra `app/bot/` que solo difieren en imports y en el stub/adapter de MinIO.
4. Errores a evitar detectados en esta codebase: `temp_dir = str(DOWNLOAD_DIR)` seguido de `temp_dir.name` / `temp_dir.cleanup()` (rompe en runtime, ocurrió en 16 variantes mrbot y fue reparado). Imports locales usados por mrbot: `shared.arca_login` (resuelve con `PYTHONPATH=bots_dev`), `app.utils.browser_limiter` (limita concurrencia de Chromium vía `BROWSER_CONCURRENCY`, portado desde la v1), `app.utils.proxies`. En `app/bot/` la versión vigente del login es `app/utils/arca_login.py`.

Variantes extendidas (opcionales, cuando el bot lo requiera) también viven como archivos hermanos en la misma carpeta plana, distinguidas solo por sufijo en el filename (no en subcarpetas): `{bot}_bot_mrbot.py` (réplica de producción de la versión clean, con MinIO/tempfile intactos; ver `## Regla de paridad mrbot ↔ app/bot`), `{bot}_test_masivo.py` / `{bot}_bot_masivo.py`, `{bot}_bot_capmonster.py`, `{bot}_bot_v2.py`, `{bot}_bot_api.py`, `{bot}_bot_rapido.py`, etc. Ver `## Convención de nombres` para reglas de flat layout.

Las dos versiones principales deben compartir la misma lógica funcional (mismos pasos de negocio), cambiando solo el nivel de instrumentación.

## Entorno virtual
El entorno virtual del proyecto puede encontrarse en `./.venv/` o `./venv/`, segun la configuracion local.

## Particularidades ARCA (login y servicios)
Para bots que ingresan a ARCA/AFIP, se deben contemplar siempre estas situaciones:

1. `captcha`: luego de `Siguiente`, detectar captcha y registrar `"Captcha detectado"`.
2. `cambio de clave`: luego de `Ingresar`, si aparece `Cambiar Clave Fiscal`, cortar flujo con mensaje `"Se requiere cambiar la clave fiscal"`.
3. `servicio no habilitado`: al intentar abrir el servicio, si no existe el boton o no abre la pantalla del servicio, cortar flujo con mensaje `"No se encuentra habilitado el servicio de {nombre_servicio}"`.

Aplicacion por version:

1. Version `logger`: registrar estos eventos en log y guardar evidencia (screenshot/html) del estado.
2. Version limpia: misma validacion funcional y mismos mensajes de error, sin instrumentacion por paso.

## Convención de nombres
Para cada bot nuevo:

1. Elegir un nombre base `x` (ejemplo: `mis_facilidades`, `portal_iva`, `sifere`) y crear carpeta `x/` en la raíz.
2. Generar dentro de `x/` (layout plano):
   - `x/x_bot_logger.py` (o `x/bot_x_logger.py` si ese patrón ya está definido para ese módulo) — obligatoria.
   - `x/x_bot.py` (o `x/bot_x.py` en el mismo criterio) — obligatoria, versión limpia **canónica**. No usar `*_clean.py` ni `*slim*` para archivos nuevos (aliases históricos deprecados).
3. No mezclar convenciones dentro del mismo bot.
4. Layout plano obligatorio: cada módulo es una carpeta plana que **no debe contener subcarpetas de variantes**. Toda variante adicional vive como archivo hermano distinguida solo por sufijo en el filename (no en subdirectorios): `x_bot_draft.py`, `x_bot_mrbot.py` (réplica de producción de la versión clean, con MinIO/tempfile intactos; no importar de siblings del mismo bot), `x_test_masivo.py` / `x_bot_masivo.py`, `x_bot_capmonster.py`, `x_bot_v2.py`, `x_bot_api.py`, `x_bot_rapido.py`, etc. Variantes previamente anidadas (`xubio_sueldos/rapido/`, `srt/capmonster/`) deben plegarse al flat.
5. Únicas subcarpetas permitidas dentro de `x/`: `samples/` (datos de entrada, con `README.md` + `.gitignore` con `*` salvo esos dos archivos) y `__pycache__/`. Excepción: directorios de assets que el bot necesita en runtime (ej. `controladores_fiscales/archivos-controladores-fiscales/` con `.pem`) están exentos — la regla apunta a subcarpetas de variantes de código.

## Estructura obligatoria para versión logger
La versión `logger` debe crear una carpeta por ejecución:

- `./temp/{bot}_{timestamp}/` — **canónica**

Formato sugerido de timestamp: `YYYYMMDD_HHMMSS`. El prefijo es el nombre base del bot (`{bot}`), no `run_`.

Dentro deben existir exactamente estas subcarpetas (nombres en minúsculas):

1. `descargas/`: archivos descargados y artefactos scrappeados (ejemplo: tablas exportadas a Excel/CSV).
2. `html/`: HTML guardado por paso para debugging.
3. `screenshots/`: captura visual por paso.
4. `har/`: tráfico de red para análisis detallado.

> Patrones deprecados (no usar en código nuevo; si se encuentran en bots legacy, migrar): `./temp/run_<timestamp>/logs/`, `./temp/run_<timestamp>/html/`, `temp/<run>/port_<port>/{screenshots,html,har}/` o `logs/` como alias de `screenshots/`. No crear subdirectorio intermedio `port_*`; HAR/screenshots/html van directo en `./temp/{bot}_{timestamp}/{har,screenshots,html}/` como arriba. Si un bot necesita distinguir por puerto/paralelismo, hacerlo por sufijo en el filename, no por subcarpeta intermedia.

## Lineamientos de logging por paso (logger)
Cada paso relevante del flujo debe:

1. Registrar inicio y fin del paso.
2. Guardar screenshot.
3. Guardar HTML del estado actual.
4. Registrar URL actual y resultado del paso (OK/ERROR).
5. En caso de error, guardar evidencia adicional antes de propagar o manejar excepción.

Convención sugerida de archivos por paso:

- `001_login_inicio.png`
- `001_login_inicio.html`
- `002_busqueda_cuit.png`
- `002_busqueda_cuit.html`

Usar prefijo numérico incremental para reconstruir la secuencia.

## Reglas para HAR (logger)
El HAR debe ser lo más detallado posible para diagnosticar:

1. performance y tiempos de carga,
2. solicitudes/respuestas de red,
3. headers,
4. cookies,
5. recursos fallidos o bloqueados.

Buenas prácticas:

1. Grabar HAR desde la creación del contexto de browser.
2. Cerrar contexto/browser correctamente para asegurar flush del HAR a disco.
3. Guardar el HAR en `./temp/{bot}_{timestamp}/har/`.

## Manejo de descargas
En versión `logger`:

1. Toda descarga debe guardarse en `./temp/{bot}_{timestamp}/descargas/`.
2. Nombrar archivos de forma determinística cuando sea posible.
3. Si se scrapea una tabla y se exporta, ese archivo también va en `descargas/`.

En versión limpia:

1. Toda descarga debe guardarse en `./descargas/`.
2. No generar estructura de evidencias por paso (`html`, `screenshots`, `har`).

## Reglas de la versión limpia (`bot_x.py`)
La versión limpia debe:

1. Ejecutar el mismo flujo funcional que `bot_x_logger.py`.
2. Omitir capturas por paso, HTML por paso y HAR.
3. Mantener solo logs mínimos de ejecución (inicio, fin, errores críticos).
4. Dejar descargas únicamente en `./descargas/`.

## Paridad entre ambas versiones
Al modificar un bot existente:

1. Primero ajustar la lógica en `bot_x_logger.py`.
2. Replicar la misma lógica en `bot_x.py` quitando solo instrumentación.
3. Verificar que entradas/salidas y comportamiento funcional coincidan.

No se permite que la versión limpia tenga reglas de negocio distintas a la versión logger. Variantes extendidas (`*_mrbot.py`, `*_masivo.py`, `*_capmonster.py`, etc.) no necesitan paridad entre sí, pero cada una debe ser self-contained y respetar `## Manejo de descargas`, `## Convención de nombres` y, para `*_mrbot.py`, la `## Regla de paridad mrbot ↔ app/bot` (no importar de siblings del mismo bot).

## Checklist de entrega para bots nuevos
Antes de considerar listo un bot:

1. Existen los dos archivos principales en la carpeta plana del bot (`{bot}/{bot}_bot_logger.py` y `{bot}/{bot}_bot.py` — sufijo `_bot.py` canónico; no `*_clean.py`/`slim`).
2. La versión logger crea `./temp/{bot}_{timestamp}/` con `descargas`, `html`, `screenshots`, `har` (nombres en minúsculas; prefijo `{bot}_`, no `run_`).
3. Se generan evidencias por paso en logger (screenshot + HTML con prefijo numérico).
4. La versión limpia descarga en `./descargas/` sin generar `html`/`screenshots`/`har`.
5. Ambos scripts ejecutan el mismo flujo funcional.
6. (si aplica) Variantes extendidas viven como archivos hermanos en la misma carpeta plana (`{bot}_bot_mrbot.py`, `{bot}_bot_capmonster.py`, etc.), sin subcarpetas de variantes. Solo `samples/` y `__pycache__/` permitidas como subdirectorios.
7. (si aplica) La variante `{bot}_bot_mrbot.py` es diff de `app/bot/{bot}_bot.py` solo en imports/stubs; incluye MinIO/S3 y tempfile igual que producción.
