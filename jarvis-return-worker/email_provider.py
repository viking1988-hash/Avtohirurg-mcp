"""Sandbox-only SMTP provider. SMTP acceptance is not proof of mailbox delivery."""
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import re
import smtplib
import time

from .transport import Classification, DeliveryError


_TEST_DOMAIN = re.compile(r"^[A-Za-z0-9.-]+\.(?:test|invalid)$", re.I)
_QUEUE_ID = re.compile(r"(?:queued as|id[=:])\s*([A-Za-z0-9._-]{1,128})", re.I)


def smtp_message_id_for(idempotency_key):
    digest = hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
    return f"<{digest}@jarvis-sandbox.invalid>"


def _observe(outcome, elapsed):
    from .metrics import observe_email
    observe_email(outcome, elapsed)


class EmailProvider:
    """SMTP submission to an isolated Mailpit/test relay; no booking rules."""
    provider = "email"

    def __init__(self, host, port, sender, timeout_seconds=8, smtp_factory=None):
        host = (host or "").strip().lower().rstrip(".")
        sandbox_host = (host in ("localhost", "127.0.0.1", "::1") or
                        host.endswith(".railway.internal") and
                        any(label in host.split(".") for label in ("mailpit", "smtp-sink", "sandbox")))
        if (not sandbox_host or not 1 <= int(port) <= 65535 or
                not 1 <= timeout_seconds <= 30 or not self._valid_sender(sender)):
            raise ValueError("Email provider requires an isolated Sandbox SMTP sink and bounded timeout")
        self.host, self.port, self.sender = host, int(port), sender.strip()
        self.timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory or smtplib.SMTP

    @staticmethod
    def _valid_sender(address):
        if not isinstance(address, str) or "\r" in address or "\n" in address or address.count("@") != 1:
            return False
        return bool(_TEST_DOMAIN.fullmatch(address.rsplit("@", 1)[1]))

    @staticmethod
    def _valid_recipient(address):
        if not isinstance(address, str) or "\r" in address or "\n" in address or address.count("@") != 1:
            return False
        return bool(_TEST_DOMAIN.fullmatch(address.rsplit("@", 1)[1]))

    def send(self, task, idempotency_key):
        started = time.monotonic()
        if not isinstance(task, dict):
            _observe("permanent", time.monotonic() - started)
            raise DeliveryError(Classification.PERMANENT, "email_invalid_request")
        recipient, subject, body = task.get("to"), task.get("subject"), task.get("body")
        if (not self._valid_recipient(recipient) or not isinstance(subject, str) or
                not subject.strip() or len(subject) > 998 or "\r" in subject or "\n" in subject or
                not isinstance(body, str) or not body.strip() or len(body) > 100000):
            _observe("permanent", time.monotonic() - started)
            raise DeliveryError(Classification.PERMANENT, "email_invalid_request")
        key = str(idempotency_key or "")
        if not key or len(key) > 200:
            _observe("permanent", time.monotonic() - started)
            raise DeliveryError(Classification.PERMANENT, "email_idempotency_key_required")

        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        smtp_message_id = smtp_message_id_for(key)
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = smtp_message_id
        message.set_content(body)

        client = None
        phase = "connect"
        try:
            client = self._smtp_factory(self.host, self.port, timeout=self.timeout_seconds)
            client.ehlo()
            phase = "mail"
            code, response = client.mail(self.sender)
            self._raise_smtp_rejection(code, response)
            code, response = client.rcpt(recipient)
            self._raise_smtp_rejection(code, response)
            phase = "data"
            code, response = client.data(message.as_bytes())
            if code != 250:
                self._raise_smtp_rejection(code, response)
        except DeliveryError as exc:
            _observe("retryable" if exc.classification == Classification.RETRYABLE else "permanent",
                     time.monotonic() - started)
            raise
        except (smtplib.SMTPServerDisconnected, TimeoutError, OSError, smtplib.SMTPException):
            elapsed = time.monotonic() - started
            # A disconnect after DATA may have been accepted; never retry it blindly.
            classification = Classification.UNKNOWN if phase == "data" else Classification.RETRYABLE
            _observe("unknown" if classification == Classification.UNKNOWN else "retryable", elapsed)
            code = "email_smtp_outcome_unknown" if classification == Classification.UNKNOWN else "email_smtp_unavailable"
            raise DeliveryError(classification, code) from None
        except Exception:
            elapsed = time.monotonic() - started
            classification = Classification.UNKNOWN if phase == "data" else Classification.RETRYABLE
            _observe("unknown" if classification == Classification.UNKNOWN else "retryable", elapsed)
            code = "email_smtp_outcome_unknown" if classification == Classification.UNKNOWN else "email_smtp_unavailable"
            raise DeliveryError(classification, code) from None
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass

        elapsed = time.monotonic() - started
        text = response.decode("ascii", "ignore") if isinstance(response, bytes) else str(response or "")
        match = _QUEUE_ID.search(text[:512])
        provider_message_id = match.group(1) if match else None
        receipt = "smtp:accepted:" + digest
        _observe("success", elapsed)
        return {
            "provider_message_id": provider_message_id,
            "smtp_message_id": smtp_message_id,
            "receipt": receipt,
            "provider_status": "accepted",
            "delivery_time": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _raise_smtp_rejection(code, response):
        if 200 <= int(code) < 300:
            return
        classification = Classification.RETRYABLE if 400 <= int(code) < 500 else Classification.PERMANENT
        raise DeliveryError(classification, "email_smtp_rejected")

    def cancel(self, provider_message_id, idempotency_key=None):
        # Standard SMTP has no recall API. Queue cancellation happens before send in Jarvis.
        raise DeliveryError(Classification.PERMANENT, "email_smtp_cancel_unsupported")

    def update(self, task, idempotency_key):
        # SMTP submission is immutable; a changed email is a new authorized event.
        raise DeliveryError(Classification.PERMANENT, "email_smtp_update_unsupported")

    def checkStatus(self, provider_message_id):
        return "UNKNOWN"

    def check_status(self, provider_message_id):
        return self.checkStatus(provider_message_id)

    def reconcile(self, task):
        # SMTP does not expose a standard lookup/DSN API; callers must use operator evidence.
        return {"status": "UNKNOWN", "reason": "smtp_delivery_status_unavailable"}

    def getReceipt(self, provider_message_id):
        return None

    def get_receipt(self, provider_message_id):
        return self.getReceipt(provider_message_id)
