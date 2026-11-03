"""Exercise descriptor passing and failure handling against a real Unix socket."""
import array
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
import zlib

from tungtungtungbrowser import Browser, DisplayScreenshot, GuestError, capture_display
from tungtungtungbrowser.cli import capture
from tungtungtungbrowser.display import _png_from_ppm


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png():
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x12\x34\x56')) + chunk(b'IEND', b''))


def ppm():
    return b'P6\n1 1\n255\n\x12\x34\x56\0'


def png_rows(data):
    """Read the generated PNG's scanlines independently of the PPM encoder."""
    offset, compressed = 8, bytearray()
    while offset < len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        if data[offset + 4:offset + 8] == b'IDAT':
            compressed.extend(data[offset + 8:offset + 8 + length])
        offset += length + 12
    return zlib.decompress(compressed)


@contextmanager
def monitor_fixture(mode='success', image=None):
    state = {'commands': [], 'errors': [], 'removed': False}
    with tempfile.TemporaryDirectory() as folder, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        path = Path(folder) / 'qmp.sock'
        server.bind(str(path))
        server.listen(1)
        server.settimeout(2)

        def serve():
            received_fd = None
            try:
                with server.accept()[0] as connection:
                    connection.settimeout(2)
                    connection.sendall(b'{"QMP":{"version":{}}}\r\n')
                    pending = b''
                    while True:
                        data, ancillary, flags, address = connection.recvmsg(65536, socket.CMSG_SPACE(4))
                        if not data:
                            break
                        for level, kind, value in ancillary:
                            if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                                descriptors = array.array('i')
                                descriptors.frombytes(value[:len(value) - len(value) % descriptors.itemsize])
                                received_fd = descriptors[0]
                                for fd in descriptors[1:]:
                                    os.close(fd)
                        pending += data
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            request = json.loads(line)
                            command = request['execute']
                            state['commands'].append(command)
                            reply = {'id': request['id'], 'return': {}}
                            if mode == 'wrong-id':
                                reply['id'] += 1
                            if command == 'add-fd':
                                if received_fd is None:
                                    raise AssertionError('Missing SCM_RIGHTS file descriptor')
                                state['access_mode'] = fcntl.fcntl(received_fd, fcntl.F_GETFL) & os.O_ACCMODE
                                reply['return'] = {'fdset-id': 7, 'fd': received_fd}
                            if command == 'screendump':
                                state['screendump'] = request['arguments']
                                if mode == 'disconnect':
                                    return
                                if mode == 'error':
                                    reply = {'id': request['id'], 'error': {'class': 'GenericError', 'desc': 'No display surface'}}
                                elif mode == 'timeout':
                                    # Events must not restart the client's total deadline.
                                    while True:
                                        connection.sendall(b'{"event":"DISPLAY_UPDATE"}\n')
                                        time.sleep(0.01)
                                else:
                                    os.write(received_fd, ppm() if image is None else image)
                            if command == 'remove-fd':
                                state['removed'] = True
                                os.close(received_fd)
                                received_fd = None
                            connection.sendall(b'{"event":"DISPLAY_UPDATE"}\n' + json.dumps(reply).encode() + b'\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                state['errors'].append(exc)
            finally:
                if received_fd is not None:
                    os.close(received_fd)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield path, state
        finally:
            thread.join(3)
            if thread.is_alive():
                raise AssertionError('Monitor fixture did not stop')
            if state['errors']:
                raise state['errors'][0]


class DisplayCaptureTest(unittest.TestCase):
    def test_host_capture_passes_write_only_descriptor_and_ignores_events(self):
        with monitor_fixture() as (socket_path, state), tempfile.TemporaryDirectory() as folder:
            class NoGuestCalls:
                def call(self, *args, **kwargs):
                    raise AssertionError('Host capture must not contact the guest')
            image = Browser(guest=NoGuestCalls(), qmp_socket=socket_path).screenshot(Path(folder) / 'screen.png')
            self.assertEqual(png_rows(image.png), b'\0\x12\x34\x56')
            self.assertEqual(image.metadata()['mime_type'], 'image/png')
            self.assertIsNone(image.coordinate_scale)
            self.assertEqual((image.width, image.height), (1, 1))
            with self.assertRaises(FileExistsError):
                image.save(Path(folder) / 'screen.png')
        self.assertEqual(state['access_mode'], os.O_WRONLY)
        self.assertEqual(state['screendump'], {'filename': '/dev/fdset/7', 'format': 'ppm'})
        self.assertTrue(state['removed'])

    def test_capture_manifest_names_png_and_retains_unverified_navigation(self):
        class GuestFixture:
            def health(self):
                return {'status': 'ok'}
            def open(self, url):
                return {'url': url}
            def call(self, method, **kwargs):
                if method == 'screen.screenshot':
                    raise AssertionError('Host backend must not call the native encoder')
                return {'locked': False} if method == 'screen.unlock' else {}
        with monitor_fixture() as (socket_path, state), tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'capture'
            result = capture(GuestFixture(), 'https://example.com/', destination, 0,
                             qmp_socket=socket_path)
            self.assertIn('screenshot.png', result['artifacts'])
            self.assertNotIn('screenshot.jpg', result['artifacts'])
            self.assertFalse(result['navigation_verified'])
            self.assertEqual(png_rows((destination / 'screenshot.png').read_bytes()), b'\0\x12\x34\x56')
            self.assertEqual(json.loads((destination / 'screenshot.json').read_text())['source'], 'qemu_display')

    def test_qmp_error_removes_descriptor_and_preserves_existing_output(self):
        with monitor_fixture('error') as (socket_path, state), tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'screen.png'
            path.write_bytes(b'previous evidence')
            with self.assertRaisesRegex(GuestError, 'No display surface'):
                Browser(qmp_socket=socket_path).screenshot(path, overwrite=True)
            self.assertEqual(path.read_bytes(), b'previous evidence')
        self.assertTrue(state['removed'])

    def test_disconnect_and_mismatched_reply_are_errors(self):
        for mode, message in [('disconnect', 'disconnected'), ('wrong-id', 'mismatched')]:
            with self.subTest(mode=mode), monitor_fixture(mode) as (socket_path, state):
                with self.assertRaisesRegex(GuestError, message):
                    capture_display(socket_path)

    def test_events_do_not_extend_total_deadline(self):
        with monitor_fixture('timeout') as (socket_path, state):
            started = time.monotonic()
            with self.assertRaises(GuestError):
                capture_display(socket_path, timeout=0.15)
            self.assertLess(time.monotonic() - started, 1)

    def test_empty_or_corrupt_capture_is_not_success(self):
        for data in [b'', ppm()[:-2], ppm() + b'garbage', png()]:
            with self.subTest(data=data[:8]), monitor_fixture(image=data) as (socket_path, state):
                with self.assertRaisesRegex(GuestError, 'Display capture'):
                    capture_display(socket_path)
            self.assertTrue(state['removed'])

    def test_ppm_padding_is_removed_without_losing_pixel_whitespace(self):
        # Two RGB rows, each six bytes; QEMU adds two bytes of row padding.
        first, second = b'\x0a\x20\x0d\xff\x00\x80', b'\x01\x02\x03\x04\x05\x06'
        header = b'P6\n2 2\n255\n'
        for pixels in (first + second, first + b'\xde\xad' + second + b'\xbe\xef'):
            image = DisplayScreenshot.from_png(_png_from_ppm(header + pixels))
            self.assertEqual((image.width, image.height), (2, 2))
            self.assertEqual(png_rows(image.png), b'\0' + first + b'\0' + second)
        for data in (header + first, header + first + second + b'\0',
                     b'P6\n0 2\n255\n', b'P6\n999999 999999\n255\n',
                     b'P6\n1 1\n65535\n' + b'\0' * 6):
            with self.subTest(data=data[:30]), self.assertRaises(GuestError):
                _png_from_ppm(data)

    def test_png_geometry_is_not_inferred_from_untrusted_metadata(self):
        data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 0, 1, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(b'\0\0\0\0')) + chunk(b'IEND', b''))
        with self.assertRaisesRegex(GuestError, 'geometry'):
            DisplayScreenshot.from_png(data)


if __name__ == '__main__':
    unittest.main()
