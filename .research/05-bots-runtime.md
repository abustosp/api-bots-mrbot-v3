# V2 bots and runtime research for V3 workers
**Date:** 2026-09-16
**Scope:** `app/bot/*`, bot runtime utilities, `bots_dev/`, and the container build files.
**Method:** source inspection only.
No existing project file was modified.
The only created file is this requested research report.
## Executive summary
V2 contains 31 modules in `app/bot/`.
29 of those modules contain Playwright automation.
`app/bot/apoc.py` and `app/bot/consulta_cuit.py` are synchronous, non-browser lookups.
The V2 job registry exposes 33 Playwright keys.
That total includes aliases and Mis Comprobantes sub-operations.
A normal browser bot creates a new Playwright driver, Chromium process, browser context, and page for each invocation.
It logs in to ARCA with a representative CUIT and password.
It then selects a represented taxpayer, navigates to a delegated service, extracts data or downloads artefacts, and closes its browser in `finally` paths.
Most ARCA bots use `app.utils.arca_login.login_arca()` plus `open_arca_service()`.
The current shared in-process browser limit defaults to three browsers per event loop.
It is explicitly not a cross-process or distributed limit.
The existing Docker image is an API image, not a worker image.
It contains FastAPI, Alembic, routes, the SQLite-oriented startup command, and an HTTP health check.
`bots_dev/` is not copied by the Dockerfile and is therefore not shipped in the production image.
The production bot modules themselves do **not** write directly to the database.
There is, however, database persistence in the V2 worker and at least one executor.
A V3 worker must not carry those executor and worker persistence paths forward.
## Evidence index
| Topic | Primary evidence |
|---|---|
| Bot registry | `app/jobs/registry.py:1-236` |
| V1 route inclusion | `app/api/api_router.py:15-83` |
| Generic executor contract | `app/jobs/executors/base.py:20-102` |
| V2 worker persistence and browser limiting | `app/jobs/worker.py:55-149` |
| Shared browser limit | `app/utils/browser_limiter.py:1-110` |
| ARCA login/session helper | `app/utils/arca_login.py:102-672` |
| ARCA image CAPTCHA helper | `app/utils/arca_captcha.py:1-250` |
| Generic CapMonster helper | `app/utils/capmonster.py:1-241` |
| Proxy helper | `app/utils/proxies.py:1-161` |
| Current image | `Dockerfile:1-37` |
| Compose deployment | `docker-compose.yml:1-74` |
| Image publishing | `build-and-push.sh:1-72` |
## 1. Complete production catalogue
### Reading the columns
`Registered` means a key exists in `PLAYWRIGHT_BOTS` and has a lazy executor mapping in `app/jobs/registry.py`.
`Route` means a V1 route module is included by `app/api/api_router.py`.
`Job only` means it is usable through the V2 jobs path but lacks its own included V1 route module.
`No` means neither a Playwright job registration nor a V1 route was found.
`Approx. LOC` is `wc -l` for the source module on the inspection date.
Inputs list business inputs only, with representative credentials omitted where all ARCA rows require them.
All external object keys mentioned below are MinIO keys unless explicitly stated otherwise.
### Productive modules
| Bot key / module | Target organism or site | What it does | Required or principal inputs | Outputs | LOC | Registered | Route |
|---|---|---|---|---|---:|---|---|
| `apoc` / `app/bot/apoc.py` | APOC local data source | Looks up a CUIT in a supplied APOC base. | `cuit`, `ruta_base`. | Lookup dict. | 47 | No, non-Playwright | Yes, `routes/apoc.py` |
| `aportes_en_linea` / `aportes_en_linea_bot.py` | ARCA, Aportes en Línea | Opens the service and consults/imports contribution history. | represented CUIT, historical file as Base64 or MinIO key, proxy flag. | Parsed result and uploaded/downloaded XLS artefact when applicable. | 231 | Yes | Yes |
| `retper_iibb_arba` / `arba_bot.py` | ARBA, Buenos Aires | Authenticates to ARBA and downloads an IIBB retention/perception report for a period. | CUIT, password, period, denomination, MinIO and proxy flags. | CSV/XLS-style downloaded report and optional object key. | 283 | Yes, plus `arba` alias | Yes, `routes/retper_iibb_arba.py` |
| `portal_iva_carga` / `carga_portal_iva_bot.py` | ARCA Portal IVA | Uploads Portal IVA source files and configures opening, fiscal credit, debit credit, and related options. | period, represented CUIT, declaration flags, six source paths, proxy, headless, diagnostics callback. | Per-step status and diagnostic callbacks. | 1,083 | Yes | Job only |
| `ccma` / `ccma_bot.py` | ARCA Cuenta Corriente de Monotributistas y Autónomos | Reads account summary and optionally movements and PDF. | represented CUIT, `movimientos`, `pdf`, proxy flag. | Summary dict, movements, optional MinIO PDF key. | 877 | Yes | Yes |
| `certificado_mipyme` / `certificado_mipyme_bot.py` | ARCA Registro PyME | Selects a represented taxpayer and downloads its MiPyME certificate. | represented CUIT, headless, delete-downloads, proxy flag. | Certificate file and optional MinIO key. | 248 | Yes | Yes |
| `sct_compensaciones` / `compensaciones_bot.py` | ARCA Sistema de Cuentas Tributarias | Selects taxpayer, queries date range, exports one or more formats. | represented CUIT, `desde`, `hasta`. | CSV/XLS/PDF-style export and MinIO object key. | 568 | Yes | Job only |
| `mis_comprobantes` / `comprobantes_bot.py` | ARCA Mis Comprobantes | Downloads issued and/or received electronic invoice consultations. | date range, login and represented identity, receipt toggles, POS filters, MinIO/Base64/JSON flags, timeout, proxy. | DataFrames, Base64/JSON optionally, ZIP/CSV files and MinIO keys. | 1,508 | Yes | Yes |
| `mis_comprobantes_solicitar` / `comprobantes_bot.py` | ARCA Mis Comprobantes | Requests an asynchronous issued/received consultation and captures the consultation ID. | date range, identities, issued/received toggles, POS filters, proxy. | Consultation state/IDs and `cookies_header`. | Included above | Yes | Via Mis Comprobantes route/job operation |
| `mis_comprobantes_historial` / `comprobantes_bot.py` | ARCA Mis Comprobantes | Locates an earlier request in History and downloads its CSV. | date range, identities, download/output flags, cookie header, proxy, verbose. | DataFrame, Base64/JSON optionally, file/object key, history metadata. | Included above | Yes | Via Mis Comprobantes route/job operation |
| `consulta_cuit` / `consulta_cuit.py` | Local CUIT validation/source | Looks up one CUIT or a list without Playwright. | CUIT or CUIT list. | Dict response. | 48 | No, non-Playwright | Yes, `routes/consulta_cuit.py` |
| `consulta_pagos_vep` / `consulta_pagos_vep_bot.py` | ARCA SETI / VEP payments | Selects taxpayer and period, queries paid VEPs, exports CSV. | represented CUIT, period, proxy, upload flag. | CSV and optional MinIO key. | 435 | Yes, separate key and VEP-related aliasing | Job only, VEP area |
| `controladores_fiscales` / `controladores_fiscales_bot.py` | ARCA Controladores Fiscales | Uploads one or more PEM declarations, presents them, obtains constancias. | source PEM directory, downloads directory, headless, proxy, upload flag. | Per-file status and constancia PDFs/MinIO keys. | 447 | Yes | Yes |
| `declaracion_en_linea` / `declaracion_en_linea_bot.py` | ARCA Declaración en Línea | Collects DDJJ across a period range, VEP statuses, downloads DDJJ Excel/PDF and VEP PDFs. | represented identity/name, range start/end, proxy, upload flag. | Structured DDJJ JSON, Excel/PDF/VEP artefacts and MinIO keys. | 1,370 | Yes | Yes |
| `facturometro` / `facturometro_bot.py` | ARCA Monotributo / Facturómetro | Opens Monotributo pages and extracts invoicing amount, category cap, and category. | login CUIT, password, represented CUIT, headless, MinIO flag. | Data dict, possibly report/object key. | 266 | Yes | Yes |
| `hacienda` / `hacienda_bot.py` | ARCA Hacienda y Carne - Liquidación | Opens Comprobantes en Línea then Hacienda, queries issuer and recipient liquidations, downloads PDFs and builds consolidated XLSX. | date range, representative and represented CUIT, password, denomination, delete/download/upload/proxy/headless flags. | PDF lists per query, consolidated XLSX, optional MinIO object keys. | 1,932 | Yes | Yes |
| `libros_portal_iva` / `libros_portal_iva_bot.py` | ARCA Portal IVA | Lists/selects fiscal periods and downloads IVA books and DDJJ PDFs. | represented identity, range, denomination, proxy, headless. | Period availability and downloaded CSV/PDF/object-key records. | 980 | Yes | Yes |
| `liquidacion_granos` / `liquidacion_granos_bot.py` | ARCA Liquidación Primaria/Secundaria de Granos | Downloads LPG/LSG issued and received records plus deposit certificates; writes table exports. | date range, representative credentials, denomination, represented CUIT, output flags. | PDFs/reports, per-table XLSX, MinIO keys. | 1,487 | Yes | Yes |
| `mis_facilidades` / `mis_facilidades_bot.py` | ARCA Mis Facilidades | Selects taxpayer, reads payment plans, installments and obligations, exports XLSX/PDF. | identities, denomination, exclusion situation, MinIO and proxy flags. | Plan data, XLSX/PDF artefacts and object keys. | 1,174 | Yes | Yes |
| `mis_retenciones` / `mis_retenciones_bot.py` | ARCA Mis Retenciones / SIRE | Selects taxpayer, date range, tax and type, and exports CSV for each requested tax. | identity, denomination, dates, `impuestos`, export mode, MinIO/proxy flags. | CSV list and object keys. | 1,169 | Yes | Yes |
| `mis_retenciones_iva_simple` / `mis_retenciones_iva_simple_bot.py` | ARCA Mis Retenciones, IVA Simple mode | Same service family, but switches into IVA Simple and its restricted date/operation flow. | identity, denomination, dates, MinIO/proxy flags. | CSV list and object keys. | 1,197 | Yes | Yes |
| `moa` / `moa_bot.py` | ARCA MOA / customs dispatches | Navigates enterprise tree, searches requested customs dispatches, extracts tables and emits CSV. | identities, dispatch list, agent type, role, search method, proxy/upload flags. | Per-dispatch data, CSV, optional MinIO key. | 368 | Yes | Yes |
| `pago_devoluciones` / `pago_devoluciones_bot.py` | ARCA Pago Devoluciones | Opens service, selects taxpayer, consults and exports payments/refunds. | identities, MinIO/proxy/headless flags. | Exported file, MinIO key, section errors. | 576 | Yes | Yes |
| `portal_iva` / `portal_iva_bot.py` | ARCA Portal IVA | Configures a period, imports from ARCA or supplied files, deletes/reloads records, downloads purchases/sales CSV. | identity, period, opening flags, files, download/import toggles, denomination, MinIO/proxy/headless. | Results map, downloaded CSV object keys, import statuses. | 1,347 | Yes | Yes |
| `rcel` / `rcel_bot.py` | ARCA Comprobantes en Línea / RECEL | Selects taxpayer by CUIT/name and downloads invoice PDFs over date range. | dates, login, taxpayer name/CUIT, delete/Base64/S3/MinIO/proxy options. | Invoice records, PDFs/Base64 and optional upload keys. | 832 | Yes | Yes |
| `retper_iibb_agip` / `retper_iibb_agip_bot.py` | AGIP, CABA | Uses AGIP email gateway then contributor selection to retrieve IIBB retention/perception reports. | AGIP user/password, taxpayer CUIT/name, range, MinIO/proxy/headless. | Downloaded report and object key. | 551 | Yes | Yes |
| `retper_iibb_misiones` / `retper_iibb_misiones_bot.py` | ATM Misiones | Logs into Misiones tax portal, handles CAPTCHA/session headers, asks the reporting endpoint for PDF. | representative credentials, date range, denomination, MinIO/proxy/headless. | Report PDF and MinIO object key. | 814 | Yes | Yes |
| `sct` / `sct_bot.py` | ARCA Sistema de Cuentas Tributarias | Obtains SCT data and supports file representations of due dates, debts, and pending DDJJ. | identities, proxy, Base64/MinIO values for Excel/CSV/PDF result groups. | Structured SCT payload and artifact references. | 526 | Yes | Yes |
| `sifere` / `sifere_bot.py` | SIFERE / Convenio Multilateral | Logs into SIFERE, queries a period and selected jurisdictions, parses HTML tables and exports reports. | identities, period, represented name, jurisdictions, proxy/upload flags. | Table results, XLSX outputs and MinIO keys. | 594 | Yes | Yes |
| `siper` / `siper_bot.py` | ARCA SIPER | Queries taxpayer risk/profile information. | identities, optional detail/category Base64 or MinIO values, proxy. | SIPER result and artefact references. | 333 | Yes | Yes |
| `srt` / `srt_bot.py` | SRT | Queries alícuotas for a set of CUITs, with CAPTCHA-capable flow. | representative credentials, `cuits_consulta`, proxy. | Per-CUIT alícuota results. | 529 | Yes | Yes |
| `vep_archivo` / `vep_archivo_bot.py` | ARCA VEP | Reads spreadsheet/TXT source, generates VEP registry files and uses ARCA to create payment slips. | input document, login, payment method, headless, PDF/Base64/MinIO/proxy options. | Generated VEP files, ZIP, PDFs and upload keys. | 696 | Yes, shares VEP executor | Via `routes/vep.py` |
| `vep_ccma` / `vep_ccma_bot.py` | ARCA CCMA / VEP | Selects CCMA debt/interest records, generates VEP, optionally returns PDF. | identities, payment method, tax/interest filters, selection lists, PDF/upload/proxy flags. | VEP data, optional PDF/Base64/object key. | 789 | Yes | Yes |
### Registry aliases and naming differences
`app/jobs/registry.py:45-83` registers 33 keys.
The module-to-key map is documented in its lines 15-43.
`arba` is an alias of `retper_iibb_arba`.
`vep_archivo` shares `app.jobs.executors.vep` with VEP handling.
`consulta_pagos_vep` is separately registered despite VEP-related naming.
`mis_comprobantes_solicitar` and `mis_comprobantes_historial` are distinct operations inside `comprobantes_bot.py`.
`compensaciones_bot.py` is registered as `sct_compensaciones`.
`carga_portal_iva_bot.py` is registered as `portal_iva_carga`.
The comments in `app/jobs/registry.py:2-13` are internally stale in wording about bot counts.
The actual assertion verifies 33 registry entries.
### Non-browser modules
`app/bot/apoc.py` and `app/bot/consulta_cuit.py` are the two files that do not import Playwright.
They should not be placed on the browser-worker queue in V3.
They can be moved to API/domain services or lightweight utility workers.
## 2. Common bot structure and lifecycle
### Typical V2 lifecycle
1. The API route or V2 executor translates an HTTP/job request into a bot function call.
2. The function validates requested flags and builds an `error_logs` list.
3. It conditionally asks `get_proxy_config()` for a browser proxy.
4. It creates a temporary work directory, commonly with `tempfile.TemporaryDirectory`.
5. It enters `async_playwright()`.
6. It launches `playwright.chromium.launch(headless=..., proxy=...)`.
7. It creates one context and one page, often with `accept_downloads=True`.
8. It calls `login_arca()` for ARCA bots.
9. It calls `open_arca_service()` to find the delegated service in the ARCA catalog.
10. It selects a represented taxpayer when the service requires it.
11. It navigates site-specific menus and forms.
12. It waits for selectors, network idleness, a popup, or a download.
13. It parses HTML, JSON responses, PDFs, CSV/ZIP files, or XLSX files.
14. It writes temporary artifacts to its work directory.
15. It optionally uploads artifacts to MinIO through `app.utils.bucket.subir_archivo_a_minio`.
16. It returns business data, error messages, success/partial state, and artifact references.
17. It closes page/context/browser, normally in `finally` or a local `_safe_close` helper.
18. It cleans the temporary directory when `eliminar_descargas=True`.
### Hacienda archetype
The following is the clearest full-service archetype.
It is implemented by `app/bot/hacienda_bot.py`.
The public wrapper is `descargar_hacienda()` at lines 1844-1932.
The browser-level routine is `hacienda()` at lines 1669-1743.
#### Start and resource allocation
`descargar_hacienda()` accepts date range, representative CUIT, represented CUIT, password, denomination, cleanup, MinIO, proxy, and headless controls.
Source: `app/bot/hacienda_bot.py:1844-1868`.
It uses `TemporaryDirectory(prefix="hacienda_")` when cleanup is enabled.
If cleanup is disabled it writes under `./descargas/hacienda/<denomination-or-CUIT>`.
It creates the directory before launching Playwright.
Source: `app/bot/hacienda_bot.py:1859-1868`.
It enters one `async_playwright()` session and calls `hacienda()`.
Source: `app/bot/hacienda_bot.py:1869` onward.
#### Browser and context
`hacienda()` launches Chromium once with the selected `headless` and `proxy` values.
It creates exactly one context with `accept_downloads=True`.
It creates one initial page.
Source: `app/bot/hacienda_bot.py:1684-1686`.
It does not use the shared `limit_browser` context manager.
That means V2 limits it only if it is dispatched through the outer job worker wrapper.
#### Login and service navigation
`ensure_login()` delegates to `login_arca()`.
Source: `app/bot/hacienda_bot.py:1425-1435`.
`open_comprobantes_service()` calls `open_arca_service()` for `COMPROBANTES EN LINEA`.
Source: `app/bot/hacienda_bot.py:1437-1447`.
`seleccionar_denominacion()` uses exact/normalized/fuzzy matching to select the intended taxpayer.
Source: `app/bot/hacienda_bot.py:1449-1617`.
`open_hacienda_service_twice()` opens Hacienda twice.
The first target page is immediately closed if it is a popup.
The second page is retained and taxpayer selection is repeated.
Source: `app/bot/hacienda_bot.py:1619-1652`.
This is a site-specific quirk, not a worker framework requirement.
#### Query and extraction
The bot creates `por_emisor` and `por_receptor` directories.
Source: `app/bot/hacienda_bot.py:1699-1702`.
For each direction, `procesar_consulta()` opens the query section, fills dates, searches, and paginates all results.
Source: `app/bot/hacienda_bot.py:1655-1666`.
It scrapes result-table HTML with BeautifulSoup.
`parse_table_records()` maps headers and infers CUIT, code, invoice type, POS, and invoice number.
Source: `app/bot/hacienda_bot.py:427-566`.
It downloads PDFs from extracted IDs and fallback row actions.
Source: `app/bot/hacienda_bot.py:943-1138` and `1245-1391`.
It builds data frames for every result page.
It writes a consolidated XLSX file with per-query sheets.
Source: `app/bot/hacienda_bot.py:1723-1739` and `1745-1786`.
#### Teardown
`hacienda()` always calls `context.close()` and `browser.close()` in `finally`.
Source: `app/bot/hacienda_bot.py:1740-1742`.
The wrapper then uploads selected PDF/XLSX files to MinIO when requested.
Source: `app/bot/hacienda_bot.py:1796-1842`.
The temporary directory is cleaned in the wrapper when it owns one.
### Common variations
Some bots use `start_arca_session()` instead of hand-written launch/login code.
Examples include `certificado_mipyme_bot.py`, `controladores_fiscales_bot.py`, and `moa_bot.py`.
`start_arca_session()` returns a bundle of browser, context, page, and login result.
Source: `app/utils/arca_login.py:131-227`.
Some bots open additional browsers to make PDFs with the same authenticated state.
Examples: Declaración en Línea opens `pdf_browser` with `storage_state`.
Source: `app/bot/declaracion_en_linea_bot.py:674-755`.
Mis Facilidades does the same for PDF rendering.
Source: `app/bot/mis_facilidades_bot.py:608-660`.
These are important because an execution can have more than one Chromium process despite the usual one-browser pattern.
## 3. Shared ARCA login, CAPTCHA, proxy, and session state
### Browser stealth defaults
`app/utils/arca_login.py:24-100` defines a stealth launch/context bundle.
Launch arguments are:
- `--disable-blink-features=AutomationControlled`
- `--disable-dev-shm-usage`
- `--no-first-run`
- `--no-default-browser-check`
- `--disable-infobars`
The context defaults are:
- locale `es-AR`
- timezone `America/Argentina/Buenos_Aires`
- viewport `1366 x 768`
- Windows/Chrome 150 user-agent string
The init script hides `navigator.webdriver`.
It presents Windows platform, Spanish language list, eight hardware threads, eight GiB device memory, plugins, Chrome runtime, notification permissions, and Intel Iris WebGL values.
This is anti-automation compatibility behavior.
It should be reviewed for legal, compliance, and site-term implications before a V3 deployment.
### `launch_arca_browser_context()`
`launch_arca_browser_context()` at `app/utils/arca_login.py:150-167` is a thin helper.
It launches Chromium with optional proxy and launch args.
It creates a new context from the provided context kwargs.
It creates one new page.
It returns `(browser, context, page)`.
The function does not itself persist a storage state.
It does not reuse an existing browser, context, or profile.
### `start_arca_session()`
`start_arca_session()` at `app/utils/arca_login.py:183-227` wraps launch plus login.
It takes representative CUIT and password.
It accepts `headless`, proxy, custom launch/context kwargs, login URL, captcha behavior, and mutable `error_logs`.
It launches a new browser/context/page.
It calls `login_arca()`.
It closes the context and browser if login fails.
On success it returns `ArcaSession(browser, context, page, login_result)`.
The caller remains responsible for closing a successful session.
### Login state machine
The login entry URL is `https://auth.afip.gob.ar/contribuyente_/login.xhtml`.
Source: `app/utils/arca_login.py:102`.
`login_arca()` is the shared state machine used by most ARCA bots.
The utility contains resilient locator helpers, page-ready waits, CAPTCHA detection, credential form handling, post-login validation, password-change detection, and error sanitization.
Its result object is `ArcaLoginResult`.
Source: `app/utils/arca_login.py:120-128`.
The object includes:
- `success`
- `error_logs`
- `captcha_detected`
- `password_change_required`
- `info_logs`
The error information is intentionally separated from informational CAPTCHA messages.
### Service opening
`open_arca_service()` is used after a successful ARCA login.
The caller provides a human service name, alternate string or regex matchers, mutable errors, and optional message overrides.
The service helper navigates the ARCA service catalogue and returns the active or popup service page.
Examples:
- Mis Comprobantes: `app/bot/comprobantes_bot.py:158-178`.
- Hacienda's Comprobantes service: `app/bot/hacienda_bot.py:1437-1447`.
- Aportes en Línea: `app/bot/aportes_en_linea_bot.py:101` onward.
V3 should keep the login and service-selection layers separate from individual service plugins.
### ARCA image CAPTCHA
ARCA's login CAPTCHA is not reCAPTCHA.
It is an image CAPTCHA page at `loginCaptchaAfip.xhtml`.
Source: `app/utils/arca_captcha.py:1-12` and `:27`.
The utility detects either the URL or a matching input.
Source: `app/utils/arca_captcha.py:85-95`.
It finds a data-URI image via `CAPTCHA_IMG_SELECTORS`.
It extracts and lightly validates the base64 payload in memory.
Source: `app/utils/arca_captcha.py:98-137`.
It finds the text input by ARCA-specific selectors and fills it, including input/change events.
Source: `app/utils/arca_captcha.py:140-164`.
It never writes CAPTCHA image HTML, image bytes, or credentials to disk.
### CapMonster image task flow
`get_arca_capmonster_config()` reads:
- `ARCA_CAPMONSTER_API_KEY`, with fallback to `SRT_CAPMONSTER_API_KEY` or `RETPER_CAPMONSTER_API_KEY`
- `ARCA_CAPMONSTER_TIMEOUT_SECONDS`, default 60 seconds
- `ARCA_CAPMONSTER_POLL_SECONDS`, default 3 seconds
- `ARCA_CAPMONSTER_MAX_ATTEMPTS`, default 5
- `ARCA_CAPMONSTER_MODULE`
- `ARCA_CAPMONSTER_CASE`, default true
- `ARCA_CAPMONSTER_THRESHOLD`
Source: `app/utils/arca_captcha.py:42-75`.
If no key is configured, the utility records an error and returns false.
Source: `app/utils/arca_captcha.py:199-208`.
For each attempt it gets the in-memory image base64.
It calls `app.utils.capmonster.solve_image_captcha()`.
It fills the response and returns success.
Source: `app/utils/arca_captcha.py:210-245`.
The image task is `ImageToTextTask`.
The CapMonster request contains the base64 `body`, case setting, optional module, and optional recognizing threshold.
Source: `app/utils/capmonster.py`, `solve_image_captcha()`.
The external endpoints are `https://api.capmonster.cloud/createTask` and `https://api.capmonster.cloud/getTaskResult`.
`requests.post` uses a 30-second HTTP timeout and is offloaded with `asyncio.to_thread`.
Source: `app/utils/capmonster.py:10-103`.
### Generic reCAPTCHA flow
`app/utils/capmonster.py` separately supports reCAPTCHA v2.
It detects site keys from `data-sitekey` elements or CAPTCHA iframes.
Source: `app/utils/capmonster.py:14-47`.
It creates `RecaptchaV2Task` by default.
Defaults are 180 seconds total and five-second polling.
Source: `app/utils/capmonster.py:57-103`.
It injects the solved token into `g-recaptcha-response` textareas and dispatches input/change events.
Source: `app/utils/capmonster.py:106` onward.
SRT and some provincial flows can use this generic family rather than the ARCA image flow.
### Proxy behavior
`get_proxy_config()` returns either a Playwright proxy dictionary or `None`.
Source: `app/utils/proxies.py:94-161`.
`SERVER_PROXY` is the final enable/disable switch.
When it is false, configuration construction is discarded and the function returns `None`.
The supported shapes are:
| Mode | Trigger | Server selection | Session property |
|---|---|---|---|
| Rotating | `ROTATING_PROXIES=True` | configured rotating host, default port 7777 | normalized customer username with country suffix |
| Sticky | `STICKY_SESSION=True` | `pr.oxylabs.io:7777` | random zero-padded session ID, `sesstime-10` |
| Datacenter | `DATACENTER_PROXY=True` | random port between configured start/end | normalized `user-...` username |
| Standard | otherwise | fixed configured port or random configured range | ordinary configured username/password |
The country suffix defaults to `ar`.
Source: `app/utils/proxies.py:17-32` and `:94-147`.
`PROXY_DEBUG=True` prints proxy configuration but masks its password.
Source: `app/utils/proxies.py:152-159`.
V3 should inject proxy settings into a per-job runtime context, not make plugin code read ambient globals at import time.
### Cookies, storage state, and reuse
Normal ARCA login creates a fresh ephemeral context.
There is no shared browser profile directory in `app/utils/arca_login.py`.
There is no persistent `storage_state` file written by the common login helper.
A fresh login is therefore the default for each bot execution.
Mis Comprobantes returns a `cookies_header` string from the asynchronous consultation operation.
The code comments say cookies are needed for later downloads/consultations.
Source: `app/bot/comprobantes_bot.py:782-805`.
That is application-level handoff inside an API/job workflow, not durable browser-profile reuse.
Declaración en Línea copies the currently authenticated `storage_state` into a second temporary context to render/download PDFs.
Source: `app/bot/declaracion_en_linea_bot.py:674-755`.
Mis Facilidades also creates an extra browser context with in-memory `storage_state` for PDFs.
Source: `app/bot/mis_facilidades_bot.py:608-660`.
V3 rule: never serialize unencrypted `storage_state` or raw cookies into normal job output/logs.
If continuation needs cookies, place encrypted session material in a short-lived secret store keyed by job/continuation ID and delete it on finalization.
## 4. Resource profile and execution duration
### Browser/process count
The normal profile is one Playwright driver plus one Chromium browser per execution.
The common code creates one `BrowserContext` and one `Page`.
Examples include Hacienda at `app/bot/hacienda_bot.py:1684-1686` and Mis Comprobantes at `app/bot/comprobantes_bot.py:398-402`.
Many other modules repeat this pattern directly.
Examples include Aportes, CCMA, Compensaciones, ARBA, AGIP, Portal IVA, Retenciones, and VEP-related bots.
An execution can spawn additional browser processes for PDF rendering.
This occurs in Declaración en Línea and Mis Facilidades.
Therefore V3 capacity planning must use **browser processes per job**, not merely jobs per worker.
### Existing concurrency controls
`BROWSER_CONCURRENCY` defaults to `3`.
Source: `app/utils/browser_limiter.py:14-16` and `:39-45`.
The semaphore is per event loop.
Source: `app/utils/browser_limiter.py:9-13`.
It is not distributed across processes, worker containers, or replicas.
Only `mis_retenciones_bot.py` and `mis_retenciones_iva_simple_bot.py` visibly use `async with limit_browser()` inside their own modules.
Source: `app/bot/mis_retenciones_bot.py:1015` and `app/bot/mis_retenciones_iva_simple_bot.py:1046`.
The V2 job worker also imports and applies the limiter around executor work.
Source: `app/jobs/worker.py:13` and `:149` onward.
Legacy direct routes may bypass job-worker coordination.
### PID safety evidence
The browser limiter documents the failure mode explicitly.
A surge of independent Chromium processes may exhaust the cgroup PID limit and cause `BrowserType.launch: Connection closed while reading from the driver`.
Source: `app/utils/browser_limiter.py:4-7`.
Compose sets `pids_limit: 512`.
Source: `docker-compose.yml:10-17`.
Compose additionally declares a single replica because a per-process semaphore would multiply actual browser concurrency across replicas.
Source: `docker-compose.yml:12-16`.
### Memory and CPU expectations
The repository supplies no measured RSS, CPU percentage, or browser duration benchmark.
Do not present invented values as observations.
**GUESS:** budget one active Chromium job as a high-memory, bursty CPU workload.
**GUESS:** start with one concurrent job per V3 worker container when resource limits are unknown.
**GUESS:** then load-test each bot family with production-like data and proxy routes to determine safe memory and CPU requests/limits.
Large data/export bots are more expensive because they retain tabular data and create XLSX/ZIP/PDF artefacts.
Those include Hacienda, Liquidación Granos, Declaración en Línea, Mis Facilidades, Portal IVA, Mis Comprobantes, SIFERE, and potentially VEP Archivo.
The greatest browser-time risks are site navigation, large result pagination, ARCA service load, CAPTCHA, and downloads.
### Concrete timeouts found
| Area | Observed values | Evidence |
|---|---|---|
| Common page ready helper | 12,000 ms DOM and 12,000 ms network idle | `app/utils/arca_login.py:139-147` |
| ARCA image CAPTCHA task | 60 s task timeout, 3 s polling, 5 attempts | `app/utils/arca_captcha.py:65-75` |
| Generic reCAPTCHA task | 180 s total, 5 s polling | `app/utils/capmonster.py:57-65` |
| ARCA browser limiter | default 3 browser permits | `app/utils/browser_limiter.py:39-69` |
| Mis Comprobantes direct service fallback | 15,000 ms navigation | `app/bot/comprobantes_bot.py:1450` |
| Hacienda service/link steps | 2 s to 30 s individual waits | `app/bot/hacienda_bot.py:620-1231` |
| Hacienda download helper | default 30,000 ms | `app/bot/hacienda_bot.py:943-949` |
| Compensaciones service navigation | 45,000 ms goto and 30,000 ms download | `app/bot/compensaciones_bot.py:230-374` |
| Certificado MiPyME download | 30,000 ms | `app/bot/certificado_mipyme_bot.py:208-210` |
| Consulta pagos VEP download | 30,000 ms | `app/bot/consulta_pagos_vep_bot.py:248-252` |
| Controladores Fiscales file download | 30,000 ms | `app/bot/controladores_fiscales_bot.py:186-200` |
| Libros Portal IVA downloads | 45,000 ms | `app/bot/libros_portal_iva_bot.py:627-675` |
| Portal IVA file inputs | 15,000 ms each | `app/bot/carga_portal_iva_bot.py:331-332` |
The code does not define a universal per-job timeout inside `app/bot/`.
A V3 worker should define a job deadline at the transport/orchestrator level.
It should also pass a remaining-time budget down to plugin waits.
### Complexity tiers for scheduling
| Tier | Bots | Why |
|---|---|---|
| Light browser query | Facturómetro, SIPER, SRT, ARBA, AGIP | Small result sets and few outputs, though CAPTCHA/proxy may dominate. |
| Standard download | Aportes, CCMA, Certificado MiPyME, Consulta Pagos VEP, Pago Devoluciones, Retenciones, MOA | One service, one/few reports, normal browser lifecycle. |
| Heavy extraction/export | Mis Comprobantes, Hacienda, Liquidación Granos, Declaración en Línea, Mis Facilidades, Libros Portal IVA, SIFERE | Pagination, multiple artefacts, DataFrames, XLSX/PDF/ZIP transformations. |
| Side-effecting/upload/import | Portal IVA, Portal IVA Carga, Controladores Fiscales, VEP Archivo, VEP CCMA | Uploads, declaration presentation, payment-slip creation, or data import/deletion actions. |
V3 should give side-effecting jobs stricter idempotency, explicit user intent, and traceable operation IDs.
## 5. Filesystem dependencies and stateless-worker consequences
### Classified paths
| Path or mechanism | Read/write behavior | Current evidence | V3 disposition |
|---|---|---|---|
| OS temp directory | Writable transient work | Many `TemporaryDirectory`/`NamedTemporaryFile` calls | Writable `emptyDir` or container temp. Per-job directory mandatory. |
| `./descargas/` | Optional persistent local fallback | Multiple bots below | Avoid as a shared worker volume. Map only to a per-job work root. |
| `./descargas/mis_comprobantes/...` | Read/write when cleanup disabled | `comprobantes_bot.py:368-372`, `:1160-1164` | Replace with `/work/<job>/...` or object storage. |
| `./descargas/hacienda/...` | Read/write when cleanup disabled | `hacienda_bot.py:1859-1868` | Replace with per-job work root. |
| `./descargas/liquidacion_granos/...` | Read/write when cleanup disabled | `liquidacion_granos_bot.py:1434-1439` | Replace with per-job work root. |
| `./descargas/mis_retenciones` | Optional base root | `mis_retenciones_bot.py:983` | Replace with injected work root. |
| `./descargas/mis_retenciones_iva_simple` | Optional base root | `mis_retenciones_iva_simple_bot.py:1014` | Replace with injected work root. |
| `./descargas/vep/<CUIT>` | Optional VEP output root | `vep_archivo_bot.py:543` | Replace with per-job work root. |
| `./descargas/Facturas/...` | RCEL local cleanup target | `rcel_bot.py:817` | Replace with per-job work root. |
| Input source directory | Read PEM files | Controladores Fiscales receives `archivos_dir` | Download uploaded inputs from object storage into per-job work. |
| Output directory parameter | Writes constancias | Controladores Fiscales receives `descargas_dir` | Inject per-job writable work directory. |
| `/code/data` | Writable API state/SQLite volume | `Dockerfile:28-30`, compose `:43` | Not mounted by worker. Worker must not contain a database. |
| `/code/logs` | Writable API logs | `Dockerfile:28-30`, compose `:44` | Use stdout/stderr structured logs. Temporary diagnostic files only. |
| `/code/mrbot-keys` | Read-only credential/key material | compose `:45` | Mount only if a worker plugin truly needs it. Prefer a secrets provider. |
| `/code/reporte` | Read-only SQL report resources | `Dockerfile:22` | Omit from worker image. Not a bot runtime dependency. |
| `Logs_Error_Individuales/` | Not found in scoped `app/bot` references | Repository-level requested path, no matching bot source | Do not mount by default. **GUESS:** legacy/dev diagnostics only. |
### Temp-work requirements
All browser workers need a writable filesystem.
This includes Playwright download saves, generated XLSX/CSV/ZIP/PDF files, and Python libraries' temporary allocations.
A V3 worker should create a unique directory such as `/work/jobs/<job-id>`.
It should pass that directory explicitly to the plugin.
It should never derive a cross-job directory from a taxpayer name.
The work directory should be deleted on both successful completion and terminal failure after outputs are uploaded.
For diagnostics retained after failure, upload the selected scrubbed files to object storage under a job-scoped retention policy.
### Artefact upload dependency
Many productive bots call `subir_archivo_a_minio` directly.
Examples:
- `app/bot/hacienda_bot.py:1823`.
- `app/bot/comprobantes_bot.py:508`, `:614`, `:1367`.
- `app/bot/ccma_bot.py:636`.
- `app/bot/controladores_fiscales_bot.py:420`.
- `app/bot/portal_iva_bot.py:1079`.
This does not make the filesystem shared.
It makes object storage a runtime external dependency.
For V3, either retain a narrow artifact-store client inside the worker or use pre-signed upload URLs supplied by the control plane.
Pre-signed URLs reduce worker access to a broad storage credential.
### `data/` and SQLite
`/code/data` is created in the image and mounted by Compose.
It exists for the API and SQLite state, not for a bot's normal artefact lifecycle.
Source: `Dockerfile:27-30`, `docker-compose.yml:40-45`.
The entrypoint changes ownership and mode to support SQLite writes.
Source: `docker-entrypoint.sh:1-31`.
A stateless worker must not mount this directory.
### `mrbot-keys/`
Compose mounts `./mrbot-keys` read-only at `/code/mrbot-keys`.
Source: `docker-compose.yml:45`.
No direct path reference was found in the scoped bot modules.
It may be consumed by code outside this scope or future certificates.
**GUESS:** the V2 API needs it for encryption/key operations rather than browser automation itself.
Do not include it in the worker by default.
Add a plugin-declared secret requirement if a future bot proves it needs a key.
## 6. External dependencies
### Browser runtime
The base image is an internal Playwright Python image:
`docker.abp.net.ar/abustosp/bb:py3.14.7-pw1.62-20260818`.
Source: `Dockerfile:1`.
The commented alternative is Microsoft Playwright Python `v1.57.0-noble`.
Source: `Dockerfile:2`.
The active base image is therefore expected to contain:
- Python 3.14.7.
- Playwright Python runtime.
- Playwright browser binaries compatible with Playwright 1.62.
- Chromium and its OS shared-library dependencies.
The exact OS package manifest cannot be established from this repository because the internal base image Dockerfile is not present.
Do not claim an exact apt package list from this source tree.
### System executable dependency
`app/utils/pem_converter.py` invokes:
`openssl cms -verify -noverify -inform PEM -in <path>`.
Source: `app/utils/pem_converter.py:59-86`.
Therefore the worker image needs `openssl` when the PEM conversion route/workflow is placed in it.
Controladores Fiscales itself uploads PEM files, but PEM conversion is exposed separately by `routes/procesar_pem.py` and is not necessarily a browser-plugin responsibility.
### Exact Python requirements file
The V2 `requirements.txt` contains exactly the following pins:
| Package | Version | Runtime relevance |
|---|---:|---|
| fastapi | 0.141.1 | API only in a worker-only image |
| uvicorn | 0.52.4 | API only in a worker-only image |
| cryptography | 50.0.1 | API/security and possible secret handling |
| passlib | 1.7.4 | API authentication |
| python-multipart | 0.0.32 | API uploads |
| email-validator | 2.3.0 | API validation |
| sqlalchemy | 2.0.52 | Must be omitted from no-DB worker if no remaining import needs it |
| alembic | 1.19.2 | API/database migration only |
| pydantic | 2.13.5 | Request/plugin schemas may use it |
| pydantic-settings | 2.15.0 | Runtime configuration may use it |
| python-dotenv | 1.2.3 | Development/local configuration, avoid production `.env` dependency |
| starlette | 1.6.0 | API only unless retained transitively |
| pandas | 3.0.5 | Required by multiple report/extraction bots |
| playwright-stealth | 2.0.3 | Potential stealth helper dependency |
| numpy | 2.5.3 | Required by Mis Comprobantes and tabular code |
| openpyxl | 3.1.5 | Required for XLSX reads/writes |
| aiofiles | 25.1.0 | Async file use where retained |
| minio | 7.2.20 | Required if worker uploads directly to MinIO |
| uuid-utils | 0.17.0 | Shared utility use, inspect actual dependency graph before retaining |
| requests | 2.34.2 | CapMonster and HTTP integrations |
| pdfplumber | 0.11.10 | `app/utils/extractor.py`, PDF parsing |
| beautifulsoup4 | 4.15.0 | Hacienda/Liquidación Granos HTML parsing |
| jinja2 | 3.1.6 | Report/template code where retained |
Source: `requirements.txt:1-25`.
### Direct Python imports not separately pinned
The code imports `playwright.async_api` throughout.
The active Docker base image likely supplies Playwright.
The requirements file does not list `playwright` itself.
This makes the base-image tag a functional dependency rather than merely an OS base choice.
V3 should explicitly document and lock the Python Playwright version, browser revision, and base-image digest together.
### External network services
| Service | Why it is needed | Evidence |
|---|---|---|
| ARCA / AFIP auth | Representative login | `app/utils/arca_login.py:102` |
| ARCA delegated services | Most bots | `open_arca_service()` usages across `app/bot/` |
| ARBA | Provincial IIBB reports | `app/bot/arba_bot.py` |
| AGIP | CABA IIBB reports | `app/bot/retper_iibb_agip_bot.py` |
| ATM Misiones | Provincial IIBB report endpoint | `app/bot/retper_iibb_misiones_bot.py:25` |
| SIFERE | Convenio Multilateral reporting | `app/bot/sifere_bot.py` |
| SRT | Alícuotas lookup | `app/bot/srt_bot.py` |
| CapMonster Cloud | Image CAPTCHA and reCAPTCHA solving | `app/utils/arca_captcha.py`, `app/utils/capmonster.py` |
| Proxy provider/Oxylabs | Optional source IP routing/sticky sessions | `app/utils/proxies.py:94-147` |
| MinIO | Artefact upload/storage | `subir_archivo_a_minio` imports across bots |
## 7. Docker setup and a worker-only image
### Current Dockerfile
The Dockerfile is single-stage.
It sets `/code` as the working directory.
Source: `Dockerfile:14`.
It copies only `requirements.txt` before installing dependencies.
It runs:
`pip install --break-system-packages --no-cache-dir -r /code/requirements.txt`
Source: `Dockerfile:16-18`.
It then copies:
- `app`
- `reporte`
- `alembic`
- `alembic.ini`
- `docker-entrypoint.sh`
Source: `Dockerfile:20-25`.
It creates `/code/data` and `/code/logs`.
It creates a non-root `mrbot` user.
It recursively changes `/code` ownership to that user.
Source: `Dockerfile:27-30`.
It sets `docker-entrypoint.sh` as the entrypoint.
Source: `Dockerfile:31`.
The health check uses `urllib.request` against `http://127.0.0.1:8000/health`.
Source: `Dockerfile:33-34`.
The command runs Alembic migration and Uvicorn:
`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`
Source: `Dockerfile:36-37`.
### Current entrypoint
The entrypoint makes `/code/data` and `/code/logs`.
It repairs ownership and permissions when the initial process is root.
Source: `docker-entrypoint.sh:8-18`.
It additionally repairs a SQLite database path under `/code` derived from `DATABASE_URL`.
Source: `docker-entrypoint.sh:20-31`.
It drops privileges to `mrbot` with `runuser`, `setpriv`, or `su` if available.
Source: `docker-entrypoint.sh:33-45`.
### Current Compose services
`docker-compose.yml` has two services.
The primary service is `api-bots-mrbot`.
It maps host port 5010 to container 8000.
It has Docker init enabled to reap orphaned Playwright/browser children.
Source: `docker-compose.yml:4-17`.
It uses an external `nginx_default` network.
It mounts:
- `./data:/code/data`
- `./logs:/code/logs`
- `./mrbot-keys:/code/mrbot-keys:ro`
Source: `docker-compose.yml:38-48`.
The second service, `api-bots-mrbot-db-v2`, runs sqlite-web.
It mounts the same data directory and serves its administration UI only on `127.0.0.1:5011`.
Source: `docker-compose.yml:50-70`.
### Current publishing script
`build-and-push.sh` requires an `.env` with `REGISTRY_URL`, `REGISTRY_USER`, and `REGISTRY_PASS`.
Source: `build-and-push.sh:14-26`.
It builds local image `abustosp/api-bots-mrbot-v2:latest`.
It tags the remote image with `latest` and a `YYYYMMDD_HHMMSS` timestamp.
It pushes both tags.
Source: `build-and-push.sh:28-72`.
### Required worker-image changes
A V3 browser worker image should:
1. Retain the compatible Playwright/Chromium base image and pin it by immutable digest.
2. Copy only worker runtime code, shared bot libraries, plugin modules, and their templates/data that are truly required.
3. Exclude FastAPI routes, Uvicorn, Alembic, SQLAlchemy, the SQLite database, `reporte/`, and `alembic/`.
4. Remove the HTTP `/health` check unless the worker deliberately exposes a health endpoint.
5. Replace the CMD with a queue consumer, one-shot job runner, or platform-native worker entrypoint.
6. Do not run migrations.
7. Keep the non-root user.
8. Provide a writable `/work` directory, preferably an ephemeral volume.
9. Ensure the container has an init process or equivalent child reaping because Chromium spawns children.
10. Set an explicit PID limit, memory limit, CPU limit, and worker concurrency.
11. Inject credentials, CapMonster key, proxy credentials, and object-store capability through a secrets mechanism.
12. Mount `mrbot-keys` only if a plugin declares a real need.
13. Use stdout structured logs rather than a shared `/code/logs` volume.
14. Use a queue liveness/readiness signal rather than an API health URL.
### One image versus per-bot images
Recommendation: begin with **one versioned worker image containing all production browser plugins**.
The shared image should contain common Playwright browser binaries, ARCA login/CAPTCHA code, tabular/PDF libraries, and plugin entrypoints.
This minimizes duplicated  browser images and reduces deployment/version skew.
It also lets a central scheduler route jobs by plugin name without image orchestration on every request.
The image must not include `bots_dev/`, API routes, DB models, or migration assets.
Use image labels and a generated plugin manifest to show supported bot versions.
Later, split out a per-bot image only where one of these conditions becomes true:
- a bot needs a different system dependency or browser channel.
- a bot needs much greater memory or a stricter network policy.
- a bot has exceptional security isolation needs.
- a bot changes much more frequently than the shared suite.
- start-up/image size becomes an observed operational issue.
**GUESS:** a future `controladores_fiscales` side-effecting image could be a candidate for stronger isolation.
**GUESS:** a future browserless image should be used for APOC and CUIT utilities, not a Chromium image.
## 8. `bots_dev/`: role, contents, and promotion
### Intended role
`bots_dev/` is a development and experimentation workspace.
It holds independent bot scripts, drafts, logger variants, test artifacts, samples, and scaffolding.
Production modules live separately in `app/bot/`.
The Dockerfile copies `app` but never copies `bots_dev`.
Source: `Dockerfile:20-25`.
Therefore development scripts are not shipped in the current product image.
### Requested AGENTS guide
No `bots_dev/AGENTS.md` exists in the inspected working tree.
A repository-wide filename search found no `AGENTS.md` under `bots_dev/`.
This contradicts the requested path rather than indicating a hidden policy.
No claim about its instructions can be made.
**GUESS:** it was removed, exists only in another branch, or the task reference is stale.
### Development directories
The top-level development directories found are:
- `agip`
- `apoc`
- `aportes_en_linea`
- `arba`
- `arca_login`
- `beneficios_mipyme`
- `bot_builder`
- `bot_builder_test`
- `carga_931`
- `carga_libro_iva`
- `cartilla_medica_union_personal`
- `ccma`
- `certificado_mipyme`
- `compensaciones`
- `consulta_cuit`
- `consulta_pagos_veps`
- `controladores_fiscales`
- `declaracion_en_linea`
- `efectores_pami`
- `facturometro`
- `habilitar_servicio`
- `hacienda`
- `libros_portal_iva`
- `liquidacion_granos`
- `mis_comprobantes`
- `mis_facilidades`
- `mis_retenciones`
- `mis_retenciones_iva_simple`
- `moa`
- `pago_devoluciones`
- `portal_iva`
- `portal_iva_ddjj_y_libros`
- `provinciales`
- `proxy_login_tester`
- `rcel`
- `retenciones_agip`
- `retper_iibb_misiones_capmonster`
- `sct`
- `shared`
- `sifere`
- `siper`
- `srt`
- `tests`
- `tucuman`
- `vep_archivo`
- `vep_ccma`
- `xubio_sueldos`
There are 48 top-level directories, counting tooling/test/shared directories.
### Development versus productive map
| Development area | Productive equivalent in `app/bot` | Status interpretation |
|---|---|---|
| `agip`, `retenciones_agip` | `retper_iibb_agip_bot.py` | Productive AGIP bot exists, names/variants differ. |
| `apoc` | `apoc.py` | Productive non-browser lookup exists. |
| `aportes_en_linea` | `aportes_en_linea_bot.py` | Productive bot exists. |
| `arba` | `arba_bot.py` | Productive bot exists. |
| `beneficios_mipyme` | `certificado_mipyme_bot.py` | Likely predecessor/alternate name. |
| `carga_931` | `declaracion_en_linea_bot.py` | Related ARCA declaration workflow, not a same-name production file. |
| `carga_libro_iva` | `carga_portal_iva_bot.py` / `portal_iva_bot.py` | Related Portal IVA import work. |
| `ccma` | `ccma_bot.py` | Productive bot exists. |
| `certificado_mipyme` | `certificado_mipyme_bot.py` | Productive bot exists. |
| `compensaciones` | `compensaciones_bot.py` | Productive as `sct_compensaciones`. |
| `consulta_cuit` | `consulta_cuit.py` | Productive non-browser lookup exists. |
| `consulta_pagos_veps` | `consulta_pagos_vep_bot.py` | Productive bot exists. |
| `controladores_fiscales` | `controladores_fiscales_bot.py` | Productive bot exists. |
| `declaracion_en_linea` | `declaracion_en_linea_bot.py` | Productive bot exists. |
| `facturometro` | `facturometro_bot.py` | Productive bot exists. |
| `hacienda` | `hacienda_bot.py` | Productive bot exists. |
| `libros_portal_iva` | `libros_portal_iva_bot.py` | Productive bot exists. |
| `liquidacion_granos` | `liquidacion_granos_bot.py` | Productive bot exists. |
| `mis_comprobantes` | `comprobantes_bot.py` | Productive bot exists under different filename. |
| `mis_facilidades` | `mis_facilidades_bot.py` | Productive bot exists. |
| `mis_retenciones` | `mis_retenciones_bot.py` | Productive bot exists. |
| `mis_retenciones_iva_simple` | `mis_retenciones_iva_simple_bot.py` | Productive bot exists. |
| `moa` | `moa_bot.py` | Productive bot exists. |
| `pago_devoluciones` | `pago_devoluciones_bot.py` | Productive bot exists. |
| `portal_iva` | `portal_iva_bot.py` and `carga_portal_iva_bot.py` | Productive download and import variants exist. |
| `rcel` | `rcel_bot.py` | Productive bot exists. |
| `retper_iibb_misiones_capmonster` | `retper_iibb_misiones_bot.py` | Productive bot exists under shorter name. |
| `sct` | `sct_bot.py` | Productive bot exists. |
| `sifere` | `sifere_bot.py` | Productive bot exists. |
| `siper` | `siper_bot.py` | Productive bot exists. |
| `srt` | `srt_bot.py` | Productive bot exists. |
| `vep_archivo` | `vep_archivo_bot.py` | Productive bot exists. |
| `vep_ccma` | `vep_ccma_bot.py` | Productive bot exists. |
| `arca_login` | `app/utils/arca_login.py` | Shared login experiments, not a service plugin. |
| `bot_builder`, `bot_builder_test` | No productive runtime equivalent | Development scaffolding/testing. |
| `shared` | `app/utils` partial parallels | Development-local utility copies. |
| `cartilla_medica_union_personal` | None | Development-only candidate. |
| `efectores_pami` | None | Development-only candidate. |
| `habilitar_servicio` | None | Development-only candidate. |
| `portal_iva_ddjj_y_libros` | Partial related productive bots | Experimental/combined workflow. |
| `provinciales` | No direct module | Development-only grouping. |
| `proxy_login_tester` | No direct module | Development diagnostics. |
| `tucuman` | None | Development-only provincial candidate. |
| `xubio_sueldos` | None | Development-only third-party candidate. |
| `tests` | No production plugin | Development test suite. |
### Shared development code
`bots_dev/shared/` contains:
- `arca_login.py`
- `arca_captcha.py`
- `proxy.py`
- `_dev_captcha_login_check.py`
- `new_bot.py`
- `__init__.py`
The development `arca_login.py` is explicitly a local copy intended to run with `PYTHONPATH=bots_dev`.
It closely mirrors production stealth/login behavior.
Source: `bots_dev/shared/arca_login.py:1-150`.
The development CAPTCHA helper is explicitly an autonomous copy of `app/utils/arca_captcha.py` intended to stay functionally equivalent.
Source: `bots_dev/shared/arca_captcha.py:1-9`.
This duplication is a maintenance risk.
V3 should publish a single versioned internal package for common browser/ARCA code rather than copying files into a development tree.
### `bot_builder`
`bots_dev/bot_builder/runner.py` defines the shared development runner.
`BotConfig` specifies bot name, env prefix, service name/matchers, headless mode, proxy flag, logger flag, and login URL.
Source: `bots_dev/bot_builder/runner.py:26-46`.
`BotContext` gives a flow access to page, browser, context, config, debug adapter, errors, and a results dictionary.
Source: `bots_dev/bot_builder/runner.py:49-59`.
`run_bot()` obtains `PREFIX_CUIT_LOGIN` and `PREFIX_CLAVE`, optionally creates a proxy, launches Chromium, records HAR in logger mode, logs in to ARCA, opens a service, calls the user flow, and tears down.
Source: `bots_dev/bot_builder/runner.py:61-123`.
Logger mode uses `DebugCapture` and embeds HAR content.
Clean mode uses `NullDebug` and avoids HAR, HTML, and screenshots.
Source: `bots_dev/bot_builder/runner.py:1-7`.
`steps.py` provides `@step()` logging and elapsed-time instrumentation.
Source: `bots_dev/bot_builder/steps.py:22-41`.
`env.py` reads `.env` and supports a per-bot variable prefix with unprefixed fallback.
Source: `bots_dev/bot_builder/env.py:8-36`.
`filenames.py` creates deterministic artefact names from represented CUIT, service, subtype, period, and denomination.
Source: `bots_dev/bot_builder/filenames.py:1-47`.
### New bot generator
`bots_dev/shared/new_bot.py` generates two files for a new ARCA service.
The generated `*_bot_logger.py` captures evidence.
The generated `*_bot.py` is the clean variant without HTML/screenshot/HAR capture.
Source: `bots_dev/shared/new_bot.py:1-12`.
The template produces a `flow(ctx)` and a `BotConfig` with service matching and optional proxy.
Source: `bots_dev/shared/new_bot.py:21-71`.
It writes files directly into an output directory given by `--out`.
Source: `bots_dev/shared/new_bot.py:97-124`.
### Promotion into production
No written promotion procedure was found because `bots_dev/AGENTS.md` is absent and no development markdown referenced `app/bot`.
The observed convention is inferable, but it is not an enforced process.
**Observed pattern:** prototype/draft/logger scripts exist in a dedicated directory.
**Observed pattern:** an `*_bot_mrbot.py` variant frequently appears as an integration candidate.
**Observed pattern:** a clean implementation is manually translated or copied into `app/bot/<name>_bot.py`.
**Observed pattern:** an executor is added in `app/jobs/executors/`.
**Observed pattern:** `app/jobs/registry.py` receives a key and lazy mapping.
**Observed pattern:** a V1 route is added and included in `app/api/api_router.py` when a direct route is wanted.
This is a manual promotion pipeline.
V3 should replace it with a reviewed plugin package template, contract tests, test fixtures, an explicit manifest, and a deployment release gate.
## 9. Direct database writes inside bot code
### Exhaustive result
**None.**
No module under `app/bot/*.py` imports the application database, SQLAlchemy session machinery, Mongo/Motor/PyMongo, or calls a database mutation method.
A source search covered database imports and common mutations including `db.add`, `commit`, `insert_one`, `update_one`, `replace_one`, `delete_one`, `execute`, `save`, `insert`, and `update`.
The matches within bot modules were only unrelated Python collection/DataFrame operations or browser JavaScript `sessionStorage` references.
Therefore there are no `app/bot/<file>:<line>` entries to list.
This is good news for the V3 no-DB-in-worker rule.
### Important V2 exception outside bot modules
The V2 **worker** writes a Mis Comprobantes log directly to the database.
It constructs `ConsultaLog`, calls `db.add(log)`, then `db.commit()`.
Source: `app/jobs/worker.py:69-141`, specifically lines 119-135.
The V2 `mis_retenciones_iva_simple` executor also writes a log model through its injected `db` argument.
Evidence:
- `app/jobs/executors/mis_retenciones_iva_simple.py:92` queries an existing log.
- `app/jobs/executors/mis_retenciones_iva_simple.py:94-110` adds and commits.
- `app/jobs/executors/mis_retenciones_iva_simple.py:124-140` adds and commits another branch.
These are not inside `app/bot/`.
They nevertheless break a broader interpretation of a no-DB V3 worker if executors are copied unchanged.
V3 must discard the V2 executor persistence behavior or move it into the control plane after the worker sends a result event.
## 10. Proposed V3 worker plugin contract
### Boundary
A worker receives a self-contained job request from the control plane.
It must not receive a database session, ORM model, connection string, or persistence callback.
It may receive short-lived credentials or a credential-reference resolvable through a secrets broker.
It writes outputs only to its job workspace and artifact storage.
It emits structured events/results to the control plane.
### Suggested request schema
```python
@dataclass(frozen=True)
class WorkerJob:
    job_id: str
    attempt: int
    plugin: str
    plugin_version: str
    deadline_at: datetime
    idempotency_key: str
    payload: dict[str, Any]
    credentials_ref: str | None
    proxy_profile_ref: str | None
    artifact_uploads: list[PresignedUpload]
    callback: ResultCallbackRef
```
`payload` contains only plugin-specific business fields.
Representative credentials must not be stored in the ordinary payload when avoidable.
`credentials_ref` should resolve to a short-lived in-memory secret bundle.
`artifact_uploads` should constrain the worker to specific object keys/presigned URLs.
### Suggested runtime context
```python
@dataclass
class BotRuntime:
    job_id: str
    work_dir: Path
    deadline: DeadlineBudget
    credentials: Credentials
    proxy: dict[str, str] | None
    artifact_store: ArtifactStore
    event_sink: EventSink
    browser_factory: BrowserFactory
    cancellation: CancellationToken
```
The runtime context owns all side effects.
Plugins should not read raw environment variables during business execution.
Plugins should not build working paths from `os.getcwd()`.
Plugins should not upload to arbitrary MinIO bucket/key values supplied by a caller.
### Suggested plugin protocol
```python
class BotPlugin(Protocol):
    manifest: PluginManifest
    async def validate(self, payload: Mapping[str, Any]) -> ValidatedPayload:
        ...
    async def execute(
        self,
        payload: ValidatedPayload,
        runtime: BotRuntime,
    ) -> BotResult:
        ...
```
A plugin manifest should declare:
- canonical name and semantic version.
- input schema version.
- required secrets.
- required external hosts.
- whether it needs Playwright/Chromium.
- whether it can trigger an external side effect.
- expected maximum browser count.
- expected artifact media types.
- conservative default deadline.
- retry safety and idempotency semantics.
### Suggested result schema
```python
@dataclass
class BotResult:
    status: Literal["succeeded", "partial", "failed", "cancelled"]
    data: dict[str, Any]
    artifacts: list[ArtifactRef]
    errors: list[PublicError]
    warnings: list[str]
    metrics: BotMetrics
    continuation: ContinuationRef | None = None
```
`ArtifactRef` must contain durable object-storage identity, media type, size, and checksum.
It must not expose local container paths.
`PublicError` must be credential-safe and selector/URL-safe, preserving V2's sanitization intent.
`BotMetrics` should include browser launch count, peak contexts, downloaded bytes, upload bytes, elapsed time, wait/retry counts, CAPTCHA outcome, and proxy profile ID without secret material.
### Login abstraction
Extract the following V3 components from `app/utils/arca_login.py`:
- `ArcaBrowserProfile` for accepted locale, timezone, UA, launch arguments, and anti-detection policy.
- `ArcaAuthenticator` for login state machine and normalized errors.
- `CaptchaSolver` for image CAPTCHA and reCAPTCHA implementations.
- `ArcaServiceNavigator` for service catalog selection/popup handling.
- `ArcaSession` as an explicitly scoped async context manager.
The session should close itself even if the plugin fails or is cancelled.
### Cancellation and time budget
All browser waits need a budget derived from `deadline_at`.
A plugin should never start a 45-second download wait when only 10 seconds remain.
The worker must terminate browser/context children on cancellation.
It must report a clear cancellation result to the control plane.
The platform must not retry side-effecting operations blindly.
### Idempotency classes
| Class | Examples | Retry approach |
|---|---|---|
| Read-only query/download | SIPER, Facturómetro, Retenciones download, Hacienda download | Retry with bounded attempts and new browser session when safe. |
| Asynchronous request then download | Mis Comprobantes request/history | Persist continuation state outside worker and resume deliberately. |
| Upload/import | Portal IVA, Portal IVA Carga | Require idempotency key and post-action verification before retry. |
| Presentation/payment-slip effect | Controladores Fiscales, VEP generation | Require explicit user intent, durable operation record in control plane, and verify before any retry. |
### Deployment recommendations
1. Run one browser job per container initially.
2. Let the scheduler, not an in-process semaphore, enforce global concurrency by plugin/site/proxy profile.
3. Add global caps for ARCA auth, each delegated ARCA service, each provincial site, and CapMonster spend.
4. Use an ephemeral per-job volume for `/work`.
5. Upload outputs before container completion.
6. Collect browser stderr/diagnostics only on failure and scrub sensitive information.
7. Keep browser profile, Playwright version, and plugin release version observable in every result.
8. Do not mount the V2 SQLite `data/` volume in workers.
9. Do not copy V2 `app/jobs/worker.py` or DB-writing executors into the worker image.
10. Test every plugin with contract-level mocked artifact store plus a controlled browser/site integration suite.
## Shared utility notes not covered above
### `app/utils/extractor.py`
`extractor.py` is a local PDF-text extraction helper.
It uses `pdfplumber` and regular expressions to extract fields from F.2002, F.731, and related fiscal PDF forms.
Source: `app/utils/extractor.py:1-244`.
It needs readable PDF input and memory/temporary space managed by its caller.
It has no browser, network, persistent filesystem, or database dependency of its own.
A V3 plugin should call it after downloading a PDF into the per-job work directory, then return only structured extracted values.
### `app/utils/pem_converter.py`
`pem_converter.py` verifies CMS PEM with the `openssl` executable then parses the returned XML into nested Python data.
Source: `app/utils/pem_converter.py:59-97`.
It is browser-independent but requires OpenSSL in the image when retained.
It must receive a readable local PEM staged in the job work directory.
### `app/utils/cuit_validation.py`
`cuit_validation.py` removes non-digits and applies the Argentine CUIT/CUIL 11-digit verifier algorithm.
Source: `app/utils/cuit_validation.py:1-21`.
It is pure Python and should be used by API/control-plane schema validation before a browser job is scheduled.
It does not belong in a heavyweight browser worker except as a small shared validation library.
## Appendix B. Research limitations
No bots were executed.
No credentials, CAPTCHA keys, proxy endpoints, ARCA/AFIP services, provincial services, or MinIO instance were contacted.
No browser resource benchmark was available in source.
No measured duration per bot was available in source.
The internal base image build definition and exact system package inventory were not in this repository.
`bots_dev/AGENTS.md` was not present.
All recommendations marked **GUESS** are design inferences rather than source facts.
