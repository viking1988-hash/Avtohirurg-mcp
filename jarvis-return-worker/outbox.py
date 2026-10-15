"""PostgreSQL transactional outbox and crash-safe internal event replay.

Subscriber handlers receive the same PostgreSQL connection used to persist their
processed marker. They must keep effects inside that transaction. External side
effects require their own provider idempotency and are deliberately not supported
by this exactly-once contract.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class Subscriber:
    name: str
    event_type: str
    version: int
    handler: object
    status: str = "ACTIVE"


class PermanentSubscriberError(Exception):
    """A subscriber rejected an event permanently; it will not be retried."""


def append_event(conn, *, aggregate_id, aggregate_type, event_type, event_version,
                 payload, correlation_id=None, causation_id=None):
    """Insert an outbox event in the caller's business transaction."""
    event_id = str(uuid.uuid4())
    row = conn.execute(
        """INSERT INTO jarvis_outbox_events
           (event_id,aggregate_id,aggregate_type,event_type,event_version,payload,
            correlation_id,causation_id,created_at,status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),'PENDING')
           ON CONFLICT (causation_id,event_type) DO UPDATE
             SET event_id=jarvis_outbox_events.event_id
           RETURNING event_id""",
        (event_id, str(aggregate_id), aggregate_type, event_type, event_version,
         Jsonb(payload), correlation_id, causation_id),
    )
    return row.fetchone()["event_id"]


class SubscriberRegistry:
    def __init__(self):
        self._subscribers = {}

    def register(self, name, event_type, handler, version=1, status="ACTIVE"):
        if not name or not event_type or version < 1 or status not in ("ACTIVE", "PAUSED"):
            raise ValueError("invalid subscriber registration")
        subscriber = Subscriber(name, event_type, int(version), handler, status)
        key = (name, event_type, int(version))
        if key in self._subscribers:
            raise ValueError("subscriber already registered")
        self._subscribers[key] = subscriber
        return subscriber

    @property
    def subscribers(self):
        return tuple(self._subscribers.values())


class OutboxReplay:
    def __init__(self, repository, registry, *, owner=None, retry_base_seconds=1,
                 retry_max_seconds=300):
        self.repository = repository
        self.registry = registry
        self.owner = owner or repository.config.worker_id
        self.retry_base_seconds = max(1, int(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, int(retry_max_seconds))

    def register_subscribers(self):
        with self.repository.connection() as conn:
            for sub in self.registry.subscribers:
                conn.execute(
                    """INSERT INTO jarvis_event_subscribers
                       (subscriber_name,event_type,event_version,status)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (subscriber_name,event_type,event_version)
                       DO UPDATE SET status=EXCLUDED.status,updated_at=clock_timestamp()""",
                    (sub.name, sub.event_type, sub.version, sub.status),
                )

    def replay_one(self, event_id=None):
        """Replay one event atomically; a crash rolls back both handler and marker."""
        attempted = None
        started = time.monotonic()
        try:
            with self.repository.connection() as conn:
                repaired = conn.execute(
                    """UPDATE jarvis_outbox_deliveries d SET status='PUBLISHED',
                         lease_owner=NULL,lease_until=NULL,
                         published_at=coalesce(d.published_at,clock_timestamp()),last_error=NULL
                       FROM jarvis_processed_events p
                       WHERE p.event_id=d.event_id AND p.subscriber_name=d.subscriber_name
                         AND p.event_version=d.event_version AND d.status<>'PUBLISHED'
                       RETURNING d.event_id,d.subscriber_name,d.event_version"""
                ).fetchall()
                for item in repaired:
                    conn.execute(
                        """INSERT INTO jarvis_outbox_audit
                           (event_id,subscriber_name,event_version,action,actor,detail)
                           VALUES (%s,%s,%s,'delivered',%s,%s)""",
                        (item["event_id"],item["subscriber_name"],item["event_version"],
                         self.owner,Jsonb({"recovered_processed_marker": True})),
                    )
                    conn.execute(
                        """UPDATE jarvis_event_subscribers SET last_success_at=clock_timestamp(),
                           updated_at=clock_timestamp() WHERE subscriber_name=%s AND event_version=%s
                           AND (event_type=(SELECT event_type FROM jarvis_outbox_events WHERE event_id=%s)
                                OR event_type='*')""",
                        (item["subscriber_name"],item["event_version"],item["event_id"]),
                    )
                    conn.execute(
                        """UPDATE jarvis_outbox_events e SET
                           status=CASE
                             WHEN EXISTS (SELECT 1 FROM jarvis_outbox_deliveries d
                               WHERE d.event_id=e.event_id AND d.status='FAILED') THEN 'FAILED'
                             WHEN EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                               WHERE (s.event_type=e.event_type OR s.event_type='*')
                                 AND s.event_version=e.event_version AND s.status='ACTIVE')
                              AND NOT EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                               LEFT JOIN jarvis_outbox_deliveries d ON d.event_id=e.event_id
                                 AND d.subscriber_name=s.subscriber_name
                                 AND d.event_version=s.event_version
                               WHERE (s.event_type=e.event_type OR s.event_type='*')
                                 AND s.event_version=e.event_version AND s.status='ACTIVE'
                                 AND coalesce(d.status,'PENDING')<>'PUBLISHED')
                             THEN 'PUBLISHED' ELSE 'PENDING' END,
                           published_at=CASE WHEN EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                             WHERE (s.event_type=e.event_type OR s.event_type='*')
                               AND s.event_version=e.event_version AND s.status='ACTIVE')
                             AND NOT EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                              LEFT JOIN jarvis_outbox_deliveries d ON d.event_id=e.event_id
                                AND d.subscriber_name=s.subscriber_name
                                AND d.event_version=s.event_version
                              WHERE (s.event_type=e.event_type OR s.event_type='*')
                                AND s.event_version=e.event_version AND s.status='ACTIVE'
                                AND coalesce(d.status,'PENDING')<>'PUBLISHED')
                             THEN coalesce(e.published_at,clock_timestamp()) ELSE NULL END
                           WHERE e.event_id=%s""",(item["event_id"],),
                    )
                row = conn.execute(
                    """SELECT e.*,s.subscriber_name,s.event_version
                       FROM jarvis_outbox_events e
                       JOIN jarvis_event_subscribers s
                         ON (s.event_type=e.event_type OR s.event_type='*')
                        AND s.event_version=e.event_version AND s.status='ACTIVE'
                       LEFT JOIN jarvis_outbox_deliveries d
                         ON d.event_id=e.event_id
                        AND d.subscriber_name=s.subscriber_name
                        AND d.event_version=s.event_version
                       WHERE (d.event_id IS NULL OR
                             (d.status='PENDING' AND d.next_attempt_at<=clock_timestamp()) OR
                             (d.status='PROCESSING' AND d.lease_until<clock_timestamp()))
                         AND NOT EXISTS (
                           SELECT 1 FROM jarvis_processed_events p
                           WHERE p.event_id=e.event_id AND p.subscriber_name=s.subscriber_name
                             AND p.event_version=s.event_version)
                         AND (%s IS NULL OR e.event_id=%s)
                       ORDER BY e.created_at,e.event_id,s.subscriber_name
                       FOR UPDATE OF e SKIP LOCKED LIMIT 1"""
                    , (event_id, event_id)
                ).fetchone()
                if not row:
                    return False
                attempted = (row["event_id"], row["subscriber_name"], row["event_version"])
                conn.execute(
                    """INSERT INTO jarvis_outbox_deliveries
                       (event_id,subscriber_name,event_version,status,lease_owner,lease_until,
                        attempt_count,started_at)
                       VALUES (%s,%s,%s,'PROCESSING',%s,
                               clock_timestamp()+interval '30 seconds',1,clock_timestamp())
                       ON CONFLICT (event_id,subscriber_name,event_version) DO UPDATE SET
                         status='PROCESSING',lease_owner=EXCLUDED.lease_owner,
                         lease_until=EXCLUDED.lease_until,
                         attempt_count=jarvis_outbox_deliveries.attempt_count+1,
                         started_at=clock_timestamp(),last_error=NULL""",
                    (row["event_id"], row["subscriber_name"], row["event_version"], self.owner),
                )
                sub = next((x for x in self.registry.subscribers
                            if x.name == row["subscriber_name"]
                            and x.version == row["event_version"]
                            and x.event_type in (row["event_type"], "*")), None)
                if sub is None:
                    raise RuntimeError("active subscriber implementation missing")
                event = {k: row[k] for k in (
                    "event_id", "aggregate_id", "aggregate_type", "event_type",
                    "event_version", "payload", "correlation_id", "causation_id", "created_at")}
                sub.handler(conn, event)
                conn.execute(
                    """INSERT INTO jarvis_processed_events
                       (event_id,subscriber_name,event_version,processed_at)
                       VALUES (%s,%s,%s,clock_timestamp()) ON CONFLICT DO NOTHING""",
                    (row["event_id"], row["subscriber_name"], row["event_version"]),
                )
                conn.execute(
                    """UPDATE jarvis_outbox_deliveries SET status='PUBLISHED',
                       lease_owner=NULL,lease_until=NULL,published_at=clock_timestamp(),
                       last_error=NULL WHERE event_id=%s AND subscriber_name=%s AND event_version=%s""",
                    attempted,
                )
                conn.execute(
                    """UPDATE jarvis_event_subscribers SET last_success_at=clock_timestamp(),
                       updated_at=clock_timestamp() WHERE subscriber_name=%s
                       AND event_version=%s AND (event_type=%s OR event_type='*')""",
                    (row["subscriber_name"], row["event_version"], row["event_type"]),
                )
                conn.execute(
                    """INSERT INTO jarvis_outbox_audit
                       (event_id,subscriber_name,event_version,action,actor,detail)
                       VALUES (%s,%s,%s,'delivered',%s,%s)""",
                    (*attempted[:3], self.owner, Jsonb({"elapsed_seconds": max(0, time.monotonic()-started)})),
                )
                conn.execute(
                    """UPDATE jarvis_outbox_events e SET
                         status=CASE
                           WHEN EXISTS (SELECT 1 FROM jarvis_outbox_deliveries d
                             WHERE d.event_id=e.event_id AND d.status='FAILED') THEN 'FAILED'
                           WHEN EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                             WHERE (s.event_type=e.event_type OR s.event_type='*')
                               AND s.event_version=e.event_version AND s.status='ACTIVE')
                            AND NOT EXISTS (SELECT 1 FROM jarvis_event_subscribers s
                             LEFT JOIN jarvis_outbox_deliveries d ON d.event_id=e.event_id
                               AND d.subscriber_name=s.subscriber_name
                               AND d.event_version=s.event_version
                             WHERE (s.event_type=e.event_type OR s.event_type='*')
                               AND s.event_version=e.event_version AND s.status='ACTIVE'
                               AND coalesce(d.status,'PENDING')<>'PUBLISHED')
                           THEN 'PUBLISHED' ELSE 'PENDING' END,
                         published_at=CASE WHEN EXISTS (
                           SELECT 1 FROM jarvis_event_subscribers s
                           WHERE (s.event_type=e.event_type OR s.event_type='*')
                             AND s.event_version=e.event_version AND s.status='ACTIVE')
                           AND NOT EXISTS (
                           SELECT 1 FROM jarvis_event_subscribers s
                           LEFT JOIN jarvis_outbox_deliveries d ON d.event_id=e.event_id
                             AND d.subscriber_name=s.subscriber_name AND d.event_version=s.event_version
                           WHERE (s.event_type=e.event_type OR s.event_type='*')
                             AND s.event_version=e.event_version AND s.status='ACTIVE'
                             AND coalesce(d.status,'PENDING')<>'PUBLISHED')
                           THEN coalesce(e.published_at,clock_timestamp()) ELSE NULL END
                       WHERE e.event_id=%s""", (row["event_id"],),
                )
            from .metrics import replay_latency
            replay_latency(max(0, time.monotonic()-started))
            return True
        except Exception as exc:
            if attempted is None:
                raise
            self._record_failure(attempted, exc)
            return True

    def _record_failure(self, attempted, exc):
        event_id, name, version = attempted
        permanent = isinstance(exc, PermanentSubscriberError)
        with self.repository.connection() as conn:
            row = conn.execute(
                """INSERT INTO jarvis_outbox_deliveries
                     (event_id,subscriber_name,event_version,status,attempt_count,last_error,
                      next_attempt_at)
                   VALUES (%s,%s,%s,%s,1,%s,
                      clock_timestamp()+(%s*interval '1 second'))
                   ON CONFLICT (event_id,subscriber_name,event_version) DO UPDATE SET
                     status=EXCLUDED.status,
                     attempt_count=jarvis_outbox_deliveries.attempt_count+1,
                     last_error=EXCLUDED.last_error,lease_owner=NULL,lease_until=NULL,
                     next_attempt_at=EXCLUDED.next_attempt_at
                   RETURNING attempt_count""",
                (event_id, name, version, "FAILED" if permanent else "PENDING",
                 str(exc)[:500], 0 if permanent else self.retry_base_seconds),
            ).fetchone()
            attempts = row["attempt_count"]
            if not permanent:
                delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** min(attempts-1, 16)))
                conn.execute(
                    """UPDATE jarvis_outbox_deliveries SET
                       next_attempt_at=clock_timestamp()+(%s*interval '1 second')
                       WHERE event_id=%s AND subscriber_name=%s AND event_version=%s""",
                    (delay, event_id, name, version),
                )
            conn.execute(
                """UPDATE jarvis_event_subscribers SET error_count=error_count+1,
                   updated_at=clock_timestamp()
                   WHERE subscriber_name=%s AND (event_type IN (
                     SELECT event_type FROM jarvis_outbox_events WHERE event_id=%s)
                     OR event_type='*') AND event_version=%s""", (name, event_id, version))
            conn.execute(
                """INSERT INTO jarvis_outbox_audit
                   (event_id,subscriber_name,event_version,action,actor,detail)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (event_id, name, version, "permanent_failure" if permanent else "retry_scheduled",
                 self.owner, Jsonb({"error": str(exc)[:500], "attempt_count": attempts})),
            )
            conn.execute(
                """UPDATE jarvis_outbox_events SET
                   retry_count=retry_count+1,
                   status=CASE WHEN %s THEN 'FAILED' ELSE 'PENDING' END
                   WHERE event_id=%s""", (permanent, event_id))

    def pending_count(self):
        with self.repository.connection() as conn:
            return conn.execute(
                """SELECT count(*) AS n FROM jarvis_outbox_events e
                   WHERE e.status='PENDING' OR EXISTS (
                     SELECT 1 FROM jarvis_outbox_deliveries d
                     WHERE d.event_id=e.event_id AND d.status IN ('PENDING','PROCESSING'))"""
            ).fetchone()["n"]


class ReplayWorker:
    def __init__(self, replay, stop, poll_seconds=1):
        self.replay, self.stop = replay, stop
        self.poll_seconds = max(0.1, float(poll_seconds))

    async def run(self):
        self.replay.register_subscribers()
        while not self.stop.is_set():
            try:
                processed = await asyncio.to_thread(self.replay.replay_one)
                from .metrics import replay_queue_size
                replay_queue_size(self.replay.pending_count())
                if processed:
                    continue
            except Exception:
                logging.getLogger(__name__).exception("outbox replay iteration failed")
            try:
                await asyncio.wait_for(self.stop.wait(), self.poll_seconds)
            except asyncio.TimeoutError:
                pass

