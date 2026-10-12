# Jarvis Return Queue — PostgreSQL staging read-only verification

Verification date: 2026-10-12. **PASS**. This report records actual results from the isolated Railway sandbox, not inferred results.

## Execution identity and provenance

- Railway project: `Avtohirurg-Jarvis-Sandbox` (`73400534-5ebc-4619-86d9-f036e1028db9`)
- Environment: `staging` (`96dc8ace-8884-47b1-b529-ee2c317c7904`)
- Temporary runner: `jarvis-queue-sql-readonly-check` (`782a5a1d-bf81-40f3-84b1-f48fa0d9f1c3`)
- Runner deployment: `3478a297-ea02-452e-b059-907400e92ff3`, status `SUCCESS`
- Runner source commit: `dfe1e37d244ad893f8b0a9ef7d268504708492ce`
- SQL: `docs/sql/jarvis-return-queue-staging-readonly-verification.sql`
- Database: `jarvis_return_queue_test`
- Role: `jarvis_sandbox`
- PostgreSQL: `18.6 (Debian 18.6-1.pgdg13+2)`
- Connection: `jarvis-sandbox-postgres.railway.internal:5432`, no public domain/TCP proxy
- Mode: `BEGIN TRANSACTION READ ONLY` and `default_transaction_read_only=on`; no data modifications

## Actual SQL results

1. **PASS — Identity:** `current_database()=jarvis_return_queue_test`, `current_user=jarvis_sandbox`.
2. **PASS — Tables:** `public.jarvis_return_actions`, `public.jarvis_return_action_events`.
3. **PASS — Index:** `jarvis_return_action_events_task_time_idx` on `public.jarvis_return_action_events`, btree `(task_id, event_time DESC)`.
4. **PASS — Approval constraint:** `jarvis_return_actions_check` enforces that APPROVED/PROCESSING/DONE have `approved_by`, `approved_at` and `approved_fingerprint = action_fingerprint`.
5. **PASS — Delivery receipt:** `jarvis_return_actions_check1` requires `delivery_receipt IS NOT NULL` when state is DONE.
6. **PASS — Other constraints:** state allowlist, `attempt_count >= 0`, action primary key, event primary key, event-to-action foreign key, required fields.
7. **PASS — Test task:** `sandbox_return_queue_smoke_20261010`; state `PROCESSING`; version `1`; attempt_count `1`; lease_owner `sandbox-worker-2`; updated_at `2026-10-10 16:28:10.164920+00:00`.
8. **PASS — Event journal:** test task has `2` events; latest `2026-10-10 16:28:10.175186+00:00`.

Runner log ended with:
```
PASS: two tables, index, approval/receipt constraints, smoke records, event journal
PASS: exact PR #27 SQL executed read-only on private Railway staging
```

## Scope and limitations

- This SQL confirms that constraints **exist**; it does not itself rerun negative insert tests or simultaneous-worker concurrency tests.
- Prior migration acceptance runner logs reported negative constraint tests and exactly one winner in a two-worker claim, but those were not repeated in this read-only run.
- A single read-only snapshot does not establish persistence across a new restart. The smoke task is still present two days after its creation; no PostgreSQL restart was requested or performed.
- CRM Booking, production Jarvis, working n8n and customer messaging were not changed.
- Railway staging already had unrelated pending changes for `jarvis-return-worker`; these were not accepted or modified.
