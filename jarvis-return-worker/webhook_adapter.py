"""Transactional outbox adapter for WebhookRequested events."""
from .outbox import PermanentSubscriberError

_ALLOWED={"GET","POST","PUT","PATCH","DELETE"}
class WebhookAdapter:
    subscriber_name="jarvis.webhook-adapter"
    @staticmethod
    def adapt(event):
        if event.get("event_type")!="WebhookRequested": return None
        payload=event.get("payload") or {}
        return {"event_id":event["event_id"],"idempotency_key":"jarvis-webhook:"+str(event["event_id"]),
                "method":str(payload.get("method","POST")).upper(),"path":payload.get("path"),
                "body":payload.get("body"),"correlation_id":event.get("correlation_id")}
    def handle(self,conn,event):
        command=self.adapt(event)
        if command is None:return
        path=command["path"]
        if (command["method"] not in _ALLOWED or not isinstance(path,str) or not path.startswith("/")
            or path.startswith("//") or "\\" in path or "?" in path or "#" in path
            or any(part==".." for part in path.split("/"))):
            raise PermanentSubscriberError("webhook_request_invalid")
        from psycopg.types.json import Jsonb
        result=conn.execute("""INSERT INTO jarvis_webhook_operations
          (event_id,idempotency_key,method,path,request_body,correlation_id,state)
          VALUES (%s,%s,%s,%s,%s,%s,'PENDING') ON CONFLICT (event_id) DO NOTHING""",
          (command["event_id"],command["idempotency_key"],command["method"],path,
           Jsonb(command["body"]) if command["body"] is not None else None,command["correlation_id"]))
        if result.rowcount==0:
            from .metrics import prevent_webhook_duplicate
            prevent_webhook_duplicate()
        else:
            from .metrics import webhook_replayed
            webhook_replayed()
    def register(self,registry):
        registry.register(self.subscriber_name,"WebhookRequested",self.handle,version=1)
