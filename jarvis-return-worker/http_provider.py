"""Provider-neutral HTTP transport for an explicitly configured Sandbox API.

This module handles HTTP framing, credentials, bounded timeouts, and response
classification only. Routing/business policy remains in the Orchestrator and
Policy Engine. Ambiguous mutations are never retried here.
"""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .transport import Classification, DeliveryError


_RECEIPT_FIELDS = (
    "provider_message_id", "message_id", "external_message_id", "receipt_id",
    "receipt", "status", "provider_status", "delivery_time",
)


class _NoRedirect(HTTPRedirectHandler):
    """Prevent a configured Sandbox host from forwarding credentials elsewhere."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, msg, headers, fp)


class HTTPProvider:
    """Generic JSON HTTP provider, restricted to a Railway Sandbox/Staging API."""

    provider = "http"

    def __init__(self, base_url, token, timeout_seconds=8, opener=None):
        parsed = urlsplit(base_url or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        labels = host.split(".")
        sandbox = any("sandbox" in label or "staging" in label for label in labels)
        production = any(label == "prod" or "production" in label for label in labels)
        if (parsed.scheme != "https" or not host.endswith(".up.railway.app")
                or not sandbox or production or not token
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not 1 <= timeout_seconds <= 30):
            raise ValueError("HTTP provider requires an HTTPS Railway Sandbox/Staging endpoint and bounded timeout")
        # Preserve an optional service prefix while discarding query/fragment data.
        self.__base_url = urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.__token = token
        self.timeout_seconds = timeout_seconds
        self._opener = opener or build_opener(_NoRedirect()).open

    def send(self, task, idempotency_key):
        if not isinstance(task, dict) or not idempotency_key:
            raise DeliveryError(Classification.PERMANENT, "http_invalid_request")
        return self._request("POST", "/messages", {
            "destination": task.get("destination_key"),
            "payload": task.get("payload"),
        }, idempotency_key, require_receipt=True)

    def update(self, task, idempotency_key):
        message_id = self._message_id(task)
        if not idempotency_key:
            raise DeliveryError(Classification.PERMANENT, "http_idempotency_key_required")
        return self._request("PATCH", "/messages/" + quote(message_id, safe=""),
                             {"payload": task.get("payload")}, idempotency_key, require_receipt=True)

    def cancel(self, task, idempotency_key=None):
        message_id = self._message_id(task)
        key = idempotency_key or "jarvis-http:cancel:" + message_id
        return self._request("POST", "/messages/" + quote(message_id, safe="") + "/cancel",
                             {}, key, require_receipt=False)

    def reconcile(self, task):
        message_id = self._message_id(task)
        return self._request("GET", "/messages/" + quote(message_id, safe=""), None, None,
                             require_receipt=False)

    def checkStatus(self, task):
        return self.reconcile(task).get("status", "UNKNOWN")

    def check_status(self, task):
        return self.checkStatus(task)

    def get_receipt(self, provider_message_id):
        result = self._request("GET", "/messages/" + quote(self._validated_id(provider_message_id), safe=""),
                               None, None, require_receipt=False)
        if not any(result.get(key) for key in ("receipt_id", "receipt")):
            return None
        return result

    @staticmethod
    def _validated_id(value):
        value = str(value or "")
        if not value or len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise DeliveryError(Classification.PERMANENT, "http_provider_message_id_required")
        return value

    @classmethod
    def _message_id(cls, task):
        if isinstance(task, str):
            return cls._validated_id(task)
        if not isinstance(task, dict):
            raise DeliveryError(Classification.PERMANENT, "http_provider_message_id_required")
        return cls._validated_id(task.get("provider_message_id") or task.get("external_message_id")
                                 or task.get("message_id"))

    def _request(self, method, path, payload, idempotency_key, *, require_receipt):
        started = time.monotonic()
        from .metrics import observe_http_provider
        headers = {"Authorization": "Bearer " + self.__token, "Accept": "application/json"}
        body = None
        if payload is not None:
            try:
                body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                raise DeliveryError(Classification.PERMANENT, "http_invalid_request") from None
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        request = Request(self.__base_url + path, data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read(32768)
        except HTTPError as exc:
            elapsed = time.monotonic() - started
            if exc.code == 429:
                retry_after = self._retry_after(exc.headers)
                observe_http_provider("retryable", elapsed)
                raise DeliveryError(Classification.RETRYABLE, "http_provider_rate_limited", retry_after) from None
            if 400 <= exc.code < 500:
                observe_http_provider("permanent", elapsed)
                raise DeliveryError(Classification.PERMANENT, "http_provider_request_rejected") from None
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_outcome_unknown") from None
        except (TimeoutError, URLError, OSError):
            observe_http_provider("unknown", time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_network_outcome_unknown") from None
        except Exception:
            observe_http_provider("unknown", time.monotonic() - started)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_transport_outcome_unknown") from None

        elapsed = time.monotonic() - started
        if status == 429:
            observe_http_provider("retryable", elapsed)
            raise DeliveryError(Classification.RETRYABLE, "http_provider_rate_limited")
        if status >= 500:
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_outcome_unknown")
        if status < 200 or status >= 300:
            observe_http_provider("permanent", elapsed)
            raise DeliveryError(Classification.PERMANENT, "http_provider_request_rejected")
        try:
            result = json.loads(raw) if raw else {}
        except Exception:
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_response_unreadable") from None
        if not isinstance(result, dict):
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_response_unreadable")
        if require_receipt and not (result.get("receipt_id") or result.get("receipt")):
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_receipt_missing")
        if require_receipt and method != "GET" and not (result.get("provider_message_id")
                                                          or result.get("external_message_id")
                                                          or result.get("message_id")):
            observe_http_provider("unknown", elapsed)
            raise DeliveryError(Classification.UNKNOWN, "http_provider_message_id_missing")
        observe_http_provider("success", elapsed)
        # Persist only the identifiers and status needed for reconciliation.
        clean = {key: result[key] for key in _RECEIPT_FIELDS if key in result}
        if "provider_message_id" not in clean:
            candidate = clean.get("external_message_id") or clean.get("message_id")
            if candidate is not None:
                clean["provider_message_id"] = str(candidate)
        if "message_id" not in clean and clean.get("provider_message_id") is not None:
            # The queue's generic receipt path persists `message_id` as the external ID.
            clean["message_id"] = clean["provider_message_id"]
        return clean

    @staticmethod
    def _retry_after(headers):
        try:
            value = float(headers.get("Retry-After"))
            return min(max(value, 0), 3600)
        except (TypeError, ValueError, AttributeError):
            return None
