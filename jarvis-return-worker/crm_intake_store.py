"""Durable, approval-gated staging for diagnostic intake before CRM dispatch."""
import hashlib
import json
import re

from psycopg.types.json import Jsonb

from .crm_intake import normalize_intake
from .queue import Conflict


_CORRELATION_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")


def _public(row, deduplicated=False):
    state = row["state"]
    status = {
        "PREPARED": "approval_required",
        "PENDING": "pending_external_confirmation",
        "PROCESSING": "pending_external_confirmation",
        "DONE": "crm_lead_saved",
        "FAILED": "failed",
        "RECONCILIATION_REQUIRED": "pending_external_confirmation",
    }[state]
    return {
        "source_id": row["source_id"],
        "state": state,
        "status": status,
        "version": row["version"],
        "external_lead_id": row["external_lead_id"],
        "external_status": row["external_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deduplicated": deduplicated,
    }


class CRMIntakeStore:
    """Persists the whitelisted intake payload; it never performs network I/O."""

    def __init__(self, repository):
        self.repository = repository

    def prepare(self, raw_payload, correlation_id, actor="jarvis-intake"):
        if not isinstance(correlation_id, str) or not _CORRELATION_ID.fullmatch(correlation_id):
            raise ValueError("invalid correlation ID")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
            raise ValueError("authenticated actor required")
        payload = normalize_intake(raw_payload)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.repository.connection() as conn:
            inserted = conn.execute(
                """INSERT INTO jarvis_crm_intake_requests
                   (source_id,request_fingerprint,correlation_id,payload,state)
                   VALUES (%s,%s,%s,%s,'PREPARED')
                   ON CONFLICT (source_id) DO NOTHING
                   RETURNING *""",
                (payload["source_id"], fingerprint, correlation_id, Jsonb(payload)),
            ).fetchone()
            if inserted:
                conn.execute(
                    """INSERT INTO jarvis_crm_intake_events
                       (source_id,action,actor,correlation_id,reason)
                       VALUES (%s,'prepared',%s,%s,'approval required before CRM dispatch')""",
                    (payload["source_id"], actor.strip(), correlation_id),
                )
                return _public(inserted)
            existing = conn.execute(
                "SELECT * FROM jarvis_crm_intake_requests WHERE source_id=%s FOR UPDATE",
                (payload["source_id"],),
            ).fetchone()
            if not existing or existing["request_fingerprint"] != fingerprint:
                raise Conflict("intake source ID already bound to different payload")
            if existing["correlation_id"] != correlation_id:
                raise Conflict("intake source ID already bound to different correlation")
            return _public(existing, deduplicated=True)

    def approve(self, source_id, expected_version, actor):
        if not isinstance(source_id, str) or not re.fullmatch(
                r"jarvis:[0-9a-fA-F-]{36}", source_id):
            raise ValueError("invalid source ID")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid expected version")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
            raise ValueError("authenticated human actor required")
        with self.repository.connection() as conn:
            row = conn.execute(
                "SELECT * FROM jarvis_crm_intake_requests WHERE source_id=%s FOR UPDATE",
                (source_id,),
            ).fetchone()
            if not row:
                raise Conflict("CRM intake not found")
            if row["version"] != expected_version:
                raise Conflict("stale CRM intake version")
            if row["state"] != "PREPARED":
                raise Conflict("CRM intake is not awaiting approval")
            approved = conn.execute(
                """UPDATE jarvis_crm_intake_requests
                   SET state='PENDING',approved_by=%s,approved_at=clock_timestamp(),
                       version=version+1,updated_at=clock_timestamp()
                   WHERE source_id=%s RETURNING *""",
                (actor.strip(), source_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO jarvis_crm_intake_events
                   (source_id,action,actor,correlation_id,reason)
                   VALUES (%s,'approved',%s,%s,'human approval; CRM delivery is still unconfirmed')""",
                (source_id, actor.strip(), row["correlation_id"]),
            )
            return _public(approved)

    def claim(self, owner):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("CRM intake lease owner required")
        with self.repository.connection() as conn:
            expired = conn.execute(
                """UPDATE jarvis_crm_intake_requests
                   SET state='RECONCILIATION_REQUIRED',
                       reconciliation_reason='lease_expired_after_claim',
                       lease_owner=NULL,lease_until=NULL,version=version+1,
                       updated_at=clock_timestamp()
                   WHERE state='PROCESSING' AND lease_until<clock_timestamp()
                   RETURNING source_id,correlation_id""",
            ).fetchall()
            for row in expired:
                conn.execute(
                    """INSERT INTO jarvis_crm_intake_events
                       (source_id,action,actor,correlation_id,reason)
                       VALUES (%s,'reconciliation_required',%s,%s,'lease_expired_after_claim')""",
                    (row["source_id"], owner, row["correlation_id"]),
                )
            row = conn.execute(
                """SELECT * FROM jarvis_crm_intake_requests
                   WHERE state='PENDING' AND next_attempt_at<=clock_timestamp()
                   ORDER BY created_at,source_id FOR UPDATE SKIP LOCKED LIMIT 1""",
            ).fetchone()
            if not row:
                return None
            claimed = conn.execute(
                """UPDATE jarvis_crm_intake_requests
                   SET state='PROCESSING',lease_owner=%s,
                       lease_until=clock_timestamp()+(%s*interval '1 second'),
                       attempt_count=attempt_count+1,version=version+1,
                       updated_at=clock_timestamp()
                   WHERE source_id=%s RETURNING *""",
                (owner, self.repository.config.lease_seconds, row["source_id"]),
            ).fetchone()
            conn.execute(
                """INSERT INTO jarvis_crm_intake_events
                   (source_id,action,actor,correlation_id)
                   VALUES (%s,'claimed',%s,%s)""",
                (row["source_id"], owner, row["correlation_id"]),
            )
            return claimed

    def _owned(self, conn, source_id, owner, attempt):
        row = conn.execute(
            "SELECT * FROM jarvis_crm_intake_requests WHERE source_id=%s FOR UPDATE",
            (source_id,),
        ).fetchone()
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if (not row or row["state"] != "PROCESSING" or row["lease_owner"] != owner
                or row["attempt_count"] != attempt or row["lease_until"] <= now):
            raise Conflict("stale CRM intake lease or fencing token")
        return row

    def finish(self, source_id, owner, attempt, result):
        lead_id = result.get("lead_id")
        receipt = result.get("receipt_id")
        status = result.get("status")
        if not lead_id or not receipt or not status:
            raise Conflict("CRM intake lead ID, receipt, and status required")
        with self.repository.connection() as conn:
            row = self._owned(conn, source_id, owner, attempt)
            saved = conn.execute(
                """UPDATE jarvis_crm_intake_requests
                   SET state='DONE',external_lead_id=%s,provider_receipt=%s,
                       external_status=%s,reconciliation_reason=NULL,
                       lease_owner=NULL,lease_until=NULL,version=version+1,
                       updated_at=clock_timestamp()
                   WHERE source_id=%s RETURNING *""",
                (str(lead_id)[:256], str(receipt)[:512], str(status)[:80], source_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO jarvis_crm_intake_events
                   (source_id,action,actor,correlation_id,reason)
                   VALUES (%s,'completed',%s,%s,'CRM receipt persisted')""",
                (source_id, owner, row["correlation_id"]),
            )
            return _public(saved)

    def fail(self, source_id, owner, attempt, classification, reason):
        from .transport import Classification
        kind = Classification(classification)
        with self.repository.connection() as conn:
            row = self._owned(conn, source_id, owner, attempt)
            retry = kind == Classification.RETRYABLE and attempt < self.repository.config.max_attempts
            if retry:
                state, action = "PENDING", "retry_scheduled"
                reconciliation_reason = None
                delay = min(2 ** max(0, attempt - 1), 3600)
            elif kind == Classification.PERMANENT or kind == Classification.RETRYABLE:
                state, action = "FAILED", "failed"
                reconciliation_reason = None
                delay = 0
            else:
                state, action = "RECONCILIATION_REQUIRED", "reconciliation_required"
                reconciliation_reason = str(reason or "provider_outcome_unknown")[:200]
                delay = 0
            saved = conn.execute(
                """UPDATE jarvis_crm_intake_requests
                   SET state=%s,reconciliation_reason=%s,
                       lease_owner=NULL,lease_until=NULL,
                       next_attempt_at=clock_timestamp()+(%s*interval '1 second'),
                       version=version+1,updated_at=clock_timestamp()
                   WHERE source_id=%s RETURNING *""",
                (state, reconciliation_reason, delay, source_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO jarvis_crm_intake_events
                   (source_id,action,actor,correlation_id,reason)
                   VALUES (%s,%s,%s,%s,%s)""",
                (source_id, action, owner, row["correlation_id"], str(reason or action)[:200]),
            )
            return _public(saved)

    def get_operation(self, source_id):
        with self.repository.connection() as conn:
            return conn.execute(
                "SELECT * FROM jarvis_crm_intake_requests WHERE source_id=%s",
                (source_id,),
            ).fetchone()

    def get(self, source_id):
        with self.repository.connection() as conn:
            row = conn.execute(
                """SELECT source_id,state,version,external_lead_id,external_status,
                          created_at,updated_at
                   FROM jarvis_crm_intake_requests WHERE source_id=%s""",
                (source_id,),
            ).fetchone()
            return _public(row) if row else None

    def events(self, source_id):
        with self.repository.connection() as conn:
            rows = conn.execute(
                """SELECT action,actor,correlation_id,reason,created_at
                   FROM jarvis_crm_intake_events WHERE source_id=%s
                   ORDER BY event_sequence""",
                (source_id,),
            ).fetchall()
            return rows
