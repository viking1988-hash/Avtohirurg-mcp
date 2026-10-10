"""PostgreSQL DDL proposal for Jarvis return queue (NOT executed).

Apply only to an isolated Jarvis staging database after reviewing existing schema.
Do not apply to CRM Booking or any production database.
"""
CREATE TABLE IF NOT EXISTS jarvis_return_actions (
    task_id text PRIMARY KEY,
    action_fingerprint char(64) NOT NULL,
    destination_key text NOT NULL,
    state text NOT NULL CHECK (state IN
      ('OPEN','READY','APPROVED','PROCESSING','RETRY','FAILED','DONE','CANCELLED')),
    version bigint NOT NULL DEFAULT 0,
    approved_by text,
    approved_at timestamptz,
    approval_expires_at timestamptz,
    approved_fingerprint char(64),
    lease_owner text,
    lease_until timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    delivery_receipt text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state NOT IN ('APPROVED','PROCESSING','DONE') OR
      (approved_by IS NOT NULL AND approved_at IS NOT NULL AND
       approved_fingerprint = action_fingerprint)),
    CHECK (state <> 'DONE' OR delivery_receipt IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS jarvis_return_action_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id text NOT NULL REFERENCES jarvis_return_actions(task_id),
    from_state text,
    to_state text NOT NULL,
    actor text NOT NULL,
    correlation_id text NOT NULL,
    event_time timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS jarvis_return_action_events_task_time_idx
  ON jarvis_return_action_events(task_id, event_time DESC);

-- Example single-winner claim, executed in one transaction after human
-- approval and delivery-status reconciliation. Bind :task_id, :owner,
-- :fingerprint, and :lease_seconds as parameters. Do not claim expired
-- PROCESSING tasks automatically: reconcile provider delivery first.
UPDATE jarvis_return_actions
SET state='PROCESSING', lease_owner=:owner,
    lease_until=now() + (:lease_seconds * interval '1 second'),
    attempt_count=attempt_count+1, version=version+1, updated_at=now()
WHERE task_id=:task_id AND state IN ('APPROVED','RETRY')
  AND approved_fingerprint=:fingerprint
  AND action_fingerprint=:fingerprint
  AND approval_expires_at > now()
RETURNING task_id, version;
