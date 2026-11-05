import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest

from tungtungtungbrowser.inspector_transport import InspectorError

SPEC = importlib.util.spec_from_file_location('stop_inspector_bridge',
    Path(__file__).resolve().parents[1] / 'scripts/stop_inspector_bridge.py')
stopper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stopper)


class InspectorStopTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, failure=None):
        expected = {'protocol': 2, 'status': 'ready', 'pid': 12,
                    'boot_uuid': '11223344-5566-7788-9900-aabbccddeeff', 'port': 1343}
        requests, released = [], []

        async def serve(reader, writer):
            try:
                command = (await reader.readline()).decode().strip()
                requests.append(command)
                answer = expected.copy()
                if command == 'health' and failure == 'wrong_boot':
                    answer['boot_uuid'] = '11223344-5566-7788-9900-aabbccddee00'
                if command.startswith('stop '):
                    if failure == 'lost_reply':
                        return
                    answer['status'] = 'invalid_request' if failure == 'replaced' else 'stopping'
                writer.write(json.dumps(answer).encode() + b'\n')
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                released.append(True)

        listener = await asyncio.start_server(serve, '127.0.0.1', 0)

        async def connected():
            sock = socket.socket()
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, listener.sockets[0].getsockname())
            return sock

        try:
            with tempfile.TemporaryDirectory() as directory:
                deployment, output = Path(directory) / 'deploy.json', Path(directory) / 'stop.json'
                deployment.write_text(json.dumps({'status': 'complete', 'health': expected}))
                if failure:
                    with self.assertRaises(InspectorError):
                        await stopper.stop(deployment, output, bridge_socket=connected, timeout=1)
                else:
                    result = await stopper.stop(deployment, output, bridge_socket=connected, timeout=1)
                    self.assertEqual(result['status'], 'stop_acknowledged')
                report = json.loads(output.read_text())
                # A second invocation cannot silently reuse an uncertain record.
                with self.assertRaises(FileExistsError):
                    await stopper.stop(deployment, output, bridge_socket=connected, timeout=1)
                async with asyncio.timeout(1):
                    while len(released) < len(requests):
                        await asyncio.sleep(0)
        finally:
            listener.close()
            await listener.wait_closed()
        self.assertEqual(requests[0], 'health')
        if failure == 'wrong_boot':
            self.assertEqual(requests, ['health'])
            self.assertFalse(report['stop_requested'])
        else:
            self.assertEqual(requests, ['health', 'stop 12 ' + expected['boot_uuid']])
            self.assertTrue(report['stop_requested'])
        self.assertEqual(len(released), len(requests))

    async def test_owned_process_receives_guarded_stop(self):
        await self.exercise()

    async def test_changed_boot_prevents_stop(self):
        await self.exercise('wrong_boot')

    async def test_replacement_between_health_and_stop_is_not_retried(self):
        await self.exercise('replaced')

    async def test_lost_stop_reply_is_not_retried(self):
        await self.exercise('lost_reply')


if __name__ == '__main__':
    unittest.main()
