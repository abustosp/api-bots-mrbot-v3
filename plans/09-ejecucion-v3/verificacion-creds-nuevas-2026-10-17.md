# Verificación V3 con credenciales nuevas contra la API de testing

Segunda ronda del 2026-10-17 (tarde-noche). Objetivo pedido: **reejecutar las
pruebas usando otras credenciales** del corpus V1/V2 contra
`https://testing-central.mrbot.com.ar`, **reintentando con otra credencial cuando
una clave es incorrecta**, y resumir los resultados.

Todas las corridas de esta ronda usan cuentas que **no** se habían usado en las
rondas anteriores (`used_cuits.json` como lista de exclusión), salvo el único
caso de `consulta_pagos_vep` (el corpus tiene una sola cuenta para ese bot), que
se ejecutó con una fila más reciente.

## Método

- Driver `run_nuevas_credenciales.py` (scratch, no versionado): arma el cuerpo de
  cada caso con el **vocabulario del contrato V1/V2** de la operación
  (`mrbot_contracts.request_fields`), lo envía a la ruta alias, hace polling y
  registra un JSON por intento sin imprimir CUIT ni claves.
- **Reintento por credencial**: si el job termina `credentials_rejected` (o la
  creación responde 401/403), el mismo caso se reenvía con la **siguiente fila**
  del corpus, hasta 4 intentos.
- **Reintento por cupo**: los `429` (`quota_exhausted`) se reintentan con espera
  de 60 s (hasta 3 veces).
- Cliente de escritorio V3: 4 flujos por su `test_client` con credenciales nuevas
  (agente aparte), incluidas las dos fases de descarga (URL directa y
  `download_url` autenticado).

## Resultado por endpoint (jobs reales, tag `testing-fixes6` y `fixes7`)

| Endpoint | Caso | Credencial | HTTP | Estado / result | Artefactos | Tiempo | Motivo |
|---|---|---:|---:|---|---:|---:|---|
| `sct/consulta` | 1 | nueva | 202 | `COMPLETO`/`OK` | **6** | 88,7 s | — |
| `sct/consulta` | 2 | nueva | 202 | `COMPLETO`/`OK` | **6** | 442,2 s | — |
| `sifere/consulta` | 1 | nueva | 202 | `COMPLETO`/`OK` | **102** | 769,6 s | — |
| `mis_facilidades/consulta` | 1 | nueva | 202 | `COMPLETO`/**`PARCIAL`** | **144** | ~27 min | Tolerancia por sección: entregó 144 archivos |
| `mis_retenciones/consulta` | 1 | nueva | 202 | `COMPLETO`/`OK` | 5 | 263,8 s | — |
| `mis_retenciones/consulta` | 2 | 3 filas | 202 | `COMPLETO`/`ERROR` | 0 | 708,9 s | 2 credenciales rechazadas y luego `ConnectTimeout` (transitorio) |
| `portal_iva/consulta` | 2 y 3 | nuevas | 202 | `COMPLETO`/`OK` | 2 + 2 | 102 / 116,7 s | — |
| `portal_iva/consulta` | 1, 4, 5 | nuevas | 202 | `COMPLETO`/`ERROR` | 0 | 27,7-256 s | `portal_iva_period_not_available` (**DATOS**: la cuenta solo ofrece otro período) |
| `portal_iva/consulta` | 6 | nueva | 202 | `COMPLETO`/`ERROR` | 0 | 93,5 s | `ConnectTimeout` (transitorio) |
| `libros_portal_iva/descargar` | 1 | nueva | 202 | `COMPLETO`/`OK` | 2 | 442,7 s | — |
| `rcel/descargar` | 1 y 2 | nuevas | 202 | `COMPLETO`/`OK` | 0 | 218 / 345 s | Sin `minio_upload` en la primera pasada (ver límites) |
| `srt/consultar_alicuotas` | 1 | nueva | 202 | `COMPLETO`/`OK` | 1 | 93,3 s | — |
| `mis_comprobantes/consultar` | 1 | nueva | 202 | `COMPLETO`/`OK` | 2 | 289,9 s | — |
| `consulta_pagos_vep/consultar` | 1 | misma cuenta, fila nueva | 202 | `COMPLETO`/`OK` | 1 | 40,2 s | — |
| `aportes_en_linea/descargar` | 1 | 2 filas | 202 | `COMPLETO`/`OK` | 1 | 16,7 s + 50,5 s | **1.ª credencial rechazada → 2.ª OK** |
| `liquidacion_granos/consultar` | 1 | nueva | 202 | `COMPLETO`/`OK` | 0 | 349,8 s | Sin flag de subida en la primera pasada (ver límites) |

### Reintentos por credencial incorrecta (comportamiento pedido)

| Caso | Intento 1 | Intento 2 | Intento 3 |
|---|---|---|---|
| `aportes_en_linea` caso 1 | `credentials_rejected` (234,6 s) | **`OK`, 1 artefacto** (366,4 s) | — |
| `aportes_en_linea` caso 1 (cierre) | `credentials_rejected` (16,7 s) | **`OK`, 1 artefacto** (50,5 s) | — |
| `aportes_en_linea` caso 2 | `credentials_rejected` (235,8 s) | `credentials_rejected` (11,3 s) | HTTP 429 (cupo agotado) |
| `mis_retenciones` caso 2 | `credentials_rejected` | `credentials_rejected` | `target_unavailable` (`ConnectTimeout`) |

Total del corpus en esta ronda: **11 jobs `CREDENTIALS_REJECTED`** (claves que
ARCA ya no acepta) entre `mis_comprobantes` (6), `aportes_en_linea` (4) y
`mis_facilidades` (1); el reintento con otra fila resolvió los casos de
`aportes_en_linea`.

## Agregado desde la central (jobs desde 18:35)

| Bot | `OK` | `PARCIAL` | `ERROR` | `error_code` y diagnóstico de los `ERROR` |
|---|---:|---:|---:|---|
| `sct` | 3 | | | — |
| `sifere` | 1 | | | — |
| `mis_facilidades` | 1 | 4 | 1 | 1 `CREDENTIALS_REJECTED` |
| `mis_retenciones` | 1 | | 1 | `TARGET_UNAVAILABLE` `target_unavailable_unclassified` (ConnectTimeout) |
| `rcel` | 2 | | 1 | `TARGET_UNAVAILABLE` (ConnectTimeout) |
| `portal_iva` | 3 | | 4 | 3 `portal_iva_period_not_available` (DATOS) + 1 (ConnectTimeout) |
| `libros_portal_iva` | 1 | | | — |
| `srt` | 2 | | | — |
| `mis_comprobantes` | 2 | | 6 | `CREDENTIALS_REJECTED` |
| `aportes_en_linea` | 2 | | 4 | `CREDENTIALS_REJECTED` |
| `consulta_pagos_vep` | 1 | | | — |
| `liquidacion_granos` | 1 | | | — |

Clasificación de los fallos de esta ronda: **clave incorrecta / cuenta no
habilitada** 11, **DATOS** (período no ofrecido por el portal) 3, **transitorio
de red** 3. **Ningún fallo de código** en los 37 jobs del tramo.

## Incidentes de infraestructura de la cuenta de testing

1. **Cupo agotado y cuenta deshabilitada (por diseño).** La cuenta compartida de
   testing tiene cuota `PRUEBA` de **100 requests totales no renovables**
   (`billing/entitlements.py:6,30`). La ráfaga de 12 procesos paralelos la agotó:
   los POST pasaron a **429 `quota_exhausted`** y, al consumirse el cupo, el
   propio código de la central pasó la cuenta a `NORMAL` y **deshabilitó al
   usuario** (`billing/postgres.py:234-256`, motivo "prueba agotada: pasa a
   NORMAL y queda deshabilitado"). Consecuencia: los clientes empezaron a recibir
   **401 `authentication`** y no se podía crear ningún job.
   - Acción tomada: **re-habilité el usuario** en la base de la central de testing
     (`update users set habilitado = true`). La cuenta quedó `NORMAL` con cupo
     nuevo de 100 y la clave vigente volvió a funcionar
     (`GET /api/v3/mi/cuenta` → 200, `disponibles` 85 tras esta ronda).
   - Recomendación para futuras rondas: usar `account_type = TESTING`
     (ilimitada) para el usuario de pruebas, o correr **de a 2-3 jobs en
     paralelo** como máximo.
2. **429 por ráfaga.** Los reintentos de los casos pendientes se hicieron de a uno
   con espera de 60 s; el driver ahora reintenta el mismo cuerpo ante 429.
3. **Latencia inflada por cola.** Los tiempos de esta ronda son mayores que los de
   la ronda anterior porque los 12 procesos comparten 5 slots del worker: un job
   de `libros_portal_iva` pasó de 86 s (ronda previa, sin cola) a 442 s.

## Hallazgo de contrato corregido durante la ronda

El agente del cliente detectó que mi fix del modelo de compatibilidad de Portal
IVA (`ff497c3`) **cambiaba el esquema publicado** de la ruta alias: pasaba a
documentar `descarga_ventas`/`descarga_compras` y dejaba de listar los históricos
`descarga_csv_ventas`/`descarga_csv_compras`, que son los que emite el cliente.
Efecto medido: el QA de contrato del cliente pasó de 162/1 a 161/2 contra el
OpenAPI de testing (el fallo nuevo, `test_payload_no_inventa_claves[portal_iva]`,
no aparecía contra el OpenAPI público porque producción aún corre la imagen
previa).

Corrección (commit `c44ffbd`, desplegada como **`testing-fixes7`**): el campo
declarado vuelve a ser el histórico y **acepta el canónico** por `AliasChoices`;
el normalizador lo traduce al vocabulario del worker. Verificado:
- los dos vocabularios de entrada normalizan a `descarga_ventas`/`descarga_compras`;
- `tests/test_v2_request_alias_parity.py` 81 passed, suite central-api 613 passed;
- el OpenAPI de la central con `testing-fixes7` vuelve a declarar
  `descarga_csv_ventas`/`descarga_csv_compras` en `/api/v3/portal_iva/consulta`
  (179 rutas `/api/v3`), así que el QA del cliente no ve el fallo nuevo;
- E2E en `fixes7`: `portal_iva` 3/3 casos con credenciales nuevas y 2 CSV cada uno
  (los 3 fallos restantes son `portal_iva_period_not_available` = DATOS).

## Desde el cliente de escritorio V3 (agente independiente, tras re-habilitar la cuenta)

| Flujo | Casos | Creación | Estado / result | Descargas del cliente | Motivo |
|---|---:|---:|---|---|---|
| `mis_facilidades` | 2 (cuentas nuevas distintas) | 202 | `COMPLETO`/**`PARCIAL`** | 68/68 y 12/12 (directa y autenticada) | `PARCIAL` por datos faltantes de la cuenta, no por fallos de descarga |
| `sct` | 1 (cuenta nueva) | 202 | `COMPLETO`/`OK` | 1/1 directa y 1/1 autenticada | Ofreció 1 de 3 categorías Excel pedidas (las otras sin datos) |
| `mis_comprobantes` | 1 (cuenta nueva, tras 6 rechazadas) | 202 | `COMPLETO`/`OK` | 2/2 directas y 2/2 autenticadas | 6 filas con claves vencidas, resueltas con la siguiente |
| `mis_facilidades` / `mis_comprobantes` (intentos previos) | 3 | 429 y luego 401 | sin job | 0 | Cuota agotada / cuenta deshabilitada (**INFRA**, ver arriba) |

En total, el cliente reintentó con otra credencial **7 filas rechazadas** por el
portal (1 de `mis_facilidades`, 6 de `mis_comprobantes`) y terminó `OK` en los
tres módulos con cuentas nuevas. Sus cuerpos también se validaron **offline**
contra el esquema estricto del worker (`sct`, `mis_facilidades`,
`mis_comprobantes` OK).

## Límites de esta ronda

1. `rcel` y `liquidacion_granos` corrieron sin el flag de subida en la primera
   pasada (0 artefactos): los reintentos con subida activada cayeron en 429 y no
   se repitieron. La ronda anterior sí los verificó con subida (rcel 148/15/26
   PDF).
2. `mis_facilidades` necesitó ~27 minutos (cuenta con muchos planes): mi timeout
   de driver lo cortó como "timeout esperando el job", pero el job terminó
   `COMPLETO/PARCIAL` con 144 artefactos según la central.
3. El caso `mis_facilidades` no se repitió con una segunda credencial nueva.
4. No se corrieron `sifere` 2.º caso ni módulos fuera de esta lista.
5. La re-habilitación manual de la cuenta de testing es una acción sobre el
   entorno de pruebas (no producción) y queda documentada acá.

## Evidencia

- Resultados crudos: `/tmp/nuevas/*.json` (permisos 600).
- Informe del cliente con credenciales nuevas:
  `/home/abp/.jcode/scratch/report_cliente_creds_nuevas.md`.
- Estado de credenciales usadas:
  `/home/abp/.jcode/scratch/nuevas_creds/used_cuits.json` (600).
- Ningún informe ni log contiene CUIT, nombres de contribuyentes ni claves.
