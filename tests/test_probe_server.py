import concurrent.futures
import importlib.util
import json
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'serve_probe.py'
spec = importlib.util.spec_from_file_location('serve_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeRequestJournalTests(unittest.TestCase):
    def test_http_delivery_records_are_bounded_and_omit_client_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / 'requests.jsonl'
            process = subprocess.Popen(
                [sys.executable, '-u', str(SCRIPT), '--port', '0',
                 '--output', str(root / 'observations'), '--request-log', str(journal),
                 '--max-request-records', '3'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 10)[0],
                                'Probe server did not announce its address')
                address = json.loads(process.stdout.readline())
                base = f"http://127.0.0.1:{address['port']}"
                secret = 'request-data-must-not-appear'
                request = urllib.request.Request(
                    base + address['path'] + '?query=' + secret,
                    headers={'Cookie': secret, 'Authorization': secret})
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read()
                    self.assertIn(b'Safari observations', body)
                request = urllib.request.Request(
                    base + address['path'] + 'collect',
                    data=json.dumps({'private_test_value': secret}).encode(),
                    headers={'Content-Type': 'application/json', 'Origin': base})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertIn('id', json.load(response))
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + '/' + secret, timeout=5)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()

                def wait_for_records(count):
                    deadline = time.monotonic() + 5
                    rows = []
                    while time.monotonic() < deadline:
                        try:
                            rows = [json.loads(line) for line in journal.read_text().splitlines()]
                        except json.JSONDecodeError:
                            pass  # A response can arrive just before its journal flush.
                        if len(rows) == count:
                            return rows
                        time.sleep(.01)
                    self.fail(f'Expected {count} request records, received {len(rows)}')

                rows = wait_for_records(3)
                by_route = {r['route']: r for r in rows}
                self.assertEqual(set(by_route), {'probe.html', 'collect', 'unrecognized'})
                self.assertEqual(by_route['probe.html']['method'], 'GET')
                self.assertEqual(by_route['collect']['method'], 'POST')
                self.assertEqual(by_route['probe.html']['response_body_bytes'], len(body))
                self.assertEqual(by_route['probe.html']['status'], 200)
                self.assertEqual(by_route['collect']['status'], 200)
                self.assertEqual(by_route['unrecognized']['status'], 404)
                for row in rows:
                    self.assertTrue(row['write_complete'])
                    self.assertGreaterEqual(row['finished_monotonic'], row['started_monotonic'])
                    self.assertGreaterEqual(row['duration_seconds'], 0)
                    self.assertNotIn('navigation_verified', row)
                # Exhausting diagnostics must not prevent another asset from loading.
                with urllib.request.urlopen(base + address['path'] + 'probe.js', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read())
                rows = wait_for_records(4)
                self.assertEqual(rows[-1], {'kind': 'limit_reached', 'max_request_records': 3})
                self.assertNotIn(secret, journal.read_text())
                self.assertNotIn(address['path'], journal.read_text())
                self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_concurrent_records_preserve_limit_and_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.jsonl'
            journal = probe.RequestJournal(path, 7)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda i: journal.record({'value': i}), range(100)))
            finally:
                journal.close()
            saved = path.read_bytes()
            rows = [json.loads(line) for line in saved.splitlines()]
            self.assertEqual(len(rows), 8)
            self.assertEqual([r['sequence'] for r in rows[:7]], list(range(1, 8)))
            self.assertEqual(rows[-1], {'kind': 'limit_reached', 'max_request_records': 7})
            with self.assertRaises(FileExistsError):
                probe.RequestJournal(path, 7)
            self.assertEqual(path.read_bytes(), saved)
            # Server shutdown can race with the end of a handler thread.
            journal.record({'value': 'late'})
            self.assertEqual(path.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
