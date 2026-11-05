# Plan 04: Cuentas y cupo de requests
> **Estado:** reescrito para reflejar el modelo implementado. El diseño original
> (con cobro automático) se descartó.
>
> **Ámbito:** `central-api` y PostgreSQL. El `bot-worker` no conoce cuentas,
> cupos ni facturación.
>
> **Fuente de verdad:** el código (`services/central-api/src/central_api/admin/subscriptions.py`,
> `models/billing.py`) y `services/central-api/BILLING.md`. Este plan resume el
> modelo; ante cualquier diferencia vale el código.
---

## 1. Objetivo y alcance

Limitar cuántos requests (jobs) puede crear cada usuario, con una reserva
atómica al crear el job y una auditoría de cada asignación. Las cuentas se
administran **manualmente** desde el panel admin. Solo existen los tipos `PRUEBA`, `TESTING` y `NORMAL`, asignados a mano.

Resuelve R7 y R8 del plan raíz en su forma reducida: un cupo por usuario según
su tipo de cuenta. No hay cobro (ver `BILLING.md`).

Punto de partida V2: no tenía billing, solo `maximas_consultas_mensuales`,
`consultas_realizadas` y `fecha_ultimo_reset` en `users` (ver
`.research/04-auth-admin-config.md` §10).

### 1.1 Fuera de alcance

- Pagos, cobro, facturación fiscal.
- Cualquier lógica de cupo en el worker.
- Facturación por organización: el dueño del cupo es `user_id`.

## 2. Tipos de cuenta

Cada usuario tiene **un** tipo de cuenta, asignado por un administrador.

| Tipo | Cupo | Renovación |
|---|---|---|
| `PRUEBA` | 100 requests en total | No renovable. Es el tipo por defecto de todo usuario nuevo |
| `TESTING` | Ilimitado | No aplica. El uso se sigue contando |
| `NORMAL` | `max_requests` por mes | Mensual y automática, mes calendario en hora de Argentina (UTC-3) |

Al consumirse el request 100 de una cuenta `PRUEBA`, la cuenta pasa a `NORMAL`
(`max_requests = 100`, contadores en cero), el usuario queda deshabilitado
(`users.habilitado = false`) y se registra `billing.prueba.finished`. Las API
keys no se revocan: al habilitar al usuario recupera el acceso.

## 3. Modelo de datos

Tablas vigentes (`models/billing.py`):

| Tabla | Rol |
|---|---|
| `billing_accounts` | Una fila por usuario: `account_type`, `max_requests`, `requests_reserved`, `requests_consumed`, `generation_id` |
| `billing_account_periods` | Período mensual de una cuenta `NORMAL`: ventana, `included_requests`, reservadas, consumidas, estado |
| `billing_usage` | Una fila por job: unidades, estado de la reserva y motivo |

No existen otras tablas de facturación (la migración `0031` eliminó las anteriores).

## 4. Reglas

- Cada job reserva **1** unidad al crearse, sin importar el bot. `costo_creditos`
  del catálogo es solo informativo.
- La reserva bloquea la fila de la cuenta y es atómica con la creación del job.
- Se confirma al terminar el job y se libera si se cancela antes de ejecutar o
  falla por infraestructura.
- Sin cupo, la API responde `429 quota_exhausted`.
- Reasignar el mismo tipo conserva el consumo; cambiar de tipo reinicia los
  contadores. En `NORMAL`, cambiar `max_requests` ajusta el mes en curso.
- El mes siguiente se abre con la primera reserva del mes. Cerrar períodos
  vencidos es higiene, no es necesario para renovar.

## 5. Operación (panel y API admin)

Todas requieren Bearer administrativo y la asignación exige motivo (10 a 500
caracteres), con auditoría.

| Método | Ruta | Función |
|---|---|---|
| `GET` | `/admin/billing/accounts/{user_id}` | Tipo de cuenta, cupo, uso y período vigente |
| `POST` | `/admin/billing/accounts/{user_id}/account-type` | Asignar `PRUEBA`, `TESTING` o `NORMAL` (`max_requests` entre 1 y 10000000 para `NORMAL`) |
| `POST` | `/admin/billing/periods/close-expired` | Cerrar períodos mensuales vencidos |

API pública: `GET /api/v3/mi/cuenta` y `GET /api/v3/usuarios/me` devuelven
`tipo_cuenta`, `limite`, `consumidas`, `reservadas`, `disponibles`, `renovable`
y `periodo`.

## 6. Invariantes

| Invariante | Diseño verificable |
|---|---|
| B-1 | Cada reserva, confirmación o liberación deja una fila en `billing_usage`. Una cuenta no consume más de lo reservado |
| B-2 | La reserva se hace al crear el job, antes de asignarlo a un worker. Un job que no ejecuta libera el cupo |
| B-3 | Asignar un tipo de cuenta es idempotente respecto del consumo: reasignar el mismo tipo no lo reinicia |
| B-4 | La reserva toma bloqueo de fila de la cuenta y verifica disponibilidad en la misma transacción, por lo que no hay sobreventa de cupo por carrera |

## 7. Casos a cubrir

Concurrencia sobre la reserva (B-4), fin de la prueba al request 100,
renovación mensual en el primer request del mes, liberación en cancelación y
fallo de infraestructura, y migración `0022` reversible.
