# Billing account modes

PostgreSQL is authoritative whenever `DATABASE_URL` is configured. Account configuration and counters live in `billing_accounts`; period quotas and counters live in `billing_account_periods`; each job's reservation/settlement state lives in `billing_usage`; credit balance is the sum of the append-only `credit_ledger`. Reservation, confirmation, cancellation, and credit ledger effects lock the account row and execute transactionally. The dictionaries in the billing modules remain the no-database test/development fallback only.

## Legacy `free` migration

Before account modes were introduced, the runtime user model defaulted existing users to the legacy `free` plan with 20 requests. Migration `0020` applies the requested compatibility rule using the durable admin enablement flag: users with `users.habilitado = true` receive `GRATUITA` (100 requests, 30-day initial grant); disabled users receive `CREDITOS` and no automatic free allowance. The migration does not infer legacy access from `subscriptions`, because the historical in-memory `free` plan was not seeded into PostgreSQL. The account insert uses `ON CONFLICT (user_id) DO NOTHING`, preserving any account already created by an administrator if the data step is rerun.
