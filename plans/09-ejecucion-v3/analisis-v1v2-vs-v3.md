# Por qué los bots funcionaban en V1/V2 y fallaban en V3

Análisis comparativo del código de V1 (`/home/abp/Desktop/api/api-bots-mrbot`),
V2 (`/home/abp/Desktop/api/api-bots-mrbot-v2`) y el port V3
(`services/bot-worker/src/bot_worker/`), con la evidencia recogida en las
verificaciones del 2026-10-17 (local con credenciales reales, central de testing
en api2 y datos de producción).

## 1. La diferencia estructural

Los bots de V1/V2 se escribieron con semántica **best-effort**: cada paso que
puede fallar por el portal (login, apertura de servicio, selección de
representado, detección de un selector, descarga de una sección) **registra un
log y continúa**. El resultado del bot se compone de `error_logs` +
`warning_logs` y la fila termina en `success` aunque haya secciones fallidas
(se ven casos reales en las bases: `status='success'` con `error_message` "No se
encuentran resultados…", "Error en sección de Deudas: No se encontró la pestaña
Deudas", "No se pudo seleccionar el representado … Continuando").

El port V3 tradujo esa semántica a **fail-fast**: cada uno de esos pasos lanza
una excepción tipada (`TargetUnavailableError` con `diagnostic_code`) que
termina el job como `FALLIDO`. Es un contrato mejor (el cliente sabe qué pasó),
pero cualquier paso que en V1/V2 era tolerante se convirtió en una causa de
`target_unavailable`.

De ahí el síntoma "casi todos los bots dan FALLIDO target_unavailable": no es un
bot puntual, es la traducción de "tolerante" a "fatal" en pasos que el portal
de ARCA cumple de forma intermitente.

## 2. Divergencias concretas del código compartido (login/servicio)

| # | Paso | V1/V2 | V3 (antes) | Efecto observado |
|---|---|---|---|---|
| 1 | Espera de página | `wait_page_ready` espera `domcontentloaded` **y** `networkidle`, 12 s cada uno (`app/utils/arca_login.py:138-146`) | sólo `domcontentloaded` (`runtime/arca_login.py::_wait_ready`) | En un enlace lento (datacenter sin proxy) la búsqueda de la tarjeta del servicio o del selector de representado corría antes de que la SPA se hidratara → `arca_service_not_visible`, `represented_cuit_not_selectable`, `sct_frame_missing` intermitentes. |
| 2 | Apertura del servicio (popup) | Si `expect_popup` no abre: reintenta el clic (3 s) y, si tampoco, dispara `el.click()` por JS y usa la misma pestaña (`app/utils/arca_login.py:645-670`) | toleraba el timeout sin reintentar el clic y devolvía la página del portal | El job seguía sobre el portal de Clave Fiscal y fallaba más adelante con un diagnóstico engañoso. |
| 3 | Selección de representado | Si no hay selector: "Selector de representado no encontrado. Asumiendo cuenta única o ya seleccionada" y **continúa**; si el CUIT no está en las opciones: log y **continúa** | lanzaba `represented_cuit_not_selectable` | Reproducido con SCT: V1 descargó 6 reportes con la misma credencial y el V3 abortaba. En producción: `represented_cuit_not_selectable` en rcel (77), carga_portal_iva (65), libros_portal_iva (22), mis_retenciones (10), portal_iva (9), consulta_pagos_vep (9), sct (8). |
| 4 | Secciones / ítems individuales | Un error de sección o de comprobante agrega a `error_logs` y sigue con el resto | en varios portales el primer error de sección abortaba el job | V1/V2 devolvían parcial + `error_message`; V3 devolvía `FALLIDO`. |
| 5 | Selectores y esperas por portal | V1/V2 usaban listas de selectores amplias con `total_timeout_ms` de 8-20 s y fallbacks (`Ver todos`, `networkidle`) | el port conservó buena parte, pero con ventanas más cortas y sin la espera de red | Mismo efecto que (1). |

Los puntos 1, 2 y 3 ya están corregidos en el repo (commits `e2ce51c`, `0d329e8`);
el 4 es el criterio de `PARCIAL` que cada portal ya implementa y que se revisó
módulo por módulo.

## 3. Por qué además aparecen fallos con credencial válida

Además de las divergencias de código, hay tres causas de datos que V1/V2 también
sufrían pero que quedaban escondidas en `error_message` sin fallar el job:

1. **Cuenta sin el servicio habilitado.** Ejemplo real de la cuenta de prueba
   `20370074829`: el log V1 del 2026-03-15 dice "No se encuentra habilitado el
   servicio de SCT" y su catálogo no lista la mayoría de las tarjetas. V1 lo
   reportaba como error del bot; V3 lo reporta como `target_unavailable`.
2. **Representado sin relación vigente / nombre que no coincide.**
3. **Período inexistente para el representado.** Ejemplo: Portal IVA sólo ofrece
   `202610` para una de las cuentas probadas; pedir `202608` da
   `portal_iva_period_not_available` (en V1/V2 era "no hay período" y el bot
   fallaba igual).

## 4. Retransmisión del fallo: por qué V1/V2 "parecían" andar mejor

En V1/V2 el cliente recibía HTTP 200 con `error_message`; en V3 el job queda
`FALLIDO` con un error público. Por eso, comparando `status` de las tablas, V1/V2
parecen tener más éxito del que tenían: las filas `success` con
`error_message` poblado son, en realidad, corridas con secciones o pasos
fallidos.

A eso se suma que V3 marca terminal en el **primer** intento: `TargetUnavailableError`
declara `reintentable = True`, pero nadie lo consume (ni el worker reintenta ni
la central reencola; ver `central_api/internal/job_results.py`). Medido con
credenciales válidas contra la central de testing: `sifere` falló a los 287 s y
el intento siguiente terminó OK (455 s) con el mismo cuerpo; `sct` tuvo el mismo
patrón; `mis_facilidades` desde el cliente terminó con `ConnectTimeout`. En
producción los `FALLIDO` de estos diagnósticos tienen `attempts = 1` de
`max_attempts = 3`.

## 5. Correcciones aplicadas a partir de este análisis

| Divergencia | Corrección |
|---|---|
| 1 | `runtime/arca_login.py::_wait_ready` y nuevo `_esperar_pagina`: `domcontentloaded` + `networkidle` acotados (port de `wait_page_ready`). |
| 2 | `runtime/arca_login.py::open_service`: reintento del clic y `el.click()` por JS cuando el popup no abre. |
| 3 | `runtime/portals/base.py::seleccionar_representado`: sin selector presente se continúa (paridad V1); sólo falla con selector presente que excluya el CUIT. Portales que dependen de la selección (libros, vep) ahora la **confirman**. |
| 4 | Revisión por módulo (ver `verificacion-modulos-2026-10-17.md` y los informes `report_div_*`). |
| Retransmisión | Recomendación pendiente de decisión de producto: reencolar `TARGET_UNAVAILABLE`/`DEADLINE_EXCEEDED` mientras `attempts < max_attempts`. |

## 6. Pruebas de contraste (V1/V2 vs V3)

- **Por módulo, local**: los agentes de divergencia corren el logger V1/V2 y el
  plugin V3 con los mismos 3 juegos de credenciales (ver `report_div_*.md`).
- **V2 en el datacenter**: se levanta la V2 en api2 (`~/v2-api-bots-mrbot`, puerto
  5011) y se repiten los mismos casos desde el mismo host donde la V3 falló
  (ver `report_v1v2_api2.md`).
- **3 casos por endpoint modificado** contra la central de testing
  (`tools/local_run/e2e_testing.py`, cuerpos en `/tmp/e2e3/`).
