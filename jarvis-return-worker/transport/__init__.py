"""Delivery providers. Telegram never retries a request whose outcome is ambiguous."""
import json
import time
from enum import Enum
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Protocol


class Classification(str, Enum):
    SUCCESS = 'SUCCESS'
    RETRYABLE = 'RETRYABLE'
    PERMANENT = 'PERMANENT'
    UNKNOWN = 'UNKNOWN'


class DeliveryError(RuntimeError):
    def __init__(self, classification, code, retry_after=None):
        super().__init__(code)
        self.classification = Classification(classification)
        self.code = code
        self.retry_after = retry_after


def _telegram_result(kind, elapsed):
    from ..metrics import observe_telegram
    observe_telegram(kind, elapsed)


class Transport(Protocol):
    def deliver(self, task: dict) -> dict: ...

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

class TelegramTransport:
    """Telegram Bot API sendMessage, pinned to one explicitly configured test chat."""
    provider = 'telegram'

    def __init__(self, bot_token, test_chat_id, timeout_seconds=10):
        if not bot_token or not test_chat_id or not 1 <= timeout_seconds <= 30:
            raise ValueError('Telegram requires a sandbox bot, allowlisted test chat and bounded timeout')
        self.__bot_token = bot_token
        self.test_chat_id = str(test_chat_id)
        self.timeout_seconds = timeout_seconds

    def deliver(self, task):
        started = time.monotonic()
        chat_id = str(task.get('destination_key', '')).removeprefix('sandbox:telegram:')
        if not chat_id or chat_id == task.get('destination_key') or chat_id != self.test_chat_id:
            _telegram_result('permanent', time.monotonic() - started)
            raise DeliveryError(Classification.PERMANENT, 'telegram_test_chat_not_allowlisted')
        text = task.get('payload')
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            _telegram_result('permanent', time.monotonic() - started)
            raise DeliveryError(Classification.PERMANENT, 'telegram_invalid_text')
        body = json.dumps({'chat_id': chat_id, 'text': text}, separators=(',', ':')).encode()
        # Bot token is necessarily part of the URL per Bot API. Never include the URL,
        # HTTPError text, or response body in an exception/log.
        request = Request('https://api.telegram.org/bot' + self.__bot_token + '/sendMessage',
                          data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = response.status
                raw = response.read(65536)
        except HTTPError as exc:
            elapsed = time.monotonic() - started
            if exc.code == 429:
                retry_after = None
                try:
                    data = json.loads(exc.read(65536))
                    retry_after = data.get('parameters', {}).get('retry_after')
                except Exception:
                    pass
                _telegram_result('retryable', elapsed)
                raise DeliveryError(Classification.RETRYABLE, 'telegram_rate_limited', retry_after) from None
            if 400 <= exc.code < 500:
                _telegram_result('permanent', elapsed)
                raise DeliveryError(Classification.PERMANENT, 'telegram_request_rejected') from None
            _telegram_result('unknown', elapsed)
            raise DeliveryError(Classification.UNKNOWN, 'telegram_server_outcome_unknown') from None
        except (TimeoutError, URLError, OSError):
            _telegram_result('unknown', time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, 'telegram_network_outcome_unknown') from None
        except Exception:
            _telegram_result('unknown', time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, 'telegram_transport_outcome_unknown') from None
        elapsed = time.monotonic() - started
        try:
            data = json.loads(raw)
            result = data.get('result') or {}
            message_id = result.get('message_id')
            returned_chat = str((result.get('chat') or {}).get('id'))
            if status < 200 or status >= 300 or data.get('ok') is not True:
                code = 'telegram_request_rejected'
                classification = Classification.PERMANENT
                if status == 429:
                    classification = Classification.RETRYABLE
                    code = 'telegram_rate_limited'
                elif status >= 500:
                    classification = Classification.UNKNOWN
                    code = 'telegram_server_outcome_unknown'
                _telegram_result(classification.value.lower(), elapsed)
                raise DeliveryError(classification, code, data.get('parameters', {}).get('retry_after'))
            if message_id is None or returned_chat != chat_id:
                _telegram_result('unknown', elapsed)
                raise DeliveryError(Classification.UNKNOWN, 'telegram_receipt_incomplete')
            message_id = str(message_id)
            # Store only fields needed for later reconciliation; discard message text and sender/profile data.
            _telegram_result('success', elapsed)
            return {'receipt_id': f'telegram:{returned_chat}:{message_id}',
                    'message_id': message_id, 'chat_id': returned_chat,
                    'response': {'ok': True, 'message_id': message_id, 'chat_id': returned_chat}}
        except DeliveryError:
            raise
        except Exception:
            _telegram_result('unknown', elapsed)
            raise DeliveryError(Classification.UNKNOWN, 'telegram_response_unreadable') from None


EmailTransport = WhatsAppTransport = CRMTransport = DisabledTransport
