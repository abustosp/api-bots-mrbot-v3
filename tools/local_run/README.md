# Pruebas locales de bot-worker (V3)

Herramientas para reproducir y diagnosticar los bots de la V3 desde la máquina
del operador, con credenciales reales de las bases V1/V2, sin la central ni el
sobre sellado.

| Archivo | Qué hace |
|---|---|
| `creds.py` | Busca en las bases SQLite V1/V2 el registro `success` más reciente por módulo y devuelve las credenciales fiscales usadas. |
| `run_bot.py` | Ejecuta el **plugin V3 real** (`REGISTRY`) contra el portal real con Chromium (headless), proxy opcional, y guarda evidencia. |

## Requisitos

```bash
.venv/bin/python --version          # 3.12 con bot_worker instalado (editable)
.venv/bin/python -m playwright install chromium   # ya presente en este host
```

## 1. Obtener credenciales

```bash
# Registros success más recientes de un módulo (claves enmascaradas)
.venv/bin/python tools/local_run/creds.py --module sct --limit 3

# Con las claves visibles (solo para correr la prueba)
.venv/bin/python tools/local_run/creds.py --module sct --limit 3 --show-secrets --json

# Filtrar por CUIT o ir directo a una tabla
.venv/bin/python tools/local_run/creds.py --module sifere --cuit 20374730429
.venv/bin/python tools/local_run/creds.py --table consulta_vep_logs --where "medio_pago = 'consulta_pagos_vep'"
```

Bases buscadas: `/home/abp/Desktop/api/DBs` y
`/home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/BD-api-v1`
(configurable con `MRBOT_DB_DIRS=dir1:dir2`).

## 2. Ejecutar un bot

```bash
# Bot completo (valida + navega + produce artefactos en --out/artifacts)
.venv/bin/python tools/local_run/run_bot.py \
  --bot sct --operacion consultar \
  --cuit-representante 20296066916 --clave 'Felipe2026' \
  --representado 33718421549 \
  --payload-json '{"representado_cuit":"33718421549","secciones":["deudas"],"formatos":["pdf"]}' \
  --proxy off --out /tmp/local_run/sct

# Solo login + catálogo de servicios de la cuenta (diagnóstico de
# "servicio no habilitado"/arca_service_not_visible)
.venv/bin/python tools/local_run/run_bot.py --mode login \
  --cuit-representante 20296066916 --clave 'Felipe2026' \
  --proxy on --out /tmp/local_run/login

# Login + abrir el servicio del bot + seleccionar representado
.venv/bin/python tools/local_run/run_bot.py --mode portal \
  --bot libros_portal_iva --operacion descargar \
  --cuit-representante ... --clave '...' --representado ... \
  --proxy off --out /tmp/local_run/portal_libros
```

Salida (en `--out` y en stdout como JSON):

- `report.json`: `outcome` (`ejecutado`/`excepcion`/`login_ok`/`portal_ok`),
  `result` (`OK`/`PARCIAL`/`ERROR`), `category` y `diagnostic_code` cuando falla,
  `data`, `warnings`, `artifacts`, `eventos` y `paginas`.
- `progress.log`: eventos de progreso del plugin.
- `final.png` / `final.html` / `final.url.txt`: pantalla al cerrar la sesión.
- `catalog.txt`: servicios visibles de la cuenta (modo login).
- `artifacts/`: archivos que el bot habría subido (copiados del `work_dir`).

Códigos de salida: `0` OK, `1` excepción (con `diagnostic_code`), `3` el bot
terminó con `result=ERROR`.

## Payload por módulo

El payload de ejemplo de cada bot está en
`services/bot-worker/src/bot_worker/bots_dev/{bot}/payload_ejemplo.json` y su
nota en `NOTA.md`. Los campos de negocio (período, desde/hasta, secciones,
formatos, impuestos) suelen salir del registro de la base usado como fuente.

## Cómo interpretar un fallo

| `diagnostic_code` | Significado | Accionable |
|---|---|---|
| `arca_login_credentials_rejected` | La clave del registro ya no sirve | Probar otro registro |
| `arca_service_not_visible` | La cuenta no tiene el servicio habilitado o el catálogo cambió | Comparar con `--mode login` |
| `represented_cuit_not_selectable` / `represented_name_not_selectable` | El representado no está en el selector del portal | Revisar la relación o el selector del portal |
| `portal_iva_period_selector_missing` / `*_period_not_found` | El período pedido no existe en el portal | Probar otro período |
| `target_unavailable_unclassified` | Falla no clasificada: mirar `traceback` y evidencia | Sí (bug del portal) |

Si el mismo módulo funciona con credenciales de la base y falla sólo con la
cuenta de prueba, la causa es de datos (servicio no habilitado / relación
inexistente), no del código.
