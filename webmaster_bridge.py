#!/usr/bin/env python3
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
TOKEN = os.environ.get("YANDEX_WEBMASTER_TOKEN", "").strip()

def yandex(path):
    if not TOKEN:
        return {"ok": False, "error": "YANDEX_WEBMASTER_TOKEN is not configured"}
    req = Request(
        "https://api.webmaster.yandex.net" + path,
        headers={"Authorization": "OAuth " + TOKEN, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "http_status": e.code, "error": body}
    except URLError as e:
        return {"ok": False, "error": str(e)}

class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self.send_json({"ok": True, "service": "avtohirurg-yandex-webmaster-bridge", "token_configured": bool(TOKEN)})
            return
        if self.path == "/webmaster/user":
            self.send_json(yandex("/v4/user"))
            return
        if self.path == "/webmaster/sites":
            user = yandex("/v4/user")
            if "uid" not in user:
                self.send_json(user, 502)
                return
            self.send_json(yandex(f"/v4/user/{user['uid']}/hosts"))
            return
        if self.path == "/":
            self.send_json({
                "service": "Avtohirurg Yandex Webmaster API bridge",
                "endpoints": ["/health", "/webmaster/user", "/webmaster/sites"],
                "site": "avtohirurg-tver.ru"
            })
            return
        self.send_json({"error": "not_found"}, 404)

    def log_message(self, fmt, *args):
        pass

HTTPServer((HOST, PORT), Handler).serve_forever()
