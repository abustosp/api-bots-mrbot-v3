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
| 4 | Revisión por módulo (ver `verificacion-modulos-2026-10-17.md` y los informes `report_div_*`). Caso concreto portado: `sct/plugin.py` acumula el error de cada sección/formato en `data.errores`, sigue con las combinaciones restantes y devuelve `PARCIAL` si entregó al menos un reporte; sólo propaga el error cuando no descargó nada (idéntico al `success` de V1/V2 con `error_logs`). `sifere` y `mis_facilidades` ya devolvían `PARCIAL`. |
| Retransmisión | Recomendación pendiente de decisión de producto: reencolar `TARGET_UNAVAILABLE`/`DEADLINE_EXCEEDED` mientras `attempts < max_attempts`. |

## 6. Hallazgos por módulo (V1/V2 vs V3, con evidencia)

| Módulo | Divergencia encontrada | Veredicto |
|---|---|---|
| `sct` | ARCA a veces redirige el **popup** de SCT a su login. V1/V2 lo detectan (`bots_dev/sct/sct_bot_logger.py:159-194`), vuelven a autenticar en el mismo popup y reabren el servicio; el V3 lo pasaba a la preparación del iframe y moría con `sct_frame_missing`. | BUG corregido: `sct/plugin.py` + `runtime/portals/sct.py` (reautenticación en el popup y reapertura). Medido: V1 3/3, V2 3/3, V3 8/9 antes y 9/9 después. |
| `declaracion_en_linea` | Sin divergencia funcional: V1, V2 y V3 consultan mes a mes, toleran períodos sin DDJJ si hay al menos uno con datos y fallan sólo con el rango vacío. `representado_nombre` es etiqueta de salida en los tres, no clave de selección. | OK / DATOS. Los 1.376 fallos de producción se explican por el build de worker previo al port (`plugin_service_api_mismatch`) y por la capa ARCA de sincronización ya corregida. Medido con rango parcial 202606-202612: V1 4/7OK, V2 4/7OK, V3 4/7OK. |
| `mis_retenciones` | V1/V2 (`app/bot/mis_retenciones_bot.py::_ensure_relation`) devuelven un booleano y agregan "No se pudo seleccionar el representado … en Mis Retenciones" a `error_logs` y **continúan**; el portal V3 lanzaba `represented_cuit_not_selectable` (10 fallos recientes). | BUG corregido (paridad V1/V2): el portal devuelve `False` y el plugin deja el aviso. |
| `sifere` | V1/V2 expanden el catálogo (`Ver todos`) **antes** de esperar la tarjeta del servicio (`bots_dev/sifere/sifere_bot_logger.py:201-225`); el V3 la esperaba hasta 15 s y recién entonces expandía. En una SPA sin hidratar ese orden agota la búsqueda y deja la sesión en Clave Fiscal → `target_unavailable`/`arca_service_not_visible` (intermitente: 1.º intento falla, 2.º OK). | BUG corregido en `runtime/arca_login.py`: sondeo inmediato de la tarjeta, si no está se pulsa `Ver todos`, se espera el catálogo y se reintenta. Medido: 3 filas × 2 corridas V3 post-ajuste = 6/6 `OK` (102 artefactos, 0 errores) vs. el logger V2 3/3 `OK`. Sin fallo baseline reproducible, así que la muestra valida el flujo, no una tasa. |
| `mis_facilidades` | En V2 el fallo de un detalle no corta el job (`continue`) pero baja `success` a `False`; V3 devuelve `PARCIAL` con los planes y artefactos ya obtenidos (`plugin.py:212-228`). | PARIDAD aceptable: misma tolerancia, distinto nombre de estado. |

## 7. Estado de las pruebas de contraste

- `sct`: 3 filas × 3 corridas V3 + V1 + V2 (ver `report_div_sct.md`).
- `declaracion_en_linea`: 3 filas + rango parcial, V1/V2/V3 (ver `report_div_declaracion_en_linea.md`).
- `sifere`: 3 filas × 2 corridas V3 locales post-ajuste + 3 corridas del logger V2 (ver `report_div_sifere.md`) y 4 corridas E2E contra la central (`testing-fixes6`): 1 falló con `target_unavailable` a los 267 s y el reintento del mismo cuerpo terminó `PARCIAL` con datos; las otras dos terminaron `OK` (407 s y 408 s). Es la tercera medición del patrón transitorio-sin-reintento.
- `mis_facilidades`: en curso (`report_div_mis_facilidades.md`). Relevante: 1.951 fallos históricos no se reproducen con el build actual; el patrón dominante es `arca_service_not_visible` = DATOS.
- V2 en api2: servicio `api-bots-mrbot-v2` levantado y `/health` OK, pero **no** se pudo lanzar un job V2: la base solo guarda el verificador HMAC de la API key (irreversible) y `POST /api/v2/user/` requiere credenciales administrativas, deja la cuenta deshabilitada y manda la clave por correo. La comparación V1/V2 en vivo por HTTP queda pendiente de una API key en texto claro por un canal aprobado (ver `report_v1v2_api2.md`); la comparación con el código legacy se hizo ejecutando los loggers V1/V2 localmente.
- 3 casos por endpoint modificado contra la central de testing: `report_3casos_central.md`, `report_3casos_misret_rcel.md`, `report_3casos_libros_piva.md`.

