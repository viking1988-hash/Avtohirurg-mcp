-- Jarvis Return Queue: read-only Railway staging verification.
-- Run ONLY on jarvis_return_queue_test as jarvis_sandbox via private Railway network.
-- No DDL, writes, restarts, public endpoints, or customer actions.
-- Evidence from this script alone cannot establish persistence across restart.
BEGIN TRANSACTION READ ONLY;
-- Fail closed if this script is pointed at any other database or role.
DO $
BEGIN
  IF current_database() <> 'jarvis_return_queue_test'
     OR current_user <> 'jarvis_sandbox' THEN
    RAISE EXCEPTION 'REFUSED: unexpected sandbox database identity';
  END IF;
END $;
SELECT current_database() AS db, current_user AS db_role, version() AS postgres_version;
SELECT to_regclass('public.jarvis_return_actions') AS actions_table,
       to_regclass('public.jarvis_return_action_events') AS events_table;
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname='public'
  AND indexname='jarvis_return_action_events_task_time_idx';
SELECT conrelid::regclass AS table_name, conname, contype,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid IN (
  to_regclass('public.jarvis_return_actions'),
  to_regclass('public.jarvis_return_action_events')
)
ORDER BY conrelid::regclass::text, conname;
-- Stable read-only snapshot: compare exact rows before/after authorized restart.
SELECT task_id, state, version, attempt_count, lease_owner, updated_at
FROM public.jarvis_return_actions
WHERE task_id LIKE 'sandbox_return_queue_smoke_20261010%'
ORDER BY task_id;
SELECT task_id, count(*) AS event_count, max(event_time) AS latest_event
FROM public.jarvis_return_action_events
WHERE task_id LIKE 'sandbox_return_queue_smoke_20261010%'
GROUP BY task_id ORDER BY task_id;
COMMIT;
