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
 ('OPEN','READY','APPROVED','PROCESSING','RETRY','RECONCILIATION_REQUIRED','DELIVERED_CONFIRMED','UNDELIVERED_CONFIRMED','FAILED','DONE','CANCELLED'));
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


-- Transactional outbox. Domain events are written by audit.record in the same
-- transaction as each queue or reconciliation state change.
CREATE TABLE IF NOT EXISTS jarvis_outbox_events (
 event_id text PRIMARY KEY,
 aggregate_id text NOT NULL,
 aggregate_type text NOT NULL,
 event_type text NOT NULL,
 event_version integer NOT NULL CHECK(event_version>0),
 payload jsonb NOT NULL,
 correlation_id text,
 causation_id text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 published_at timestamptz,
 retry_count integer NOT NULL DEFAULT 0 CHECK(retry_count>=0),
 status text NOT NULL DEFAULT 'PENDING'
   CHECK(status IN ('PENDING','PUBLISHED','FAILED')),
 UNIQUE(causation_id,event_type)
);
CREATE INDEX IF NOT EXISTS jarvis_outbox_pending_idx
 ON jarvis_outbox_events(status,created_at,event_id);

CREATE TABLE IF NOT EXISTS jarvis_event_subscribers (
 subscriber_name text NOT NULL,
 event_type text NOT NULL,
 event_version integer NOT NULL CHECK(event_version>0),
 status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','PAUSED')),
 error_count bigint NOT NULL DEFAULT 0 CHECK(error_count>=0),
 last_success_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(subscriber_name,event_type,event_version)
);

CREATE TABLE IF NOT EXISTS jarvis_outbox_deliveries (
 event_id text NOT NULL REFERENCES jarvis_outbox_events(event_id),
 subscriber_name text NOT NULL,
 event_version integer NOT NULL,
 status text NOT NULL DEFAULT 'PENDING'
   CHECK(status IN ('PENDING','PROCESSING','PUBLISHED','FAILED')),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_owner text,
 lease_until timestamptz,
 started_at timestamptz,
 published_at timestamptz,
 last_error text,
 PRIMARY KEY(event_id,subscriber_name,event_version)
);
CREATE INDEX IF NOT EXISTS jarvis_outbox_delivery_pending_idx
 ON jarvis_outbox_deliveries(status,next_attempt_at,event_id);

CREATE TABLE IF NOT EXISTS jarvis_processed_events (
 event_id text NOT NULL REFERENCES jarvis_outbox_events(event_id),
 subscriber_name text NOT NULL,
 event_version integer NOT NULL,
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(event_id,subscriber_name,event_version)
);

CREATE TABLE IF NOT EXISTS jarvis_outbox_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id text NOT NULL REFERENCES jarvis_outbox_events(event_id),
 subscriber_name text NOT NULL,
 event_version integer NOT NULL,
 action text NOT NULL CHECK(action IN ('delivered','retry_scheduled','permanent_failure')),
 actor text NOT NULL,
 detail jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS jarvis_outbox_audit_event_idx
 ON jarvis_outbox_audit(event_id,created_at);

-- A transactionally updated projection demonstrates a durable built-in
-- subscriber while keeping delivery handlers inside the database boundary.
CREATE TABLE IF NOT EXISTS jarvis_event_projection (
 event_id text NOT NULL REFERENCES jarvis_outbox_events(event_id),
 subscriber_name text NOT NULL,
 aggregate_id text NOT NULL,
 event_type text NOT NULL,
 event_version integer NOT NULL,
 payload jsonb NOT NULL,
 projected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(event_id,subscriber_name)
);

-- CRM operations are materialized only by the outbox subscriber. The unique
-- event/idempotency keys fence repeated replay; provider delivery is a separate
-- Orchestrator-controlled step and is never performed inside replay's DB txn.
CREATE TABLE IF NOT EXISTS jarvis_crm_operations (
 event_id text PRIMARY KEY REFERENCES jarvis_outbox_events(event_id),
 idempotency_key text NOT NULL UNIQUE,
 booking_id text NOT NULL,
 operation text NOT NULL CHECK(operation IN ('send','update','cancel')),
 crm_status text NOT NULL,
 external_booking_id text,
 provider_receipt text,
 reconciliation_reason text,
 payload jsonb NOT NULL,
 correlation_id text,
 state text NOT NULL DEFAULT 'PENDING'
   CHECK(state IN ('PENDING','PROCESSING','DONE','FAILED','RECONCILIATION_REQUIRED')),
 version bigint NOT NULL DEFAULT 0 CHECK(version>=0),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_owner text,
 lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(state <> 'RECONCILIATION_REQUIRED' OR reconciliation_reason IS NOT NULL)
);
ALTER TABLE jarvis_crm_operations ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS jarvis_crm_pending_idx
 ON jarvis_crm_operations(state,next_attempt_at,created_at,event_id);

CREATE TABLE IF NOT EXISTS jarvis_crm_operation_events (
 event_id text NOT NULL REFERENCES jarvis_crm_operations(event_id),
 event_sequence bigint GENERATED ALWAYS AS IDENTITY,
 action text NOT NULL,
 actor text NOT NULL,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(event_id,event_sequence)
);

-- Email intents are created by the outbox adapter. SMTP acceptance is QUEUED,
-- not proof of final delivery; ambiguous results are held for reconciliation.
CREATE TABLE IF NOT EXISTS jarvis_email_operations (
 event_id text PRIMARY KEY REFERENCES jarvis_outbox_events(event_id),
 idempotency_key text NOT NULL UNIQUE,
 recipient text NOT NULL,
 payload jsonb NOT NULL,
 correlation_id text,
 state text NOT NULL DEFAULT 'PENDING'
   CHECK(state IN ('PENDING','PROCESSING','QUEUED','SENT','FAILED','CANCELLED','RECONCILIATION_REQUIRED')),
 version bigint NOT NULL DEFAULT 0 CHECK(version>=0),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_owner text,
 lease_until timestamptz,
 provider_message_id text,
 smtp_message_id text,
 receipt text,
 provider_status text,
 delivery_time timestamptz,
 reconciliation_reason text,
 reconciled_by text,
 reconciliation_evidence text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(state <> 'RECONCILIATION_REQUIRED' OR reconciliation_reason IS NOT NULL),
 CHECK(state <> 'QUEUED' OR (smtp_message_id IS NOT NULL AND receipt IS NOT NULL)),
 CHECK(state <> 'SENT' OR (provider_message_id IS NOT NULL OR receipt IS NOT NULL))
);
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS provider_message_id text;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS smtp_message_id text;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS receipt text;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS provider_status text;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS delivery_time timestamptz;
ALTER TABLE jarvis_email_operations ADD COLUMN IF NOT EXISTS reconciliation_reason text;
CREATE INDEX IF NOT EXISTS jarvis_email_pending_idx
 ON jarvis_email_operations(state,next_attempt_at,created_at,event_id);

CREATE TABLE IF NOT EXISTS jarvis_email_operation_events (
 event_id text NOT NULL REFERENCES jarvis_email_operations(event_id),
 event_sequence bigint GENERATED ALWAYS AS IDENTITY,
 action text NOT NULL,
 actor text NOT NULL,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(event_id,event_sequence)
);


-- CRM diagnostic intake is persisted before any external call. PENDING can only
-- be reached through an explicit human approval; no booking is created here.
CREATE TABLE IF NOT EXISTS jarvis_crm_intake_requests (
 source_id text PRIMARY KEY,
 request_fingerprint char(64) NOT NULL,
 correlation_id text NOT NULL,
 payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'PREPARED'
   CHECK(state IN ('PREPARED','PENDING','PROCESSING','DONE','FAILED','RECONCILIATION_REQUIRED','DELIVERED_CONFIRMED')),
 version bigint NOT NULL DEFAULT 0 CHECK(version>=0),
 approved_by text,
 approved_at timestamptz,
 lease_owner text,
 lease_until timestamptz,
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 external_lead_id text,
 provider_receipt text,
 external_status text,
 reconciliation_reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(state NOT IN ('PENDING','PROCESSING','DONE') OR
       (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
 CHECK(state<>'DONE' OR (external_lead_id IS NOT NULL AND provider_receipt IS NOT NULL)),
 CHECK(state<>'RECONCILIATION_REQUIRED' OR reconciliation_reason IS NOT NULL),
 CHECK(state<>'DELIVERED_CONFIRMED' OR
       (external_lead_id IS NOT NULL AND reconciled_by IS NOT NULL AND reconciliation_evidence IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS jarvis_crm_intake_pending_idx
 ON jarvis_crm_intake_requests(state,next_attempt_at,created_at,source_id);

CREATE TABLE IF NOT EXISTS jarvis_crm_intake_events (
 source_id text NOT NULL REFERENCES jarvis_crm_intake_requests(source_id),
 event_sequence bigint GENERATED ALWAYS AS IDENTITY,
 action text NOT NULL CHECK(action IN
   ('prepared','approved','claimed','completed','retry_scheduled','failed','reconciliation_required',
   'reconciled_delivered','reconciled_not_delivered')),
 actor text NOT NULL,
 correlation_id text NOT NULL,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(source_id,event_sequence)
);
CREATE INDEX IF NOT EXISTS jarvis_crm_intake_events_source_time_idx
 ON jarvis_crm_intake_events(source_id,created_at);
