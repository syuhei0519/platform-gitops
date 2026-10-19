"""Disposable in-memory local Alertmanager webhook. No forwarding or payload logs."""
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

EVENTS = deque(maxlen=100)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path not in ('/healthz', '/events'):
            self.send_error(404)
            return
        data = json.dumps(list(EVENTS) if self.path == '/events' else {'status': 'ok'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != '/notifications':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('size')
            event = json.loads(self.rfile.read(length))
            if event['status'] not in ('firing', 'resolved') or not isinstance(event['alerts'], list):
                raise ValueError('event')
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
            return
        EVENTS.append(event)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()


if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
