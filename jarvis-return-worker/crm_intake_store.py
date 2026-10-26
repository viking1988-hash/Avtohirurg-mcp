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
