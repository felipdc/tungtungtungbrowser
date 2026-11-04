#!/usr/bin/env python3
"""Prepare a separate, build-pinned cache file; never modify a mounted guest.

The repair recipe is project-authored. No firmware is distributed. See
docs/touch-repair.md for the native dataflow, prerequisites and validation.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]


class Span(NamedTuple):
    offset: int
    original: bytes
    repaired: bytes


class Recipe(NamedTuple):
    size: int
    original_sha256: str
    repaired_sha256: str
    signature_offset: int
    signature_size: int
    directory_offset: int
    page_size: int
    page_count: int
    spans: tuple


RECIPE = Recipe(
    size=90800128,
    original_sha256='86cb4b372e883bc57921100cc3aaa694c07fedc67d8d9e931bf0d199eef60e86',
    repaired_sha256='be0895305390a4cae7fb9abf721c50fcc233acca82edbac36712332678ebe5d9',
    signature_offset=90619904, signature_size=180224,
    directory_offset=90619940, page_size=16384, page_count=5531,
    spans=(
        Span(12536592, bytes.fromhex('0ead436d0a2540fd'), bytes.fromhex('0ba9436d0e2d40fd')),
        Span(90644610,
             bytes.fromhex('d18c2c42a4320b9ede85a776861d69c2672db27a18591872a0e148907a86b9bf'),
             bytes.fromhex('147b3b93bdfb46e214bfcccbf88547eefe4d6e88c6724a8e8af6b581f8d938eb')),
    ),
)
CACHE_NAME = 'dyld_shared_cache_arm64e.11'
REPAIR_ID = 'touch-fields-23G90-8624.5.1.10.3-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_at(source, offset, size):
    source.seek(offset)
    data = source.read(size)
    require(len(data) == size, 'Incomplete cache read')
    return data


def file_hash(source, size):
    source.seek(0)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = source.read(min(65536, remaining))
        require(block, 'Source became shorter while hashing')
        digest.update(block)
        remaining -= len(block)
    require(not source.read(1), 'Source became longer while hashing')
    return digest.hexdigest()


def signature_layout(source, recipe):
    """Check the embedded SHA-256 CodeDirectory, with bounded reads."""
    header = read_at(source, 0, 0x38)
    require(header[:7] == b'dyld_v1' and b'arm64e' in header[:16], 'Unexpected dyld header')
    offset, size = struct.unpack_from('<QQ', header, 0x28)
    require((offset, size) == (recipe.signature_offset, recipe.signature_size),
            'Unexpected signature location')
    require(offset + size == recipe.size, 'Signature does not end at the cache boundary')
    signature = read_at(source, offset, size)
    magic, length, count = struct.unpack_from('>III', signature)
    require(magic == 0xFADE0CC0 and 1 <= count <= 16
            and 12 + count * 8 <= length <= size, 'Invalid signature SuperBlob')
    directories = []
    for i in range(count):
        slot, at = struct.unpack_from('>II', signature, 12 + i * 8)
        require(12 + count * 8 <= at <= length - 8, 'Invalid signature index')
        blob_magic, blob_length = struct.unpack_from('>II', signature, at)
        require(8 <= blob_length <= length - at, 'Invalid signature blob length')
        if blob_magic == 0xFADE0C02:
            require(slot == 0, 'Alternate CodeDirectories are unsupported')
            directories.append((at, blob_length))
    require(len(directories) == 1, 'Expected one CodeDirectory')
    at, cd_length = directories[0]
    require(offset + at == recipe.directory_offset and cd_length >= 44,
            'Unexpected CodeDirectory location or size')
    cd = signature[at:at + cd_length]
    version = struct.unpack_from('>I', cd, 8)[0]
    hash_offset, _, special_count, code_count, code_limit = struct.unpack_from('>IIIII', cd, 16)
    hash_size, hash_type, _, page_log = struct.unpack_from('4B', cd, 36)
    if version >= 0x20300 and code_limit == 0:
        require(cd_length >= 64, 'Missing 64-bit code limit')
        code_limit = struct.unpack_from('>Q', cd, 56)[0]
    require(hash_size == 32 and hash_type == 2 and page_log == 14,
            'Expected SHA-256 hashes of 16 KiB pages')
    require(code_count == recipe.page_count and (1 << page_log) == recipe.page_size
            and code_limit == code_count * recipe.page_size == offset,
            'Unexpected signed page range')
    require(hash_offset - special_count * hash_size >= 44
            and hash_offset + code_count * hash_size <= cd_length,
            'CodeDirectory hashes are out of bounds')
    slot_offset = offset + at + hash_offset
    code_span, signature_span = recipe.spans
    require(code_span.offset // recipe.page_size ==
            (code_span.offset + len(code_span.original) - 1) // recipe.page_size,
            'Repair crosses a code-page boundary')
    require(signature_span.offset == slot_offset + code_span.offset // recipe.page_size * hash_size,
            'Repair signature slot does not cover the changed code')
    return cd[hash_offset:hash_offset + code_count * hash_size]


def inspect(source, recipe=RECIPE):
    require(os.fstat(source.fileno()).st_size == recipe.size, 'Unsupported cache size')
    digest = file_hash(source, recipe.size)
    if digest == recipe.original_sha256:
        state = 'original'
    elif digest == recipe.repaired_sha256:
        state = 'repaired'
    else:
        raise ValueError('Unsupported cache SHA-256; offsets will not be applied to this build')
    for span in recipe.spans:
        expected = span.original if state == 'original' else span.repaired
        require(read_at(source, span.offset, len(expected)) == expected, 'Unexpected repair bytes')
    hashes = signature_layout(source, recipe)
    source.seek(0)
    for index in range(recipe.page_count):
        page = source.read(recipe.page_size)
        require(len(page) == recipe.page_size and hashlib.sha256(page).digest() == hashes[index * 32:(index + 1) * 32],
                f'Signed code page {index} failed verification')
    return {'state': state, 'sha256': digest, 'cache_bytes': recipe.size,
            'code_pages_verified': recipe.page_count}


@contextmanager
def open_source(path):
    # NONBLOCK avoids waiting on a FIFO before its file type can be checked.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Source must be a regular file')
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield source


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def prepare(source_path, output_dir, operation, recipe=RECIPE):
    require(operation in ('apply', 'revert'), 'Expected apply or revert')
    output_dir = Path(output_dir).absolute()
    require(not output_dir.resolve().is_relative_to(ROOT), 'Keep generated firmware outside the source repository')
    expected_state = 'original' if operation == 'apply' else 'repaired'
    with open_source(source_path) as source:
        before = inspect(source, recipe)
        require(before['state'] == expected_state, f'{operation} requires a {expected_state} cache')
        # Exclusive directory ownership preserves any previous attempt, even an
        # empty directory or dangling symlink. A failure leaves its evidence.
        output_dir.mkdir(mode=0o700)
        sync_directory(output_dir.parent)
        partial = output_dir / (CACHE_NAME + '.partial')
        with partial.open('x+b') as output:
            source.seek(0)
            remaining = recipe.size
            while remaining:
                block = source.read(min(65536, remaining))
                require(block, 'Source became shorter while copying')
                output.write(block)
                remaining -= len(block)
            require(not source.read(1), 'Source became longer while copying')
            for span in recipe.spans:
                output.seek(span.offset)
                output.write(span.repaired if operation == 'apply' else span.original)
            output.flush()
            os.fsync(output.fileno())
            after = inspect(output, recipe)
            require(after['state'] != before['state'], 'Output did not change to the expected state')
        # Recheck the source as well; an advisory lock cannot stop other writers.
        require(inspect(source, recipe) == before, 'Source changed during preparation')
        partial.rename(output_dir / CACHE_NAME)
        receipt = {
            'status': 'complete', 'repair_id': REPAIR_ID, 'operation': operation,
            'before': before, 'after': after, 'source_modified': False,
            'guest_install_performed': False, 'stock_provenance_verified': False,
            'spans': [{'offset': s.offset, 'original_hex': s.original.hex(),
                       'repaired_hex': s.repaired.hex()} for s in recipe.spans],
            'bytes_written_in_copy': sum(len(s.original) for s in recipe.spans),
            'changed_bytes': sum(a != b for s in recipe.spans for a, b in zip(s.original, s.repaired)),
        }
        write_json(output_dir / 'receipt.json.partial', receipt)
        (output_dir / 'receipt.json.partial').rename(output_dir / 'receipt.json')
        sync_directory(output_dir)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('inspect', 'apply', 'revert'))
    parser.add_argument('source', type=Path, help='extracted cache file in an external workspace')
    parser.add_argument('--output', type=Path, help='new external directory for a copy and receipt')
    args = parser.parse_args(argv)
    if (args.operation == 'inspect') != (args.output is None):
        parser.error('apply/revert require --output; inspect does not accept it')
    try:
        if args.operation == 'inspect':
            with open_source(args.source) as source:
                result = {'status': 'verified', **inspect(source), 'source_modified': False}
        else:
            result = prepare(args.source, args.output, args.operation)
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(1, f'Preparation failed: {exc}. Existing files and partial outputs were preserved.\n')
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
