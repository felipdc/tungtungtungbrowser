"""Owned native Inspector stream over VSOCK; protocol library imported on demand."""
import asyncio
import contextlib
import json
import socket
import uuid


class InspectorError(RuntimeError):
    """Native inspection failed; actions are never automatically replayed."""


class _HandoffRejected(InspectorError):
    def __init__(self, reply):
        self.reason = reply.get('handoff_error')
        super().__init__('Native Inspector bridge status: open_failed'
                         + (f' ({self.reason})' if isinstance(self.reason, str) else ''))


async def vsock_connection(cid, port):
    if type(cid) is not int or not 3 <= cid < 2**32 - 1:
        raise ValueError('Invalid guest CID')
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        for option in (socket.SO_VM_SOCKETS_BUFFER_MAX_SIZE, socket.SO_VM_SOCKETS_BUFFER_SIZE):
            sock.setsockopt(socket.AF_VSOCK, option, 4 * 1024 * 1024)
        await asyncio.get_running_loop().sock_connect(sock, (cid, port))
        return sock
    except BaseException:
        sock.close()
        raise


class InspectorTransport:
    """Own one bridge socket and its native plist stream.

    A factory may return a connected socket through an externally managed tunnel.
    No credentials or provider logic belongs here. The bridge must be installed
    and native Web Inspector enabled. After its single JSON greeting, protocol 2
    carries native Inspector frames directly; EOF closes the guest service socket.
    """
    def __init__(self, *, cid=3, timeout=30, bridge_socket=None, max_open_attempts=3):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError('timeout must be between 0 and 300 seconds')
        if type(max_open_attempts) is not int or not 1 <= max_open_attempts <= 3:
            raise ValueError('max_open_attempts must be an integer between 1 and 3')
        self.timeout = timeout
        self.max_open_attempts = max_open_attempts
        self.handoff_attempts = []
        self._opening_identity = None
        self._bridge_socket = bridge_socket or (lambda: vsock_connection(cid, 1343))
        self.reader = self.writer = self.service = None
        self.identity = None
        self.failure = None
        self.closed = False

    async def _control(self, command, expected):
        async with asyncio.timeout(self.timeout):
            self.writer.write(command.encode('ascii') + b'\n')
            await self.writer.drain()
            line = await self.reader.readline()
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != 2:
                    raise ValueError('Expected bridge protocol 2')
                if type(value.get('pid')) is not int or value['pid'] <= 1:
                    raise ValueError('Invalid bridge process')
                if (not isinstance(value['boot_uuid'], str)
                        or str(uuid.UUID(value['boot_uuid'])).lower() != value['boot_uuid'].lower()):
                    raise ValueError('Invalid bridge boot identity')
                if command == 'open':
                    identity = (value['pid'], value['boot_uuid'].lower())
                    if self._opening_identity is not None and identity != self._opening_identity:
                        raise ValueError('Native bridge identity changed during open')
                    self._opening_identity = identity
                    if value.get('status') == 'open_failed':
                        if type(value.get('port')) is not int or value['port'] != 0:
                            raise ValueError('Invalid failed handoff port')
                        raise _HandoffRejected(value)
                if value.get('status') != expected:
                    raise InspectorError('Native Inspector bridge status: ' + str(value.get('status'))[:100])
                if type(value.get('port')) is not int or value['port'] != 1343:
                    raise ValueError('Invalid bridge port')
                if self.identity and any(value[k] != self.identity[k] for k in ('pid', 'boot_uuid', 'port')):
                    raise ValueError('Native bridge identity changed')
            except (ValueError, KeyError, TypeError) as error:
                raise InspectorError('Invalid native Inspector handoff') from error
            return value

    async def open(self):
        if self.writer is not None or self.closed:
            raise InspectorError('Inspector transport is single-use')
        from pymobiledevice3.service_connection import ServiceConnection
        try:
            async with asyncio.timeout(self.timeout):
                for attempt in range(self.max_open_attempts):
                    record = {'attempt': attempt + 1}
                    self.handoff_attempts.append(record)
                    started = asyncio.get_running_loop().time()
                    try:
                        sock = await self._bridge_socket()
                        try:
                            self.reader, self.writer = await asyncio.open_connection(sock=sock, limit=1024)
                        except BaseException:
                            sock.close()
                            raise
                        self.identity = await self._control('open', 'opened')
                    except _HandoffRejected as error:
                        record.update(status='rejected', reason=error.reason)
                        # The bridge explicitly closed its native stream and
                        # cancelled the failed XPC handoff before this reply.
                        # No native plist frame or browser action has been sent.
                        # Unknown/legacy failures and timeouts are not retried.
                        if error.reason != 'connection_interrupted' or attempt + 1 == self.max_open_attempts:
                            raise
                        await self._close_socket()
                        await asyncio.sleep(0.25)
                    except BaseException as error:
                        record.update(status='failed', error_type=type(error).__name__)
                        raise
                    else:
                        record['status'] = 'opened'
                        break
                    finally:
                        record['seconds'] = asyncio.get_running_loop().time() - started
                # readexactly, used by ServiceConnection, is independent of the
                # line-reader limit. It can consume a large, bounded plist frame.
                self.service = ServiceConnection(self.reader, self.writer)
                self.service.max_frame_size = 32 * 1024**2
                return self.service
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.service is not None:
            try:
                await asyncio.wait_for(self.service.close(), 3)
            except Exception as error:
                self.failure = error
            self.service = None
        await self._close_socket()

    async def _close_socket(self):
        if self.writer is not None:
            self.writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.writer.wait_closed(), 3)
            self.writer = None
        self.reader = None
