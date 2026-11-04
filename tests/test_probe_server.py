import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
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
                    identity = json.load(response)['id']
                bundle = root / 'observations' / identity
                self.assertEqual(json.loads((bundle / 'observations.json').read_text()),
                                 {'private_test_value': secret})
                self.assertIn('received_at', json.loads((bundle / 'request.json').read_text()))
                self.assertEqual(stat.S_IMODE((bundle / 'observations.json').stat().st_mode), 0o600)
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
                # The sequence page uses the same private asset prefix and input
                # collector; callbacks must remain attributable to their page run.
                for asset, mime in [('display-sequence.html', 'text/html'),
                                    ('display-sequence.js', 'text/javascript')]:
                    with urllib.request.urlopen(base + address['path'] + asset, timeout=5) as response:
                        self.assertEqual(response.headers.get_content_type(), mime)
                        self.assertTrue(response.read())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + '/' + asset, timeout=5)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
                observation = {'diagnostic': 'display-sequence', 'run': 'test-run',
                               'phase': 'step', 'sequence': 7}
                request = urllib.request.Request(base + address['path'] + 'input',
                    data=json.dumps(observation).encode(),
                    headers={'Content-Type': 'application/json', 'Origin': base})
                with urllib.request.urlopen(request, timeout=5) as response:
                    identity = json.load(response)['id']
                saved_input = json.loads((root / 'observations' / (identity + '.json')).read_text())
                self.assertEqual(saved_input['observation'], observation)
                self.assertIn('received_at', saved_input)
                self.assertEqual(stat.S_IMODE((root / 'observations' / (identity + '.json')).stat().st_mode), 0o600)
                before = set((root / 'observations').iterdir())
                for route in ['collect', 'input']:
                    request = urllib.request.Request(base + address['path'] + route,
                        data=b'{"invalid":NaN}',
                        headers={'Content-Type': 'application/json', 'Origin': base})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                self.assertEqual(set((root / 'observations').iterdir()), before)
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


class CallbackPublicationTests(unittest.TestCase):
    def test_input_polling_cannot_see_a_partial_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'input-example.json'
            value = {'observation': {'sequence': 3}}
            def interrupted_dump(data, stream, **kwargs):
                encoded = json.dumps(data)
                stream.write(encoded[:5])
                stream.flush()
                self.assertEqual(list(root.glob('input-*.json')), [])
                stream.write(encoded[5:])
            with patch.object(probe.json, 'dump', side_effect=interrupted_dump):
                probe.publish_json(path, value)
            self.assertEqual(json.loads(path.read_text()), value)
            self.assertEqual(list(root.iterdir()), [path])

    def test_observation_becomes_visible_after_its_companions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'observation-example'
            observation, request, png = {'value': 7}, {'received_at': 'test'}, b'canvas bytes'
            dump = json.dump
            def inspect_publication(value, stream, **kwargs):
                if value == observation:
                    self.assertEqual(list(root.glob('*/observations.json')), [])
                    self.assertEqual((destination / 'canvas.png').read_bytes(), png)
                    self.assertEqual(json.loads((destination / 'request.json').read_text()), request)
                dump(value, stream, **kwargs)
            with patch.object(probe.json, 'dump', side_effect=inspect_publication):
                probe.publish_observation(destination, observation, request, png)
            self.assertEqual(json.loads((destination / 'observations.json').read_text()), observation)
            with self.assertRaises(FileExistsError):
                probe.publish_observation(destination, {'replacement': True}, {}, None)
            self.assertEqual(json.loads((destination / 'observations.json').read_text()), observation)

    def test_competing_input_writer_preserves_original_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input-example.json'
            link = os.link
            def competitor(source, destination):
                path.write_text('{"owner":"earlier callback"}')
                link(source, destination)
            with patch.object(probe.os, 'link', side_effect=competitor):
                with self.assertRaises(FileExistsError):
                    probe.publish_json(path, {'owner': 'later callback'})
            self.assertEqual(json.loads(path.read_text()), {'owner': 'earlier callback'})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_write_never_publishes_completion_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'observation-example'
            dump = json.dump
            def fail_observation(value, stream, **kwargs):
                if value == {'observation': True}:
                    stream.write('{"partial":')
                    stream.flush()
                    raise OSError('Interrupted artifact write')
                dump(value, stream, **kwargs)
            with patch.object(probe.json, 'dump', side_effect=fail_observation):
                with self.assertRaises(OSError):
                    probe.publish_observation(destination, {'observation': True}, {}, b'canvas')
            self.assertEqual(list(root.glob('*/observations.json')), [])
            self.assertEqual({p.name for p in destination.iterdir()}, {'canvas.png', 'request.json'})


if __name__ == '__main__':
    unittest.main()
