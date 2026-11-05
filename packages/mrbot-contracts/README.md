# mrbot-contracts

Paquete de contratos compartidos entre `central-api` y `bot-worker`.

> **Estado: implementado (v1.0.0).** Diseño original del protocolo en
> [`../../plans/00-arquitectura/plan.md`](../../plans/00-arquitectura/plan.md) §5;
> si difiere del código, manda el código.

---

## Por qué existe

`central-api` y `bot-worker` son servicios separados que no se importan entre
sí. Se comunican por HTTP/JSON. Sin un paquete compartido, el esquema de cada
mensaje quedaría duplicado en los dos lados y divergiría en silencio.

Este paquete es la **única definición** de:

| Contenido | Detalle |
|---|---|
| Mensajes del protocolo | Modelos Pydantic de cada mensaje, en las dos direcciones |
| Enumeraciones de estado | Estados de job y de worker, y el eje `result` |
| Categorías de error | Taxonomía heredada de la disciplina de errores públicos de la V2 |
| Versión del protocolo | La constante que ambos servicios comparan |

## Regla de versionado

Un cambio incompatible en este paquete **obliga** a subir la versión del
protocolo. Un worker cuya versión de protocolo no es compatible con la central
se marca `DRENANDO` y no recibe trabajo nuevo.

Cambios compatibles: agregar un campo opcional, agregar un valor a una
enumeración que el receptor tolera.

Cambios incompatibles: quitar o renombrar un campo, cambiar un tipo, volver
obligatorio un campo opcional, cambiar la semántica de un valor existente.

## Mensajes

```mermaid
graph LR
    W["bot-worker"]
    C["central-api"]

    W -->|"registro"| C
    W -->|"latido"| C
    W -->|"aceptacion o rechazo"| C
    W -->|"evento de progreso"| C
    W -->|"resultado"| C
    W -->|"solicitud de URLs de subida"| C
    C -->|"asignacion de job"| W
    C -->|"cancelacion"| W
```

| Mensaje | Dirección | Propósito |
|---|---|---|
| `WorkerRegistration` | worker → central | Alta en la flota, declara capacidad y bots soportados |
| `WorkerHeartbeat` | worker → central | Salud, jobs en ejecución, jobs en cola, capacidad libre, recursos |
| `JobEnvelope` | central → worker | Sobre autocontenido con todo lo necesario para ejecutar |
| `JobAcceptance` | worker → central | Acepta o rechaza por saturación |
| `JobProgressEvent` | worker → central | Avance dentro de la ejecución |
| `JobResultReport` | worker → central | Resultado, artefactos, métricas, categoría de error |
| `JobCancellation` | central → worker | Cancelación de un job en vuelo |
| `PresignedUploadRequest` | worker → central | Pide URLs prefirmadas de subida |

## Estructura

```
packages/mrbot-contracts/
├── pyproject.toml               # versión 1.0.0, depende solo de pydantic
├── tests/                       # test_datos_csv, test_log_redaction, test_worker_capacity
└── src/mrbot_contracts/
    ├── __init__.py              # reexporta los modelos públicos
    ├── version.py               # PROTOCOL_VERSION y regla de compatibilidad
    ├── enums.py                 # estados de job y worker, JobResult, ErrorCategory
    ├── worker.py                # registro, latido, nodos
    ├── jobs.py                  # sobre, aceptación, eventos, resultado, cancelación
    ├── artifacts.py             # URLs prefirmadas, descriptor de artefacto
    ├── errors.py                # categorías y su mapeo a HTTP
    ├── request_fields.py        # campos por operación (alimenta los schemas de la API)
    ├── log_redaction.py         # redacción de URLs prefirmadas en logs
    ├── datos_csv.py             # CSV `|` de datos scrapeados (RCEL, CCMA) y su inversa
    └── planillas.py             # lectura y filtrado de planillas CSV/ZIP de ARCA
```

`datos_csv.py`, `planillas.py` y `log_redaction.py` son utilidades compartidas
(las usan el worker al escribir o descargar y la central al reconstruir), no
mensajes del protocolo.

## Uso

```python
from mrbot_contracts import PROTOCOL_VERSION
from mrbot_contracts.enums import JobStatus, WorkerStatus
from mrbot_contracts.jobs import JobEnvelope, JobResultReport
```

## Reglas del paquete

1. No depende de ningún otro módulo del repositorio.
2. Los mensajes son solo definiciones de datos y validación. Las utilidades
   (`datos_csv`, `planillas`, `log_redaction`, `request_fields`) sí tienen lógica
   de parseo y normalización compartida, pero sin red ni secretos.
3. No conoce PostgreSQL, ni Playwright, ni FastAPI. Depende solo de Pydantic y de
   la biblioteca estándar.
4. Los dos servicios lo declaran como dependencia con la misma versión.

## Documentos relacionados

- [`plans/00-arquitectura/plan.md`](../../plans/00-arquitectura/plan.md) §5 - especificación del protocolo
- [`plans/00-arquitectura/plan.md`](../../plans/00-arquitectura/plan.md) §6 - este paquete
- Pruebas: `.venv/bin/python -m pytest -q packages/mrbot-contracts/tests`
