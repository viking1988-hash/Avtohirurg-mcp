from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/alerts':
            self.send_error(404)
            return
        length = min(int(self.headers.get('Content-Length', '0')), 65536)
        try:
            data = json.loads(self.rfile.read(length))
            alerts = [{'name': a.get('labels', {}).get('alertname'), 'status': a.get('status')} for a in data.get('alerts', [])]
            print('PRIVATE_SANDBOX_ALERT_RECEIVED ' + json.dumps(alerts), flush=True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        except (ValueError, TypeError):
            self.send_error(400)
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
