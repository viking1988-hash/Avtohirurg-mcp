"""Bounded privacy-safe HTTP client with DNS-pinned public destinations."""
import http.client, ipaddress, json, logging, random, socket, ssl, time, uuid
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request
from .transport import Classification, DeliveryError

_LOCAL = {"localhost", "127.0.0.1", "::1"}

def _dial(host, port, timeout, resolver, allow_localhost, source_address=None):
    resolver = resolver or socket.getaddrinfo
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise
    if not answers:
        raise OSError("webhook host did not resolve")
    validated = []
    for family, kind, proto, _canon, sockaddr in answers:
        try:
            address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except (ValueError, IndexError, AttributeError):
            raise OSError("webhook host returned an invalid address") from None
        if not address.is_global and not (allow_localhost and host.lower().rstrip(".") in _LOCAL and address.is_loopback):
            raise OSError("webhook destination address is not public")
        validated.append((family, kind, proto, sockaddr))
    last_error = None
    for family, kind, proto, sockaddr in validated:
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise last_error or OSError("webhook host has no usable address")

class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 source_address=None, blocksize=8192, *, resolver=None, allow_localhost=False):
        self._resolver, self._allow_localhost = resolver, allow_localhost
        super().__init__(host, port, timeout, source_address, blocksize)

    def connect(self):
        self.sock = _dial(self.host, self.port, self.timeout, self._resolver,
                          self._allow_localhost, self.source_address)

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 source_address=None, context=None, blocksize=8192,
                 *, resolver=None, allow_localhost=False):
        self._resolver, self._allow_localhost = resolver, allow_localhost
        super().__init__(host, port=port, timeout=timeout, source_address=source_address, context=context, blocksize=blocksize)

    def connect(self):
        sock = _dial(self.host, self.port, self.timeout, self._resolver,
                     self._allow_localhost, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

class _Response:
    def __init__(self, response, connection):
        self._response, self._connection = response, connection
        self.status, self.headers = response.status, response.headers
    def read(self, size=-1):
        return self._response.read(size)
    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()

class HTTPClient:
    def __init__(self, timeout_seconds=8, max_retries=2, opener=None, sleep=time.sleep,
                 random_fn=random.random, backoff_seconds=.2, *, allow_localhost=False,
                 resolver=None):
        if not .01 <= float(timeout_seconds) <= 30 or not 0 <= int(max_retries) <= 5:
            raise ValueError("invalid HTTP retry bounds")
        self.timeout_seconds, self.max_retries = float(timeout_seconds), int(max_retries)
        self._sleep, self._random = sleep, random_fn
        self.backoff_seconds = float(backoff_seconds)
        self._allow_localhost, self._resolver = bool(allow_localhost), resolver
        self._opener = opener or self._open_pinned

    def _open_pinned(self, req, timeout):
        parts = urlsplit(req.full_url)
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            raise OSError("webhook URL is invalid")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise OSError("webhook URL port is invalid") from None
        options = {"resolver": self._resolver, "allow_localhost": self._allow_localhost}
        if parts.scheme == "https":
            conn = _PinnedHTTPSConnection(host, port, timeout=timeout, **options)
        else:
            conn = _PinnedHTTPConnection(host, port, timeout=timeout, **options)
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn.request(req.get_method(), target, body=req.data, headers=dict(req.header_items()))
            return _Response(conn.getresponse(), conn)
        except BaseException:
            conn.close()
            raise

    def request(self, method, url, *, body=None, headers=None, correlation_id=None,
                idempotency_key=None, request_id=None):
        method = str(method).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise DeliveryError(Classification.PERMANENT, "webhook_method_not_allowed")
        rid, corr = str(request_id or uuid.uuid4()), str(correlation_id or "")
        encoded = None if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        h = {"Accept": "application/json", "X-Request-ID": rid}
        if corr: h["X-Correlation-ID"] = corr
        if idempotency_key: h["Idempotency-Key"] = str(idempotency_key)
        if encoded is not None: h["Content-Type"] = "application/json"
        custom = headers or {}
        reserved = {"host","content-length","connection","transfer-encoding","idempotency-key","x-request-id","x-correlation-id"}
        if any(str(k).strip().lower() in reserved for k in custom):
            raise ValueError("custom headers cannot override HTTP request identity or framing")
        for k,v in custom.items():
            if not isinstance(k,str) or not isinstance(v,str) or any(c in k+v for c in "\r\n"):
                raise ValueError("invalid custom HTTP header")
        h.update(custom)
        started, retries, safe = time.monotonic(), 0, method == "GET"
        while True:
            from .metrics import webhook_request_attempt
            webhook_request_attempt()
            req = Request(url, data=encoded, headers=h, method=method)
            status, rh, raw = None, {}, b""
            try:
                response = self._opener(req, timeout=self.timeout_seconds)
                try:
                    status = int(response.status)
                    rh = dict(response.headers.items()) if getattr(response,"headers",None) else {}
                    raw = response.read(32768)
                finally:
                    close = getattr(response,"close",None)
                    if close: close()
            except (TimeoutError, URLError, OSError, http.client.HTTPException):
                elapsed = time.monotonic()-started
                if safe and retries < self.max_retries:
                    retries += 1; self._log(rid,corr,method,None,retries,0,"retry")
                    self._sleep(self._delay(retries,None)); continue
                cls = Classification.RETRYABLE if safe else Classification.UNKNOWN
                self._observe(cls,elapsed); self._log(rid,corr,method,None,retries,elapsed,cls.value.lower())
                raise DeliveryError(cls,"webhook_network_outcome_unknown",details={"request_id":rid,"retry_count":retries,"duration_ms":round(elapsed*1000,2)}) from None
            elapsed = time.monotonic()-started
            if 200 <= status < 300:
                result = self._receipt(raw)
                result.update({"request_id":rid,"response_id":rh.get("X-Response-ID") or rh.get("X-Request-ID"),
                    "http_status":status,"provider_status":result.get("provider_status") or ("accepted" if status==202 else "success"),
                    "duration_ms":round(elapsed*1000,2),"retry_count":retries})
                self._observe(Classification.SUCCESS,elapsed); self._log(rid,corr,method,status,retries,elapsed,"success")
                return result
            if status == 429:
                if safe and retries < self.max_retries:
                    retries += 1; self._log(rid,corr,method,status,retries,0,"retry")
                    self._sleep(self._delay(retries,rh.get("Retry-After"))); continue
                cls, code = Classification.RETRYABLE, "webhook_rate_limited"
            elif status >= 500 or status in (408,409):
                cls = Classification.RETRYABLE if safe else Classification.UNKNOWN
                code = "webhook_server_unavailable" if safe else "webhook_server_outcome_unknown"
                if safe and retries < self.max_retries:
                    retries += 1; self._log(rid,corr,method,status,retries,0,"retry")
                    self._sleep(self._delay(retries,None)); continue
            else:
                cls, code = Classification.PERMANENT, "webhook_request_rejected"
            self._observe(cls,elapsed); self._log(rid,corr,method,status,retries,elapsed,cls.value.lower())
            raise DeliveryError(cls,code,rh.get("Retry-After") if status==429 else None,
                details={"request_id":rid,"http_status":status,"retry_count":retries,"duration_ms":round(elapsed*1000,2)})

    def _delay(self,n,value):
        try: delay=float(value)
        except (TypeError,ValueError): delay=self.backoff_seconds*2**(n-1)*(.75+self._random()*.5)
        return max(0,min(10,delay))

    @staticmethod
    def _receipt(raw):
        if not raw: return {"receipt":{"accepted":True}}
        try: value=json.loads(raw)
        except (ValueError,UnicodeDecodeError):
            import hashlib
            value={"body_sha256":hashlib.sha256(raw).hexdigest()}
        if not isinstance(value,dict): value={"response_type":type(value).__name__}
        keys=("receipt","receipt_id","id","message_id","status","accepted")
        receipt={k:value[k] for k in keys if k in value and isinstance(value[k],(str,int,float,bool,dict,list))}
        return {"receipt":receipt or {"accepted":True},"provider_status":str(value.get("status")) if value.get("status") is not None else None}

    @staticmethod
    def _observe(cls,elapsed):
        from .metrics import observe_webhook
        observe_webhook(cls.value.lower(),elapsed)

    @staticmethod
    def _log(rid,corr,method,status,retries,elapsed,outcome):
        logging.getLogger(__name__).info(json.dumps({"provider":"webhook","request_id":rid,"correlation_id":corr or None,
            "method":method,"http_status":status,"retry_count":retries,"duration_ms":round(elapsed*1000,2),
            "outcome":outcome},sort_keys=True,separators=(",",":")))
