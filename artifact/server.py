#!/usr/bin/env python3
"""Loopback-only static server for the recorded paper contract replay."""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SIGNATURE = 'deepshovel-paper-contract-replay-v1'

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == '/health':
            body = json.dumps({'app': SIGNATURE, 'status': 'ok'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        resolved = Path(self.translate_path(self.path)).resolve()
        if ROOT != resolved and ROOT not in resolved.parents:
            self.send_error(403)
            return
        super().do_GET()

    def list_directory(self, path):
        self.send_error(403, 'Directory listing disabled')
        return None

if __name__ == '__main__':
    try:
        with ThreadingHTTPServer(('127.0.0.1', 8137), partial(Handler, directory=str(ROOT))) as server:
            print('DeepShovel recorded replay: http://127.0.0.1:8137/contract-replay.html', flush=True)
            print('Stop with Ctrl+C. Only the artifact directory is served.', flush=True)
            server.serve_forever()
    except OSError as exc:
        raise SystemExit(f'Cannot bind 127.0.0.1:8137; no existing process was stopped. {exc}')
    except KeyboardInterrupt:
        pass
