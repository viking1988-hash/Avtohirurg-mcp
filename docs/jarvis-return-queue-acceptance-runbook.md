# Jarvis Return Queue — sandbox acceptance execution

This document describes how to verify the isolated handler without modifying production,
Railway, CRM Booking, n8n or real customer channels.

## Offline tests

Run from the repository root:

```sh
python -m unittest discover -s tests -p 'test_jarvis_return_queue_offline_e2e.py' -v
python -m unittest discover -s tests -p 'test_jarvis_return_queue_adversarial.py' -v
```

## PostgreSQL 18 ephemeral CI service

The PostgreSQL integration suite requires `JARVIS_CI_DATABASE_URL`. It must point
only to database `jarvis_return_queue_test` with role `jarvis_sandbox`.
The repository guard checks both values. Use an ephemeral database rather than
Railway's persistent staging data for these destructive tests.

```sh
python -m unittest discover -s tests -p 'test_jarvis_return_queue_postgres.py' -v
python -m unittest discover -s tests -p 'test_jarvis_return_queue_postgres_sandbox.py' -v
```

## Acceptance gates

1. Offline end-to-end and adversarial suites pass.
2. PostgreSQL 18 schema/constraints and sandbox handler integration suites pass.
3. Test runner uses a disposable database with the required database and role.
4. No external delivery channels are configured; receipts are synthetic.
5. A concurrent claim test using two independent PostgreSQL connections passes.
6. Failure handling is manually reconciled before any retry.
7. No production rollout is permitted based on these tests alone.

The GitHub Actions workflow now executes the full isolated acceptance runner:
`scripts/run_jarvis_return_queue_acceptance.py`. On commit `938ea282`,
workflow run `38074219278` completed successfully with 22 acceptance tests
against an ephemeral PostgreSQL 18 service, plus the separate safety-tests job.
This certifies only the ephemeral CI tests, not Railway persistent staging.

## Persistent Railway staging: read-only first

Project: `Avtohirurg-Jarvis-Sandbox`; environment: `staging`.
Existing `jarvis-sandbox-postgres` must retain its private networking and
persistent volume. Before any restart or runner deployment, inspect service
inventory, status, mount, and latest successful deployment. Never run the
CI acceptance runner against this persistent database: it uses destructive
schema setup and cleanup.

For persistent staging, use the previously prepared read-only SQL
`docs/sql/jarvis-return-queue-staging-readonly-verification.sql` via an
approved private connection, capture actual SQL output, and compare before and
after any deliberately authorized restart. Do not report persistence as
independently verified until both SQL snapshots exist. Do not attach public
domains, TCP proxies, customer transport credentials, or working n8n.
