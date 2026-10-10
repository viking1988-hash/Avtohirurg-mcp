from typing import Protocol

class Transport(Protocol):
    def deliver(self, task: dict) -> str: ...

class FakeTransport:
    def __init__(self, repository, worker_id):
        self.repository, self.worker_id = repository, worker_id

    def deliver(self, task):
        return self.repository.fake_delivery(task['task_id'], self.worker_id, task['attempt_count'])

class DisabledTransport:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('real transports are disabled in Sandbox')

# Interface slots only: no SDK, credentials, network or real send implementation.
TelegramTransport = EmailTransport = WhatsAppTransport = CRMTransport = DisabledTransport
