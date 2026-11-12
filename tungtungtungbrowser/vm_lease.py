"""Advisory leases shared by the launcher and offline bundle copier."""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path


@contextmanager
def lease_vm(vm, *, cid=None, registry=None):
    """Hold bundle, writable disk inode and optional host CID leases.

    Never unlink lease files: replacing the inode would let another process
    acquire a different lock while this one still owns the original. Passing
    the returned descriptors to QEMU keeps ownership if its parent dies.
    Raw upstream launchers do not participate in this advisory protocol.
    """
    vm = Path(vm).resolve(strict=True)
    with ExitStack() as stack:
        descriptors = []

        def acquire(path, *, create, description):
            flags = os.O_RDWR | os.O_CLOEXEC
            if create:
                flags |= os.O_CREAT | os.O_NOFOLLOW
            fd = os.open(path, flags, 0o600)
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f'{description} is already leased by another process: {path}') from error
            descriptors.append(fd)

        acquire(vm / '.tungtungtungbrowser.lock', create=True, description='VM bundle')
        # Different bundle directories may contain hard links or symlinks to
        # one disk. Lock the real inode as well as the bundle's own lock file.
        for name in ('Disk.img', 'nvram.bin', 'SEPStorage'):
            path = vm / name
            if name == 'SEPStorage' and not path.exists():
                continue
            acquire(path, create=False, description='Writable guest disk')
        if registry is not None:
            if type(cid) is not int or not 3 <= cid < 2**32 - 1:
                raise ValueError('A CID lease requires a guest CID between 3 and 4294967294')
            directory = Path(registry).resolve(strict=True)
            acquire(directory / f'cid-{cid}.lock', create=True, description=f'Guest CID {cid}')
        yield tuple(descriptors)
