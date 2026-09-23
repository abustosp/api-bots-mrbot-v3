# Anexo A: decisiones pendientes, con evidencia para resolverlas

> **Propósito.** Las siete preguntas de [`plan.md`](../plan.md) §11 y los cuatro
> valores `PENDIENTE_Q1` de [`04-billing`](../plans/04-billing/plan.md) requieren
> una decisión del dueño del producto. Este anexo no las decide: reúne la
> evidencia disponible y propone una opción por defecto para cada una, de modo
> que resolverlas sea una revisión y no una investigación.
>
> **Estado:** ninguna de estas decisiones bloquea las fases F0, F1, F2 ni F3. La
> primera que hace falta es la de tiers, y recién en F4.

---

## 0. Origen de los datos

Todas las cifras de este anexo salen de la base de datos de producción de la V2,
`data/sql_app.db`, leída en modo solo lectura. No son estimaciones.

| Dato | Valor |
|---|---|
| Usuarios en producción | 8 |
| Jobs en el historial | 751 |
| Ventana de los datos | 2026-08-25 a 2026-09-14, unos 20 días |

La muestra es chica y corta. Sirve para ordenar magnitudes relativas, por ejemplo
qué bot tarda diez veces más que otro, y **no** para proyectar demanda. Cualquier
decisión de precio basada solo en estos 20 días sería frágil.

---

## 1. Tiers concretos, pregunta Q1

### Evidencia: ya existe una escala de facto

La V2 no tiene planes, pero el administrador viene asignando
`maximas_consultas_mensuales` a mano. Esos valores **son** una segmentación
implícita que el negocio ya practica:

| Cuota asignada | Usuarios | Consumo observado en la ventana |
|---:|---:|---|
| 100.000 | 1 | 1.302 ejecuciones, 1,3 % de su cuota |
| 10.000 | 1 | 86 ejecuciones, 0,9 % |
| 3.000 | 1 | 29 ejecuciones, 1,0 % |
| 800 | 1 | 6 ejecuciones, 0,8 % |
| 120 | 1 | 19 ejecuciones, 15,8 % |
| 20 (default del código) | 3 | 2 ejecuciones cada uno, 10 % |

Dos lecturas incómodas pero importantes:

1. **Las cuotas actuales no limitan a nadie.** Nadie pasa del 16 % de su cuota.
   La cuota hoy no es un instrumento de monetización, es un tope de seguridad.
   Si los tiers de la V3 se fijan cerca de estos números, tampoco van a
   diferenciar. Si el negocio quiere que el tier influya en la facturación, los
   valores tienen que estar más cerca del uso real que del tope actual.
2. **El único usuario con volumen real concentra el 90 %** de las ejecuciones
   (1.302 de 1.448 contabilizadas). El diseño de tiers va a estar dominado por
   ese caso. Conviene preguntarse si es un cliente representativo o una
   excepción que merece un acuerdo propio.

### Propuesta por defecto

Escala derivada de la que el negocio ya usa, no inventada:

| Código | Cuota mensual | Concurrencia | Bots | Prioridad | Precio |
|---|---:|---:|---|---:|---|
| `free` | 20 | 1 | subconjunto de consulta | 10 | 0 |
| `basico` | 150 | 2 | catálogo de consulta | 50 | a definir |
| `pro` | 1.500 | 5 | catálogo completo | 100 | a definir |
| `empresa` | 15.000 | 10 | catálogo completo | 200 | acuerdo |

`free` conserva el 20 que ya es el default del código
(`app/models/user.py:36`), lo que hace que un usuario existente sin plan caiga
en un tier equivalente al que ya tenía. Los otros tres siguen la progresión
aproximada de diez en diez que el administrador venía usando.

**Lo que hay que decidir:** los precios, y si el cliente de 100.000 va a
`empresa` o mantiene un acuerdo fuera del catálogo.

---

## 2. Equivalencia de créditos, pregunta Q2

### Evidencia: los bots no cuestan lo mismo

Duración media por ejecución exitosa, sobre jobs `COMPLETO` con al menos 3
muestras:

| Bot | n | Media | Máximo |
|---|---:|---:|---:|
| `mis_facilidades` | 30 | **358 s** | 1.800 s |
| `consulta_pagos_vep` | 23 | 40 s | 686 s |
| `mis_retenciones` | 28 | 37 s | 224 s |
| `sifere` | 29 | 27 s | 172 s |
| `vep_ccma` | 24 | 24 s | 362 s |
| `portal_iva` | 27 | 23 s | 102 s |
| `declaracion_en_linea` | 30 | 20 s | 166 s |
| `moa` | 28 | 16 s | 180 s |
| `ccma` | 36 | 11 s | 59 s |
| `mis_comprobantes` | 47 | 9 s | 55 s |
| `sct` | 31 | 6 s | 67 s |
| `rcel` | 24 | 3 s | 39 s |
| `retper_iibb_arba` | 23 | <1 s | 9 s |

El rango es de tres órdenes de magnitud. `mis_facilidades` ocupa un slot de
worker **358 veces más** que `retper_iibb_arba`. Con un crédito plano, quien usa
`mis_facilidades` subsidia al resto, y el costo de infraestructura no guarda
relación con el ingreso.

### Propuesta por defecto

Costo por bandas de duración, no por segundo. Las bandas son legibles para el
cliente y estables ante variaciones de un bot:

| Banda | Duración media | Créditos | Cantidad | Bots |
|---|---|---:|---:|---|
| Ligera | < 15 s | 1 | 18 | `retper_iibb_arba` (0 s), `mis_comprobantes_historial` (1 s), `mis_comprobantes_solicitar` (1 s), `rcel` (3 s), `aportes_en_linea` (4 s), `pago_devoluciones` (4 s), `hacienda` (5 s), `liquidacion_granos` (6 s), `sct` (6 s), `certificado_mipyme` (8 s), `sct_compensaciones` (8 s), `mis_comprobantes` (9 s), `libros_portal_iva` (10 s), `ccma` (11 s), `retper_iibb_agip` (12 s), `siper` (12 s), `srt` (13 s), `retper_iibb_misiones` (14 s) |
| Media | 15 a 60 s | 3 | 8 | `moa` (16 s), `facturometro` (17 s), `declaracion_en_linea` (20 s), `portal_iva` (23 s), `vep_ccma` (24 s), `sifere` (27 s), `mis_retenciones` (37 s), `consulta_pagos_vep` (40 s) |
| Pesada | > 60 s | 10 | 1 | `mis_facilidades` (358 s) |

Los 27 bots clasificados son los que tienen al menos 3 ejecuciones exitosas en la
ventana. Los restantes del catálogo de 33 no tienen muestra suficiente y entran
como `Ligera` con costo 1 hasta que haya datos, porque cobrar de más sin
evidencia es peor que cobrar de menos.

El esquema ya soporta esto: `bot_operations.unit_cost`. No hace falta cambiar
nada, solo poblar los valores.

**Advertencia sobre las bandas `EFECTO`.** Los bots que ejecutan actos
irreversibles (`controladores_fiscales`, `rcel`, `vep_ccma`, `portal_iva_carga`,
ver [`04-billing`](../plans/04-billing/plan.md) §6.6) podrían justificar un costo
mayor que su duración, porque su valor para el cliente no es el tiempo de CPU
sino el trámite. Eso es una decisión comercial, no técnica.

---

## 3. Vencimiento de créditos, pregunta Q3

No hay evidencia en la V2: el sistema de créditos no existe.

| Opción | A favor | En contra |
|---|---|---|
| Sin vencimiento | Simple, no genera reclamos | Pasivo contable que crece sin límite |
| Vence a 12 meses | Acota el pasivo, es lo habitual | Genera reclamos si no se avisa |
| Vence al cerrar la suscripción | Alinea crédito y relación comercial | Castiga al que compró y pausó |

**Propuesta por defecto: sin vencimiento en la V1 del sistema de créditos.** El
volumen actual es chico y un pasivo acotado es preferible a un reclamo de
soporte temprano. El esquema `credit_ledger` es append-only, así que introducir
un vencimiento después es un movimiento `VENCIMIENTO` más, sin migración de
datos. Decidir ahora no aporta y decidir después no cuesta.

---

## 4. Ventana de deprecación de sync, pregunta Q4

### Evidencia

La V2 expone unos 35 módulos de rutas bajo `/api/v1` que ejecutan bots dentro
del request, más la superficie `/api/v2`. Hay 8 usuarios, de los cuales 3 están
en el default de 20 consultas y prácticamente no usan el servicio.

Eso cambia el cálculo: **migrar 8 clientes no es lo mismo que migrar 8.000.** El
caso difícil es uno solo, el usuario de 1.302 ejecuciones.

### Propuesta por defecto

| Fase | Duración | Qué pasa |
|---|---|---|
| Convivencia | 3 meses desde F6 | `/api/v1`, `/api/v2` y `/api/v3` en paralelo. Las respuestas legacy llevan `Deprecation` y `Sunset` |
| Aviso activo | mes 2 | El panel muestra qué clientes siguen llamando legacy. Contacto directo, son pocos |
| Solo lectura | mes 4 | Legacy responde `410` para creación. Consulta de jobs existentes sigue |
| Apagado | mes 5 | Se retira |

Tres meses es defendible porque el universo de clientes es chico y conocido. Con
más clientes esta ventana sería corta.

---

## 5. Migración de datos históricos, pregunta Q5

### Evidencia

| Fuente | Volumen |
|---|---|
| `data/sql_app.db` | unos 133 MB |
| Tablas `consulta_*_logs` | 28 |
| Jobs en historial | 751 |
| Antigüedad de los datos | la ventana observada es de 20 días |

751 jobs en 28 tablas distintas es poco dato repartido en mucha estructura.
Transformarlo al modelo nuevo costaría más que su valor.

**Propuesta por defecto: esquema `legacy` de solo lectura**, como ya plantea
[`01-database`](../plans/01-database/plan.md) §10. Las tablas se importan tal
cual, con sus IDs enteros, y `legacy.user_id_map` preserva el vínculo con los
UUID nuevos. Si más adelante hace falta consultar historia desde la V3, se hace
una vista, no una migración.

**Lo que hay que confirmar:** si existe obligación legal de conservar esos
registros y por cuánto tiempo. Eso cambia la política de retención, no el
diseño.

---

## 6. Multi-tenant, pregunta Q6

### Evidencia

La V2 no tiene concepto de organización: `users` es plano y una clave API
pertenece a un usuario. Con 8 usuarios no hay presión estructural para cambiarlo.

**Propuesta por defecto: no implementar multi-tenant en la V3 inicial**, pero no
cerrarle la puerta. Concretamente:

- La suscripción pertenece a `users.id`, como está hoy en el plan.
- `api_keys` ya es tabla aparte, así que un usuario puede tener varias claves.
  Eso cubre el caso de "un cliente con varios sistemas" sin inventar
  organizaciones.
- Si después hace falta, se agrega `organizations` y `users.organization_id`
  nullable. Es una migración aditiva, del tipo expandir/contraer que el plan ya
  exige.

El costo de postergarlo es bajo. El costo de construirlo sin un cliente que lo
pida es un modelo de permisos que nadie usa.

---

## 7. Destino de despliegue, pregunta Q7

### Evidencia

| Hecho | Fuente |
|---|---|
| La V2 corre hoy en Docker Compose sobre VPS | `docker-compose.yml` |
| Existe un registry privado propio | `readme-registry.md` |
| Ya hay un plan de k3s + KEDA redactado | `plan-migracion-cluster.md` |
| El pico real de concurrencia es bajo | 751 jobs en 20 días |

751 jobs en 20 días son unos 38 por día. Con 5 jobs simultáneos por worker y
duraciones medias de segundos, **un solo worker cubre la carga actual con
margen enorme**. Kubernetes resolvería un problema de escala que todavía no
existe, y agregaría una superficie operativa considerable.

### Propuesta por defecto

| Fase | Plataforma | Cuándo pasar a la siguiente |
|---|---|---|
| F0 a F6 | Docker Compose en VPS, 2 workers | Es el destino inicial |
| Posterior | k3s + KEDA, reutilizando el plan de la V2 | Cuando la cola sostenga profundidad > 0 de forma habitual, o se necesite tolerancia a fallo de nodo |

El diseño push de la V3 ya deja esto preparado: KEDA decidiría **cuántos**
workers existen y la API central seguiría decidiendo **qué worker** ejecuta cada
job. Son responsabilidades separadas y no entran en conflicto, como documenta
[`00-arquitectura`](../plans/00-arquitectura/plan.md) §4.

---

## 8. Hallazgo adicional: la tasa de éxito es baja

No es una pregunta abierta, pero surge de los mismos datos y afecta al negocio:

| Bot | n | Éxito |
|---|---:|---:|
| `retper_iibb_misiones` | 35 | **57 %** |
| `mis_comprobantes` | 49 | **65 %** |
| `declaracion_en_linea` | 30 | **67 %** |
| `pago_devoluciones` | 32 | 78 % |
| `ccma` | 39 | 79 % |
| `sct` | 31 | 84 % |

Uno de cada tres intentos de `mis_comprobantes`, el bot más usado, no termina
bien. Esto tiene una consecuencia directa sobre la facturación: **si se cobra
por intento, se le cobra al cliente un tercio de ejecuciones fallidas.** Eso
genera reclamos y es difícil de defender.

La política de [`04-billing`](../plans/04-billing/plan.md) §6.6 ya distingue
error de infraestructura (se reembolsa) de error funcional (no se reembolsa),
pero con estas tasas conviene revisar dónde cae cada fallo concreto antes de
cobrar. Un `CREDENTIALS_REJECTED` es del cliente; un timeout de ARCA no lo es.

**Sugerencia:** medir la distribución de categorías de error durante F3, antes
de activar el cobro en F4. Los datos van a existir porque `jobs.error_code` es
parte del esquema.

---

## 9. Resumen para decidir

| # | Decisión | Propuesta por defecto | ¿Bloquea? |
|---|---|---|---|
| Q1 | Tiers | 20 / 150 / 1.500 / 15.000, precios a definir | F4 |
| Q2 | Créditos por bot | 3 bandas: 1, 3 y 10 créditos | F4 |
| Q3 | Vencimiento de créditos | Sin vencimiento en la V1 | No |
| Q4 | Deprecación de sync | 3 meses de convivencia, apagado al mes 5 | F6 |
| Q5 | Datos históricos | Esquema `legacy` de solo lectura | F1 |
| Q6 | Multi-tenant | No ahora. Varias claves por usuario alcanza | No |
| Q7 | Despliegue | Compose en VPS. k3s cuando haga falta | F6 |

Si se aceptan las siete propuestas por defecto, lo único que queda por definir
son **los precios**, y eso recién hace falta en F4.
