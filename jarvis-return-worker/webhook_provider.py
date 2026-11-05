"""Configured HTTP provider; event data never contains destination URLs."""
import ipaddress,uuid
from urllib.parse import quote,urlsplit
from .http_client import HTTPClient
from .transport import Classification,DeliveryError
class WebhookProvider:
 provider="webhook"
 def __init__(self,base_url,timeout_seconds=8,max_retries=2,headers=None,http_client=None,allow_insecure_localhost=False):
  p=urlsplit((base_url or "").strip());host=(p.hostname or "").lower().rstrip(".");local=allow_insecure_localhost and host in ("localhost","127.0.0.1","::1")
  try:ip=ipaddress.ip_address(host)
  except ValueError:ip=None
  if p.scheme!=("http" if local else "https") or not host or p.username or p.password or p.query or p.fragment or (ip and not local) or (host=="localhost" and not local):raise ValueError("Webhook requires HTTPS base URL without credentials or query")
  if not .01<=float(timeout_seconds)<=30:raise ValueError("invalid webhook timeout")
  self.__base_url=base_url.rstrip("/");self.__headers=dict(headers or {});self.http=http_client or HTTPClient(timeout_seconds,max_retries)
 @staticmethod
 def _path(task):
  p=task.get("path")
  if not isinstance(p,str) or not p.startswith("/") or p.startswith("//") or "\\" in p or "?" in p or "#" in p or any(x==".." for x in p.split("/")):raise DeliveryError(Classification.PERMANENT,"webhook_path_invalid")
  return p
 def _request(self,method,task,key=None):
  if not isinstance(task,dict):raise DeliveryError(Classification.PERMANENT,"webhook_request_invalid")
  m=str(task.get("method",method)).upper()
  if m not in ("GET","POST","PUT","PATCH","DELETE"):raise DeliveryError(Classification.PERMANENT,"webhook_method_not_allowed")
  return self.http.request(m,self.__base_url+self._path(task),body=task.get("body"),headers=self.__headers,correlation_id=task.get("correlation_id"),idempotency_key=key,request_id=str(task.get("request_id") or uuid.uuid4()))
 def send(self,task,idempotency_key):return self._request(task.get("method","POST"),task,idempotency_key)
 def update(self,task,idempotency_key):return self._request(task.get("method","PATCH"),task,idempotency_key)
 def cancel(self,task,idempotency_key=None):return self._request("DELETE",dict(task,method="DELETE"),idempotency_key)
 def reconcile(self,task):
  t=dict(task,method="GET",path=self._path(task).rstrip("/")+"/status")
  try:return self._request("GET",t,task.get("idempotency_key"))
  except DeliveryError:return {"status":"UNKNOWN","reason":"webhook_status_unavailable"}
 def checkStatus(self,task):
  r=self.reconcile(task);return r.get("provider_status",r.get("status","UNKNOWN"))
 def check_status(self,task):return self.checkStatus(task)
 def get_receipt(self,pid):return self.getReceipt(pid)
 def getReceipt(self,pid):
  if not pid or len(str(pid))>256:return None
  try:return self.http.request("GET",self.__base_url+"/receipts/"+quote(str(pid),safe=""),headers=self.__headers)
  except DeliveryError:return None
