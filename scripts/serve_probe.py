#!/usr/bin/env python3
"""Serve read-only browser observations privately; never inject into other pages."""
import argparse
import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {'': ('probe.html', 'text/html; charset=utf-8'),
          **{name: (name, 'text/javascript; charset=utf-8')
             for name in ('probe-common.js', 'probe-worker.js', 'probe.js')}}


class RequestJournal:
    """Bounded HTTP delivery evidence, without URLs, headers or request bodies."""

    def __init__(self, path, limit):
        self.limit = limit
        self.count = 0
        self.truncated = False
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.output = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')

    def record(self, row):
        with self.lock:
            if self.output.closed or self.truncated:
                return
            if self.count == self.limit:
                row = {'kind': 'limit_reached', 'max_request_records': self.limit}
                self.truncated = True
            else:
                self.count += 1
                row = row | {'kind': 'response', 'sequence': self.count}
            self.output.write(json.dumps(row) + '\n')
            self.output.flush()

    def close(self):
        with self.lock:
            self.output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--request-log', type=Path,
                        help='New private JSONL file for bounded HTTP delivery timings')
    parser.add_argument('--max-request-records', type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.max_request_records <= 100000:
        parser.error('--max-request-records must be between 1 and 100000')
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = '/' + secrets.token_urlsafe(24) + '/'
    journal = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type='application/json'):
            complete = False
            try:
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
                complete = True
            finally:
                if journal is not None:
                    finished = time.monotonic()
                    journal.record({
                        'received_at': self.request_received_at,
                        'started_monotonic': self.request_started,
                        'finished_monotonic': finished,
                        'duration_seconds': finished - self.request_started,
                        'method': self.command, 'route': self.route,
                        'status': status, 'response_body_bytes': len(body),
                        'write_complete': complete,
                    })

        def begin_request(self, route):
            self.request_started = time.monotonic()
            self.request_received_at = datetime.now(timezone.utc).isoformat()
            self.route = route

        def do_GET(self):
            self.begin_request('unrecognized')
            path = urlsplit(self.path).path
            if not path.startswith(prefix) or path[len(prefix):] not in ASSETS:
                self.reply(404, b'{}'); return
            name, mime = ASSETS[path[len(prefix):]]
            self.route = name
            self.reply(200, (ROOT / 'diagnostics' / name).read_bytes(), mime)

        def do_POST(self):
            self.begin_request('unrecognized')
            if self.path not in {prefix + 'collect', prefix + 'input'}:
                self.reply(404, b'{}'); return
            self.route = 'collect' if self.path == prefix + 'collect' else 'input'
            if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
                self.reply(403, b'{}'); return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('Invalid body length')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected object')
                canvas = data.pop('canvasPNG', None)
                png = None
                if canvas is not None:
                    if not isinstance(canvas, str) or not canvas.startswith('data:image/png;base64,'):
                        raise ValueError('Expected PNG data URL')
                    png = base64.b64decode(canvas.split(',', 1)[1], validate=True)
                    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('Invalid PNG framing')
                    data['canvasSHA256'] = hashlib.sha256(png).hexdigest()
            except (ValueError, TypeError):
                self.reply(400, b'{}'); return
            if self.path == prefix + 'input':
                identity = 'input-' + uuid.uuid4().hex
                (args.output / (identity + '.json')).write_text(json.dumps({
                    'received_at': datetime.now(timezone.utc).isoformat(),
                    'peer': self.client_address[0], 'observation': data,
                }, indent=2))
                self.reply(200, json.dumps({'id': identity}).encode())
                print(json.dumps({'saved_input': identity}), flush=True)
                return
            identity = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]
            destination = args.output / identity
            destination.mkdir()
            if png is not None:
                (destination / 'canvas.png').write_bytes(png)
            (destination / 'observations.json').write_text(json.dumps(data, indent=2))
            (destination / 'request.json').write_text(json.dumps({
                'received_at': datetime.now(timezone.utc).isoformat(),
                'peer': self.client_address[0], 'headers': list(self.headers.items()),
                'transport': 'HTTP; TLS not measured', 'navigation_verified': False,
            }, indent=2))
            self.reply(200, json.dumps({'id': identity}).encode())
            print(json.dumps({'saved': str(destination)}), flush=True)

    with ExitStack() as cleanup:
        if args.request_log is not None:
            journal = RequestJournal(args.request_log, args.max_request_records)
            cleanup.callback(journal.close)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        cleanup.callback(server.server_close)
        print(json.dumps({'listen': '127.0.0.1', 'port': server.server_port,
                          'path': prefix, 'qemu_guest_url': f'http://10.0.2.2:{server.server_port}{prefix}'}), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
