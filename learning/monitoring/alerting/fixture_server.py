# 成功・失敗の計測値を人工的に返す exporter です。通知試験で使い、Account API には接続しません。
"""Independent synthetic exporter. Never contacts Account or a cluster."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import time

started = time.monotonic()
healthy_at = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        global healthy_at
        if self.path != '/recover':
            self.send_error(404)
            return
        healthy_at = time.monotonic()
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        elapsed = time.monotonic() - started
        errors = (elapsed if healthy_at is None else healthy_at - started) * 10
        successes = elapsed * 90 + (0 if healthy_at is None else (time.monotonic() - healthy_at) * 10000)
        count = successes + errors
        payload = f'''pe_go_goroutines{{job="account-backend"}} 8
pe_http_server_requests_total{{job="account-backend",status_class="2xx"}} {successes}
pe_http_server_requests_total{{job="account-backend",status_class="5xx"}} {errors}
pe_http_server_request_duration_seconds_count{{job="account-backend"}} {count}
pe_http_server_request_duration_seconds_bucket{{job="account-backend",le="0.25"}} {count}
'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(('0.0.0.0', 8889), Handler).serve_forever()
