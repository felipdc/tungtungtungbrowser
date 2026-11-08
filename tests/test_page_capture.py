import asyncio
import base64
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

from tungtungtungbrowser import PageScreenshot
from tungtungtungbrowser.inspector import DocumentChangedError, InspectorError
from tungtungtungbrowser.page_capture import decode_page_png
from test_inspector import session_fixture


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


def png(*, color=6, width=2, height=2, raw=None, compressed=None):
    channels = 4 if color == 6 else 3
    raw = raw if raw is not None else (b'\0' + b'\xff' * width * channels) * height
    compressed = zlib.compress(raw) if compressed is None else compressed
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0))
            + chunk(b'sRGB', b'\0') + chunk(b'IDAT', compressed) + chunk(b'IEND', b''))


def data_url(data):
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def page_session():
    session = session_fixture()
    session._inspector.results = {
        'DOM.getDocument': {'root': {'nodeId': 1, 'documentURL': 'https://fixture.invalid/page'}},
        'Page.snapshotRect': {'dataURL': data_url(png())},
    }
    return session


class PageImageTests(unittest.TestCase):
    def test_rgb_and_rgba_images_preserve_exact_bytes_and_geometry(self):
        for color in (2, 6):
            image = png(color=color)
            self.assertEqual(decode_page_png(data_url(image), len(image)), (image, 2, 2))

    def test_corrupt_truncated_and_invalid_pixel_data_are_rejected(self):
        invalid = [png()[:-1], png() + b'extra', png()[:40] + b'\0' + png()[41:],
                   png(color=0), png(width=32_000_001, raw=b''),
                   png(raw=b'\x05' + b'\xff' * 8 + b'\0' + b'\xff' * 8),
                   png(raw=b'\0' * 17), png(raw=b'\0' * 19),
                   png(compressed=b'invalid zlib'), png(compressed=zlib.compress(b'\0' * 18) + b'extra'),
                   png(width=1, height=1, compressed=zlib.compress(b'\0' * 1000000)),
                   png()[:-12] + chunk(b'abcd', b'') + png()[-12:]]
        for value in invalid:
            with self.subTest(bytes=len(value)), self.assertRaises(InspectorError):
                decode_page_png(data_url(value), 1024 * 1024)

    def test_data_url_encoding_and_byte_limit_are_enforced(self):
        for value in [None, {}, 'data:image/jpeg;base64,AA==', 'data:image/png;base64,%%',
                      'data:image/png;base64,é', data_url(png())]:
            with self.subTest(value=type(value)), self.assertRaises(InspectorError):
                decode_page_png(value, len(png()) - 1)


class PageCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_page_contract_needs_no_browser_and_records_document(self):
        for coordinates in ('viewport', 'page'):
            s = page_session()
            sent = []
            original = s._inspector.send_socket_data
            async def send(*args):
                sent.append(json.loads(args[-1]['params']['message']))
                await original(*args)
            s._inspector.send_socket_data = send
            result = await s.snapshot_rect(width=100, height=200, x=3, y=4,
                                           coordinate_system=coordinates)
            self.assertIsInstance(result, PageScreenshot)
            self.assertEqual([v['method'] for v in sent], ['DOM.getDocument', 'Page.snapshotRect'])
            self.assertEqual(sent[-1]['params'], {'x': 3, 'y': 4, 'width': 100, 'height': 200,
                                                 'coordinateSystem': coordinates.title()})
            self.assertEqual(result.png, png())
            self.assertEqual(result.metadata(), {
                'source': 'webkit_page_snapshot', 'mime_type': 'image/png', 'bytes': len(png()),
                'width': 2, 'height': 2, 'rect': {'x': 3, 'y': 4, 'width': 100, 'height': 200},
                'coordinate_system': coordinates, 'document_url': 'https://fixture.invalid/page',
                'target_id': 'page-a', 'document_generation': 0})

    async def test_invalid_rectangle_options_fail_before_any_command(self):
        for patch in [{'width': True}, {'width': 0}, {'height': 4097}, {'x': -1}, {'y': 0.5},
                      {'x': 2147483647}, {'width': 3000, 'height': 3000},
                      {'coordinate_system': 'screen'}, {'max_bytes': True},
                      {'max_bytes': 16 * 1024**2 + 1}, {'timeout': 0}]:
            s = page_session()
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                await s.snapshot_rect(**({'width': 393, 'height': 650} | patch))
            self.assertFalse(s._inspector.commands)

    async def test_document_change_or_target_replacement_never_saves_old_image(self):
        for replacement in (False, True):
            s = page_session()
            original = s._inspector.send_socket_data
            async def send(*args):
                await original(*args)
                if json.loads(args[-1]['params']['message'])['method'] == 'Page.snapshotRect':
                    if replacement:
                        s._receive(s._session_id, {'method': 'Target.didCommitProvisionalTarget',
                            'params': {'oldTargetId': 'page-a', 'newTargetId': 'page-b'}})
                    else:
                        s._event('page-a', {'method': 'DOM.documentUpdated', 'params': {}})
            s._inspector.send_socket_data = send
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'snapshot.png'
                with self.assertRaises(DocumentChangedError):
                    await s.snapshot_rect(path, width=393, height=650)
                self.assertFalse(path.exists())
            self.assertEqual(s._inspector.commands.count('Page.snapshotRect'), 1)

    async def test_timeout_and_cancellation_discard_late_reply_without_replay_or_file(self):
        for cancel in (False, True):
            s = page_session()
            ready = asyncio.Event()
            inner = None
            original = s._inspector.send_socket_data
            async def send(*args):
                nonlocal inner
                nested = json.loads(args[-1]['params']['message'])
                if nested['method'] == 'Page.snapshotRect':
                    s._inspector.silent = True
                    inner = nested['id']
                    ready.set()
                await original(*args)
            s._inspector.send_socket_data = send
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'snapshot.png'
                task = asyncio.create_task(s.snapshot_rect(path, width=393, height=650, timeout=.02))
                await ready.wait()
                if cancel:
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else TimeoutError):
                    await task
                s._inspector.reply(inner, 'page-a', {'dataURL': data_url(png())})
                self.assertFalse(path.exists())
            self.assertFalse(s._pending)
            self.assertFalse(s._outer)
            self.assertEqual(s._inspector.commands.count('Page.snapshotRect'), 1)

    async def test_timeout_includes_waiting_for_document_lock(self):
        s = page_session()
        await s._dom_lock.acquire()
        try:
            with self.assertRaises(TimeoutError):
                await s.snapshot_rect(width=393, height=650, timeout=.01)
        finally:
            s._dom_lock.release()
        self.assertFalse(s._inspector.commands)
        self.assertFalse(s._native_lock.locked())

    async def test_image_validation_cannot_publish_after_deadline(self):
        s = page_session()
        def delayed_decode(*args):
            time.sleep(.02)
            return decode_page_png(*args)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.png'
            with patch('tungtungtungbrowser.inspector.decode_page_png', delayed_decode):
                with self.assertRaises(TimeoutError):
                    await s.snapshot_rect(path, width=393, height=650, timeout=.01)
            self.assertFalse(path.exists())

    async def test_malformed_response_and_missing_url_leave_existing_evidence_intact(self):
        for missing_url in (False, True):
            s = page_session()
            if missing_url:
                del s._inspector.results['DOM.getDocument']['root']['documentURL']
            else:
                s._inspector.results['Page.snapshotRect'] = {'dataURL': data_url(png()[:-1])}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'snapshot.png'
                path.write_bytes(b'prior evidence')
                with self.assertRaises(InspectorError):
                    await s.snapshot_rect(path, width=393, height=650, overwrite=True)
                self.assertEqual(path.read_bytes(), b'prior evidence')

    async def test_file_output_preserves_existing_evidence_unless_explicitly_overwritten(self):
        s = page_session()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'snapshot.png'
            result = await s.snapshot_rect(path, width=393, height=650)
            self.assertEqual(path.read_bytes(), result.png)
            path.write_bytes(b'prior evidence')
            with self.assertRaises(FileExistsError):
                await s.snapshot_rect(path, width=393, height=650)
            self.assertEqual(path.read_bytes(), b'prior evidence')
            await s.snapshot_rect(path, width=393, height=650, overwrite=True)
            self.assertEqual(path.read_bytes(), png())
