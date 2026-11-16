"""Bounded proxy recovery after a completed, confirmed login-required response.

The caller supplies provider credentials and native routing verification through
an async callback. This policy never navigates, retries a capture, or changes its
outcome. Use a separate persistent directory for each phone.
"""
import asyncio
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time
import uuid


class ProxyRecoveryError(RuntimeError):
    """Recovery is incomplete or uncertain; do not submit more browser work."""


class ProxyRecoveryExhausted(ProxyRecoveryError):
    """The phone has consumed its configured automatic reset budget."""


class LoginProxyRecovery:
    """Run one verified reset per login failure, with a durable total limit.

    Call ``after_capture`` only after the original request and its evidence are
    complete. A successful callback must verify a new upstream session, browser
    routing, non-browser direct routing, and unchanged container/guest identity.
    A failed or interrupted callback is never automatically repeated, including
    after restarting Python. The original capture remains a failure.
    """
    REQUIRED_PROOF = ('session_changed', 'upstream_verified', 'browser_verified',
                      'non_browser_direct_verified', 'identities_preserved')

    def __init__(self, directory, *, cid, reset_and_verify, max_resets=3, timeout_seconds=120):
        if type(cid) is not int or not 3 <= cid < 2**32 - 1:
            raise ValueError('A valid phone CID is required')
        if type(max_resets) is not int or not 1 <= max_resets <= 10:
            raise ValueError('max_resets must be between 1 and 10')
        if not callable(reset_and_verify):
            raise ValueError('An async reset_and_verify callback is required')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not .01 <= timeout_seconds <= 300):
            raise ValueError('timeout_seconds must be between 0.01 and 300')
        self.directory = Path(directory).resolve()
        if self.directory.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError('Keep recovery records outside the source checkout')
        self.cid, self.max_resets = cid, max_resets
        self.timeout_seconds = timeout_seconds
        self.reset_and_verify = reset_and_verify
        self.check_ready()

    def _load(self):
        path = self.directory / 'state.json'
        if not path.exists():
            return dict(version=1, cid=self.cid, max_resets=self.max_resets, resets=[])
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ProxyRecoveryError('Invalid recovery record')
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or type(state.get('version')) is not int
                or state.get('version') != 1 or type(state.get('cid')) is not int or state.get('cid') != self.cid
                or state.get('max_resets') != self.max_resets
                or not isinstance(state.get('resets'), list)
                or len(state['resets']) > self.max_resets):
            raise ProxyRecoveryError('Recovery record belongs to a different phone or policy')
        if any(not isinstance(row, dict) or row.get('status') != 'verified' for row in state['resets']):
            raise ProxyRecoveryError('Previous proxy reset is uncertain; inspect its existing evidence')
        if state.get('exhausted_attempt_id'):
            raise ProxyRecoveryExhausted('Automatic proxy reset budget exhausted')
        return state

    def check_ready(self):
        """Refuse new requests after an uncertain reset or exhausted budget."""
        if not self.directory.exists():
            return
        fd = os.open(self.directory / 'recovery.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProxyRecoveryError('Another proxy recovery owns this phone') from None
            self._load()

    def _save(self, state):
        path = self.directory / ('state-' + uuid.uuid4().hex + '.tmp')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream, allow_nan=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            path.replace(self.directory / 'state.json')
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            path.unlink(missing_ok=True)

    async def after_capture(self, attempt_id, *, login_required):
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', attempt_id):
            raise ValueError('Use an opaque attempt ID, not a URL or credential')
        if type(login_required) is not bool:
            raise ValueError('login_required must be a confirmed boolean result')
        if not login_required:
            return dict(status='not_required', reset_requested=False)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.directory / 'recovery.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProxyRecoveryError('Another proxy recovery owns this phone') from None
            state = self._load()
            previous = next((row for row in state['resets'] if row['attempt_id'] == attempt_id), None)
            if previous:
                return dict(status='already_verified', reset_requested=False, reset_count=len(state['resets']))
            if len(state['resets']) >= self.max_resets:
                state['exhausted_attempt_id'] = attempt_id
                self._save(state)
                raise ProxyRecoveryExhausted('Automatic proxy reset budget exhausted')
            row = dict(attempt_id=attempt_id, status='reset_requested', started_unix=time.time())
            state['resets'].append(row)
            # Persist before calling anything that can change the proxy. An
            # interrupted process leaves this terminal marker, preventing replay.
            self._save(state)
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    proof = await self.reset_and_verify()
                if not isinstance(proof, dict) or any(proof.get(key) is not True for key in self.REQUIRED_PROOF):
                    raise ProxyRecoveryError('Proxy reset did not provide complete routing verification')
                # Persist only known non-secret proof fields, never callback
                # exception text, credentials, arbitrary URLs or provider output.
                row.update(status='verified', finished_unix=time.time(),
                           proof={key: True for key in self.REQUIRED_PROOF})
                self._save(state)
            except BaseException as error:
                row.update(status='failed', failure_type=type(error).__name__, finished_unix=time.time())
                self._save(state)
                if not isinstance(error, Exception):
                    raise
                raise ProxyRecoveryError('Proxy recovery failed; inspect its existing evidence before continuing') from None
            return dict(status='verified', reset_requested=True, reset_count=len(state['resets']),
                        elapsed_seconds=row['finished_unix'] - row['started_unix'])
