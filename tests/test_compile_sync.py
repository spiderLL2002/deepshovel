import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location('compile_sync', Path(__file__).parents[1] / 'deploy/runtime/compile-sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

SECRET = 'pump-test-secret-0123456789abcdefghij'


DEFAULT_REPLY = {'ok': True, 'compile': {'active': 2, 'advanced': 1, 'completed': 1, 'failed': 0, 'errors': []},
                 'work': {'claimed': 3, 'succeeded': 2, 'retried': 1, 'failed': 0}, 'open': 4, 'more': True}


class Recorder(BaseHTTPRequestHandler):
    requests = []
    reply = DEFAULT_REPLY
    status = 200

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        type(self).requests.append({
            'path': self.path,
            'authorization': self.headers.get('authorization'),
            'body': json.loads(self.rfile.read(length) or b'{}'),
        })
        body = json.dumps(type(self).reply).encode()
        self.send_response(type(self).status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append({'path': self.path, 'method': 'GET'})
        self.send_response(405)
        self.end_headers()

    def log_message(self, *_):
        pass


class WorkPumpTests(unittest.TestCase):
    def setUp(self):
        Recorder.requests = []
        Recorder.status = 200
        Recorder.reply = DEFAULT_REPLY
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Recorder)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_one_tick_posts_only_to_the_internal_work_endpoint_with_the_credential(self):
        payload = sync.tick(self.base, SECRET, budget_ms=5000)
        self.assertTrue(payload['more'])
        self.assertEqual(Recorder.requests, [{
            'path': '/api/internal/work',
            'authorization': f'Bearer {SECRET}',
            'body': {'budget_ms': 5000},
        }])

    def test_status_summary_carries_counters_only(self):
        summary = sync.summarize(sync.tick(self.base, SECRET))
        self.assertEqual(summary['compile_completed'], 1)
        self.assertEqual(summary['work_retried'], 1)
        self.assertTrue(summary['more'])
        self.assertNotIn(SECRET, json.dumps(summary))
        # Counters only: no identifiers, document text, URLs or messages.
        self.assertTrue(all(isinstance(value, (bool, int)) for value in summary.values()))

    def test_remote_or_non_http_targets_are_refused(self):
        for target in ('https://deepshovel.example', 'http://192.168.1.5:8100', 'http://127.0.0.1:8100/api'):
            with self.assertRaises(ValueError):
                sync.tick(target, SECRET)
        self.assertEqual(Recorder.requests, [])

    def test_http_failures_are_recorded_by_status_and_back_off(self):
        Recorder.status = 503
        Recorder.reply = {'ok': False, 'error': {'code': 'STORE_BUSY', 'message': 'busy'}}
        result, ok = sync.run_once({'web_base_url': self.base}, SECRET)
        self.assertFalse(ok)
        self.assertEqual(result, {'errors': [{'error': 'HTTP 503'}]})
        self.assertEqual(sync.next_delay(result, 1), 2.0)
        self.assertEqual(sync.next_delay(result, 10), sync.MAX_BACKOFF_SECONDS)
        self.assertEqual(sync.next_delay({'more': True}, 0), sync.BUSY_PAUSE_SECONDS)
        self.assertEqual(sync.next_delay({'more': False}, 0), sync.IDLE_SECONDS)

    def test_shared_secret_is_read_from_the_installed_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'web.env'
            path.write_text(f'OTHER=1\nexport COMPILER_SHARED_SECRET="{SECRET}"\n')
            self.assertEqual(sync.read_shared_secret(path), SECRET)
            path.write_text('OTHER=1\n')
            with self.assertRaises(ValueError):
                sync.read_shared_secret(path)

    def test_once_mode_writes_status_without_database_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / 'web.env'
            secret_file.write_text(f'COMPILER_SHARED_SECRET={SECRET}\n')
            status = Path(tmp) / 'status' / 'status.json'
            config = Path(tmp) / 'compile-sync.json'
            config.write_text(json.dumps({'web_base_url': self.base, 'secret_file': str(secret_file),
                                          'status_file': str(status)}))
            import sys
            argv = sys.argv
            sys.argv = ['compile-sync.py', str(config), '--once']
            try:
                sync.main()
            finally:
                sys.argv = argv
            written = json.loads(status.read_text())
            self.assertIn('checked_at', written)
            self.assertEqual([request['path'] for request in Recorder.requests], ['/api/internal/work'])


if __name__ == '__main__':
    unittest.main()
