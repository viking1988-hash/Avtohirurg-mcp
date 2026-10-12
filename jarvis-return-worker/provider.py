"""Provider contract and compatibility adapter; providers never receive a Queue repository."""
from typing import Protocol


class Provider(Protocol):
    provider: str
    def send(self, task: dict, idempotency_key: str) -> dict: ...
    def get_receipt(self, provider_message_id: str) -> dict | None: ...
    def check_status(self, provider_message_id: str) -> str: ...
    def reconcile(self, task: dict) -> dict: ...
    def cancel(self, provider_message_id: str) -> bool: ...


class ProviderAdapter:
    """Adapts an existing protocol client to the provider-neutral contract."""
    provider = "unknown"

    def __init__(self, client):
        self.client = client
        self.provider = getattr(client, "provider", self.provider)

    def send(self, task, idempotency_key):
        sender = getattr(self.client, "send", None) or getattr(self.client, "deliver")
        result = sender(task) if getattr(self.client, "send", None) is None else sender(task, idempotency_key)
        if isinstance(result, dict):
            return result
        return {"receipt_id": str(result)} if result is not None else {}

    def get_receipt(self, provider_message_id):
        method = getattr(self.client, "get_receipt", None)
        return method(provider_message_id) if method else None

    def check_status(self, provider_message_id):
        method = getattr(self.client, "check_status", None)
        return method(provider_message_id) if method else "UNKNOWN"

    def reconcile(self, task):
        method = getattr(self.client, "reconcile", None)
        return method(task) if method else {"status": "UNKNOWN"}

    def cancel(self, provider_message_id):
        method = getattr(self.client, "cancel", None)
        return bool(method(provider_message_id)) if method else False
