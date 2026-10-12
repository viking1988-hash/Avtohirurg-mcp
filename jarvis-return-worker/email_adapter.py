"""Outbox adapter for EmailRequested and the email lifecycle event family."""
from .outbox import PermanentSubscriberError


EMAIL_EVENT_TYPES = (
    "EmailRequested", "EmailQueued", "EmailSent", "EmailUnknown",
    "EmailFailed", "EmailCancelled",
)


class EmailAdapter:
    subscriber_name = "jarvis.email-adapter"

    @staticmethod
    def adapt(event):
        if event.get("event_type") != "EmailRequested":
            return None
        from .email_provider import smtp_message_id_for
        payload = event.get("payload") or {}
        return {
            "event_id": event["event_id"],
            "idempotency_key": "jarvis-email:" + str(event["event_id"]),
            "to": payload.get("to"),
            "subject": payload.get("subject"),
            "body": payload.get("body"),
            "correlation_id": event.get("correlation_id"),
            "smtp_message_id": smtp_message_id_for("jarvis-email:" + str(event["event_id"])),
        }

    def handle(self, conn, event):
        command = self.adapt(event)
        if command is None:
            return
        recipient = command["to"]
        if not isinstance(recipient, str) or "@" not in recipient or not recipient.rsplit("@", 1)[1].lower().endswith((".test", ".invalid")):
            raise PermanentSubscriberError("email_recipient_must_be_test_domain")
        if (not isinstance(command["subject"], str) or not command["subject"].strip() or
                len(command["subject"]) > 998 or not isinstance(command["body"], str) or
                not command["body"].strip() or len(command["body"]) > 100000):
            raise PermanentSubscriberError("email_payload_invalid")
        from psycopg.types.json import Jsonb
        inserted = conn.execute(
            """INSERT INTO jarvis_email_operations
               (event_id,idempotency_key,recipient,payload,correlation_id,smtp_message_id,state)
               VALUES (%s,%s,%s,%s,%s,%s,'PENDING')
               ON CONFLICT (event_id) DO NOTHING""",
            (command["event_id"], command["idempotency_key"], recipient,
             Jsonb({"subject": command["subject"], "body": command["body"]}),
             command["correlation_id"],command["smtp_message_id"]),
        )
        if inserted.rowcount == 0:
            from .metrics import prevent_email_duplicate
            prevent_email_duplicate()

    def register(self, registry):
        for event_type in EMAIL_EVENT_TYPES:
            registry.register(self.subscriber_name, event_type, self.handle, version=1)
