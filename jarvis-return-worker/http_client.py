"""Bounded privacy-safe HTTP client."""
import json,logging,random,time,uuid
from urllib.error import HTTPError,URLError
from urllib.request import HTTPRedirectHandler,Request,build_opener
from .transport import Classification,DeliveryError
class _NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None
class HTTPClient:
 def __init__(self,timeout_seconds=8,max_retries=2,opener=None,sleep=time.sleep,random_fn=random.random,backoff_seconds=.2):
  if not .01<=float(timeout_seconds)<=30 or not 0<=int(max_retries)<=5:raise ValueError("invalid HTTP retry bounds")
  self.timeout_seconds=float(timeout_seconds);self.max_retries=int(max_retries);self._sleep=sleep;self._random=random_fn;self.backoff_seconds=float(backoff_seconds);self._opener=opener or build_opener(_NoRedirect()).open
 def request(self,method,url,*,body=None,headers=None,correlation_id=None,idempotency_key=None,request_id=None):
  method=str(method).upper()
  if method not in {"GET","POST","PUT","PATCH","DELETE"}:raise DeliveryError(Classification.PERMANENT,"webhook_method_not_allowed")
  rid=str(request_id or uuid.uuid4());corr=str(correlation_id or "");encoded=None if body is None else json.dumps(body,separators=(",",":"),ensure_ascii=False).encode()
  h={"Accept":"application/json","X-Request-ID":rid}
  if corr:h["X-Correlation-ID"]=corr
  if idempotency_key:h["Idempotency-Key"]=str(idempotency_key)
  if encoded is not None:h["Content-Type"]="application/json"
  custom=headers or {}
  reserved={"host","content-length","connection","transfer-encoding","idempotency-key","x-request-id","x-correlation-id"}
  if any(str(name).strip().lower() in reserved for name in custom):
   raise ValueError("custom headers cannot override HTTP request identity or framing")
  h.update(custom)
  started=time.monotonic();retries=0;safe=method=="GET"
  while True:
   from .metrics import webhook_request_attempt
   webhook_request_attempt()
   req=Request(url,data=encoded,headers=h,method=method);status,rh,raw=None,{},b""
   try:
    response=self._opener(req,timeout=self.timeout_seconds)
    try:status=int(response.status);rh=dict(response.headers.items()) if getattr(response,"headers",None) else {};raw=response.read(32768)
    finally:
     close=getattr(response,"close",None)
     if close:close()
   except HTTPError as exc:status=int(exc.code);rh=dict(exc.headers.items()) if exc.headers else {};raw=exc.read(32768)
   except (TimeoutError,URLError,OSError):
    elapsed=time.monotonic()-started
    if safe and retries<self.max_retries:
     retries+=1;self._log(rid,corr,method,None,retries,0,"retry");self._sleep(self._delay(retries,None));continue
    cls=Classification.RETRYABLE if safe else Classification.UNKNOWN;self._observe(cls,elapsed);self._log(rid,corr,method,None,retries,elapsed,cls.value.lower())
    raise DeliveryError(cls,"webhook_network_outcome_unknown",details={"request_id":rid,"retry_count":retries,"duration_ms":round(elapsed*1000,2)}) from None
   elapsed=time.monotonic()-started
   if 200<=status<300:
    result=self._receipt(raw);result.update({"request_id":rid,"response_id":rh.get("X-Response-ID") or rh.get("X-Request-ID"),"http_status":status,"provider_status":result.get("provider_status") or ("accepted" if status==202 else "success"),"duration_ms":round(elapsed*1000,2),"retry_count":retries})
    self._observe(Classification.SUCCESS,elapsed);self._log(rid,corr,method,status,retries,elapsed,"success");return result
   if status==429:
    if safe and retries<self.max_retries:
     retries+=1;self._log(rid,corr,method,status,retries,0,"retry");self._sleep(self._delay(retries,rh.get("Retry-After")));continue
    cls,code=Classification.RETRYABLE,"webhook_rate_limited"
   elif status>=500 or status in (408,409):
    cls=Classification.RETRYABLE if safe else Classification.UNKNOWN;code="webhook_server_unavailable" if safe else "webhook_server_outcome_unknown"
    if safe and retries<self.max_retries:
     retries+=1;self._log(rid,corr,method,status,retries,0,"retry");self._sleep(self._delay(retries,None));continue
   else:cls,code=Classification.PERMANENT,"webhook_request_rejected"
   self._observe(cls,elapsed);self._log(rid,corr,method,status,retries,elapsed,cls.value.lower())
   raise DeliveryError(cls,code,rh.get("Retry-After") if status==429 else None,details={"request_id":rid,"http_status":status,"retry_count":retries,"duration_ms":round(elapsed*1000,2)})
 def _delay(self,n,value):
  try:d=float(value)
  except (TypeError,ValueError):d=self.backoff_seconds*2**(n-1)*(.75+self._random()*.5)
  return max(0,min(10,d))
 @staticmethod
 def _receipt(raw):
  if not raw:return {"receipt":{"accepted":True}}
  try:v=json.loads(raw)
  except (ValueError,UnicodeDecodeError):
   import hashlib
   v={"body_sha256":hashlib.sha256(raw).hexdigest()}
  if not isinstance(v,dict):v={"response_type":type(v).__name__}
  keys=("receipt","receipt_id","id","message_id","status","accepted");r={k:v[k] for k in keys if k in v and isinstance(v[k],(str,int,float,bool,dict,list))}
  return {"receipt":r or {"accepted":True},"provider_status":str(v.get("status")) if v.get("status") is not None else None}
 @staticmethod
 def _observe(cls,elapsed):
  from .metrics import observe_webhook
  observe_webhook(cls.value.lower(),elapsed)
 @staticmethod
 def _log(rid,corr,method,status,retries,elapsed,outcome):
  logging.getLogger(__name__).info(json.dumps({"provider":"webhook","request_id":rid,"correlation_id":corr or None,"method":method,"http_status":status,"retry_count":retries,"duration_ms":round(elapsed*1000,2),"outcome":outcome},sort_keys=True,separators=(",",":")))
