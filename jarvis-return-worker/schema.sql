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

-- Separate portfolio registry: recording work never enqueues an external action.
CREATE TABLE IF NOT EXISTS jarvis_projects (
 project_key text PRIMARY KEY CHECK(project_key ~ '^[a-z0-9][a-z0-9_-]{1,63}$'),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
 description text NOT NULL DEFAULT '',
 state text NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','PAUSED','ARCHIVED')),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jarvis_project_tasks (
 task_id text PRIMARY KEY CHECK(task_id ~ '^[A-Za-z0-9:_-]{1,128}$'),
 project_key text NOT NULL REFERENCES jarvis_projects(project_key),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 240),
 details text NOT NULL DEFAULT '',
 priority text NOT NULL DEFAULT 'NORMAL' CHECK(priority IN ('LOW','NORMAL','HIGH','URGENT')),
 source text NOT NULL DEFAULT '',
 state text NOT NULL DEFAULT 'INBOX'
   CHECK(state IN ('INBOX','READY','IN_PROGRESS','BLOCKED','REVIEW','DONE','CANCELLED')),
 due_at timestamptz,
 completion_evidence text,
 completed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK((state='DONE') = (completed_at IS NOT NULL AND completion_evidence IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS jarvis_project_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_key text NOT NULL REFERENCES jarvis_projects(project_key),
 actor text NOT NULL,
 action text NOT NULL,
 event_time timestamptz NOT NULL DEFAULT now(),
 metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS jarvis_project_task_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 task_id text NOT NULL REFERENCES jarvis_project_tasks(task_id),
 from_state text,
 to_state text NOT NULL,
 actor text NOT NULL,
 event_time timestamptz NOT NULL DEFAULT now(),
 metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS jarvis_project_tasks_queue_idx
 ON jarvis_project_tasks(state,priority,created_at);
CREATE INDEX IF NOT EXISTS jarvis_project_tasks_project_idx
 ON jarvis_project_tasks(project_key,state,created_at);
CREATE INDEX IF NOT EXISTS jarvis_project_task_events_idx
 ON jarvis_project_task_events(task_id,event_time DESC);
