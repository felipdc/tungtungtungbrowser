# Native control syscall profile

`seccomp-vsock.json` is the Moby default profile at commit
[`6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef`](https://github.com/moby/profiles/blob/6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef/seccomp/default.json),
with one appended allow rule for `socket(AF_VSOCK, SOCK_STREAM, 0)`.
The rule permits the usual socket flags, including `SOCK_CLOEXEC` used by
Python. Datagram VSOCK sockets remain outside this exception.
The upstream license is in `LICENSE.moby-profiles`.

The default Docker profile refuses AF_VSOCK with EPERM. Native vphoned
control requires this socket family. The pinned QEMU build does not contain
the optional `virtio-vsock-uds-pci` device mentioned in upstream documentation.

The runtime retains dropped capabilities, no-new-privileges, a read-only
root filesystem, and only `/dev/vhost-vsock` passthrough. There is no GPU
passthrough, privileged mode or host networking. VSOCK CIDs are host-wide;
use a dedicated host and allocate a distinct CID to each future concurrent
guest. This runtime has not been evaluated for hostile multiple-tenant use.

## Display capture

The optional PNG backend connects to the VM bundle's `control.sock`. QMP grants
VM administration access, so restrict the bundle directory to trusted users.
The controller passes an anonymous, write-only file descriptor with
`SCM_RIGHTS`; QEMU writes raw PPM through `/dev/fdset`, and the controller removes
the descriptor set before closing the connection. The controller validates the
bounded raw frame and encodes PNG after releasing that connection. No shared output directory
or TCP QMP listener is needed. Capture uses the same Linux host as QEMU, either
inside its container or through the bind-mounted Unix socket on the host.
