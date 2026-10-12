# Jarvis Return Queue — staging migration gate

Verified 2026-10-10, GitHub Actions run 38066764536, commit 1093b9e237ad5919369529cce122611ad374d839:

- safety-tests: SUCCESS
- postgres-integration (disposable PostgreSQL 18): SUCCESS
- MCP check run 38066764475: SUCCESS

Railway isolated project: Avtohirurg-Jarvis-Sandbox
Environment: staging
Service: jarvis-sandbox-postgres
Database: jarvis_return_queue_test
Expected role: jarvis_sandbox

## Important limitation
These CI results do NOT establish that the schema exists in the persistent Railway database. As of this report the migration has not been verified as applied there.

## Operator procedure (only in sandbox)
1. Confirm Railway project and environment IDs match the isolated sandbox, not production or CRM.
2. Confirm private host is jarvis-sandbox-postgres.railway.internal, database jarvis_return_queue_test and role jarvis_sandbox.
3. Obtain the sandbox-only connection URL through Railway variable references, without printing the password.
4. Run scripts/apply_jarvis_sandbox_schema.py --apply in a trusted sandbox runner with JARVIS_SANDBOX_DATABASE_URL and JARVIS_SANDBOX_DATABASE_NAME set. The script checks host, role, database and table/index existence.
5. Verify SELECT current_database(), current_user; and verify the two tables and event index.
6. Record migration execution logs with secrets redacted.
7. Do not connect production n8n, enable customer messaging, or merge PR until reviewed.

## Rollback
The schema is isolated. Do not drop a database or volume. If validation fails, stop, retain logs, and review manually before any destructive change.
