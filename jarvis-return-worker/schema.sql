-- Additive migration of the accepted sandbox tables, never production.
CREATE TABLE IF NOT EXISTS jarvis_return_actions (
 task_id text PRIMARY KEY, action_fingerprint char(64) NOT NULL,
 destination_key text NOT NULL, state text NOT NULL CHECK(state IN
 ('OPEN','READY','APPROVED','PROCESSING','RETRY','RECONCILIATION_REQUIRED','DELIVERED_CONFIRMED','UNDELIVERED_CONFIRMED','FAILED','DONE','CANCELLED')),
 version bigint NOT NULL DEFAULT 0, approved_by text, approved_at timestamptz,
 approval_expires_at timestamptz, approved_fingerprint char(64),
 lease_owner text, lease_until timestamptz,
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 delivery_receipt text, external_message_id text, delivery_started_at timestamptz,
 chat_id text, delivery_response jsonb,
 updated_at timestamptz NOT NULL DEFAULT now(),
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
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS external_message_id text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS delivery_started_at timestamptz;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS chat_id text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS delivery_response jsonb;
-- Replace the original unnamed state check additively so existing rows remain intact.
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_state_check;
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_state_allowed;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_state_allowed CHECK(state IN
 ('OPEN','READY','APPROVED','PROCESSING','RETRY','FAILED','DONE','CANCELLED','RECONCILIATION_REQUIRED'));
CREATE INDEX IF NOT EXISTS jarvis_worker_claim_idx ON jarvis_return_actions(state,next_attempt_at,created_at);
CREATE INDEX IF NOT EXISTS jarvis_return_action_events_task_time_idx ON jarvis_return_action_events(task_id,event_time DESC);
CREATE TABLE IF NOT EXISTS jarvis_fake_receipts (
 idempotency_key text PRIMARY KEY, task_id text NOT NULL REFERENCES jarvis_return_actions(task_id),
 fingerprint char(64) NOT NULL, receipt text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

-- Provider-neutral durable delivery and reconciliation model (additive Sandbox migration).
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS provider_message_id text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS provider_receipt text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS delivery_status text NOT NULL DEFAULT 'PENDING';
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'NONE';
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS reconciliation_reason text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS reconciliation_by text;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS reconciliation_at timestamptz;
ALTER TABLE jarvis_return_actions ADD COLUMN IF NOT EXISTS reconciliation_comment text;

-- Keep the legacy columns readable by PR #34 while backfilling the canonical provider fields.
UPDATE jarvis_return_actions SET
 provider=coalesce(provider,CASE WHEN chat_id IS NOT NULL THEN 'telegram' WHEN delivery_receipt IS NOT NULL THEN 'fake' END),
 provider_message_id=coalesce(provider_message_id,external_message_id),
 provider_receipt=coalesce(provider_receipt,delivery_receipt),
 delivery_status=CASE
   WHEN delivery_receipt IS NOT NULL THEN 'DELIVERED'
   WHEN state='RECONCILIATION_REQUIRED' OR delivery_started_at IS NOT NULL THEN 'UNKNOWN'
   WHEN state='FAILED' THEN 'FAILED'
   ELSE delivery_status END,
 reconciliation_status=CASE
   WHEN reconciliation_status<>'NONE' THEN reconciliation_status
   WHEN state='RECONCILIATION_REQUIRED' THEN 'REQUIRED'
   WHEN state='DONE' AND delivery_receipt IS NOT NULL THEN 'DELIVERED_CONFIRMED'
   ELSE 'NONE' END
WHERE delivery_receipt IS NOT NULL OR external_message_id IS NOT NULL
   OR provider IS NOT NULL OR provider_message_id IS NOT NULL OR provider_receipt IS NOT NULL
   OR state='RECONCILIATION_REQUIRED';

-- Extend supported lifecycle states while preserving all existing state invariants.
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_state_check;
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_state_allowed;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_state_allowed CHECK(state IN
 ('OPEN','READY','APPROVED','PROCESSING','RETRY','RECONCILIATION_REQUIRED',
  'DELIVERED_CONFIRMED','UNDELIVERED_CONFIRMED','FAILED','DONE','CANCELLED'));
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_delivery_status_check;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_delivery_status_check
 CHECK(delivery_status IN ('PENDING','STARTED','UNKNOWN','DELIVERED','UNDELIVERED','FAILED'));
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_reconciliation_status_check;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_reconciliation_status_check
 CHECK(reconciliation_status IN ('NONE','REQUIRED','IN_REVIEW','DELIVERED_CONFIRMED',
                                  'UNDELIVERED_CONFIRMED','APPROVAL_REQUESTED'));
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_delivery_receipt_match;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_delivery_receipt_match
 CHECK(provider_receipt IS NULL OR delivery_receipt IS NULL OR provider_receipt=delivery_receipt);
ALTER TABLE jarvis_return_actions DROP CONSTRAINT IF EXISTS jarvis_return_actions_message_id_match;
ALTER TABLE jarvis_return_actions ADD CONSTRAINT jarvis_return_actions_message_id_match
 CHECK(provider_message_id IS NULL OR external_message_id IS NULL OR provider_message_id=external_message_id);

-- Structured audit columns make the action journal directly queryable and replayable.
ALTER TABLE jarvis_return_action_events ADD COLUMN IF NOT EXISTS action text;
ALTER TABLE jarvis_return_action_events ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE jarvis_return_action_events ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE jarvis_return_action_events ADD COLUMN IF NOT EXISTS previous_state text;
ALTER TABLE jarvis_return_action_events ADD COLUMN IF NOT EXISTS new_state text;
UPDATE jarvis_return_action_events SET
 action=coalesce(action,metadata->>'action','legacy_event'),
 previous_state=coalesce(previous_state,from_state),
 new_state=coalesce(new_state,to_state)
WHERE action IS NULL OR previous_state IS NULL OR new_state IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS jarvis_provider_message_unique_idx
 ON jarvis_return_actions(provider,destination_key,provider_message_id)
 WHERE provider IS NOT NULL AND provider_message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS jarvis_provider_receipt_unique_idx
 ON jarvis_return_actions(provider,provider_receipt)
 WHERE provider IS NOT NULL AND provider_receipt IS NOT NULL;
CREATE INDEX IF NOT EXISTS jarvis_reconciliation_queue_idx
 ON jarvis_return_actions(state,reconciliation_status,updated_at);
CREATE INDEX IF NOT EXISTS jarvis_return_events_action_idx
 ON jarvis_return_action_events(action,event_time);
