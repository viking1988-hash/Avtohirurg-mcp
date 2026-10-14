"""CRM transport contract and transactional outbox event adapter.

The adapter only persists idempotent CRM operation intents in the supplied
PostgreSQL transaction. It never calls a provider. Network calls belong to the
Orchestrator/provider worker path.
"""
import json
import hashlib
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from .transport import Classification, DeliveryError


EVENT_OPERATIONS = {
    "BookingCreated": "send",
    "BookingUpdated": "update",
    "BookingCancelled": "cancel",
    "BookingConfirmed": "update",
    "BookingCompleted": "update",
}


def crm_operation_fingerprint(event_id, booking_id, operation, payload):
    stable=json.dumps({"event_id":str(event_id),"booking_id":str(booking_id),
        "operation":operation,"payload":payload},sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


class CRMProvider:
    """Small HTTP provider for a test-only CRM Booking API.

    This class has transport concerns only: request encoding, authentication,
    timeouts and response classification. Booking rules live in CRMAdapter.
    """
    provider = "crm"

    def __init__(self, base_url, token, timeout_seconds=8, opener=None):
        parsed = urlsplit(base_url or "")
        host = (parsed.hostname or "").lower()
        labels=host.split(".")
        sandbox_host=any("sandbox" in label for label in labels)
        production_host=any("production" in label or label=="prod" or label.startswith("prod-") for label in labels)
        if (parsed.scheme != "https" or not host.endswith(".up.railway.app") or not token
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not sandbox_host or production_host
                or not 1 <= timeout_seconds <= 30):
            raise ValueError("CRM transport requires HTTPS Railway Sandbox endpoint and bounded timeout")
        self.__base_url = base_url.rstrip("/")
        self.__token = token
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def send(self, operation, idempotency_key):
        return self._request("POST", "/bookings", operation, idempotency_key)

    def update(self, operation, idempotency_key):
        booking_id = self._booking_id(operation)
        return self._request("PATCH", "/bookings/" + quote(booking_id, safe=""), operation, idempotency_key)

    def cancel(self, operation, idempotency_key=None):
        booking_id = self._booking_id(operation)
        idempotency_key = idempotency_key or "jarvis-crm:cancel:" + booking_id
        return self._request("POST", "/bookings/" + quote(booking_id, safe="") + "/cancel", operation, idempotency_key)

    def reconcile(self, operation):
        booking_id = self._booking_id(operation)
        return self._request("GET", "/bookings/" + quote(booking_id, safe=""), None, None)

    def checkStatus(self, operation):  # Provider contract uses this public spelling.
        result = self.reconcile(operation)
        return result.get("status", "UNKNOWN")

    def check_status(self, operation):
        return self.checkStatus(operation)

    @staticmethod
    def _booking_id(operation):
        booking_id = str(operation if isinstance(operation, str) else (operation or {}).get("booking_id", ""))
        if not booking_id:
            raise DeliveryError(Classification.PERMANENT, "crm_booking_id_required")
        return booking_id

    def _request(self, method, path, payload, idempotency_key):
        started = time.monotonic()
        from .metrics import observe_crm
        headers = {"Authorization": "Bearer " + self.__token, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = Request(self.__base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.timeout_seconds) as response:
                status_code = response.status
                raw = response.read(32768)
        except HTTPError as exc:
            elapsed = time.monotonic() - started
            if exc.code == 429:
                observe_crm("retryable", elapsed)
                raise DeliveryError(Classification.RETRYABLE, "crm_rate_limited") from None
            if 400 <= exc.code < 500:
                observe_crm("permanent", elapsed)
                raise DeliveryError(Classification.PERMANENT, "crm_request_rejected") from None
            observe_crm("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "crm_server_outcome_unknown") from None
        except (TimeoutError, URLError, OSError):
            observe_crm("unknown", time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, "crm_network_outcome_unknown") from None
        except Exception:
            observe_crm("unknown", time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, "crm_transport_outcome_unknown") from None
        elapsed = time.monotonic() - started
        try:
            result = json.loads(raw) if raw else {}
        except Exception:
            observe_crm("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "crm_response_unreadable") from None
        if status_code == 429:
            observe_crm("retryable", elapsed)
            raise DeliveryError(Classification.RETRYABLE, "crm_rate_limited")
        if status_code >= 500:
            observe_crm("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "crm_server_outcome_unknown")
        if status_code < 200 or status_code >= 300 or not isinstance(result, dict):
            observe_crm("permanent", elapsed)
            raise DeliveryError(Classification.PERMANENT, "crm_request_rejected")
        if method!='GET' and not (result.get('receipt_id') or result.get('provider_receipt')):
            observe_crm('unknown',elapsed)
            raise DeliveryError(Classification.UNKNOWN,'crm_provider_receipt_missing')
        if method!='GET' and (not result.get('external_booking_id') or not result.get('status')):
            observe_crm('unknown',elapsed)
            raise DeliveryError(Classification.UNKNOWN,'crm_provider_booking_evidence_missing')
        observe_crm("success", elapsed)
        # Keep only CRM identifiers/status/receipt, never arbitrary PII response data.
        return {key: result[key] for key in ("external_booking_id", "booking_id", "status", "receipt_id", "message_id") if key in result}


class CRMAdapter:
    """Translate Jarvis booking events into durable, deduplicated CRM intents."""
    subscriber_name = "jarvis.crm-adapter"

    @staticmethod
    def adapt(event):
        event_type = event.get("event_type")
        operation = EVENT_OPERATIONS.get(event_type)
        payload = event.get("payload") or {}
        booking_id = payload.get("booking_id") or event.get("aggregate_id")
        if not operation or not booking_id:
            return None
        status = {
            "BookingCreated": "created", "BookingUpdated": "updated",
            "BookingCancelled": "cancelled", "BookingConfirmed": "confirmed",
            "BookingCompleted": "completed",
        }[event_type]
        command = {
            "event_id": event["event_id"],
            "idempotency_key": "jarvis-crm:" + str(event["event_id"]),
            "booking_id": str(booking_id),
            "operation": operation,
            "crm_status": status,
            "payload": {**payload, "booking_id": str(booking_id), "status": status},
            "correlation_id": event.get("correlation_id"),
        }
        command["action_fingerprint"] = crm_operation_fingerprint(
            command["event_id"], command["booking_id"],
            command["operation"], command["payload"])
        return command

    def handle(self, conn, event):
        command = self.adapt(event)
        if command is None:
            return
        from psycopg.types.json import Jsonb
        inserted = conn.execute(
            """INSERT INTO jarvis_crm_operations
               (event_id,idempotency_key,booking_id,operation,crm_status,payload,operation_fingerprint,
                correlation_id,state,created_at,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',clock_timestamp(),clock_timestamp())
               ON CONFLICT (event_id) DO NOTHING""",
            (command["event_id"], command["idempotency_key"], command["booking_id"],
             command["operation"], command["crm_status"], Jsonb(command["payload"]),
             command["action_fingerprint"], command["correlation_id"]),
        )
        if inserted.rowcount == 0:
            from .metrics import prevent_crm_duplicate
            prevent_crm_duplicate()

    def register(self, registry):
        for event_type in EVENT_OPERATIONS:
            registry.register(self.subscriber_name, event_type, self.handle, version=1)
