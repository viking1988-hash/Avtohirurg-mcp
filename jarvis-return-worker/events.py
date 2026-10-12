"""Extensible process-local event notifications over the durable audit journal."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock


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


class EventBus:
    """Subscribers are notifications; the PostgreSQL action journal remains authoritative."""
    def __init__(self, observers=()):
        self._observers = list(observers)
        self._lock = RLock()
        self._seen = set()
        self._events_total = 0

    def subscribe(self, observer):
        with self._lock:
            self._observers.append(observer)

    def publish(self, event):
        key=(event.task_id,event.name,event.new_state,event.reason)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            self._events_total += 1
            observers=tuple(self._observers)
        errors=[]
        for observer in observers:
            try:
                observer(event)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("event_observer_failed") from errors[0]
        return True

    @property
    def events_total(self):
        return self._events_total
