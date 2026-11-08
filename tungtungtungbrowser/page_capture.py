"""Native WebKit page images, separate from device/display captures."""
import base64
import binascii
from dataclasses import dataclass, field
from pathlib import Path
import struct
import zlib

from .inspector_transport import InspectorError

MAX_PAGE_IMAGE_BYTES = 16 * 1024**2


def decode_page_png(data_url, max_bytes):
    prefix = 'data:image/png;base64,'
    if (not isinstance(data_url, str) or not data_url.startswith(prefix)
            or len(data_url) > len(prefix) + 4 * ((max_bytes + 2) // 3)):
        raise InspectorError('Page snapshot has an invalid PNG data URL or exceeds its byte limit')
    try:
        data = base64.b64decode(data_url[len(prefix):], validate=True)
    except (ValueError, binascii.Error) as error:
        raise InspectorError('Page snapshot has invalid base64 data') from error
    if len(data) > max_bytes or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise InspectorError('Page snapshot has invalid PNG framing or exceeds its byte limit')

    offset, width, height, row_bytes, expected = 8, 0, 0, 0, 0
    seen_data = ended_data = False
    decoder, decoded = zlib.decompressobj(), 0
    try:
        while offset + 12 <= len(data):
            size = struct.unpack_from('>I', data, offset)[0]
            kind = data[offset + 4:offset + 8]
            end = offset + 8 + size
            if end + 4 > len(data):
                break
            if not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) or kind[2] & 32:
                raise ValueError('invalid chunk type')
            if zlib.crc32(memoryview(data)[offset + 4:end]) != struct.unpack_from('>I', data, end)[0]:
                raise ValueError('corrupt chunk')
            payload = memoryview(data)[offset + 8:end]
            if offset == 8:
                if kind != b'IHDR' or size != 13:
                    raise ValueError('missing header')
                width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
                if (width < 1 or height < 1 or width * height > 32_000_000
                        or depth != 8 or color not in (2, 6) or compression or filtering or interlace):
                    raise ValueError('unsupported geometry or encoding')
                row_bytes = 1 + width * (3 if color == 2 else 4)
                expected = row_bytes * height
            elif kind == b'IDAT':
                if ended_data:
                    raise ValueError('noncontiguous image data')
                seen_data = True
                pending = payload
                while pending:
                    raw = decoder.decompress(pending, min(65536, expected - decoded + 1))
                    pending = decoder.unconsumed_tail
                    if (decoded + len(raw) > expected or decoder.unused_data
                            or any(raw[i] > 4 for i in range((-decoded) % row_bytes, len(raw), row_bytes))):
                        raise ValueError('invalid image rows')
                    decoded += len(raw)
            elif kind == b'IEND':
                if size or end + 4 != len(data) or not seen_data or not decoder.eof or decoded != expected:
                    raise ValueError('invalid termination or pixel data')
                return data, width, height
            elif kind == b'IHDR' or not kind[0] & 32:
                raise ValueError('unexpected critical chunk')
            elif seen_data:
                ended_data = True
            offset = end + 4
    except (ValueError, zlib.error, struct.error) as error:
        raise InspectorError('Page snapshot has invalid PNG data: ' + str(error)) from error
    raise InspectorError('Page snapshot PNG is truncated or has no terminator')


@dataclass(frozen=True)
class PageScreenshot:
    png: bytes = field(repr=False)
    width: int
    height: int
    rect: tuple[int, int, int, int]
    coordinate_system: str
    document_url: str
    target_id: str
    document_generation: int

    def save(self, path, *, overwrite=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb' if overwrite else 'xb') as output:
            output.write(self.png)
        return path

    def metadata(self):
        return {'source': 'webkit_page_snapshot', 'mime_type': 'image/png', 'bytes': len(self.png),
                'width': self.width, 'height': self.height, 'rect': dict(zip(('x', 'y', 'width', 'height'), self.rect)),
                'coordinate_system': self.coordinate_system, 'document_url': self.document_url,
                'target_id': self.target_id, 'document_generation': self.document_generation}
