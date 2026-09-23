# V2 documentation, tests, and operational history research
- **Research date:** 2026-09-16.
- **Source repository:** `/mnt/ssd1/Proyectos Python/Scripts/Mr bot/api/api-bots-mrbot-v2`.
- **Destination:** V3 research artifact only. No V2 source, configuration, data, or Git history was modified.
- **Method:** static reading of requested files and repository metadata. Existing execution claims in prior reports are cited as historical claims, not rerun here.
- **Secrets:** this report intentionally omits credential values. One documentation leak is described without reproducing the value.
## 1. Executive conclusions
1. The V2 authors already have a substantive cluster plan in `plan-migracion-cluster.md`.
2. Its principal direction is valid for a transition from a one-instance SQLite monolith to PostgreSQL plus horizontally scaled workers.
3. V3 must not implement that plan verbatim, because its worker data-plane model is materially different.
4. The plan has workers connect directly to PostgreSQL, claim jobs directly, update leases, and run a DB-driven reaper.
5. V3 explicitly requires workers with **no database**, assigned by a central orchestration API.
6. The plan also retains `/api/v1` and `/api/v2` exposure and therefore leaves synchronous legacy execution possible.
7. V3 requires synchronous executions to be deprecated, so its compatibility boundary must be intentional rather than inherited.
8. V2 already contains valuable regression assets for asynchronous jobs, UUIDv7 job IDs, secure errors, URL-free persistence, dynamic result rehydration, and V1/V2 response parity.
9. V2’s current operational deployment remains a single Docker Compose API container with SQLite, not the target k3s cluster.
10. The current branch is a late “cierre” hardening stream, not an in-progress implementation of `plan-migracion-cluster.md`.
## 2. Requested source map
| Area | Primary sources read | Notes |
|---|---|---|
| Cluster plan | `plan-migracion-cluster.md` | 355 lines, read in full. |
| Safe-error work plan | `PLAN.md`, `ORIGINAL_REQUEST.md` | This is a different, completed/closing V2 workstream. |
| Current public documentation | `README.md`, `doc.md` | Some content is historical or inconsistent with deployed Compose values. |
| Registry/deployment note | `readme-registry.md`, `build-and-push.sh` | Registry host recorded, secret value deliberately omitted. |
| Tests | `pytest.ini`, `tests/`, `tests/TEST_README.md`, `.github/workflows/ci.yml` | Inventory follows below. |
| Earlier V2 design | `docs/migration_v2/` | Useful durable decisions, but older than current cluster plan. |
| Agent notes | `.agents/**/handoff.md`, reports, audits | Treated as dated evidence, not current truth. |
| Operations | `docker-compose.yml`, `Dockerfile`, CI, reports | No separate production runbook, monitoring system, or crontab was found. |
| History | `git log --oneline -60`, `git log --stat -12`, `git branch -a` | Snapshot recorded in section 8. |
## 3. `plan-migracion-cluster.md`: faithful summary
### 3.1 Goal and starting diagnosis
- Source: `plan-migracion-cluster.md:1-27`.
- Goal: move from a stateful, single-instance FastAPI monolith with embedded worker and SQLite to a worker cluster.
- Kubernetes choice: self-managed k3s on owned VPS infrastructure.
- Scaling signal: queue depth.
- Database target: PostgreSQL.
- Data movement: a short maintenance window with complete dump/load, including audit data.
- The plan explicitly says its changes should remain in the working tree without commits. That was a plan-specific instruction, not a V3 architecture decision.
- Existing job queue: `playwright_jobs_active` with optimistic claims in `app/jobs/manager.py`.
- The plan considers this persistent queue “the best asset” and says it can already serve multiple workers.
- Existing SQLite WAL is the first clustering blocker because replicas cannot share a local DB file.
- Existing worker: singleton created from the FastAPI lifespan, with process-local `Semaphore(MAX_BOTS)`.
- Consequence: every API replica starts a worker and concurrency is not coordinated across replicas.
- Existing recovery cancels all globally pending/running jobs on restart.
- The plan labels that behavior a critical bug because one replica in a rolling update can kill other replicas’ work.
- Existing `worker_id` and `attempts` fields are described as foundations for leases and retries.
- Existing `/health` returns 503 when the **local** worker is not alive, preventing separate API and worker scaling.
- Existing shutdown cancels and marks work `CANCELADO`, which loses in-flight work during deployment.
- MinIO is already centralized through `app/utils/bucket.py`; the plan says no code change is needed for it.
- RSA key material and `JOB_SECRETS_KEY` must be identical at every replica.
- Concurrency multiplies by replica count: the plan calls out `MAX_BOTS=4` and `BROWSER_CONCURRENCY=3` as process-local controls.
### 3.2 Key decisions quoted verbatim
> “Kubernetes **k3s self-managed en VPS propios**.”
> “PostgreSQL como **pod único con volumen persistente** (sin HA por ahora).”
> “Cutover de datos con **ventana de mantenimiento corta** (dump/load completo, incluida auditoría).”
> “API y worker son **la misma imagen**, distinto comando.”
> “La API es stateless y escala libre; el worker es el ‘trabajador’ que se agrega a demanda.”
> “un deploy nunca cambia la semántica de un job en vuelo”
> “Cada fase es independientemente deployable y reversible.”
### 3.3 Target architecture proposed by the cluster plan
- Source: `plan-migracion-cluster.md:30-64`.
- Internet enters through k3s Traefik Ingress.
- A stateless API Deployment exposes `/api/v1`, `/api/v2`, `/health`, and `/ready`.
- The API does not execute workers in the desired deployment.
- API scales with an HPA based on CPU.
- PostgreSQL is a one-pod StatefulSet with a PersistentVolumeClaim.
- MinIO remains centralized, either outside the cluster or in it.
- An Alembic Job performs schema migrations.
- A KEDA `ScaledObject` queries pending jobs and scales a worker Deployment.
- The worker Deployment runs `python -m app.worker_main`.
- API and workers use the same image, but distinct commands.
- Worker image versions are pinned, so old workers drain old jobs during a release.
### 3.4 Phase 0: correctness prerequisites before cluster work
- Source: `plan-migracion-cluster.md:67-106`.
- Phase 0 is designed to be safe in current Docker Compose, before PostgreSQL or Kubernetes.
- It is mandatory because horizontal scaling otherwise destroys work.
- **0.1 Recovery scope:** change `recover_jobs_after_restart` to accept `worker_id` and `stale_before`.
- With a worker ID, recovery filters to that worker instead of globally cancelling `PENDIENTE|CORRIENDO` rows.
- Orphaned jobs of other workers are to be handled by a reaper, not startup recovery.
- `app/main.py` and `app/jobs/worker.py` should pass their own worker ID.
- **0.2 Graceful drain:** set `_draining=True` so polling stops claiming new jobs.
- Wait for running jobs for `WORKER_DRAIN_TIMEOUT`, default 120 seconds.
- Jobs not actually started should be requeued, rather than cancelled.
- Started jobs that do not finish before the timeout should also be requeued and increment attempts.
- Proposed `requeue_job` sets `PENDIENTE`, clears start/worker/lease values, and increments attempts.
- Compose should give a stop grace period at least as long as the drain timeout.
- **0.3 Leases and reaper:** add `lease_expires_at` to active jobs and index it.
- Running workers refresh the lease at `JOB_HEARTBEAT_INTERVAL` to `now + JOB_LEASE_TTL`.
- Every worker’s poll loop runs `requeue_expired_leases`.
- Expired jobs return to `PENDIENTE`; after `JOB_MAX_ATTEMPTS`, they are cancelled as `system_lease`.
- The plan intentionally does not require a CronJob for that reaper.
- **0.4 Health/readiness split:** `/health` becomes process liveness only and always returns 200 if responsive.
- `/ready` checks `SELECT 1`, schema version, and local worker only when embedded-worker mode is enabled.
- Compose healthcheck should call `/ready`.
- **0.5 Embedded worker toggle:** add `RUN_WORKER_IN_API`, default true for backward compatibility.
- Kubernetes sets that value false for the API and uses a distinct worker process.
- **0.6 Secret leak:** rotate the credential exposed in `readme-registry.md` and replace it with CI secret usage.
### 3.5 Phase 1: PostgreSQL and data migration
- Source: `plan-migracion-cluster.md:109-155`.
- Add `psycopg[binary]==3.2.*` and use `postgresql+psycopg://`.
- Only apply SQLite `check_same_thread` and SQLite PRAGMAs to SQLite URLs.
- Proposed PostgreSQL pool: `pool_size=5`, `max_overflow=5`, `pool_pre_ping=True`, `pool_recycle=1800`.
- The plan warns that total DB connections are replicas times pool capacity.
- Remove or condition the `/code/` SQLite URL normalization hack.
- Make Alembic’s `render_as_batch=True` SQLite-only.
- Add a migration for `lease_expires_at`, later `app_version`, and `idx_jobs_active_claim(status, app_version, created_at)`.
- Stop executing `alembic upgrade head` in every container startup.
- Run migrations in a single Job during a later phase.
- For PostgreSQL claims, use `FOR UPDATE SKIP LOCKED` on pending jobs ordered by creation time.
- Preserve the current optimistic claim path for SQLite test/local environments.
- Add `scripts/migrate_sqlite_to_postgres.py`.
- ETL order: `users`, then `consulta_*_logs`, then job history, then active jobs.
- Use `Base.metadata`, `bulk_insert_mappings`, batches, table counts, and fail on mismatch.
- `pgloader` is noted as less deterministic for JSON/timestamps and not preferred.
- Cutover: drain API to zero, wait for no running jobs, checkpoint/copy SQLite, migrate schema and data, verify counts plus three job/log spot checks, deploy PostgreSQL URL.
- Rollback: redeploy old SQLite tag. Writes during the short migration window are accepted as lost.
### 3.6 Phase 2: separate API and worker services
- Source: `plan-migracion-cluster.md:158-179`.
- Add `app/worker_main.py`.
- It logs to stdout, validates worker config, creates `WorkerManager`, and runs the poll loop through `asyncio.run`.
- It handles SIGTERM/SIGINT by draining with `stop()`.
- It should not import FastAPI or Uvicorn.
- It should write a heartbeat to a DB table or DB record for readiness/reaper use.
- Dockerfile no longer runs Alembic at CMD time.
- Uvicorn remains default command for compatibility.
- Worker command is `python -m app.worker_main`.
- Compose validates the split before k3s: PostgreSQL service, API with no embedded worker, and N worker replicas.
- The proposed worker and API both directly use PostgreSQL.
- A SQLite legacy/development overlay remains.
### 3.7 Phase 3: k3s, Kustomize, and KEDA
- Source: `plan-migracion-cluster.md:182-234`.
- Store manifests under `deploy/k8s/` using native Kustomize `base/` plus `overlays/prod/`.
- Install KEDA and Traefik with Helm.
- Namespace: `mrbot`.
- One Secret holds full environment, `JOB_SECRETS_KEY`, and RSA keypair.
- A ConfigMap holds non-secret configuration.
- Create an imagePullSecret for `docker.abp.net.ar`.
- PostgreSQL remains one StatefulSet replica with PVC.
- API starts with two replicas, embedded worker disabled, and HPA CPU scaling.
- Worker Deployment is KEDA-managed and should not fix `replicas` itself.
- Alembic Job is a PreSync ArgoCD hook or CI step.
- Ingress routes `/api/*`, `/health`, and `/ready` with TLS.
- NetworkPolicy allows API/worker only to PostgreSQL and MinIO; worker has no ingress.
- KEDA query counts pending job rows.
- Proposed worker floor is one replica to avoid Chromium cold start.
- Proposed maximum is 20 replicas.
- KEDA polls every five seconds with 300 second cooldown.
- Target is two pending jobs per worker replica.
- Playwright requirements: memory-backed `/dev/shm` with size limit, because Chromium crashes without it under concurrency.
- Suggested worker request/limit: 500m/1Gi request and 2 CPU/2Gi limit.
- K8s pod PID controls do not directly map from Compose `pids_limit`.
- `terminationGracePeriodSeconds` must meet/exceed drain timeout.
- API rolling updates should use `maxUnavailable: 0`.
- The exact same `JOB_SECRETS_KEY` and read-only RSA keypair must be shared by API and workers or queued credential decryption fails.
### 3.8 Phase 4: continuous deployment and version-pinned jobs
- Source: `plan-migracion-cluster.md:237-273`.
- `VERSION` becomes the immutable image tag/SHA.
- Add `app_version` to active and history jobs, populated on job creation.
- Claims filter jobs by `app_version`, allowing old workers to drain old jobs and new workers to take new jobs.
- Old-version pending jobs older than `JOB_VERSION_STALE_MINUTES`, default 15, are cancelled as `system_stale_version`.
- Schema changes use expand/contract releases: nullable addition, deployment, backfill, later contraction.
- Existing CI is described as compile, offline pytest, parity, Playwright gate, build plus Trivy plus gitleaks.
- CI should push registry images by branch/tag and record the immutable digest.
- Trivy HIGH/CRITICAL failure remains required.
- Recommended CD: ArgoCD GitOps with an Alembic PreSync hook.
- Minimum CD: GitHub Actions running `kubectl apply -k`, rollout status, and migration Job.
- Both approaches must pin image **digests**, not `latest`.
- Release flow: bot/executor PR, CI/parity/browser gate, merge, build/scan/push, expand migration, API/worker deployment, drain old jobs, safe rollback by prior digest.
### 3.9 Proposed file list, verification, risks, and sequencing
- Source: `plan-migracion-cluster.md:276-355`.
- Modify DB engine, job manager, worker, job config, main, job models, Alembic env, requirements, Dockerfile, Compose, CI, and registry README.
- Create worker entrypoint, migration, SQLite-to-PostgreSQL script, k8s base/production manifests, k8s README, and worker heartbeat persistence.
- Required tests: PostgreSQL single-winner claims, scoped recovery, graceful drain, expired leases/retry ceiling, version pinning, and migration counts.
- Local validation: two workers, twenty jobs, no duplicate work, total concurrency approximately workers times browser concurrency.
- Local failure validation: restart one worker mid-job and let the other resume it.
- k3s validation: enqueue 50 jobs, observe KEDA scaling, drain back to minimum replicas, update during jobs, prove no `system_restart` cancellation, test Chromium `/dev/shm`, and rollback schema/image.
- Cutover validation: SQLite/PostgreSQL counts, samples, and Alembic at head.
- Risks: single PostgreSQL pod is an SPOF, SQLite/PostgreSQL type changes, KEDA host saturation, ARCA rate limits, inconsistent secrets, concurrent migrations, and deployments with live jobs.
- Proposed mitigations include daily `pg_dump` to MinIO, deterministic ETL, replica/resource caps, optional distributed Redis semaphore/fair share, shared Secret, one Alembic Job, pinning plus drain.
- Execution order: phase 0 correctness, phase 1 PostgreSQL, phase 2 split services in Compose, phase 3 k3s/KEDA, phase 4 CI/CD/version pinning.
### 3.10 Decisions already made versus remaining design questions
| Topic | Status in cluster plan | V3 handling note |
|---|---|---|
| Orchestrator platform | Decided: self-managed k3s on owned VPS | Reuse only if V3 infrastructure decision remains accepted. |
| Database | Decided: PostgreSQL one pod/PVC, no HA initially | V3 requires PostgreSQL, but decide managed/HA posture separately. |
| Data cutover | Decided: short maintenance window, full audit migration | Applicable only if V3 imports V2 historical data. |
| API/worker image | Decided: same image, different command | V3 monorepo may build separate images or shared base. This is open for V3. |
| Job source of truth | Decided: PostgreSQL job tables | Compatible for central API only. Not compatible for direct worker DB access. |
| Queue scaling | Decided: KEDA queries database | Conflicts with V3 central-API assignment model. |
| Worker health | Suggested DB heartbeat | V3 requires worker-reported health and depth to central API. Protocol is open. |
| Worker max jobs | No fixed per-worker value in plan | V3 fixes this at five. |
| Rate fairness | Optional future Redis semaphore and fair share | V3 should decide whether tiers/credits drive fairness. |
| Payments | Not addressed | V3 must design MercadoPago subscription and top-up flows. |
| IDs | Not addressed in cluster plan | V3 must enforce UUIDv4 user IDs and UUIDv7 bot/job table IDs. |
| User timestamps/reset | Not addressed | V3 must omit the three specified user columns. |
| V1/V2 sync compatibility | Plan intentionally retains both routes | V3 must choose deprecation/migration behavior. |
### 3.11 Conflicts with the explicit V3 requirements
| V3 requirement | Cluster-plan statement or implication | Assessment |
|---|---|---|
| Central API assigns jobs to healthy workers | Worker polls and claims directly from PostgreSQL; KEDA also queries PostgreSQL directly. | **DIRECT CONFLICT.** Workers must not claim from DB in V3. |
| Workers have no database | `WorkerManager(session_factory=SessionLocal)`, DB heartbeat/lease/reaper, NetworkPolicy to PostgreSQL. | **DIRECT CONFLICT.** V3 worker interface must call only central API. |
| Secondary worker API answers only central API | Plan says worker has no ingress and does not define an assignment/result API. | **DIRECT CONFLICT / missing capability.** V3 needs authenticated private ingress or reverse connection protocol. |
| Central API load-balances | KEDA scales deployments from a SQL count, while workers self-select jobs. | **DIRECT CONFLICT.** KEDA may remain infrastructure autoscaling, but not as the execution allocator. |
| Worker reports health and queue depth | Heartbeat is proposed in DB, not an API report; no queue-depth contract. | **PARTIAL CONFLICT.** Replace with central control-plane reporting. |
| Five simultaneous jobs per worker | Plan uses configurable process-local `MAX_BOTS` and `BROWSER_CONCURRENCY`; examples are 3/4 and peak multiplies by replicas. | **REQUIRES CHANGE.** Enforce hard V3 maximum of 5 at worker and scheduler. |
| Async only, sync deprecated | API Deployment exposes V1/V2. Older V1 routes execute inline. | **DIRECT CONFLICT** unless V3 retains legacy only behind an explicit deprecation adapter. |
| User UUIDv4 and bot/job UUIDv7 IDs | Plan is silent. Earlier V2 documents retain integer user foreign keys. | **MISSING / likely conflict with inherited schema.** Make IDs a V3 schema invariant. |
| No sequential IDs anywhere | Plan does not specify replacing legacy numeric primary keys. | **MISSING.** V3 schema/migrations must prevent sequence IDs. |
| Drop user `fecha_ultimo_reset`, `created_at`, `updated_at` | Plan does not address user model redesign. | **MISSING.** V3 must not copy V2 user model. |
| Tier quotas, credit top-ups, MercadoPago | Not addressed. | **MISSING.** This is new V3 domain work. |
| Monorepo and per-part READMEs | Plan describes one application/image and k8s manifests. | **NOT A LOGICAL CONFLICT**, but the V3 repo layout must supersede this packaging choice. |
## 4. `PLAN.md` and `ORIGINAL_REQUEST.md`
### 4.1 `PLAN.md`
- Source: `PLAN.md:1-79`.
- This is not the cluster migration plan.
- It is a plan to centralize safe public error handling while preserving V1/V2 response shapes and HTTP status codes.
- It prohibits public exposure of Playwright details, selectors, internal URLs, local paths, MinIO details, OpenSSL, tracebacks, and internal component names.
- It proposes `app/utils/public_errors.py` with categories for validation, authentication, additional validation, disabled service, external timeout, navigation, query, download, processing, storage, and unexpected errors.
- Exceptions are classified by type/context, never by copying `str(exc)` into public output.
- Expected business errors remain specific through an allowlist.
- Technical diagnostics are retained only in internal logs with bot, functional stage, and job ID, excluding sensitive payloads/credentials/URLs.
- It requires changes across bots, utilities, executors, worker, V2 factory, V1 routes, FastAPI handlers, and historical-read sanitization.
- It requires no V1/V2 field/type/shape changes, no new public error codes, and no schema migration.
- It preserves job fields including `status`, `result`, `error`, `files`, and `data`.
- It asks for synthetic exception category tests, checks across V1/V2 polling/cancel/history surfaces, caplog diagnostic checks, and a static ban on public `str(exc)` assignments.
- It explicitly retains V1/V2 parity, HTTP status, partial result, persistence, and cancellation testing.
### 4.2 `ORIGINAL_REQUEST.md`
- Source: `ORIGINAL_REQUEST.md:1-35`.
- Timestamped original request: 2026-09-02.
- It asked for a final exhaustive audit/certification of `PLAN.md`, technical-error sanitization, V1 parity against `BD-api-v1`, and confirmation of completion.
- R1 lists the central utility, full `except` audit, consumer-layer protections, historical DB sanitization, and backward-compatible HTTP contracts.
- R2 asks to execute selected safe-error tests and `scripts/execute_parity_runner_and_audit.py` across 513 cases from 27 `BD-api-v1/requests-data/` modules.
- R3 asks to verify work on `feat/verify-plan-safe-errors`, no direct master commits, and reports under `outputs/`.
- Acceptance criteria called for every plan item audited, 74/74 pytest tests, 513/513 cases at 100% functional parity and zero leaks, correct branch, and a final report.
- This request explains the strong safe-error/parity focus of many current commits and `.agents` artifacts.
## 5. Current public contract documented in `README.md` and `doc.md`
### 5.1 `README.md`
- Source: `README.md:1-257`.
- Product label: “Mis Comprobantes API”, a FastAPI service for asynchronous AFIP receipt consultation.
- Development prerequisites: Docker/Compose and Python 3.9+.
- It documents SMTP and MinIO TLS as mandatory, with invalid/false TLS rejected without insecure fallback.
- It states sqlite-web binds only to `127.0.0.1:5011` and receives only its database/password variables.
- It documents RSA-3072 OAEP-SHA256/Base64 credential transport.
- Clients fetch `GET /api/v1/security/public-key`.
- RSA generation is manual through `python scripts/generate_rsa_keys.py`.
- It says clients must never access the private key and key rotation must replace a matched pair atomically.
- CORS defaults to public origins without credentials, with explicit allowlist/disabled options.
- It documents the bot development path: draft, logger, clean, mrbot, then application integration.
- Clean/dev bot code should keep MinIO/tempfile behavior aligned with production.
- Upload bots place incoming files in temporary object storage and executors download them to a temp directory.
- Docker startup shown: `docker-compose up --build`.
- Local startup shown: dependencies, Playwright Chromium/install-deps, then `uvicorn app.main:app --reload`.
- Documented user routes are `POST /api/v1/users/`, `POST /api/v1/users/reset-key/`, and `GET /api/v1/users/consultas/{email}`.
- The documented receipt operation is `POST /api/v1/comprobantes/consulta` with `X-API-Key` and a body containing dates, CUITs, represented name, and password.
- Swagger/ReDoc are documented at `/docs` and `/redoc`.
- CI’s offline command excludes `test_endpoints.py`, `test_single.py`, `parity`, and `playwright_gate`.
- The parity job runs `tests/test_parity_v1_v2_with_bd_samples.py` separately.
- Live endpoint scripts need a running API, `test.env`, and valid credentials.
- GitHub live tests are manual `workflow_dispatch`, protected by `MRBOT_TEST_ENV`, and remove `test.env` afterward.
### 5.2 `doc.md`
- Source: `doc.md:1-115`.
- It describes REST APIs for AFIP/ARCA services including Mis Comprobantes, RCEL, SCT, CCMA, SIPER, apócrifos, and CUIT lookup.
- It describes API-key authentication, user management, audit logs, Docker, and local Uvicorn.
- It maps application modules: main, router aggregator, dependencies, bots, SQLAlchemy models, Pydantic schemas, utility functions, and Docker files.
- It documents user creation, API-key reset, monthly balance, API-key/email validation, user enabled check, and monthly reset behavior.
- It lists V1 route families below.
| Documented route under `/api/v1` | Method | Documented behavior |
|---|---:|---|
| `/mis_comprobantes/consulta` | POST | Receipt consultation with files/base64/MinIO URLs or JSON. |
| `/mis_comprobantes/logs` | POST | Filtered Mis Comprobantes logs. |
| `/rcel/consulta` | POST | Download issued invoices and return invoice list/MinIO URLs. |
| `/rcel/procesar_pdf` | POST | Upload/process a PDF and return extracted JSON. |
| `/sct/consulta` | POST | Tax accounts query with base64 or URL output. |
| `/ccma/consulta` | POST | Monotributista/autónomo current account. |
| `/siper/consulta` | POST | Risk profile and optional report artifacts. |
| `/apoc/consulta/{cuit}` | GET | Apocryphal taxpayer lookup. |
| `/consulta_cuit/individual` | POST | Single registration certificate lookup. |
| `/consulta_cuit/masivo` | POST | Multiple certificate lookup. |
| `/user` | POST | User/API-key creation. |
| `/user/reset-key` | POST | API-key reset. |
| `/user/consultas/{email}` | GET | Remaining monthly calls. |
| `/health` | GET | Simple service-health response. |
- `doc.md` says operations are currently synchronous in the request thread and recommends future FastAPI background tasks or a queue.
- It recommends possible future microservice splitting, which was later superseded by V2’s job system and the cluster plan.
- It recommends storage selection flags and central bucket helpers.
- It recommends HTTPS, API-key rotation, CORS configuration, and optional IP/duration logging.
### 5.3 Documentation drift to account for in V3
- `README.md` is not a complete V2 route reference. It foregrounds older V1 names and only partially describes V2 jobs.
- `doc.md` is older: it says all operations run in-request, while V2 has asynchronous job routes documented under `docs/migration_v2/API_SPEC.md`.
- `README.md` says Docker API is at localhost port 8000, while current `docker-compose.yml:9` publishes host `5010:8000`.
- `readme-registry.md:171` says Compose exposes `5008:8000`, but current Compose publishes `5010:8000`.
- `README.md` tells readers to create `.env` under `app`; current Compose uses root `.env` through `env_file: .env`.
- Treat these conflicts as documentation drift, not reliable V3 contract requirements.
## 6. `readme-registry.md`: registry and deployment concept
- Source: `readme-registry.md:1-172`.
- It documents building, tagging, pushing, listing, pulling, and Compose deployment of a V2 Docker image.
- Private registry host: `docker.abp.net.ar`.
- Image namespace: `abustosp/api-bots-mrbot-v2`.
- Required environment variable names: `REGISTRY_URL`, `REGISTRY_USER`, `REGISTRY_PASS`.
- Login uses password stdin, which is the correct command pattern.
- Build uses `docker build -t abustosp/api-bots-mrbot-v2:latest .`.
- It pushes `latest` and a date/timestamp tag.
- It suggests checking tags using Registry API v2 authentication.
- Server deployment is currently Docker Compose, pull latest then recreate `api-bots-mrbot`.
- `build-and-push.sh` automates load-env, login, build, tag, and pushing both latest/timestamp labels.
- The document includes an actual registry credential in its deployment example.
- The cluster plan explicitly identifies that as a leaked credential requiring rotation and documentation replacement.
- This report does not reproduce that credential.
- The V3 registry concept should retain: private registry, CI secrets, image-pull credentials, immutable tags/digests.
- The V3 registry concept should reject: committed/documented plaintext credentials and reliance on mutable `latest` for deployment.
## 7. Test inventory and V3 regression assessment
### 7.1 Test execution taxonomy
- Source: `pytest.ini`, `.github/workflows/ci.yml`, `README.md:163-257`, `tests/TEST_README.md`.
- `pytest.ini` has three declared markers: `playwright_gate`, `parity`, and `live`.
- Default collection path is `tests`.
- “Offline” below means no real browser, external network, external API, production MinIO, or real credentials are intended.
- Several offline HTTP tests use `httpx.ASGITransport`. That is in-process HTTP contract testing, not a network dependency.
- “External local fixture” means a fixed local V1 checkout/database path may be required, but not remote network access.
- “Adapt” means assertions are valuable but paths/models/contracts are V2-specific.
- “High” means a V3 equivalent should be preserved early.
### 7.2 Test files
### `tests/test_admin_html_safe_errors.py`
- Covers safe, catalogued errors in admin HTML/download surfaces and correlation-ID exposure boundaries.
- Dependency: offline rendering/ASGI-style checks; no real browser or network required.
- V3 suitability: **High, adapt** to the new central admin panel and correlation/error contract.
### `tests/test_admin_jobs.py`
- Covers admin job listing and cancellation of pending/running jobs, missing jobs, and bulk cancellation.
- Dependency: in-process async tests with test DB; no real browser or external network indicated.
- V3 suitability: **High, adapt** for central-admin worker/job controls.
### `tests/test_adversarial_concurrency.py`
- Covers high contention, fault injection, cancellation races, crash recovery, multi-worker contention, registry coverage, and Playwright cancellation cleanup.
- Dependency: synthetic/fake executors for most cases; no real browser/network should be required by default.
- V3 suitability: **High, redesign assertions** around scheduler assignment, worker API reports, and five-job cap.
### `tests/test_adversarial_safe_errors.py`
- Covers technical-error leak denial across exception types, nested payloads, endpoints, worker status, historical logs, AST audit, and fuzzing.
- Dependency: offline mocks/ASGI and local data; no live browser or remote network required.
- V3 suitability: **High, carry forward** for central API and worker-result sanitization.
### `tests/test_adversarial_safe_errors_stress_gate.py`
- Covers hostile exceptions, category rendering, FastAPI routes, historical states, AST leak audit, and obfuscation/bypass attempts.
- Dependency: offline synthetic/ASGI tests; no real browser/network required.
- V3 suitability: **High, carry forward** as a security gate.
### `tests/test_adversarial_storage.py`
- Covers adversarial persistence sanitization, externalization, rehydration, dynamic signing, and parity behavior.
- Dependency: intended synthetic storage/test configuration, not live object storage.
- V3 suitability: **High, adapt** if V3 retains object storage and presigned-result delivery.
### `tests/test_adversarial_storage_fuzz.py`
- Covers URL-sanitization fuzzing, business-key preservation, and deep-nesting stress.
- Dependency: offline pure/persistence logic; no browser/network.
- V3 suitability: **High, carry forward** unchanged in spirit.
### `tests/test_api_key_digest.py`
- Covers HMAC storage/authentication, legacy plaintext upgrade, non-exposure during creation/reset/admin, and migration behavior.
- Dependency: offline SQLite/ORM/template tests; no browser/network.
- V3 suitability: **High, adapt** to UUIDv4 users and V3 authentication design.
### `tests/test_arca_captcha.py`
- Covers ARCA CAPTCHA parsing/detection/fill logic and mocked CapMonster behavior without contacting ARCA or CapMonster.
- Dependency: offline mocks; no live browser/network.
- V3 suitability: **Medium.** Retain within bot-worker repository tests.
### `tests/test_audit_acceptance.py`
- Covers public interfaces across factory, manager, router, executors, storage metadata, registry packaging, cancellation, isolation, and validation.
- Dependency: in-process ASGI plus fakes; no real browser/network.
- V3 suitability: **High, split** into central API acceptance and worker protocol acceptance suites.
### `tests/test_authorization_surfaces.py`
- Covers admin authorization for user operations, generic user responses, V2 log wrapper auth, and job-route API-key dependencies.
- Dependency: offline ASGI/ORM.
- V3 suitability: **High, adapt** for central roles/admin/users and payment surfaces.
### `tests/test_auxiliary_transport_security.py`
- Covers Docker context exclusions plus secure MinIO TLS construction and fail-closed rehydration behavior.
- Dependency: static/mocked network clients; no real network/browser.
- V3 suitability: **High, retain** for every service image and object-storage client.
### `tests/test_batch_status.py`
- Covers batch status, request limits, auth, queue-full `Retry-After`, and idempotent creation.
- Dependency: in-process ASGI/test DB.
- V3 suitability: **High, adapt** to central API job batch/status semantics.
### `tests/test_bot_builder_runner_cleanup.py`
- Covers bot-builder closure of context/browser when a flow raises.
- Dependency: fake/mocked Playwright lifecycle, no real Chromium/network.
- V3 suitability: **Medium.** Keep in bot worker development tooling.
### `tests/test_browser_limiter_lifecycle_gate.py`
- Covers event-loop/thread-local semaphore isolation and release on timeout/cancellation.
- Dependency: fully synthetic; explicitly no external browser/service/distributed coordination.
- V3 suitability: **High, adapt** local worker limiter to hard maximum five.
### `tests/test_challenger_concurrency_gate.py`
- Covers V1 semaphore, V2 burst 202 behavior, UUIDv7 shape, collision resistance, combined load, and multi-worker duplicate prevention.
- Dependency: synthetic ASGI/test DB/executors; no real browser or remote network.
- V3 suitability: **High, rewrite** to test central allocator and worker capacity reporting.
### `tests/test_cierre_legacy_multipart.py`
- Covers encrypted multipart credential handling and the legacy IVA Simple V1 route contract.
- Dependency: in-process HTTP tests; no real browser/network.
- V3 suitability: **Medium.** Preserve only if V3 maintains the legacy migration adapter.
### `tests/test_clean_urls.py`
- Covers cleanup of legacy URLs in SQLite JSON/text/job tables and nonzero verification failure when URLs remain.
- Dependency: local temporary SQLite and script subprocess behavior; no browser/network.
- V3 suitability: **High, adapt** as data-migration hygiene test.
### `tests/test_controladores_fiscales_upload_http.py`
- Covers public HTTP rejection of unsafe uploads before bot execution.
- Dependency: in-process ASGI, no external network/browser.
- V3 suitability: **High, retain** in central upload intake or worker-upload protocol.
### `tests/test_controladores_fiscales_upload_security.py`
- Covers zip traversal, absolute/nested paths, allowed PEM extraction, symlinks, duplicates, atomic validation, and basename reduction.
- Dependency: offline filesystem fixtures, no network/browser.
- V3 suitability: **High, retain** near upload-processing boundary.
### `tests/test_cors_configuration.py`
- Covers public/allowlist/disabled CORS and invalid configuration rejection.
- Dependency: synthetic FastAPI application, no network/browser.
- V3 suitability: **High, retain** for central API.
### `tests/test_credential_aliases.py`
- Covers JSON/multipart fiscal credential aliases, canonical mapping, conflict rejection, and generated schema exposure.
- Dependency: offline Pydantic/form tests.
- V3 suitability: **Medium.** Preserve bot request compatibility if it remains public.
### `tests/test_cuit_validation.py`
- Contains no collected `test_*` function according to static AST inventory.
- Dependency: not a runnable regression suite in its present form.
- V3 suitability: **Low until repaired.** GUESS: it is a stale/support file rather than an active test.
### `tests/test_deployment_hardening.py`
- Covers static Docker/Compose hardening: init reaping, PID limit, healthcheck, Docker context exclusion, and sqlite-web isolation.
- Dependency: static file inspection only; no container build/network/browser.
- V3 suitability: **High, rewrite** for central API and worker images, Kubernetes manifests, non-root, probes, and secrets.
### `tests/test_dual_app_inventory_gate.py`
- Covers separate-interpreter inventory parity of V1 and V2 apps, including cleanup if child launch fails.
- Dependency: subprocess and local V1/V2 source availability; no remote network/browser.
- V3 suitability: **Medium/High** during V2-to-V3 migration if V2 checkout remains available.
### `tests/test_dual_app_multipart_contract.py`
- Covers V1/V2 multipart validation/success in separate processes to avoid importing the wrong top-level `app` package.
- Dependency: subprocess plus local V1/V2 sources; no remote network/browser.
- V3 suitability: **High for migration adapters**, otherwise medium.
### `tests/test_empirical_concurrency_gate1.py`
- Covers V1 and browser semaphore bounds, V2 queue bursts, UUIDv7, worker concurrency, cancellation/failure, and dataset samples.
- Dependency: synthetic workloads/ASGI/test DB; no live browser/network intended.
- V3 suitability: **High, redesign** around per-worker capacity 5 and central dispatch.
### `tests/test_endpoints.py`
- CLI-style live suite for APOC and multiple ARCA/AFIP endpoints.
- Dependency: **requires running API, `test.env`, valid credentials, and remote network/external services**.
- V3 suitability: **Medium.** Keep as manually enabled end-to-end smoke suite after endpoint migration.
### `tests/test_executor_mc.py`
- Covers Mis Comprobantes executor success/error persistence, files, worker deduplication, and quota behavior.
- Dependency: mocked bot/storage/test DB; no real browser/network.
- V3 suitability: **High.** Move to worker service contract/unit tests.
### `tests/test_factory_multipart.py`
- Covers temp object keys/buckets, multipart job creation, named files, compatibility fields, and pre-enqueue validation.
- Dependency: in-process ASGI/mocked storage; no live network/browser.
- V3 suitability: **High, split** between central intake/upload and worker payload retrieval.
### `tests/test_factory_rehydratacion.py`
- Covers JSON key tokens, nested data injection, ambiguity behavior, completed-job rehydration, and plain data preservation.
- Dependency: mocked/local storage paths; no browser/network.
- V3 suitability: **High** if central API owns result retrieval/rehydration.
### `tests/test_factory_url_regeneration.py`
- Covers regenerating presigned URLs from sibling object keys, nesting, no-overwrite behavior, and fallback cases.
- Dependency: mocked storage, no real network/browser.
- V3 suitability: **High** if central API returns artifacts.
### `tests/test_fiscal_credential_logging.py`
- Covers redacting fiscal credentials from ORM reads, all fiscal log columns, admin read surface, and retention/audit behavior.
- Dependency: offline ORM/ASGI.
- V3 suitability: **High, retain** for central audit/log design and worker result redaction.
### `tests/test_helpers_force_inline.py`
- Covers forced-inline versus externalized JSON helper behavior.
- Dependency: offline mocked helper, no browser/network.
- V3 suitability: **Medium/High** if result externalization stays.
### `tests/test_job_lifecycle_operational.py`
- Covers limiter cancellation, worker browser slot, atomic claims/quota, cancellation quota, recovery idempotence, and lifespan readiness failure.
- Dependency: explicitly synthetic executors and SQLite; no real browser/network.
- V3 suitability: **High, rewrite** claims/recovery into central lease/assignment tests.
### `tests/test_job_manager.py`
- Covers registry membership, create/get/claim/complete, cancellation, isolation, restart recovery, validation, FIFO, and no raw text SQL.
- Dependency: offline SQLite/ORM; no browser/network.
- V3 suitability: **High, rewrite** database manager tests for central API only.
### `tests/test_job_secrets.py`
- Covers encrypted runtime secret round trip without plaintext database storage.
- Dependency: offline crypto/test DB.
- V3 suitability: **High.** V3 workers need a secure task-secret protocol without direct DB reads.
### `tests/test_jobs_cierre.py`
- Covers sequential/concurrent idempotency and per-user limits across JSON/multipart jobs.
- Dependency: in-process HTTP/test DB.
- V3 suitability: **High, adapt** to quotas/credits and dispatch idempotency.
### `tests/test_jobs_lifecycle.py`
- Covers history purge, invalid purge settings, idempotency variants, and pending queue depth.
- Dependency: offline SQLite/ORM.
- V3 suitability: **High, adapt** to central retention/metrics and reported worker depth.
### `tests/test_jobs_retention_metrics.py`
- Covers periodic retention/shutdown, percentile metrics, admin metric auth, and configured retention.
- Dependency: in-process async/test DB; no external browser/network.
- V3 suitability: **High**, especially unavailable-worker and queue/latency admin metrics.
### `tests/test_mis_comprobantes_v1_aliases.py`
- Covers legacy V1 aliases accepted by V2 Mis Comprobantes schema.
- Dependency: offline Pydantic/schema tests.
- V3 suitability: **Medium** if V3 must retain client payload aliases.
### `tests/test_mis_retenciones_iva_simple_contract.py`
- Covers date constraints, optional SIAP flag, registry/executor/log model registration.
- Dependency: offline schema/registry tests.
- V3 suitability: **Medium.** Bot-specific worker regression.
### `tests/test_paridad_v1body_facturometro_pagodev.py`
- Covers exact V1 `data` body shape for Facturometro and Pago Devoluciones, including presigned artifact output.
- Dependency: offline mocked helpers.
- V3 suitability: **High migration parity** for these bot responses.
### `tests/test_paridad_v1body_libros_iva.py`
- Covers V1 response-body parity for Libros IVA.
- Dependency: offline unit test.
- V3 suitability: **High migration parity** for that bot.
### `tests/test_paridad_v1body_moa.py`
- Covers MOA V1-body parity for success and error cases.
- Dependency: offline unit test.
- V3 suitability: **High migration parity** for that bot.
### `tests/test_paridad_v1body_srt_mipyme.py`
- Covers V1 body keys/values/errors and MIPYME URL behavior.
- Dependency: offline unit test.
- V3 suitability: **High migration parity** for these bots.
### `tests/test_paridad_v1body_vepccma_controladores.py`
- Covers V1 body parity for VEP CCMA and Controladores Fiscales.
- Dependency: offline unit test.
- V3 suitability: **High migration parity**, including upload-sensitive results.
### `tests/test_paridad_v1body_vep_pagosvep.py`
- Covers V1 body parity for VEP uploads/errors and VEP payment query.
- Dependency: synthetic/mocked storage behavior; no remote network/browser intended.
- V3 suitability: **High migration parity**.
### `tests/test_parity_v1_v2_with_bd_samples.py`
- Marked `parity`; tests two successful samples per scraping/download endpoint using fixtures extracted from `BD-api-v1`.
- Dependency: in-memory SQLite, in-process ASGI, mocked bot handlers and HTTPX ASGI transport; **no live browser or remote network**.
- V3 suitability: **Critical.** Port fixtures/comparison expectations to V2-versus-V3 semantic parity.
### `tests/test_playwright_orphan_pids_gate.py`
- Marked `playwright_gate`; measures actual OS Chromium child processes through `/proc` across success, exception, timeout, cancellation, and shutdown.
- Dependency: **requires Playwright Chromium installed and launches a real browser**, but does not need live ARCA/network.
- V3 suitability: **Critical.** Retain in each worker image/CI gate.
### `tests/test_public_errors.py`
- Covers catalog completeness, type/context classification, allowlist behavior, nested sanitization, success/error envelopes, safe labels, and no exception interpolation.
- Dependency: offline unit/AST behavior; no browser/network.
- V3 suitability: **Critical.** Preserve centrally and at worker-result boundary.
### `tests/test_public_errors_remediation.py`
- Covers Pydantic validation detail removal and nested public-envelope sanitization.
- Dependency: offline unit test.
- V3 suitability: **High.** Preserve public error contract.
### `tests/test_public_exception_handlers.py`
- Covers HTTP status preservation, nested sanitization, admin Basic auth challenge, unhandled errors, correlation headers, and JSON boundary sanitization.
- Dependency: offline FastAPI/ASGI tests.
- V3 suitability: **High.** Adapt central API exception middleware.
### `tests/test_rsa_credentials.py`
- Covers RSA round trip/public-key contract, encrypted/plaintext schema behavior, active-job/log storage boundary, and invalid Base64 non-echo.
- Dependency: offline cryptographic tests.
- V3 suitability: **High, redesign** secret delivery so no worker database is needed.
### `tests/test_run_moa_dedicated_import.py`
- Covers dedicated MOA runner imports under synthetic environment.
- Dependency: offline import test.
- V3 suitability: **Medium**, bot-worker packaging regression.
### `tests/test_sensitive_storage_boundary.py`
- Covers recursive sensitive-key redaction, ORM bind redaction, and API-key function under JSON redaction.
- Dependency: offline storage/ORM tests.
- V3 suitability: **High.** Preserve around central database/audits.
### `tests/test_single.py`
- CLI helper for one live endpoint module.
- Dependency: **requires live running API and its configured network/credentials** when used as intended.
- V3 suitability: **Medium** manual diagnostic tool, not CI regression.
### `tests/test_smoke_compare.py`
- Covers normalized JSON comparison, link preservation, and failure on non-202/nonterminal jobs.
- Dependency: offline comparison fixtures; no live browser/network.
- V3 suitability: **Critical.** Reuse for V2-to-V3 asynchronous contract migration.
### `tests/test_storage_no_url.py`
- Covers object-key return, S3 error behavior, presigned URLs, expiry env, deprecated wrapper, and prevention of persisted URLs.
- Dependency: mocked MinIO/S3 clients; no real network/browser.
- V3 suitability: **High.** Preserve central artifact policy.
### `tests/test_transport_security.py`
- Covers secure SMTP/MinIO defaults, invalid TLS refusal, no connection on invalid configuration, and no insecure source fallback.
- Dependency: offline mocks/static inspection.
- V3 suitability: **High.** Retain for service configuration.
### `tests/test_typed_error_status.py`
- Covers status mapping from typed categories rather than exception text and legacy business messages.
- Dependency: offline unit tests.
- V3 suitability: **High.** Preserve error/status semantics where externally compatible.
### `tests/test_user_secret_exposure.py`
- Covers user response schemas/templates and recursive removal of API-key/digest values.
- Dependency: offline Pydantic/Jinja/SQLAlchemy/ASGI-style checks.
- V3 suitability: **High.** Adapt to UUIDv4 user identity and new subscription/credit output schemas.
### `tests/test_v1_database_sanitization.py`
- Covers read-only sanitization of error fields in three V1 SQLite snapshots and asserts their SHA-256 remains unchanged.
- Dependency: **external local fixture** at `../BD-api-v1`; skips unavailable snapshots; no network/browser.
- V3 suitability: **High migration evidence** if V2/V1 audit history is imported or exposed.
### `tests/test_v2_endpoints.py`
- Covers V2 202 creation, polling states, status/error/file contracts, and input validation through in-process HTTP.
- Dependency: in-memory SQLite plus HTTPX ASGI; no real browser/network.
- V3 suitability: **Critical.** Rewrite route names but retain async lifecycle expectations.
### `tests/test_worker_max_bots.py`
- Enqueues five jobs with `MAX_BOTS=2`, tracks executor and DB active count, and asserts cap/release/completion.
- Dependency: fake executor and in-memory SQLite; no real browser/network.
- V3 suitability: **Critical.** Change to five maximum per worker and central allocation.
### 7.3 Parity suites specifically worth preserving
- `tests/test_parity_v1_v2_with_bd_samples.py` is the named CI `parity` suite.
- It uses representative historical request bodies but mocks bot execution.
- It is an executable semantic shape regression, not a live ARCA certification.
- `tests/test_paridad_v1body_*.py` files test exact V1 response `data` shapes by bot.
- `tests/test_smoke_compare.py` tests comparison semantics: V2 must return 202 and a terminal result, and must not lose V1 artifacts.
- `tests/test_dual_app_inventory_gate.py` and `tests/test_dual_app_multipart_contract.py` avoid accidental same-package import comparisons by using subprocesses.
- `test-request-versions/` is the live comparison harness for external V1 and V2 targets, separate from pytest.
- `scripts/execute_parity_runner_and_audit.py` is a larger synthetic execution/security audit harness over 513 V1 dataset requests.
- V3 should retain a layered approach: static inventory, per-bot semantic fixtures, in-process async API parity, and separately configured live smoke comparison.
## 8. Recent Git history and in-flight direction
### 8.1 Branch state
- Command inspected: `git branch -a`.
- Active branch: `fix/cierre-parcial-a-go-20260914`.
- Local branches: `feat/verify-plan-safe-errors`, `fix/cierre-parcial-a-go-20260914`, `master`, `release/v2-candidate`.
- Remote branches observed: `origin/master`, `origin/release/v2-candidate`.
- This does not show a branch implementing the cluster migration plan.
### 8.2 Direction of the most recent commits
- Command inspected: `git log --oneline -60`.
- The top seven commits are a closing/hardening series from 2026-09-14.
- `1a7d488` automates retention and exposes admin metrics.
- `9e23de7` classifies errors by type without materializing exception text.
- `9a0d345` fixes correlation-ID and per-user limit regressions.
- `c8575fa` makes correlation IDs traceable in logs.
- `a695c95` sanitizes admin errors and adds tracing.
- `67d98ad` restores legacy IVA Simple route and encrypted multipart coverage.
- `39efd44` fixes bot development runner closure and restores IVA Simple content.
- `d53fbec` adds job idempotency and per-user limits.
- `0489cb7` cleans legacy URLs and updates supporting documentation.
- `72fdaf5` separates ARCA CAPTCHA informational logs from error logs.
- `92dea7b` refactors Aportes en Línea to shared ARCA login/service helpers.
- `e11c1e3` adds optional automatic ARCA CAPTCHA resolution.
- `245b412` improves CAPTCHA handling and login retries.
- `d40bc22` adds fiscal credential aliases.
- `60d6e86` propagates IVA Simple retentions into V2.
- `9eae6f4` adds RSA credential encryption for V2.
- `e259b0a` adds audited fiscal-log retention.
- `bc9bbb4` restores admin auth redaction and Docker contract.
- `0a70c31` adds browser admin login interface.
- `3f39f2c`, `7286ed5`, and `aa50336` improve job creation races and reproducible isolated CI gates.
- `e2caa7d` hardens controller archive uploads.
- `f5f688b`, `2f444da`, `25d3251`, and `e4fa8e4` expand parity, wrappers, backpressure, and job-batch support.
### 8.3 What `git log --stat -12` confirms
- The current work is concrete V2 closure, not merely documentation.
- Latest metrics commit modifies `app/jobs/metrics.py`, worker, admin route/config, and adds `tests/test_jobs_retention_metrics.py`.
- Safe-error changes cover many V1 routes plus `app/utils/public_errors.py` and typed-status tests.
- Correlation/admin changes alter router/main/admin and dedicated tests.
- IVA Simple restoration adds a V1 route, RSA adjustment, and multipart acceptance coverage.
- Idempotency work adds an Alembic migration, factory/manager configuration, and `tests/test_jobs_cierre.py`.
- URL cleanup modifies multiple log models and `scripts/clean_urls.py`.
### 8.4 In-flight refactor assessment
- **Finding:** an in-flight V2 “cierre”/hardening refactor is evident.
- Focus: safe public errors, correlation IDs, admin safe rendering, V1 legacy compatibility, encrypted multipart inputs, idempotent queued jobs, quotas, retention, and metrics.
- **Finding:** no recent commit name indicates PostgreSQL, k3s, KEDA, worker split, leases, or `app/worker_main.py` work.
- **Conclusion:** treat `plan-migracion-cluster.md` as a drafted architecture proposal, not an active implementation baseline.
## 9. Operational knowledge
### 9.1 Current deployment
- Source: `docker-compose.yml`.
- Current API service name: `api-bots-mrbot`.
- Current container name: `api-bots-mrbot-v2`.
- Current image: `docker.abp.net.ar/abustosp/api-bots-mrbot-v2`.
- Compose publishes host `5010` to container `8000`.
- `init: true` is enabled to reap orphaned browser children.
- Compose deliberately configures one replica because the semaphore is process/event-loop local.
- Compose sets `pids_limit: 512` to limit runaway child processes.
- Compose healthcheck calls `http://127.0.0.1:8000/health` inside the container.
- API uses root `.env`, mounts `./data`, `./logs`, and read-only `./mrbot-keys`.
- API uses `restart: always` and external Docker network `nginx_default`.
- A `sqlite-web` companion is named `api-bots-mrbot-db-v2`.
- sqlite-web binds only to host loopback `127.0.0.1:5011`.
- sqlite-web mounts the same data directory but does not receive the whole API environment.
### 9.2 Container build/runtime
- Source: `Dockerfile`, `docker-entrypoint.sh`, `readme-registry.md`.
- Base image currently references a private Playwright/Python image at `docker.abp.net.ar`.
- Dockerfile installs pinned requirements, copies app/reports/Alembic, creates non-root user `mrbot`, and creates data/log directories.
- Dockerfile currently runs `alembic upgrade head && uvicorn ...` at startup.
- That per-container migration behavior is explicitly targeted for removal by the cluster plan.
- The current image has a local `/health` Docker healthcheck.
### 9.3 Registry and CI/CD
- Registry host: `docker.abp.net.ar`.
- Current publishing script tags mutable `latest` and a timestamp.
- CI logs into the private registry with GitHub secrets.
- CI build platform is `linux/amd64`.
- CI build/scan job does not push the application image in the checked workflow.
- CI runs gitleaks, Trivy HIGH/CRITICAL failure, generates CycloneDX SBOM, and records an image identity artifact.
- The cluster plan proposes changing release deployment to image digests and adding actual CI push/CD.
### 9.4 Object storage and retention
- Sources: `.agents/explorer_survey_storage/handoff.md`, `.agents/challenger_storage/challenge_report.md`, `docs/migration_v2/DECISIONS.md`.
- MinIO is centralized, using durable object keys/buckets rather than persisting presigned URLs.
- `mrbot-json` stores externalized large JSON.
- `mrbot-temp` stores multipart input files.
- Result reads dynamically generate expiring URLs.
- If an artifact has expired/deleted under privacy retention, V2 retains completed job status and returns a privacy message instead of a 500.
- Prior storage note says there is **no internal application cron** deleting `mrbot-temp` inputs.
- Operational action recorded there: configure MinIO bucket lifecycle expiration, suggested 1–7 days.
- This is an operational gap, not proof that lifecycle rules already exist.
### 9.5 Monitoring and scheduled operations
- Admin metrics were added in latest commit `1a7d488`; tests cover percentiles/admin metric authorization/retention config in `tests/test_jobs_retention_metrics.py`.
- The cluster plan proposes worker heartbeats, readiness, KEDA queue-depth scaling, stdout logs, and `kubectl logs` instead of a logs volume.
- The plan proposes daily PostgreSQL `pg_dump` to MinIO as mitigation for a one-pod PostgreSQL SPOF.
- No actual CronJob manifest, crontab, systemd unit, Prometheus/Grafana configuration, alert manager, or production monitoring runbook was found in requested files.
- Therefore all such future monitoring/backup statements are proposals, not confirmed deployments.
### 9.6 Known incidents / operational risks
- **Critical current cluster blocker:** global startup recovery cancels other workers’ pending/running jobs during rolling updates. Source: `plan-migracion-cluster.md:19-20`.
- **Work-loss behavior:** current worker shutdown cancels active work. Source: `plan-migracion-cluster.md:23`.
- **Credential incident:** registry secret appears in `readme-registry.md`; plan requires rotation. Secret intentionally omitted here.
- **SQLite scaling limitation:** shared file DB cannot be safely shared between replicas; optimistic locking is a stopgap.
- **Local semaphore limitation:** limits multiply across processes/replicas and are not global.
- **Chromium risk:** no memory-backed `/dev/shm` causes browser crashes under Kubernetes concurrency.
- **Database lock caveat:** earlier concurrency challenge notes a cancellation update could fail under SQLite lock contention, leaving `CORRIENDO` until restart recovery. Source: `.agents/challenger_concurrency/challenge_report.md:29-34`.
- **PostgreSQL risk:** planned one-pod database is an acknowledged SPOF until HA/cloud-native operator adoption.
- **ARCA risk:** high horizontal concurrency may trigger external rate limits; plan proposes optional distributed limits/fair sharing.
## 10. `data/` and `test-request-versions/`
### 10.1 `data/`
- Source: repository `data/` metadata only; database contents were not opened or copied.
- `data/sql_app.db` is the primary V2 SQLite database, approximately 133 MB.
- `data/sql_app.db.bak_urls_20260825_004859` is a large SQLite backup associated by name with URL cleanup.
- WAL/SHM sidecar files are present for the primary and several runtime/test SQLite databases.
- `runtime_check*.db`, `full_suite_runtime.db`, and `test_suite_runtime*.db` are small SQLite runtime/test artifacts.
- `FacturasApocrifas.txt` is a large text data file used by APOC-related functionality.
- `vep_consolidado_modelo.xlsx` is a VEP input/model workbook.
- `data/files/` exists as a directory.
- V3 must not treat these files as a PostgreSQL schema source without an explicit data-migration/audit decision.
### 10.2 `test-request-versions/`
- Source: `test-request-versions/README.md` and directory metadata.
- This is a configurable, live V1-versus-V2 comparison harness, not the normal pytest suite.
- It generates request cases from a private local `.env` plus `.env.example` template.
- It supports per-request fields, per-case hosts/endpoints, and `CREAR_REQUEST_*` toggles.
- V1 flow is synchronous request and saved result.
- V2 flow is POST, receive 202 job ID, poll until `COMPLETO`, save result.
- Normalized comparison ignores volatile presigned URL signatures, timestamps, and job IDs.
- Smoke rejects missing V2 202/UUID job ID, nonterminal polling, data mismatches, and loss of V1 artifacts.
- Outputs include cases, V1/V2 response captures, diffs, CSV examples, and comparison reports.
- Existing output artifacts include both normal and smoke/live-run directories; they are historic evidence, not a clean current certification.
- This harness will be a strong V3 migration tool after changing the second target from V2 to V3 and making result comparisons secret-safe.
## 11. Durable conclusions from `docs/`, `reporte/`, and `.agents/`
### 11.1 How these notes were treated
- `.agents/` contains prior agent briefs, dispatches, progress chatter, handoffs, reports, and audit artifacts.
- Only conclusions repeated with source paths or reflected in code/tests are retained below.
- Several notes are from older branches and describe an earlier state of V2.
- A prior report’s “PASS” is historical evidence, not a claim that the current checkout was rerun today.
- Where older notes conflict with the newer cluster plan, the newer plan’s explicitly stated diagnosis wins for planning.
### 11.2 Storage conclusions
- Source: `.agents/explorer_survey_storage/handoff.md`.
- Store object metadata (`name`, `object_key`, `bucket`, `size`) rather than presigned URLs in relational data.
- Generate presigned URLs only at result-read time.
- JSON externalization uses a separate JSON bucket and deterministic/collision-resistant keys incorporating job IDs.
- Multipart inputs go to a temp bucket and are fetched by executors into local temp directories.
- Persistence sanitizers strip HTTP/S3/presigned URL content before database writes while avoiding false positives on business keys.
- Large JSON falls back to inline persistence if MinIO upload fails; the bot job should not fail solely because externalization is unavailable.
- Missing privacy-retained objects must not turn a completed job into a server error.
- V3 conclusion: central API should own metadata/result persistence and URL minting. Workers should return result references/payloads through the private API, never write those records directly.
### 11.3 Concurrency conclusions
- Source: `.agents/challenger_concurrency_gate/handoff.md` and `.agents/challenger_concurrency/challenge_report.md`.
- V2 jobs are UUIDv7 and V2 post routes return 202 rather than wait for execution.
- V2 worker uses a process-local semaphore and `finally` release to avoid permit leaks.
- V2 claim uses FIFO selection plus optimistic `UPDATE ... WHERE status='PENDIENTE'` to produce a single winner on SQLite.
- Earlier tests report zero duplicate execution under several concurrent workers on one SQLite DB.
- Those tests do **not** prove a distributed scheduler or global browser cap across production replicas.
- Older notes describe restart recovery as cancelling all orphaned pending/running work into history.
- The newer cluster plan correctly identifies that global recovery as unsafe during rolling updates and proposes leases/scoped recovery/drain.
- V3 conclusion: retain idempotency, cancellation safety, FIFO/fair scheduling, permits, retry/lease semantics, and browser process cleanup.
- V3 must replace direct database claiming and process-local-only capacity decisions with central assignment plus worker-reported capacity.
### 11.4 Error and privacy conclusions
- Source: `.agents/challenger_safe_errors_gate/handoff.md`, `PLAN.md`, safe-error tests.
- Public response paths must never echo raw exception strings or details.
- Error category/type/context mapping can preserve functional HTTP behavior without leaking internals.
- Sanitization must cover nested payloads, historical error data, job status, cancellation, V1/V2 routes, and FastAPI fallback handlers.
- Older safe-error report claimed 62 selected tests passed and a broad AST audit found no prohibited direct public exception stringification.
- It also notes a deliberate caveat: internal admin tooling may retain diagnostics when not returning a public response.
- V3 conclusion: distinguish public/admin diagnostic authorization carefully. The new central admin panel needs safe default rendering and audited privileged diagnostic access.
### 11.5 Parity conclusions
- Source: `docs/migration_v2/paridad_teorica/artifacts/global_report.md`, `.agents/explorer_survey_parity_infra/handoff.md`, test inventory.
- A static earlier V1→V2 report declared `PARIDAD_PARCIAL`, with 4 OK, 19 partial, and 7 failures across 30 bot endpoints.
- Therefore V2 parity should not be assumed simply because later tests exist.
- A later agent handoff reported 60 passing mocked parity samples across 27 module endpoints. This proves test fixture behavior, not live external-site equivalence.
- The parity tooling has distinct levels: static inventory, mocked ASGI semantic tests, local dual-app tests, and live external comparisons.
- V3 conclusion: label parity results by level and do not describe mocked fixture parity as production/external-system certification.
### 11.6 Reporting and usage conclusions
- Source: `reporte/README.md`.
- Reporting scripts aggregate successful/non-successful requests by user, service, date range, and optionally represented CUIT.
- Existing report schema assumes `user_id` and V2/legacy log table names.
- V3 must redesign report queries for UUID IDs, subscription/credit events, central job lifecycle, and a PostgreSQL schema.
## 12. V3 implementation guardrails derived from this research
1. Preserve the V2 cluster plan’s drain, lease, retry, version-pinning, and deploy safety intent.
2. Do not preserve its direct worker-to-PostgreSQL control path.
3. Make central API the only PostgreSQL owner and only scheduler/allocator.
4. Define private worker API endpoints or a secured pull/long-poll protocol that only the central API can use.
5. Have workers register, heartbeat, report queue depth/current slots, receive assignments, acknowledge start, heartbeat jobs, and submit terminal results.
6. Enforce at both scheduler and worker that a worker has at most five simultaneous jobs.
7. Treat worker health and queue depth as central-admin observability data, including unavailable worker alerts.
8. Design assignment idempotency and lease/retry behavior before implementing workers.
9. Use PostgreSQL with explicit UUID columns: UUIDv4 for users and UUIDv7 for bot/job-related tables, with no sequence primary keys.
10. Do not migrate V2 `fecha_ultimo_reset`, `created_at`, or `updated_at` into V3 users table.
11. Model subscription tier allowance, consumed quota, credits, top-up purchase, MercadoPago payment state/webhooks, and audit ledger centrally.
12. Preserve URL-free relational persistence and dynamically minted artifact URLs if MinIO/object storage is retained.
13. Make async job execution the only V3 execution path. If legacy synchronous endpoints survive temporarily, isolate them in a documented deprecation adapter with a sunset plan.
14. Create monorepo root README plus service-specific READMEs for central API, worker, shared packages, infrastructure, and migrations.
15. Port high-value V2 tests before broad bot migration: job lifecycle, assignment/capacity, safe errors, secrets, storage, V1/V2/V3 parity, and real Chromium orphan-PID gate.
## 13. Research limitations and GUESS labels
- **GUESS:** Current production hostnames beyond the private registry and public V1 comparison host are not documented in the inspected files.
- **GUESS:** The Docker external network `nginx_default` is backed by an Nginx reverse proxy, inferred from its name only.
- No live service calls, browser runs, database reads, migrations, deployments, credential use, or test execution were performed for this report.
- Existing test/agent pass counts are historical claims extracted from artifacts.
- Documentation has drift, particularly port numbers, synchronous descriptions, and environment-file location.
- No standalone recurring job configuration or full observability stack was found; absence is limited to the requested/reviewed scope.
## 14. Validation performed
- Read all 355 lines of `plan-migracion-cluster.md`.
- Read requested top-level documentation files.
- Enumerated every `tests/test_*.py` source file and reviewed pytest/CI taxonomy.
- Inspected core parity, lifecycle, worker-cap, V2 endpoint, sanitization, registry, Compose, Dockerfile, and CI sources.
- Inspected `git branch -a`, `git log --oneline -60`, and `git log --stat -12`.
- Inspected requested directories at metadata/targeted durable-note level without opening secrets or modifying V2.
- Verified this report’s required headings and target location after writing.
