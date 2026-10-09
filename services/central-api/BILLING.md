# Tipos de cuenta y cupo de requests

La API v3 ya no administra pagos automáticos (no hay MercadoPago, webhooks,
créditos ni planes comerciales). Cada usuario tiene **un** tipo de cuenta, que
asigna un administrador desde el panel o con
`POST /admin/billing/accounts/{user_id}/account-type`.

| Tipo | Cupo | Renovación |
|---|---|---|
| `PRUEBA` | 100 requests en total | **No renovable.** Es el tipo por defecto de todo usuario nuevo. Al consumirse el request 100, la cuenta pasa a `NORMAL` (cupo 100/mes) y el usuario queda **deshabilitado** hasta que un admin lo habilite. |
| `TESTING` | Ilimitado | No aplica. El uso se sigue contando. |
| `NORMAL` | `max_requests` por mes | **Mensual y automática.** Mes calendario en hora de Argentina (UTC-3). |

## Reglas

- Cada request (job) reserva **1** unidad al crearse, sin importar el bot ni la
  operación. El campo `costo_creditos` del catálogo es solo informativo.
- La reserva se confirma al terminar el job y se **libera** si se cancela antes
  de ejecutar o si falla por infraestructura: el cupo se devuelve.
- Sin cupo, la API responde `429 quota_exhausted`.
- Reasignar el **mismo** tipo conserva el consumo (una cuenta `PRUEBA` no se
  renueva re-guardándola). Cambiar de **tipo** reinicia los contadores.
- En `NORMAL`, cambiar `max_requests` ajusta el mes en curso sin perder consumo.
- El mes siguiente se abre solo con la primera reserva del mes. El cierre de
  períodos vencidos (`POST /admin/billing/periods/close-expired`) es higiene,
  nunca es necesario para que un `NORMAL` se renueve.
- Toda asignación exige motivo (10 a 500 caracteres en el panel) y queda auditada.

## Fin de la prueba

Cuando se confirma el request 100 de una cuenta `PRUEBA`:
- la cuenta pasa a `NORMAL` con `max_requests = 100` y contadores en cero;
- `users.habilitado` queda en `false` (el acceso se corta);
- se registra el evento de auditoría `billing.prueba.finished`.

Las API keys **no** se revocan: al habilitar al usuario desde el panel recupera
su acceso. El admin debería ajustar el cupo mensual `NORMAL` si corresponde.

## Persistencia

PostgreSQL es la fuente de verdad cuando hay `DATABASE_URL`. La cuenta y sus
contadores viven en `billing_accounts`, el período mensual de `NORMAL` en
`billing_account_periods` y cada job en `billing_usage`. La reserva bloquea la fila
de la cuenta y es atómica con la creación del job. Los diccionarios de
`billing.entitlements` y `billing.reservation` son solo el respaldo sin base.

## Migración `0022`

| Antes | Después |
|---|---|
| `CREDITOS`, `GRATUITA` | `PRUEBA` (100). Se conserva lo ya consumido, con tope 100 |
| `TIER` | `NORMAL`, con `max_requests` igual a las unidades del tier |
| `LIBRE` | `NORMAL`, con su tope manual como cupo mensual |

Las tablas de pagos, suscripciones y ledgers (`payments`, `payment_events`,
`subscriptions`, `credit_ledger`, ...) **no se borran**: conservan el historial.
El saldo de créditos deja de tener efecto. La migración es reversible.

## API pública

`GET /api/v3/mi/cuenta` y `GET /api/v3/usuarios/me` devuelven `tipo_cuenta`,
`limite` (`null` si es ilimitado), `consumidas`, `reservadas`, `disponibles`,
`renovable` y el `periodo` vigente (solo `NORMAL`).
