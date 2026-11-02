#!/usr/bin/env python3
"""Wire-level UNLINK regression checks against the pinned USB/IP bridge.

No guest or USB hardware is used. Frames follow Linux v6.12's
Documentation/usb/usbip_protocol.rst, including the 24-byte UNLINK padding.
"""
import argparse
import asyncio
import errno
import importlib.util
from pathlib import Path
import struct


async def check(module):
    class USB:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.aborted = []

        async def control(self, setup, data):
            return b'test'

        async def transfer(self, ep, direction, **kwargs):
            self.started.set()
            await self.release.wait()
            return b'test'

        async def abort(self, ep, direction):
            self.aborted.append((ep, direction))
            await asyncio.sleep(0)

    usb = USB()
    bridge = module.USBIPBridge(usb)
    bridge.device_record = bytes(312)
    server = await asyncio.start_server(bridge.handle_connection, '127.0.0.1', 0)
    reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
    writer.write(struct.pack('!HHI', 0x0111, 0x8003, 0) + b'1-1'.ljust(32, b'\0'))
    await reader.readexactly(320)

    def submit(seq, ep=0):
        setup = bytes.fromhex('8006000100000400') if ep == 0 else bytes(8)
        return struct.pack('!IIIIIIiiii8s', 1, seq, 0x10001, 1, ep, 0, 4, 0, 0, 0, setup)

    def unlink(seq, target):
        return struct.pack('!IIIIII24x', 2, seq, 0x10001, 1, 1, target)

    async def response(command, seq, status):
        header = await asyncio.wait_for(reader.readexactly(48), 2)
        assert struct.unpack('!II', header[:8]) == (command, seq), header.hex()
        assert struct.unpack('!i', header[20:24])[0] == status, header.hex()
        if command == 4:
            assert header[24:] == bytes(24), 'UNLINK padding must be zero'
        else:
            assert struct.unpack('!i', header[24:28])[0] == 4
            assert await reader.readexactly(4) == b'test'

    try:
        # Unknown/completed UNLINK must not consume the next SUBMIT's header.
        writer.write(unlink(1, 999) + submit(2))
        await response(4, 1, 0)
        await response(3, 2, 0)
        writer.write(unlink(3, 2))
        await response(4, 3, 0)

        # Cancel an active read: one RET_UNLINK, no duplicate RET_SUBMIT.
        writer.write(submit(4, 1))
        await asyncio.wait_for(usb.started.wait(), 2)
        writer.write(unlink(5, 4) + submit(6))
        await response(4, 5, -errno.ECONNRESET)
        await response(3, 6, 0)
        assert usb.aborted == [(1, True)]

        # Cancelling a queued request must leave the active endpoint alone.
        usb.started.clear()
        writer.write(submit(7, 1) + submit(8, 1))
        await asyncio.wait_for(usb.started.wait(), 2)
        writer.write(unlink(9, 8))
        await response(4, 9, -errno.ECONNRESET)
        assert usb.aborted == [(1, True)]
        usb.release.set()
        await response(3, 7, 0)
        writer.write(submit(10))
        await response(3, 10, 0)
        print('PASS: full UNLINK frames; late, active and queued cancellation; stream remains aligned')
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('vendor/vphone-linux'))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('vusb_bridge', args.source / 'tools/vusb-usbip.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    asyncio.run(check(module))


if __name__ == '__main__':
    main()
