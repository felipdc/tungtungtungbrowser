#!/usr/bin/env python3
"""Run one headless CPU-rendered iOS guest. Holds the disk lease until QEMU exits."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser.clock import (ClockMaintenance, CLOCK_RPC_TIMEOUT_SECONDS,
                                     HEALTH_RPC_TIMEOUT_SECONDS)


def check_vm_access(vm, source):
    """Exercise filesystem access without writing guest data or starting QEMU."""
    operation = 'inspect'
    path = vm
    try:
        for name, writable, required in (
                ('Disk.img', True, True), ('nvram.bin', True, True),
                ('ecid', False, True), ('config.plist', False, True),
                ('SEPStorage', True, False), ('gfx-res', False, False),
                ('.tungtungtungbrowser.lock', True, False),
                ('console.log', True, False), ('launch.json', True, False),
                ('exit.json', True, False)):
            path = vm / name
            if not required and not path.exists():
                continue
            operation = 'open for reading and writing' if writable else 'open for reading'
            # No O_CREAT or O_TRUNC: checking a disk must never alter its bytes.
            fd = os.open(path, os.O_RDWR if writable else os.O_RDONLY)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('expected a regular file')
            finally:
                os.close(fd)
        for path in (vm, vm / 'cache', vm / 'cache/mesa'):
            if path != vm and not path.exists():
                continue
            operation = 'create and remove a temporary file in'
            with tempfile.NamedTemporaryFile(prefix='.ttt-access-', dir=path):
                pass
        path = source / 'run-vphone.sh'
        operation = 'read and execute'
        with path.open('rb'):
            pass
        if not os.access(path, os.X_OK, effective_ids=True):
            raise PermissionError('runtime launcher is not executable')
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f'Cannot {operation} {path}: {exc}. Runtime UID={os.geteuid()} '
            f'GID={os.getegid()}. Check bind-mount ownership and read/write access '
            'for that identity; root with dropped capabilities cannot bypass '
            'file permissions. See docs/setup.md#filesystem-access.') from exc


def stop_guest(process, guest, grace_seconds, *, clock=None):
    """Ask iOS to flush/shut down before escalating; preserve the observed outcome."""
    if clock is not None:
        clock.stop()
    deadline = time.monotonic() + grace_seconds
    report = {'requested_unix': time.time(), 'forced_termination': False}
    try:
        report['native_response'] = guest.shutdown()
    except GuestError as exc:
        # A disconnect may mean that shutdown is already in progress. Still wait
        # for the VM to exit rather than immediately killing it on an RPC error.
        report['native_request_error'] = str(exc)
    try:
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        report['forced_termination'] = True
        process.terminate()
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            report['forced_kill'] = True
            process.kill()
            code = process.wait()
    report['finished_unix'] = time.time()
    return code, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vm", type=Path)
    parser.add_argument('--check-access', action='store_true',
                        help='Check mounted filesystem access and exit without starting the guest')
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("TTT_RUNTIME_DIR", str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tungtungtungbrowser/vphone-linux"))))
    parser.add_argument("--memory-gib", type=int, choices=range(2, 9), default=3)
    parser.add_argument("--cpus", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--render-threads", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--tcg-cache-mib", type=int, choices=(256, 512, 1024, 2048), default=1024)
    parser.add_argument("--cid", type=int, default=3)
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=90)
    parser.add_argument("--clock-interval-seconds", type=float,
                        default=os.environ.get('TTT_CLOCK_INTERVAL_SECONDS', '0'),
                        help="Native clock maintenance: 0 disables it; otherwise 10–3600 seconds")
    args = parser.parse_args()
    try:
        vm, source = args.vm.resolve(strict=True), args.source.resolve(strict=True)
    except OSError as exc:
        parser.error(f'Cannot resolve VM or runtime directory: {exc}')
    if not 3 <= args.cid < 2**32 - 1:
        parser.error("CID must be between 3 and 4294967294")
    if not 1 <= args.shutdown_timeout_seconds <= 110:
        parser.error("Shutdown timeout must be between 1 and 110 seconds")
    if args.clock_interval_seconds != 0 and not 10 <= args.clock_interval_seconds <= 3600:
        parser.error("Clock interval must be 0 or between 10 and 3600 seconds")
    if ',' in str(vm) or ',' in str(source):
        parser.error("QEMU paths cannot contain commas")
    try:
        check_vm_access(vm, source)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.check_access:
        print(json.dumps({'filesystem_access': 'passed', 'vm': str(vm),
                          'source': str(source), 'uid': os.geteuid(), 'gid': os.getegid(),
                          'guest_started': False}))
        return
    candidates = sorted(Path('/usr/share/vulkan/icd.d').glob('lvp_icd*.json'))
    if not candidates:
        parser.error("Mesa lavapipe is required; hardware GPUs are not supported")
    # Only select Mesa's CPU ICD; do not inherit the host's driver selection.
    env = os.environ | {
        "VK_DRIVER_FILES": str(candidates[0]), "VK_ICD_FILENAMES": str(candidates[0]),
        "LP_NUM_THREADS": str(args.render_threads), "GFX": "1", "WINDOW": "0", "TOOLBAR": "0",
        "MEM": f"{args.memory_gib}G", "SMP": str(args.cpus), "CID": str(args.cid),
        "TB_SIZE": str(args.tcg_cache_mib),
        "VSOCK": "vhost", "VPHONE_CPUS": "", "REIMS_VGPU_WINDOW_TITLE": "tungtungtungbrowser",
        # Do not silently relay otherwise-fatal renderer degradations. Diagnostic
        # runs may opt in explicitly, and the launch record exposes that choice.
        "REIMS_VGPU_CENSUS_DIR": os.environ.get("REIMS_VGPU_CENSUS_DIR", ""),
    }
    # Match the upstream resolution selection and retain it with resource data.
    # Guest-reported points and scale still need separate native verification.
    display_resolution = os.environ.get('GFX_RES')
    if not display_resolution:
        display_file = vm / 'gfx-res'
        display_resolution = display_file.read_text().strip() if display_file.exists() else '1290x2796'
    env['GFX_RES'] = display_resolution
    qmp = vm / "control.sock"
    if len(os.fsencode(qmp)) > 100:
        parser.error("VM path is too long for a QMP Unix socket")
    # Advisory lock shared by this project's host/container entry point. Upstream's raw
    # run-vphone.sh does not honor it: never start that launcher on this live bundle.
    with (vm / '.tungtungtungbrowser.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("This VM bundle already has a tungtungtungbrowser process")
        with (vm / 'console.log').open('ab', buffering=0) as log:
            stop_requested = False

            def request_stop(signum, frame):
                nonlocal stop_requested
                stop_requested = True

            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, request_stop)
            start_clock = time.monotonic()
            start_wall = time.time()
            clock = None
            if args.clock_interval_seconds:
                clock = ClockMaintenance(Guest(args.cid, timeout=CLOCK_RPC_TIMEOUT_SECONDS),
                                         vm / ('clock-' + uuid.uuid4().hex + '.json'),
                                         args.clock_interval_seconds,
                                         launch_started_unix=start_wall,
                                         health_guest=Guest(args.cid, timeout=HEALTH_RPC_TIMEOUT_SECONDS))
            process = subprocess.Popen([str(source / 'run-vphone.sh'), str(vm),
                                        '-qmp', f'unix:{qmp},server=on,wait=off',
                                        '-netdev', 'user,id=ttt-net,ipv6=off',
                                        '-device', 'virtio-net-pci,netdev=ttt-net,romfile=,ctrl_vq=off,ctrl_rx=off,'
                                        'ctrl_vlan=off,ctrl_rx_extra=off,ctrl_mac_addr=off,'
                                        'ctrl_guest_offloads=off,guest_announce=off'],
                                       cwd=source, env=env, stdout=log, stderr=log,
                                       pass_fds=(lock.fileno(),))
            started = {"pid": process.pid, "started_unix": start_wall,
                       "started_monotonic": start_clock,
                       "pid_start_ticks": Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[19],
                       "host_boot_id": Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       "vm": str(vm),
                       "boot_kind": "cold", "memory_gib": args.memory_gib, "cpus": args.cpus,
                       "render_threads": args.render_threads, "renderer": "lavapipe",
                       "display_resolution": display_resolution,
                       "tcg_cache_mib": args.tcg_cache_mib,
                       "renderer_diagnostics": bool(env["REIMS_VGPU_CENSUS_DIR"]),
                       "network_adapter": "virtio-net-without-control-queue",
                       "clock_maintenance": {
                           "enabled": clock is not None,
                           "interval_seconds": args.clock_interval_seconds,
                           "health_timeout_seconds": HEALTH_RPC_TIMEOUT_SECONDS,
                           "offer_timeout_seconds": CLOCK_RPC_TIMEOUT_SECONDS,
                           "state_file": str(clock.path) if clock is not None else None},
                       "cid": args.cid, "qmp": str(qmp), "safari_ready": False}
            (vm / 'launch.json').write_text(json.dumps(started, indent=2))
            print(json.dumps(started), flush=True)
            if clock is not None:
                clock.start()
            shutdown = None
            try:
                while process.poll() is None:
                    if stop_requested:
                        code, shutdown = stop_guest(process, Guest(args.cid, timeout=min(5, args.shutdown_timeout_seconds)),
                                                    args.shutdown_timeout_seconds, clock=clock)
                        break
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                clock_state = clock.stop() if clock is not None else None
            code = process.returncode
            (vm / 'exit.json').write_text(json.dumps({
                "exit_code": code, "finished_unix": time.time(),
                "started_unix": start_wall, "shutdown": shutdown,
                "clock_maintenance": clock_state}, indent=2))
            raise SystemExit(code if code >= 0 else 128 - code)


if __name__ == '__main__':
    main()
