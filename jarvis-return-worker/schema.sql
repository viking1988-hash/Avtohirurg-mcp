-- Additive migration of the accepted sandbox tables, never production.
CREATE TABLE IF NOT EXISTS jarvis_return_actions (
 task_id text PRIMARY KEY, action_fingerprint char(64) NOT NULL,
 destination_key text NOT NULL, state text NOT NULL CHECK(state IN
 ('OPEN','READY','APPROVED','PROCESSING','RETRY','FAILED','DONE','CANCELLED')),
 version bigint NOT NULL DEFAULT 0, approved_by text, approved_at timestamptz,
 approval_expires_at timestamptz, approved_fingerprint char(64),
 lease_owner text, lease_until timestamptz,
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 delivery_receipt text, updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(state NOT IN ('APPROVED','PROCESSING','DONE') OR
 (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_fingerprint=action_fingerprint)),
 CHECK(state<>'DONE' OR delivery_receipt IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS jarvis_return_action_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 task_id text NOT NULL REFERENCES jarvis_return_actions(task_id), from_state text,
 to_state text NOT NULL, actor text NOT NULL, correlation_id text NOT NULL,
 event_time timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS payload jsonb;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS correlation_id text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS last_error text;
CREATE INDEX IF NOT EXISTS jarvis_worker_claim_idx ON jarvis_return_actions(state,next_attempt_at,created_at);
CREATE INDEX IF NOT EXISTS jarvis_return_action_events_task_time_idx ON jarvis_return_action_events(task_id,event_time DESC);
CREATE TABLE IF NOT EXISTS jarvis_fake_receipts (
 idempotency_key text PRIMARY KEY, task_id text NOT NULL REFERENCES jarvis_return_actions(task_id),
 fingerprint char(64) NOT NULL, receipt text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
