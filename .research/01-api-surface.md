# MrBot API V2: HTTP API surface research for V3

**Scope.** Read-only review of the V2 repository at `api-bots-mrbot-v2` on branch `fix/cierre-parcial-a-go-20260914`.

**Method.** I read the application mounting code, V1 router registration, dependencies, every `app/api/routes/*.py` module at registration level, the V2 router/factory/batch/status/wrapper modules, every schema module's declared model structure, and representative V1 modules fully: `vep.py`, `portal_iva.py`, `hacienda.py`, and `consulta_cuit.py`.

**Citation convention.** `path:line` points to the source reviewed. A range indicates the relevant implementation span. “Job” means the V2 persisted Playwright-job protocol, not merely an `async def` Python handler.

## 1. Mounting, versioning, and public roots

- The FastAPI application uses `openapi_url="/"`, so the OpenAPI document is served at the root, not at `/openapi.json` (`app/main.py:130-136`).
- `app/main.py` mounts the legacy router first, then the separate admin router, then V2: `app.include_router(api_router.api_router)`, `app.include_router(admin_router.router)`, `app.include_router(api_v2_router)` (`app/main.py:197-200`).
- The legacy aggregate router is `APIRouter(prefix=os.getenv("API_V1_STR", "/api/v1"))` (`app/api/api_router.py:50`). Therefore its default public root is `/api/v1`, but an environment value can change it.
- V2 is hard-coded as `APIRouter(prefix="/api/v2")` (`app/api/v2/router.py:12`). It is not controlled by `API_V1_STR`.
- The admin router is independently mounted from `app.main`, rather than below V1. Its module-level prefix is `/admin` (observed in `app/api/routes/admin.py:408` and its router declaration). Its endpoints are outside `/api/v1` and `/api/v2`.
- The process health endpoint is independent of either API version: `GET /health` (`app/main.py:205-224`). It returns 503 if recovery/worker readiness is false.
- The V1 aggregate explicitly includes 32 public router modules. The import and `include_router` list is the V1 registration authority (`app/api/api_router.py:15-83`).
- V2 is only partially conditional. Its factory-backed bots are included at import time according to `V2_ENABLED_BOTS`; an empty or absent value enables all (`app/api/v2/router.py:17-31`).
- A configured V2 allow-list accepts exact bot names, V2 prefixes, normalized hyphen/slash forms, and special aliases for Libros IVA, SCT compensaciones, and Portal IVA carga (`app/api/v2/router.py:33-47`).
- The explicit V2 non-job wrappers and global batch router are imported/included unconditionally after the factory registrations (`app/api/v2/router.py:356-362`).

### Effective top-level prefixes

| Root | Meaning | Source |
|---|---|---|
| `/` | OpenAPI JSON | `app/main.py:130-136` |
| `/health` | Worker-aware health/readiness | `app/main.py:205-224` |
| `${API_V1_STR:-/api/v1}` | Legacy synchronous HTTP API | `app/api/api_router.py:50-83` |
| `/api/v2` | Job-oriented V2 plus intentionally retained non-job wrappers | `app/api/v2/router.py:12,356-362` |
| `/admin` | Separate HTML/admin management area | `app/main.py:198` and `app/api/routes/admin.py:408-1616` |

## 2. V2 factory: dynamic endpoint generation

### Contract and input registry

`make_v2_router()` is a router factory. It accepts a bot key, a Pydantic request type, the corresponding SQLAlchemy log model, operation name, optional route/prefix overrides, and multipart options (`app/api/v2/factory.py:199-211`). The V2 registry is not one central data structure. It is the series of conditional `make_v2_router(...)` calls in `app/api/v2/router.py:49-354`.

Each call is a registry record encoded in Python code:

```python
# app/api/v2/router.py:117-129
api_v2_router.include_router(make_v2_router(
    "portal_iva_carga", CargaPortalIvaRequest, ConsultaPortalIvaCargaLog,
    v2_prefix="portal_iva/carga", multipart=True,
    multipart_fields=PORTAL_IVA_MULTIPART_FIELDS,
    require_multipart_file=True,
))
```

The `bot_name` and `operation` are passed to `PlaywrightJobManager.create_job`; the request schema validates the incoming payload, and `log_model` is used later to locate data/files for a completed job (`app/api/v2/factory.py:844-852,956-986`). This is a registry coupled across router code, the job manager, workers/executors, and log models. `job_status.resolve_log_model()` also duplicates a bot-to-log-model registry for batch lookup (`app/api/v2/job_status.py:199-263`).

### Path derivation

- The default router prefix is `/{bot_name}` and default operation path is `/consulta` (`app/api/v2/factory.py:257-281`).
- If `v2_prefix` contains `/`, the factory splits on the **last** slash. Everything before it becomes router prefix and the final segment becomes operation path (`app/api/v2/factory.py:245-255`).
- Example: `v2_prefix="mis_comprobantes/solicitar_consulta"` becomes prefix `/mis_comprobantes` and route `/solicitar_consulta`.
- Explicit `route_path`, `router_prefix`, and the legacy named `prefix` override inferred/default values in that precedence order (`app/api/v2/factory.py:257-279`).
- The factory creates an `APIRouter(prefix=final_router_prefix, tags=[f"V2 - {bot_name}"])` (`app/api/v2/factory.py:281-282`).

### The generated endpoint triplet

For every factory registry record, it registers this concrete triplet. The cancellation route is registered before the status route so `cancelar` cannot be parsed as `{job_id}` (`app/api/v2/factory.py:854-887`).

```text
POST {operation-path}                    -> 202 JobCreateResponse
POST {operation-path}/cancelar/{job_id}  -> JobStatusResponse
GET  {operation-path}/{job_id}           -> JobStatusResponse
```

- JSON creation uses `payload: request_schema`, `validate_api_key`, `get_db`, and optional `Idempotency-Key` (`app/api/v2/factory.py:799-805`).
- It normalizes any CUIT-named strings/lists and handles the `mis_comprobantes` naming exception before persistence (`app/api/v2/factory.py:289-358,830-843`).
- `protect_runtime_secrets()` is applied to request data immediately before `create_job` (`app/api/v2/factory.py:840-845`).
- It returns `{"success": true, "job_id": ..., "status": "PENDIENTE"}` with HTTP 202 (`app/schemas/v2/job.py:7-10`, `app/api/v2/factory.py:852`).
- The generated GET only retrieves a job owned by the authenticated user. Missing/foreign jobs are a 404 `"Job no encontrado"` (`app/api/v2/factory.py:887-903`).
- Completed jobs query the supplied `log_model` by `job_id`, hydrate files and data, and sanitize the public result (`app/api/v2/factory.py:956-986`).

### Multipart branch

When `multipart=True`, the creation endpoint changes its request binding to `Depends(request_schema.as_form)` and declares generic `files` plus all named Portal IVA/VEP file slots (`app/api/v2/factory.py:587-605`).

- Generic uploads and only configured named fields are accepted (`app/api/v2/factory.py:644-656`).
- Empty file content is rejected with HTTP 400 (`app/api/v2/factory.py:658-679`).
- `require_multipart_file=True` rejects a request without a usable file. Portal IVA receives a domain-specific error that asks for a sales/purchases TXT pair or opening CSV (`app/api/v2/factory.py:725-738`).
- A job is created **before** each usable file is uploaded to the temp bucket (`app/api/v2/factory.py:748-767`).
- File references are appended into `request_data` under `archivos_subidos`, plus first-file aliases and named-field maps (`app/api/v2/factory.py:758-795`).
- The default field registry is local to the factory: Portal IVA eight fields; VEP/vep_alias `archivo_txt` (`app/api/v2/factory.py:27-43`).

### Queue gates and idempotency

Both JSON and multipart creators check global and per-user pending depths, returning 429 with `Retry-After: 30` when full (`app/api/v2/factory.py:607-630,806-829`). Defaults are configured as `MAX_PENDING_JOBS=500` and per-user defaults to that global value unless overridden (`app/jobs/config.py:52-70`). The factory passes `Idempotency-Key` through to the manager but does not itself define collision semantics (`app/api/v2/factory.py:751,804,845`).

## 3. Exhaustive V2 factory-backed async/job surface

**Legend.** Every line below is behind API-key authentication (`validate_api_key`) and the creation operation is asynchronous job submission. `Reg.` cites the registry record. The factory citations in the section above establish the generated POST/cancel/GET implementations for every record.
### Mis comprobantes

- `POST /api/v2/mis_comprobantes/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:49-53`.
- `POST /api/v2/mis_comprobantes/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_comprobantes/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Mis comprobantes, solicitar consulta

- `POST /api/v2/mis_comprobantes/solicitar_consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:55-59`.
- `POST /api/v2/mis_comprobantes/solicitar_consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_comprobantes/solicitar_consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Mis comprobantes, historial

- `POST /api/v2/mis_comprobantes/historial` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:81-85`.
- `POST /api/v2/mis_comprobantes/historial/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_comprobantes/historial/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### CCMA

- `POST /api/v2/ccma/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:87-91`.
- `POST /api/v2/ccma/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/ccma/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### SIPER

- `POST /api/v2/siper/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:93-97`.
- `POST /api/v2/siper/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/siper/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### SCT

- `POST /api/v2/sct/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:99-103`.
- `POST /api/v2/sct/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/sct/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### SCT compensaciones

- `POST /api/v2/sct/compensaciones/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:105-109`.
- `POST /api/v2/sct/compensaciones/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/sct/compensaciones/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Portal IVA

- `POST /api/v2/portal_iva/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:111-115`.
- `POST /api/v2/portal_iva/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/portal_iva/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Portal IVA carga

- `POST /api/v2/portal_iva/carga` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:117-129`.
- `POST /api/v2/portal_iva/carga/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/portal_iva/carga/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### RCEL

- `POST /api/v2/rcel/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:131-135`.
- `POST /api/v2/rcel/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/rcel/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Hacienda

- `POST /api/v2/hacienda/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:137-141`.
- `POST /api/v2/hacienda/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/hacienda/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### SIFERE

- `POST /api/v2/sifere/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:143-147`.
- `POST /api/v2/sifere/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/sifere/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Aportes en Línea

- `POST /api/v2/aportes-en-linea/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:149-153`.
- `POST /api/v2/aportes-en-linea/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/aportes-en-linea/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Declaración en Línea

- `POST /api/v2/declaracion-en-linea/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:155-159`.
- `POST /api/v2/declaracion-en-linea/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/declaracion-en-linea/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Mis Facilidades

- `POST /api/v2/mis_facilidades/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:161-165`.
- `POST /api/v2/mis_facilidades/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_facilidades/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Mis Retenciones

- `POST /api/v2/mis_retenciones/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:167-171`.
- `POST /api/v2/mis_retenciones/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_retenciones/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Mis Retenciones IVA Simple

- `POST /api/v2/mis_retenciones_iva_simple/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:173-183`.
- `POST /api/v2/mis_retenciones_iva_simple/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/mis_retenciones_iva_simple/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Ret/Per IIBB Misiones

- `POST /api/v2/retenciones_percepciones_iibb/misiones/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:185-189`.
- `POST /api/v2/retenciones_percepciones_iibb/misiones/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/retenciones_percepciones_iibb/misiones/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Ret/Per IIBB AGIP

- `POST /api/v2/retenciones_percepciones_iibb/agip/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:191-195`.
- `POST /api/v2/retenciones_percepciones_iibb/agip/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/retenciones_percepciones_iibb/agip/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Ret/Per IIBB ARBA

- `POST /api/v2/retenciones_percepciones_iibb/arba/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:197-201`.
- `POST /api/v2/retenciones_percepciones_iibb/arba/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/retenciones_percepciones_iibb/arba/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### ARBA alias

- `POST /api/v2/arba/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:203-207`.
- `POST /api/v2/arba/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/arba/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Pago devoluciones

- `POST /api/v2/pago_devoluciones/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:209-213`.
- `POST /api/v2/pago_devoluciones/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/pago_devoluciones/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### MOA

- `POST /api/v2/moa/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:215-219`.
- `POST /api/v2/moa/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/moa/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Libros IVA

- `POST /api/v2/libros_iva/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:221-225`.
- `POST /api/v2/libros_iva/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/libros_iva/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Facturómetro

- `POST /api/v2/facturometro/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:227-231`.
- `POST /api/v2/facturometro/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/facturometro/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Controladores fiscales

- `POST /api/v2/controladores-fiscales/carga` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:233-244`.
- `POST /api/v2/controladores-fiscales/carga/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/controladores-fiscales/carga/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Certificado MiPyME

- `POST /api/v2/certificado-mipyme/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:246-250`.
- `POST /api/v2/certificado-mipyme/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/certificado-mipyme/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### SRT alícuotas

- `POST /api/v2/srt/alicuotas/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:252-256`.
- `POST /api/v2/srt/alicuotas/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/srt/alicuotas/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### VEP carga

- `POST /api/v2/vep/carga` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:258-271`.
- `POST /api/v2/vep/carga/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/vep/carga/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### VEP archivo alias

- `POST /api/v2/vep_archivo/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:273-289`.
- `POST /api/v2/vep_archivo/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/vep_archivo/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### VEP CCMA

- `POST /api/v2/vep-ccma/generar` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:291-295`.
- `POST /api/v2/vep-ccma/generar/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/vep-ccma/generar/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Consulta pagos VEP

- `POST /api/v2/vep/consulta-pagos` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:297-301`.
- `POST /api/v2/vep/consulta-pagos/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/vep/consulta-pagos/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
### Liquidación granos

- `POST /api/v2/liquidacion_granos/consulta` | async job submission, **202** | API key | Registry: `app/api/v2/router.py:350-354`.
- `POST /api/v2/liquidacion_granos/consulta/cancelar/{job_id}` | async job cancellation | API key | Generated at `app/api/v2/factory.py:855-885`.
- `GET /api/v2/liquidacion_granos/consulta/{job_id}` | async job status polling | API key | Generated at `app/api/v2/factory.py:887-1022`.
## 4. V2 retained non-job surface

These endpoints are V2 paths but do **not** create a Playwright job. “Direct” means request execution returns in the same HTTP response, including wrappers whose Python function happens to be declared `async`. All list/history wrappers explicitly delegate to a V1 handler, so they retain V1 request/response contracts (`app/api/v2/non_job_router.py:12-16`).

### Direct execution and utility endpoints

| Path | Method | Mode | Auth | Source |
|---|---|---|---|---|
| `/api/v2/vep/zip` | POST | Direct ZIP generation | API key | `app/api/v2/router.py:313-320` |
| `/api/v2/vep/txt` | POST | Direct TXT generation | API key | `app/api/v2/router.py:322-329` |
| `/api/v2/vep/modelo` | GET | Direct static file download | API key | `app/api/v2/router.py:331-336` |
| `/api/v2/rcel/procesar_pdf` | POST | Direct local PDF extraction | API key | `app/api/v2/non_job_router.py:250-257` |
| `/api/v2/apoc/consulta/{cuit}` | GET | Direct DB lookup | API key | `app/api/v2/non_job_router.py:263-271` |
| `/api/v2/consulta_cuit/individual` | POST | Direct CUIT lookup | API key | `app/api/v2/non_job_router.py:273-284` |
| `/api/v2/consulta_cuit/masivo` | POST | Direct bulk CUIT lookup | API key | `app/api/v2/non_job_router.py:273-284` |
| `/api/v2/scrapping/f2002/extraer` | POST | Direct PDF extraction | API key | `app/api/v2/non_job_router.py:286-300` |
| `/api/v2/scrapping/f731/extraer` | POST | Direct PDF extraction | API key | `app/api/v2/non_job_router.py:286-300` |
| `/api/v2/scrapping/f931/extraer` | POST | Direct PDF extraction | API key | `app/api/v2/non_job_router.py:286-300` |
| `/api/v2/procesar-pem/convertir` | POST | Direct PEM-to-JSON conversion | API key | `app/api/v2/non_job_router.py:302-310` |
| `/api/v2/user/` | POST | Direct account creation | HTTP Basic admin | `app/api/v2/non_job_router.py:312-320` |
| `/api/v2/user/reset-key/` | POST | Direct API-key reset | HTTP Basic admin | `app/api/v2/non_job_router.py:321-323` |
| `/api/v2/user/consultas/{email}` | GET | Direct quota read | HTTP Basic admin | `app/api/v2/non_job_router.py:324-326` |

### V2 history/log reads

All rows in this table are direct, read-only in intent, API-key-authenticated wrapper calls, not jobs. Their source follows the same one-line `Depends(validate_api_key)` pattern, e.g. CCMA (`app/api/v2/non_job_router.py:23-27`) and Portal IVA (`app/api/v2/non_job_router.py:237-244`).

| Path | Method | Path | Method |
|---|---|---|---|
| `/api/v2/mis_comprobantes/logs` | POST | `/api/v2/ccma/logs` | POST |
| `/api/v2/siper/logs` | POST | `/api/v2/sct/logs` | POST |
| `/api/v2/sct/compensaciones/logs` | POST | `/api/v2/sifere/logs` | POST |
| `/api/v2/hacienda/logs` | POST | `/api/v2/liquidacion_granos/logs` | POST |
| `/api/v2/aportes-en-linea/logs` | POST | `/api/v2/declaracion-en-linea/logs` | POST |
| `/api/v2/mis_facilidades/logs` | POST | `/api/v2/mis_retenciones/logs` | POST |
| `/api/v2/retenciones_percepciones_iibb/misiones/logs` | POST | `/api/v2/retenciones_percepciones_iibb/agip/logs` | POST |
| `/api/v2/retenciones_percepciones_iibb/arba/logs` | POST | `/api/v2/pago_devoluciones/logs` | POST |
| `/api/v2/srt/alicuotas/logs` | POST | `/api/v2/certificado-mipyme/logs` | POST |
| `/api/v2/moa/logs` | POST | `/api/v2/libros_iva/logs` | POST |
| `/api/v2/facturometro/logs` | POST | `/api/v2/controladores-fiscales/logs` | POST |
| `/api/v2/vep-ccma/logs` | POST | `/api/v2/portal_iva/logs` | POST |
| `/api/v2/portal_iva/carga/logs` | POST | `/api/v2/rcel/logs` | POST |
| `/api/v2/vep/logs` | POST |  |  |

The definitions are at `app/api/v2/router.py:69-79,338-348` and `app/api/v2/non_job_router.py:23-257`. They pass authenticated user identity or raw key to their V1 target, so V2 has a duplicate adapter layer rather than a native query API.

## 5. Batch job status endpoint

- `POST /api/v2/jobs/status:batch` is a global, API-key-protected V2 endpoint (`app/api/v2/batch_router.py:44-53`; mounted `app/api/v2/router.py:360-362`).
- Request body is `{ "items": [{"endpoint": string, "job_id": UUID}, ...] }`. `items` requires 1 through 200 elements, and `endpoint` requires 1 through 120 characters (`app/api/v2/batch_router.py:21-31`).
- The implementation does not use `endpoint` after validation. It keys and retrieves solely by `job_id` and current user ID (`app/api/v2/batch_router.py:61-76`).
- It calls `manager.get_job(db, job_id, usuario.id)`. A missing job and a job belonging to someone else both produce an item-level `{"error":"not_found"}` and not a global 404, avoiding cross-user existence disclosure (`app/api/v2/batch_router.py:54-58,70-72`).
- Internal retrieval/serialization failures are item-level `{"error":"internal"}`; other items continue (`app/api/v2/batch_router.py:63-79`).
- Successful completed items load the log by bot and reuse `job_to_status_response`; active items receive the same status projection but no log (`app/api/v2/batch_router.py:73-76`).
- Response is `{ "results": { "<job UUID>": <BatchItemResult>, ... } }`. `BatchItemResult` permits extra status fields and has optional `status`/`error` (`app/api/v2/batch_router.py:33-42`).
- It is explicitly “solo lectura, sin cuota” in the module comment (`app/api/v2/batch_router.py:1-2`). It is **not** a multi-create/bulk-execution endpoint.

## 6. Job polling, cancellation, and states

### Shared response shape

`JobStatusResponse` is the public status contract (`app/schemas/v2/job.py:19-32`):

```json
{
  "job_id": "string UUID",
  "status": "PENDIENTE | CORRIENDO | COMPLETO | CANCELADO",
  "result": "OK | PARCIAL | ERROR | null",
  "bot": "string",
  "operation": "string",
  "created_at": "datetime",
  "started_at": "datetime | null",
  "finished_at": "datetime | null",
  "cancel_reason": "string | null",
  "cancelled_by": "user | admin | system_restart | null",
  "error": "string | list | null",
  "files": [{"name": "string", "url": "string", "size": "int | null"}],
  "data": "bot-specific JSON | null"
}
```

- `PENDIENTE`: active-job projection, no `started_at`, result/error/files/data all null/empty (`app/api/v2/factory.py:905-921`).
- `CORRIENDO`: active-job projection with `started_at`; result/error/files/data remain null/empty (`app/api/v2/factory.py:922-937`).
- `COMPLETO`: result comes from history. Error comes first from history then log. Files and bot-specific data are populated from `log_model` and sanitized (`app/api/v2/factory.py:956-987`).
- `CANCELADO`: includes finish/cancellation metadata and possibly sanitized error. It has no files/data (`app/api/v2/factory.py:988-1003`).
- The `result` comment restricts terminal completed results to `OK | PARCIAL | ERROR`; status and result are separate axes (`app/schemas/v2/job.py:21-23`).
- `POST .../cancelar/{job_id}` passes `cancelled_by="user"` to the manager and returns `CANCELADO` data (`app/api/v2/factory.py:855-885`).
- Admin cancellation is a separate `/admin/jobs/{job_id}/cancel` facility (`app/api/routes/admin.py:1183`), and startup recovery can label cancellation `system_restart` (`app/main.py:57-66`; schema comment `app/schemas/v2/job.py:29`).

### File/data hydration behavior

- New-format `log.archivos` entries are interpreted as name/key/bucket/size and receive a newly generated presigned URL (`app/api/v2/job_status.py:27-76`).
- If an object does not exist, the URL becomes the privacy message rather than making a completed job fail (`app/api/v2/job_status.py:48-60`).
- Legacy `*_url_minio` model columns are a fallback for file enumeration (`app/api/v2/job_status.py:78-93`).
- Data lookup prefers a JSON artifact in a JSON bucket, then `response_data`, then best-effort SQLAlchemy column introspection (`app/api/v2/job_status.py:96-143`).
- Batch status obtains the log through the duplicated `resolve_log_model()` map (`app/api/v2/job_status.py:199-273`), whereas a factory status handler uses the `log_model` argument it closed over (`app/api/v2/factory.py:956-967`).

## 7. Auth, quota, and route dependencies

### API-key dependency

`validate_api_key` requires `X-API-Key` and `Email` HTTP headers plus a DB session (`app/api/deps.py:102-106`). Its observed behavior is:

1. Missing API key -> 401 (`app/api/deps.py:107-109`).
2. Missing email -> 400 (`app/api/deps.py:110-112`).
3. Lookup is by lower-cased email and constant-time/HMAC-aware verification, with a compatibility migration for legacy plaintext rows (`app/api/deps.py:37-67,113-115`).
4. Disabled user -> 403 (`app/api/deps.py:117-119`).
5. Monthly counter resets on a new UTC month (`app/api/deps.py:120-131`).
6. Reaching `maximas_consultas_mensuales` -> 429 (`app/api/deps.py:133-136`).

The dependency is used by V1 bot execution endpoints, e.g. Portal IVA (`app/api/routes/portal_iva.py:53-57`), Hacienda (`app/api/routes/hacienda.py:33-37`), Consulta CUIT (`app/api/routes/consulta_cuit.py:24-28`), and VEP (`app/api/routes/vep.py:166-171`). Factory-backed V2 creation, cancellation, and polling all use it (`app/api/v2/factory.py:591-605,799-805,856-860,887-891`).

### Admin dependency

`require_admin` invokes `HTTPBasic` and lazily delegates verification to the existing admin router's `verify_admin` to avoid creating a second policy (`app/api/deps.py:19-34`). The V1 user endpoints and V2 user wrappers use it (`app/api/routes/user.py:30-102`, `app/api/v2/non_job_router.py:312-326`). For 401 under `/admin`, the public exception handler restores `WWW-Authenticate: Basic` (`app/api/api_router.py:105-118`).

### Legacy logs exception

Many V1 `/logs` handlers take `mail` and `api_key` as direct parameters and manually call `get_user_by_api_key`, rather than declaring `validate_api_key`. VEP makes this explicit (`app/api/routes/vep.py:924-945`). V2 wrappers normalize most of these by accepting `X-API-Key` and authenticating with `validate_api_key` before calling the V1 function (`app/api/v2/non_job_router.py:23-27`).

### Quota and backpressure are different controls

- `incrementar_consultas_realizadas` performs an atomic SQL increment, but it is not a global FastAPI dependency (`app/api/deps.py:141-155`). V1 routes call it themselves, usually on success, for example Portal IVA (`app/api/routes/portal_iva.py:128-132`) and Hacienda (`app/api/routes/hacienda.py:123-127`).
- The V2 job lifecycle documents that transition/acquisition increments the monthly counter, and the manager code conditionally increments only when under max (`app/jobs/manager.py:23,225-228`).
- V2 factory queue backpressure is independent from the monthly user quota: global/per-user pending-depth checks return 429/`Retry-After`, as documented above (`app/api/v2/factory.py:607-630`).
- No conventional per-second/IP rate-limiter dependency was found in the inspected route/dependency modules. The observed limits are monthly user quota and V2 pending-job capacity.

## 8. Request/response schema conventions

### Structure across `app/schemas`

- Schemas are per-domain modules, generally defining `<Bot>Request`, `<Bot>Response`, a log filter request, an individual log registry, and a `LogsResponse` containing `registros`. The declarations are visible, for example, in Portal IVA (`app/schemas/portal_iva.py:8-145`) and Hacienda (`app/schemas/hacienda.py:8-49`).
- There is no shared domain success envelope. Most V1 bot responses independently choose fields such as `success`, `message`, `header`, artifact lists, and `error` lists. Portal IVA is representative (`app/schemas/portal_iva.py:94-103`); Hacienda is another variant (`app/schemas/hacienda.py:27-32`).
- `app/schemas/__init__.py` is empty. No package-level registry or common API response base was found.
- Most Playwright credential-bearing request types inherit `CredentialRequestBase`. Examples include every domain from Aportes through VEP, as shown by the import/inheritance declarations in `app/schemas/*.py`; locally processed/non-login models such as `ConsultaCUIT*`, PDF extraction, PEM, user, and APOC do not.
- `Field(...)` is widely used for OpenAPI descriptions/examples, but naming is inconsistent: `cuit_representante`, `cuit_inicio_sesion`, `representado_cuit`, and `Cuit_representado` coexist.

### Credential payload convention

`CredentialRequestBase` is the shared transport for plaintext and RSA-encrypted credentials (`app/schemas/credentials.py:1-23`).

- It adds optional `clave_encriptada`, documented as Base64 RSA-OAEP-SHA256 (`app/schemas/credentials.py:19-22`).
- A pre-validation model validator resolves encrypted credentials and input aliases before normal model validation (`app/schemas/credentials.py:72-77`).
- Public aliases are `clave`, `clave_representante`, and `contrasena`, while each concrete request retains its canonical field (`app/schemas/credentials.py:11-17,30-35`).
- Its custom JSON-schema hook publishes `x-credential-aliases` and `x-credential-canonical-field`, and documents the aliases in schema/property descriptions (`app/schemas/credentials.py:24-70`).
- Example mismatch is intentional by compatibility: Portal IVA’s canonical credential is `clave_representante` (`app/schemas/portal_iva.py:16-20`); Hacienda’s is `clave` (`app/schemas/hacienda.py:8-19`).

### Examples of response conventions

- A V1 direct query typically returns `success`, human `message`, a display-oriented `header`, optional artifacts/data, and `error`; Portal IVA constructs exactly that shape (`app/api/routes/portal_iva.py:148-156`).
- V1 logs expose `{ "registros": [...] }`, with each registry commonly carrying timestamps, CUIT fields, `status`, `error_message`, and optional `job_id` (`app/schemas/portal_iva.py:131-146`, `app/schemas/hacienda.py:40-49`).
- User responses deliberately exclude API-key material. `UserInDB` has no key field, and `UserResponse.data` recursively strips normalized API-key/HMAC-digest keys (`app/schemas/user.py:16-25,35-69`).
- V2 job creation and polling are the only clearly shared, version-wide response models (`app/schemas/v2/job.py:7-32`).

## 9. Error envelope and `public_errors.py`

### HTTP boundary envelope

- `HTTPException` becomes `{ "detail": <sanitized value> }`, preserving status and adding `X-Correlation-ID` (`app/api/api_router.py:105-118`).
- Request validation becomes HTTP 422 `{ "detail": <sanitized list> }`; raw Pydantic `input` is intentionally not echoed (`app/api/api_router.py:121-132`).
- Unhandled exceptions are logged with traceback internally but returned as HTTP 500 with a safe generic `detail` and correlation ID (`app/api/api_router.py:135-154`).
- Middleware assigns a fresh correlation ID to each request and does a final pass over JSON response error-bearing fields, while explicitly skipping the OpenAPI document (`app/main.py:142-192`).
- This is a boundary overlay, not full domain standardization. Many V1 handlers construct their own detail object such as `{message, error_code, success}` before the global handler sees it. Portal IVA has both ARCA and internal variants (`app/api/routes/portal_iva.py:134-145,183-192`).

### `app/utils/public_errors.py` behavior

- `ErrorCategory` defines validation, authentication, additional validation, service-not-enabled, external timeout, navigation, query, download, processing, storage, and unexpected categories (`app/utils/public_errors.py:20-31`).
- It maps categories to Spanish public messages and canonical stage labels (`app/utils/public_errors.py:36-56`).
- `classify_exception` classifies from exception type/MRO and trusted context, deliberately avoiding `str(exc)` (`app/utils/public_errors.py:136-151`).
- `safe_public_message` uses a narrowly accepted business fallback or a category template. It does not expose the exception representation (`app/utils/public_errors.py:186-191`).
- The sanitizer uses sensitive/technical regexes to reject Playwright locators, URLs/presigned URLs, paths, stack traces, passwords, keys, database details, and similar implementation data (`app/utils/public_errors.py:76-85`).
- It deletes unsafe structural keys including raw input/body/request/response, exception/cause, traceback/stack, and debug/internal details (`app/utils/public_errors.py:93-105`).
- It retains only a controlled metadata set inside error structures, including `error_code`, status, success, invalid fields, and operation/status/result identifiers (`app/utils/public_errors.py:107-117`).
- Batch sentinel errors `not_found` and `internal` are explicitly accepted public values (`app/utils/public_errors.py:159-168`).
- Job status sanitizes `error` and `data` before serializing terminal job results (`app/api/v2/job_status.py:146-196`).

## 10. Legacy V1 endpoint inventory, paths only

The default V1 prefix for every path below is `/api/v1`; substitute `API_V1_STR` if configured. This section intentionally lists paths only, as requested. Registration comes from `app/api/api_router.py:52-83`; individual decorator locations were skimmed across every route module.

### User, lookup, and security

- `POST /api/v1/user/`
- `POST /api/v1/user/reset-key/`
- `GET /api/v1/user/consultas/{email}`
- `GET /api/v1/security/public-key`
- `GET /api/v1/apoc/consulta/{cuit}`
- `POST /api/v1/consulta_cuit/individual`
- `POST /api/v1/consulta_cuit/masivo`

### Mis comprobantes, CCMA, SIPER, SCT

- `POST /api/v1/mis_comprobantes/consulta`
- `POST /api/v1/mis_comprobantes/solicitar_consulta`
- `POST /api/v1/mis_comprobantes/logs`
- `POST /api/v1/mis_comprobantes/historial`
- `POST /api/v1/ccma/consulta`
- `POST /api/v1/ccma/logs`
- `POST /api/v1/siper/consulta`
- `POST /api/v1/siper/logs`
- `POST /api/v1/sct/consulta`
- `POST /api/v1/sct/compensaciones/consulta`
- `POST /api/v1/sct/logs`
- `POST /api/v1/sct/compensaciones/logs`

### ARCA/AFIP domain bots

- `POST /api/v1/portal_iva/consulta`
- `POST /api/v1/portal_iva/carga`
- `POST /api/v1/portal_iva/carga/logs`
- `POST /api/v1/portal_iva/logs`
- `POST /api/v1/rcel/consulta`
- `POST /api/v1/rcel/procesar_pdf`
- `POST /api/v1/rcel/logs`
- `POST /api/v1/hacienda/consulta`
- `POST /api/v1/hacienda/logs`
- `POST /api/v1/liquidacion_granos/consulta`
- `POST /api/v1/liquidacion_granos/logs`
- `POST /api/v1/libros_iva/consulta`
- `POST /api/v1/libros_iva/logs`
- `POST /api/v1/facturometro/consulta`
- `POST /api/v1/facturometro/logs`
- `POST /api/v1/certificado-mipyme/consulta`
- `POST /api/v1/certificado-mipyme/logs`
- `POST /api/v1/moa/consulta`
- `POST /api/v1/moa/logs`
- `POST /api/v1/aportes-en-linea/consulta`
- `POST /api/v1/aportes-en-linea/logs`
- `POST /api/v1/declaracion-en-linea/consulta`
- `POST /api/v1/declaracion-en-linea/logs`
- `POST /api/v1/mis_facilidades/consulta`
- `POST /api/v1/mis_facilidades/logs`
- `POST /api/v1/mis_retenciones/consulta`
- `POST /api/v1/mis_retenciones/logs`
- `POST /api/v1/mis_retenciones_iva_simple/consulta`

### Provincial and other queries

- `POST /api/v1/retenciones_percepciones_iibb/misiones/consulta`
- `POST /api/v1/retenciones_percepciones_iibb/misiones/logs`
- `POST /api/v1/retenciones_percepciones_iibb/agip/consulta`
- `POST /api/v1/retenciones_percepciones_iibb/agip/logs`
- `POST /api/v1/retenciones_percepciones_iibb/arba/consulta`
- `POST /api/v1/retenciones_percepciones_iibb/arba/logs`
- `POST /api/v1/pago_devoluciones/consulta`
- `POST /api/v1/pago_devoluciones/logs`
- `POST /api/v1/sifere/consulta`
- `POST /api/v1/sifere/logs`
- `POST /api/v1/srt/alicuotas/consulta`
- `POST /api/v1/srt/alicuotas/logs`

### VEP and file services

- `POST /api/v1/vep/carga`
- `POST /api/v1/vep/consulta-pagos`
- `POST /api/v1/vep/zip`
- `POST /api/v1/vep/txt`
- `GET /api/v1/vep/modelo`
- `POST /api/v1/vep/logs`
- `POST /api/v1/vep-ccma/generar`
- `POST /api/v1/vep-ccma/logs`
- `POST /api/v1/controladores-fiscales/carga`
- `POST /api/v1/controladores-fiscales/logs`
- `POST /api/v1/scrapping/f2002/extraer`
- `POST /api/v1/scrapping/f731/extraer`
- `POST /api/v1/scrapping/f931/extraer`
- `POST /api/v1/procesar-pem/convertir`

### Admin root, outside the V1 prefix

- `GET /admin`, `POST /admin/login`, `POST /admin/logout`, `GET /admin/legacy`
- `GET /admin/users`, `POST /admin/users/create`, `POST /admin/users/bulk`
- `POST /admin/users/{user_id}/toggle`, `POST /admin/users/{user_id}/api-key`, `POST /admin/users/{user_id}/monthly-limit`
- `GET /admin/tables`, `POST /admin/tables/clear-consulta-logs`, `POST /admin/tables/purge-consulta-logs`
- `GET /admin/jobs`, `POST /admin/jobs/{job_id}/cancel`, `POST /admin/jobs/purge`, `GET /admin/jobs/metrics`, `POST /admin/jobs/bulk_cancel`
- `GET /admin/reporting`, `GET /admin/reporting/download`, `GET /admin/reports/download`

These decorators are in `app/api/routes/admin.py:408-1616`. The admin UI routes are not API-key endpoints; their route parameters/dependencies use the admin session/Basic flow.

## 11. Representative V1 behavior observed

### Portal IVA

- The legacy `/portal_iva/consulta` performs bot work in the request lifecycle: validates CUITs, calls `await bot_portal_iva(...)`, writes a log, increments quota on success, then returns the domain response (`app/api/routes/portal_iva.py:53-156`).
- `/portal_iva/carga` uses form-bound request data plus eight named uploads. It requires complete sales/compras TXT pairs or at least one opening CSV (`app/api/routes/portal_iva.py:196-269`).
- This maps to V2 factory multipart with file references, rather than passing local upload paths to the worker (`app/api/v2/router.py:117-129`, `app/api/v2/factory.py:758-795`).

### Hacienda

- Legacy `/hacienda/consulta` validates representative and represented CUITs, requires a nonblank denomination, executes `descargar_hacienda`, logs result/error, increments quota on success, and regenerates presigned URLs for V1 response output (`app/api/routes/hacienda.py:33-149`).
- Its `/logs` route actually declares `validate_api_key`, filters logs by authenticated `usuario.id`, and returns a typed `registros` response (`app/api/routes/hacienda.py:166-211`).

### Consulta CUIT

- Individual and batch routes are synchronous Python `def` handlers, API-key-protected, CUIT-normalizing, and directly call `consulta_cuit` / `consulta_cuit_masiva` before returning `JSONResponse` (`app/api/routes/consulta_cuit.py:19-78`).
- Their decorator `response_model` is the **request** type rather than an output schema (`app/api/routes/consulta_cuit.py:19-22,47-50`). V2 preserves that same incorrect response model in its wrappers (`app/api/v2/non_job_router.py:277-283`).

### VEP

- VEP has heterogeneous direct contracts under one prefix: Playwright-style `.txt` upload/carga, payment consultation, local Excel-to-ZIP processing, direct TXT rendering, a static model download, and logs (`app/api/routes/vep.py:160,421,518,719,894,918`).
- Its `/zip` path returns a streaming attachment in the same request after Excel processing (`app/api/routes/vep.py:518-593`), rather than a JSON response.
- VEP logs manually authenticate `mail` + `api_key`, returning 400 for invalid key/user instead of the dependency's standard 401 (`app/api/routes/vep.py:924-945`).

## 12. Synchronous execution that V3 must replace

V3 deprecates synchronous execution entirely. The entries below identify **work-producing** synchronous interfaces, not normal reads such as status polling, logs, health, or account metadata.

### V1: all bot execution remains inline today

All legacy `/api/v1` bot execution endpoints in section 10 are synchronous at HTTP-protocol level, even when their Python handler is `async def`: the handler awaits the bot in the request process and returns the completed domain result. Portal IVA and Hacienda demonstrate this directly (`app/api/routes/portal_iva.py:88-156`, `app/api/routes/hacienda.py:72-149`).

Therefore V3 must replace these V1 execution paths with job submissions: Mis Comprobantes, CCMA, SIPER, SCT including compensations, Portal IVA including carga, RCEL, Hacienda, Liquidación Granos, Libros IVA, Facturómetro, Certificado MiPyME, MOA, Aportes, Declaración, Mis Facilidades, Mis Retenciones including IVA Simple, all Ret/Per IIBB variants, Pago Devoluciones, SIFERE, SRT, VEP carga/pagos/CCMA, and Controladores Fiscales.

### V2: direct work endpoints with no job

- `POST /api/v2/vep/zip`
- `POST /api/v2/vep/txt`
- `GET /api/v2/vep/modelo` only if V3’s “no sync execution” rule also bans immediate artifact delivery. Otherwise it is a static artifact read, not execution.
- `POST /api/v2/rcel/procesar_pdf`
- `GET /api/v2/apoc/consulta/{cuit}` if the V3 policy applies to direct lookup work.
- `POST /api/v2/consulta_cuit/individual`
- `POST /api/v2/consulta_cuit/masivo`
- `POST /api/v2/scrapping/f2002/extraer`
- `POST /api/v2/scrapping/f731/extraer`
- `POST /api/v2/scrapping/f931/extraer`
- `POST /api/v2/procesar-pem/convertir`

The first, PDF, CUIT, scrapping, and PEM cases are explicitly implemented as V2 “sin Job” wrappers (`app/api/v2/router.py:303-348`, `app/api/v2/non_job_router.py:246-310`). User administration remains a direct management API, not bot execution (`app/api/v2/non_job_router.py:312-326`).

## 13. Coupling and V3 tech-debt avoidance

1. **The endpoint registry is executable code duplicated in several places.** V2 registration calls, factory defaults, worker dispatch, and `resolve_log_model` must be changed in concert. The duplicated log registry at `app/api/v2/job_status.py:199-263` is concrete evidence. V3 should have one declarative capability/operation registry consumed by routing, dispatch, validation, and result projection.
2. **Naming is an API compatibility maze.** Hyphens, underscores, nested paths, aliases, and naming aliases require normalization plus special cases (`app/api/v2/router.py:14-47`). V3 should choose stable public resource IDs and represent compatibility aliases at a gateway/version adapter.
3. **The factory infers routes by splitting strings.** `v2_prefix.rsplit("/", 1)` encodes path semantics in a string rather than typed route metadata (`app/api/v2/factory.py:245-255`). V3 should model `resource` and `operation` separately.
4. **V2 combines transport adaptation, storage, validation, secret protection, queue admission, job persistence, and output projection in one 1,000-line factory.** Examples include CUIT normalization (`app/api/v2/factory.py:289-358`), multipart upload (`587-795`), and status reconstruction (`887-1022`). V3 should separate HTTP adapters, command creation, object storage, and result serializers.
5. **Completed-job output depends on best-effort ORM introspection.** Fallback data extraction scans arbitrary model columns (`app/api/v2/job_status.py:115-143`). This makes public response shape an emergent property of per-bot database models. V3 should persist a versioned canonical result document per operation.
6. **V1/V2 direct wrapper delegation preserves old contracts and bugs.** `non_job_router.py` imports V1 handlers directly (`app/api/v2/non_job_router.py:17-20,263-326`). It prevents independent service extraction and carries the Consulta CUIT request-as-response-model bug forward.
7. **Authentication/error semantics differ by endpoint family.** `validate_api_key` has standardized 401/403/429 behavior (`app/api/deps.py:102-138`), whereas legacy logs can manually authenticate and return 400 (`app/api/routes/vep.py:924-945`). V3 should apply one auth middleware and one authorization/error mapping.
8. **Quota charging is split.** V1 routes decide when to atomically increment; V2 job manager charges at lifecycle acquisition (`app/api/deps.py:141-155`, `app/jobs/manager.py:23,225-228`). V3 should have a documented, transactionally enforceable billing/quota event policy.
9. **Global and per-user pending limits are process/application-manager concerns.** They are queried in the HTTP factory and can be bypassed in test-like failure paths (`app/api/v2/factory.py:607-615`). V3 should enforce queue admission atomically at the durable queue/database boundary.
10. **Job paths are operation-local instead of resource-global.** Every bot has an independent `{operation}/{job_id}` status/cancel endpoint. The global batch endpoint exists separately. V3 should prefer a single `/jobs/{id}` resource, with commands submitted under domain paths and consistent cross-domain status/cancel/list APIs.
11. **The response contract is inconsistent.** Bot-specific V1 `success/message/header/error`, custom `detail`, `JobStatusResponse`, streamed ZIPs, and direct raw JSON coexist. V3 should specify canonical command acceptance, problem details, job state, and artifact/result envelopes.
12. **Artifact links are regenerated during reads and have privacy sentinel strings in a URL field.** The status code may place human privacy text in `FileResponse.url` (`app/api/v2/job_status.py:48-60`). V3 should expose artifact state separately from a nullable URL.
13. **Runtime feature flags change the documented V2 surface.** `V2_ENABLED_BOTS` affects registration at import time (`app/api/v2/router.py:17-47`). V3 should keep externally published routes stable and use capability availability/state responses rather than disappearing paths where practical.
14. **Admin, API-key users, and browser/session flows are co-located.** `/admin` HTML routes are mounted inside the API app (`app/main.py:197-200`). V3 should isolate management UI/auth from public API service deployment and security policy.

## 14. V3 migration implications grounded in this surface

- Preserve V2 job creation as a compatibility adapter only, then offer a single V3 operation submission format that returns 202 plus job resource location.
- Normalize domain request schemas while retaining input-alias translation at the V2 boundary. Do not expose legacy credential field multiplicity in the internal command format.
- Keep job polling, cancellation, artifact access, and batch status as read/control interfaces, but decouple their persistence projections from bot-log tables.
- Convert every work-producing V2 non-job wrapper to an asynchronous command. Decide separately whether static template download and database lookup remain direct reads.
- Treat legacy V1 as a sunset compatibility surface. Its many inline execution endpoints should not be copied into microservices.

## 15. Validation of this research artifact

- No file in the V2 repository was modified.
- No git commit was created.
- The report was written only to the requested V3 `.research` directory.
- Source coverage included all 33 V2 factory registrations, V2 direct/batch/status modules, all legacy route decorators, all schema declaration modules, and the named representative full modules.
