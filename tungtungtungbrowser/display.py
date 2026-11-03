"""Capture QEMU's rendered display without invoking the guest image encoder.

Linux only: SCM_RIGHTS passes an anonymous output file to QEMU. Its container
need not share a filesystem path with the caller. QMP access controls the VM;
use a trusted local Unix socket and one controller per guest.
"""
import array
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
import zlib

from .guest import GuestError

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024


def _png_from_ppm(data):
    """Encode QEMU's raw RGB rows after releasing the monitor connection.

    The pinned QEMU writes pixman's four-byte-aligned row stride into PPM.
    Accept that layout and ordinary packed PPM; discard only known row padding.
    """
    if not isinstance(data, bytes) or len(data) > MAX_IMAGE_BYTES:
        raise GuestError('Display capture has invalid PPM data or size')
    try:
        magic, dimensions, maximum, _ = data[:64].split(b'\n', 3)
        width, height = map(int, dimensions.split())
    except ValueError as exc:
        raise GuestError('Display capture has an invalid PPM header') from exc
    if (magic != b'P6' or maximum != b'255' or width < 1 or height < 1
            or width * height > 32_000_000):
        raise GuestError('Display capture has unsupported PPM geometry or encoding')
    header_bytes = len(magic) + len(dimensions) + len(maximum) + 3
    pixels = memoryview(data)[header_bytes:]
    row_bytes = width * 3
    stride = row_bytes if len(pixels) == row_bytes * height else (row_bytes + 3) & ~3
    if len(pixels) != stride * height:
        raise GuestError('Display capture has truncated or extra PPM pixel data')

    encoder = zlib.compressobj(level=3)
    compressed = []
    for offset in range(0, len(pixels), stride):
        compressed.append(encoder.compress(b'\0'))  # PNG row filter: none.
        compressed.append(encoder.compress(pixels[offset:offset + row_bytes]))
    compressed.append(encoder.flush())

    def chunk(kind, payload):
        checksum = zlib.crc32(payload, zlib.crc32(kind))
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', checksum)

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', b''.join(compressed)) + chunk(b'IEND', b''))


@dataclass(frozen=True)
class DisplayScreenshot:
    png: bytes = field(repr=False)
    width: int
    height: int
    # QEMU knows pixel dimensions, not the guest's screen-point scale.
    coordinate_scale: None = None

    @classmethod
    def from_png(cls, data):
        if not isinstance(data, bytes) or len(data) > MAX_IMAGE_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise GuestError('Display capture has invalid PNG framing or size')
        offset, width, height, seen_data = 8, None, None, False
        while offset + 12 <= len(data):
            size = struct.unpack_from('>I', data, offset)[0]
            kind = data[offset + 4:offset + 8]
            end = offset + 8 + size
            if end + 4 > len(data):
                break
            checksum = struct.unpack_from('>I', data, end)[0]
            if zlib.crc32(memoryview(data)[offset + 4:end]) != checksum:
                raise GuestError('Display capture has a corrupt PNG chunk')
            if offset == 8:
                if kind != b'IHDR' or size != 13:
                    raise GuestError('Display capture is missing its PNG header')
                width, height, depth, color, compression, filtering, interlace = struct.unpack_from('>IIBBBBB', data, offset + 8)
                # QEMU emits noninterlaced RGB PNGs. Reject unexpected formats
                # rather than guessing how their geometry should be interpreted.
                if (width < 1 or height < 1 or width * height > 32_000_000
                        or depth != 8 or color != 2 or compression or filtering or interlace):
                    raise GuestError('Display capture has unsupported PNG geometry or encoding')
            elif kind == b'IHDR':
                raise GuestError('Display capture has repeated PNG headers')
            if kind == b'IDAT' and size:
                seen_data = True
            if kind == b'IEND':
                if size or not seen_data or end + 4 != len(data):
                    raise GuestError('Display capture has invalid PNG termination')
                return cls(data, width, height)
            offset = end + 4
        raise GuestError('Display capture is truncated or has no PNG terminator')

    def save(self, path, *, overwrite=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb' if overwrite else 'xb') as output:
            output.write(self.png)
        return path

    def metadata(self):
        return {'mime_type': 'image/png', 'bytes': len(self.png),
                'width': self.width, 'height': self.height,
                'coordinate_scale': None, 'source': 'qemu_display'}


class _Monitor:
    """One bounded QMP session; events can precede a command's response."""
    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.buffer = bytearray()
        self.sequence = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Display capture timed out (deadline exceeded)')
        self.connection.settimeout(remaining)

    def receive(self):
        while True:
            self.remaining()
            newline = self.buffer.find(b'\n')
            if newline >= 0:
                if newline > MAX_MESSAGE_BYTES:
                    raise GuestError('QMP message exceeds 1 MiB')
                line = self.buffer[:newline]
                del self.buffer[:newline + 1]
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise GuestError('QMP returned a non-object message')
                return value
            if len(self.buffer) > MAX_MESSAGE_BYTES:
                raise GuestError('QMP message exceeds 1 MiB')
            chunk = self.connection.recv(min(65536, MAX_MESSAGE_BYTES + 1 - len(self.buffer)))
            if not chunk:
                raise GuestError('QMP disconnected before returning a response')
            self.buffer.extend(chunk)

    def send(self, command, arguments=None, *, fd=None):
        self.sequence += 1
        message = {'execute': command, 'id': self.sequence}
        if arguments is not None:
            message['arguments'] = arguments
        data = json.dumps(message).encode() + b'\n'
        self.remaining()
        if fd is not None:
            sent = self.connection.sendmsg([data], [
                (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
            data = data[sent:]
        self.connection.sendall(data)
        return self.sequence

    def call(self, command, arguments=None, *, fd=None):
        request_id = self.send(command, arguments, fd=fd)
        while True:
            response = self.receive()
            if 'event' in response:
                continue
            if response.get('id') != request_id:
                raise GuestError('QMP returned a mismatched response ID')
            if 'error' in response:
                raise GuestError(f'QMP {command} failed: {response["error"]}')
            if 'return' not in response:
                raise GuestError('QMP returned no command result')
            return response['return']


def capture_display(qmp_socket, *, timeout=15):
    """Return a PNG of the virtual display, including display-device overlays.

    Does not contact vphoned, navigate, or assert that Safari is ready. QEMU
    writes raw PPM; PNG encoding runs here after closing the monitor socket.
    The timeout bounds the QMP session; failure cleanup gets at most one extra
    second. Raw capture is limited to 32 MiB. Native input uses screen points;
    this image reports pixels only.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('Display timeout must be between 0 and 300 seconds')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection, tempfile.TemporaryFile() as output:
            monitor = _Monitor(connection, time.monotonic() + timeout)
            monitor.remaining()
            connection.connect(os.fspath(qmp_socket))
            if 'QMP' not in monitor.receive():
                raise GuestError('QMP greeting missing')
            monitor.call('qmp_capabilities')
            # fdset lookup requires an exact access-mode match. TemporaryFile
            # is O_RDWR, while screendump opens O_WRONLY; pass a write-only fd.
            write_fd = os.open(f'/proc/self/fd/{output.fileno()}', os.O_WRONLY | os.O_CLOEXEC)
            try:
                descriptor = monitor.call('add-fd', fd=write_fd)
            finally:
                os.close(write_fd)
            fdset = descriptor.get('fdset-id') if isinstance(descriptor, dict) else None
            if isinstance(fdset, bool) or not isinstance(fdset, int) or fdset < 0:
                raise GuestError('QMP returned an invalid file-descriptor set')
            try:
                monitor.call('screendump', {'filename': f'/dev/fdset/{fdset}', 'format': 'ppm'})
            except BaseException:
                # A timed-out request may still run in QEMU. Queue removal on
                # this connection, then close; never retry the capture silently.
                monitor.deadline = time.monotonic() + 1
                try:
                    monitor.send('remove-fd', {'fdset-id': fdset})
                except OSError:
                    pass
                raise
            else:
                monitor.call('remove-fd', {'fdset-id': fdset})
            output.seek(0)
            raw = output.read(MAX_IMAGE_BYTES + 1)
        return DisplayScreenshot.from_png(_png_from_ppm(raw))
    except (OSError, ValueError) as exc:
        raise GuestError(f'Display capture failed: {exc}') from exc
