"""Subscriber registry for the PostgreSQL outbox replay engine.

Events are emitted only from the transaction that changes queue state. The old
process-local publish/deduplicate API is intentionally not offered.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .outbox import SubscriberRegistry


@dataclass(frozen=True)
class OrchestratorEvent:
    name: str
    task_id: str
    correlation_id: str | None
    provider: str | None
    previous_state: str | None
    new_state: str | None
    reason: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attributes: dict = field(default_factory=dict)


class EventBus(SubscriberRegistry):
    """Compatibility name for the durable subscriber registry.

    Calling publish directly would bypass the business transaction and is rejected.
    """

    def publish(self, event):
        raise RuntimeError("events must be appended through a business transaction")
