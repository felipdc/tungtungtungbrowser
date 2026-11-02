# Restore transport workaround

`usbmuxd_send_fix.c` normalizes positive error returns from `usbmuxd_send`
to negative errno. It is preloaded only by the host firmware creation script.
It does not modify Safari, WebKit or the iOS guest.

On the Debian research host, the cloudOS restore reported `Status: Restore
Finished` after unmounting and sealing its filesystems, then spun in the
final `ReceivedFinalStatusMsg` send. A debugger stack showed
`socket_send` → `usbmuxd_send` → `idevice_connection_send`. The
[libusbmuxd implementation](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.0/src/libusbmuxd.c)
negates an already negative socket error; the
[libimobiledevice caller](https://github.com/libimobiledevice/libimobiledevice/blob/1.3.0/src/idevice.c)
recognizes only negative errors and otherwise repeats with zero bytes sent.

The shim preserves successful and already negative returns. Its native check
first sends real bytes through a socket pair, then closes the receiving end
and requires a negative error with zero bytes sent. Run both against the
installed library and with `LD_PRELOAD` to reproduce the difference. A full
iOS 26.6.2 restore completed with the shim enabled on 2026-11-02 at 16:08 UTC;
the host returned `DONE` and resumed at CFW installation. The socket-pair
check separately verifies the disconnect-error path.
