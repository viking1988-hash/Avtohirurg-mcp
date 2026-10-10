from typing import Protocol

class Transport(Protocol):
    def deliver(self, task: dict) -> str: ...

class FakeTransport:
    def __init__(self, repository, worker_id, delay_seconds=0):
        if not 0 <= delay_seconds <= 180:
            raise ValueError('bounded Fake delay required')
        self.repository, self.worker_id = repository, worker_id
        self.delay_seconds = delay_seconds

    def deliver(self, task):
        if self.delay_seconds:
            __import__('time').sleep(self.delay_seconds)
        return self.repository.fake_delivery(task['task_id'], self.worker_id, task['attempt_count'])

class DisabledTransport:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('real transports are disabled in Sandbox')

# Interface slots only: no SDK, credentials, network or real send implementation.
TelegramTransport = EmailTransport = WhatsAppTransport = CRMTransport = DisabledTransport
