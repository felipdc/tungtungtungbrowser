"""Native clock offers owned by one VM launcher, with explicit lifecycle state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from .guest import GuestError


class ClockMaintenance:
    """Wait for read-only health, then make serial offers until stopped or failed.

    The caller must stop this worker before requesting guest shutdown. A stop
    waits for the current bounded guest request; it does not cancel that request.
    """

    def __init__(self, guest, path, interval, *, launch_started_unix,
                 startup_timeout=600, health_interval=5):
        if not 10 <= interval <= 3600:
            raise ValueError('Clock interval must be between 10 and 3600 seconds')
        self.guest = guest
        self.path = Path(path)
        self.interval = interval
        self.startup_timeout = startup_timeout
        self.health_interval = health_interval
        self.stopping = threading.Event()
        self.thread = None
        self.state = {
            'status': 'prepared', 'launch_started_unix': launch_started_unix,
            'interval_seconds': interval, 'health_checks': 0, 'offers': 0,
        }
        # Each launch chooses a new file, preserving earlier runs and mode 0600.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(self.state, output, indent=2)

    def _save(self, status):
        self.state.update(status=status, updated_unix=time.time())
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent,
                                         prefix=self.path.name + '.', delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(self.state, output, indent=2)
                output.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Clock maintenance has already started')
        self.thread = threading.Thread(target=self._run, name='native-clock')
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
        else:
            self._save('stopped')
        return dict(self.state)

    def _run(self):
        try:
            self._maintain()
        except Exception as exc:
            self.state['error'] = str(exc)
            self.state['status'] = 'failed'
            try:
                self._save('failed')
            except OSError:
                pass
            print(json.dumps({'clock_maintenance': 'failed', 'error': str(exc),
                              'state_file': str(self.path)}), file=sys.stderr, flush=True)

    def _maintain(self):
        deadline = time.monotonic() + self.startup_timeout
        self._save('waiting_for_guest')
        while not self.stopping.is_set():
            if time.monotonic() >= deadline:
                raise GuestError('Clock maintenance guest-health deadline exceeded')
            self.state['health_checks'] += 1
            try:
                self.guest.health()
                break
            except GuestError as exc:
                # Only read-only health is retried. A clock offer is never retried
                # after failure, even when the failed exchange may have reached iOS.
                self.state['last_health_error'] = str(exc)
                self._save('waiting_for_guest')
                self.stopping.wait(min(self.health_interval,
                                       max(0, deadline - time.monotonic())))
        while not self.stopping.is_set():
            self.state['offers'] += 1
            self.state['offer_started_unix'] = time.time()
            self._save('offering_time')
            started = time.monotonic()
            response = self.guest.sync_clock()
            self.state['last_response'] = response
            self.state['last_offer_seconds'] = time.monotonic() - started
            if response['automatic'] is False:
                self._save('manual_time_preserved')
                return
            if response['automatic'] is not True:
                raise GuestError('Automatic guest time is unavailable')
            self._save('waiting_for_interval')
            # Begin the interval after the response; no overlapping or catch-up work.
            self.stopping.wait(self.interval)
        self._save('stopped')
