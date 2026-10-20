What this repo does: MrBot accepts requests for 34 tax and social-security bots, runs them in the background, and returns data and files. Some bots only read data. Others submit files, import records, confirm offsets, or create payment slips.

**Load assumed:** many users, several jobs at once, workers running five jobs by default, and processes that stay up for days or weeks. The stored production path matters more than the in-memory test path. This is a report only: no application code, models, dependencies, or migrations were changed.

Review date: 20 October 2026. See [validation evidence](validacion.md), [API analysis](api.md), [worker analysis](workers.md), and the [34 bot reviews](README.md).

## Must fix

1. **Rejected results are treated as saved results** (`services/bot-worker/src/bot_worker/reporting/results.py:L63-69`, `services/bot-worker/src/bot_worker/main.py:L1510-1535`)
   - **What this is:** A callback is the worker's message that tells the API a job has finished. The worker must know whether the API saved that result.
   - **Problem:** `send_result()` returns success for every HTTP status below 500, including 302, 401, 403, 409, and 422. A local fake receiver reproduced each case, so an expired token or rejected file description is marked as delivered.
   - **Fix:** Accept only a valid success response, normally HTTP 200 with the expected job confirmation. Keep rejected results pending, and handle an identical replay separately from a conflicting result.
   - **If we skip it:** A bot may finish a real action but lose its result and files from the user's view. Support cannot tell whether retrying the action is safe.

2. **Failed delivery deletes the material needed to recover** (`services/bot-worker/src/bot_worker/reporting/results.py:L55-78`, `services/bot-worker/src/bot_worker/main.py:L1504-1535`)
   - **What this is:** The worker retries sending its final result four times. Those retries resend the report, not the external action.
   - **Problem:** If every request times out or returns 503, the function returns false, but the runner still deletes its work directory and releases the job. The log says the task is kept, yet there is no pending-result store on this path, and the in-memory result cache is populated only after a claimed success.
   - **Fix:** Keep the serialized result and required files in a bounded pending-delivery record until the API confirms them. Use storage that survives a restart if recovery across restarts is required, and retry only delivery, never the completed bot action.
   - **If we skip it:** A short API outage can turn a completed external action into a failed job with no recoverable output. Users may repeat a submission that already happened.

3. **Internal failures consume quota instead of using the refund path** (`services/bot-worker/src/bot_worker/main.py:L1399-1489`, `services/central-api/src/central_api/internal/job_results.py:L129-140`, `:L264-269`)
   - **What this is:** Each job reserves one request from the user's allowance. The API decides whether to consume or return that request when the job ends.
   - **Problem:** The API returns quota only for the exact category `INFRASTRUCTURE_ERROR`, but the runner emits categories such as `CHROMIUM_CRASHED`, `ARTIFACT_UPLOAD_FAILED`, and `INTERNAL`. A local call through the real result handler confirmed consumption for all three and a refund only for `INFRASTRUCTURE_ERROR`; the stored path uses the same condition.
   - **Fix:** Map worker failure categories to the agreed quota policy in one place before settlement. Add a test that sends the runner's actual error report through the result handler and checks the allowance, without renaming away the useful diagnosis.
   - **If we skip it:** A service fault consumes the user's quota while the documented infrastructure-refund path is never used for those reports. This creates incorrect account balances and manual support work.

4. **Two files in one ZIP can silently replace each other** (`services/bot-worker/src/bot_worker/runtime/input_files.py:L32-58`)
   - **What this is:** Uploaded ZIP files are unpacked before the bot reads their contents. The unpacker removes directory names from each entry.
   - **Problem:** A ZIP with `carpeta-a/reporte.txt` and `carpeta-b/reporte.txt` writes both to `reporte.txt`. The local reproduction returned that name twice and left only the second file's contents.
   - **Fix:** Reject duplicate normalized destination names before writing any member. Apply the same rule across the full input list so separate ZIP files cannot replace earlier inputs.
   - **If we skip it:** A presentation bot can receive different data from what the user uploaded, without an explicit error. A missing first file may change the external submission.

5. **Input preparation can run past the job deadline** (`services/bot-worker/src/bot_worker/main.py:L1347-1385`, `services/bot-worker/src/bot_worker/runtime/input_files.py:L61-77`)
   - **What this is:** A deadline is the latest time a job is allowed to keep running. The worker uses a timed wait around the bot's main execution.
   - **Problem:** Input downloads and `plugin.validate()` happen before that timed wait. With two seconds left and a slow sequence of downloads, preparation can exceed the deadline, then execution still starts using the old timeout value and a minimum one-second wait.
   - **Fix:** Put preparation, validation, and execution under the same remaining-time limit. Recheck cancellation and the time left before starting the external action.
   - **If we skip it:** Large or slow inputs occupy worker slots beyond the advertised limit. An action can begin after its deadline, when the caller may already regard it as failed.

6. **Required bot fields are checked only after a job is queued** (`services/central-api/src/central_api/api/bots.py:L194-208`, `:L517-619`, `:L793-817`, `services/bot-worker/src/bot_worker/main.py:L1381-1385`)
   - **What this is:** The canonical API accepts a `payload` object and separate credentials. A bot's field rules should tell the user whether that request can run.
   - **Problem:** A local call to the real CCMA handler with `payload: {}` and fake credentials returned `202 PENDIENTE`, but the stored payload failed `plugin.validate()` because `representado_cuit` was missing. The request had already reached the quota-reservation step before this failure was knowable to the caller.
   - **Fix:** Validate each normalized bot input against its operation contract before reserving quota or saving the job. Keep that contract shared as data or pure validation code, without importing the bot runtime into the API.
   - **If we skip it:** Users get a job identifier for a request that cannot run. They wait for a late failure, occupy scheduling capacity, and may consume quota for an invalid request.

## Should fix

7. **Partial-result details disappear between the bot and the user** (`services/bot-worker/src/bot_worker/runtime/context.py:L76-90`, `services/bot-worker/src/bot_worker/main.py:L1386-1393`, `:L1464-1489`, `services/central-api/src/central_api/internal/job_results.py:L59-95`)
   - **What this is:** Bots can return warnings, errors, and measurements alongside their data. Warnings explain which part of a partial result is missing.
   - **Problem:** The runner collects warnings and measurements but does not put them in the terminal report. The receiving model has no fields for them, and `BotResult.errors` is not projected either, so a warning that exists only in `BotResult.warnings` is lost.
   - **Fix:** Define one small, sanitized public warning field in the result contract and pass it through the report, storage, and job response. Keep private measurements separate if they are not part of the user's contract.
   - **If we skip it:** Users see `PARCIAL` without a reliable explanation for the missing section. Tests that inspect only the plugin result keep passing while the public response stays incomplete.

8. **Worker memory grows with every historical job** (`services/bot-worker/src/bot_worker/scheduler/supervisor.py:L68-70`, `:L107-120`, `:L183-197`, `services/bot-worker/src/bot_worker/reporting/results.py:L20-37`)
   - **What this is:** A cache remembers earlier admissions and delivered results so repeated messages do not run twice. These caches live for the full process lifetime.
   - **Problem:** Releasing a job does not remove its admission entry, and the result store has no eviction. A local run of 1,000 completed and released jobs left zero active jobs but 1,000 cached admissions and 1,000 cached results, including their result bodies.
   - **Fix:** Keep only the replay window the protocol actually needs, with an age limit and a size limit. Use the API's saved terminal result as the authority after that window.
   - **If we skip it:** A healthy long-running worker keeps more memory after every batch. Large result bodies make the growth faster, even when concurrency stays at five.

9. **The advertised whole-repo test command cannot collect the suite** (`Makefile:L23-24`, `packages/mrbot-contracts/tests/test_worker_capacity.py`, `services/bot-worker/tests/test_worker_capacity.py`)
   - **What this is:** `make test` runs all tests from the repository root. Collecting tests is the step that imports them before they run.
   - **Problem:** The root command stops because two test files import as the same module name. Switching only to importlib mode then fails on helper imports such as `fakes_playwright` and `runner`; separate package runs pass.
   - **Fix:** Make the test entry point run each package and the compatibility tool in separate processes. This is smaller than changing all helper imports and avoids claiming that importlib alone fixes the suite.
   - **If we skip it:** Contributors cannot use the documented acceptance command. They may stop after a partial run and miss a real regression in another package.

10. **Three generated catalogue examples cannot run as shown** (`services/central-api/src/central_api/api/bots.py:L820-845`, `services/central-api/src/central_api/api/bot_payloads.py:L1348-1371`)
   - **What this is:** The catalogue publishes sample requests so users can learn which inputs a bot needs. The samples should pass the same input checks as a real request after its upload references are resolved.
   - **Problem:** Checking all 45 generated samples against the actual plugin validators found three failures: `carga_portal_iva/cargar` has no required TXT pair or opening CSV, `controladores_fiscales/presentar` has no files, and `vep_archivo/generar` has no input file. The other 42 passed this local input check.
   - **Fix:** Include the required upload reference fields and a valid fake uploaded-file reference in those operation samples. Add one offline example-to-validator test for every published operation, with file downloads replaced only by local names.
   - **If we skip it:** Users copy a sample that is accepted or displayed by the API but fails before the intended action. The new written guides explain the upload requirement, but the generated catalogue still needs the same correction.

## Evidence and scope

The callback, quota, ZIP, cache, and late-validation cases above were reproduced locally with fake transport or in-memory objects. They did not contact public portals, change real accounts, submit documents, or access production data. The delivery-cleanup and deadline cases were traced in the full runner and its finalization path.

The per-bot reviews record further field-level differences, ignored options, defaults, and output limits. Those are not all separate global findings because they need bot-specific contract decisions. No code is declared dead merely because it has a historical name or a second implementation.

**Verdict:** Fix result delivery and quota settlement first. Do not promise reliable action confirmation until rejected and undelivered reports are recoverable.

**Not checked:** Real organism sessions, production credentials, live deployments, payment-provider calls, a sustained load test, and external-provider availability. Database-only tests skipped by the local suite are not counted as passing. Admin, billing, and infrastructure code was inspected where it meets the traced job flows, not as a line-by-line security audit of every file. Historical prototypes and every migration were not reviewed in full.
