# Third-party notices

The root MIT license covers this project's original Python, shell, C and
diagnostic-page code and documentation. It does not replace the licenses below.
No emulator, renderer, proxy executable, firmware or prepared VM is committed.

| Component | Relationship | License source |
| --- | --- | --- |
| vphone-linux | Downloaded runtime glue; USB and setup patches modify its files | MIT; copyright 2026 Yahia, retained in [LICENSES/MIT-vphone-linux.txt](LICENSES/MIT-vphone-linux.txt) |
| qemu-vphone `a13_gxf.c` | `patches/qemu-sprr-user-mask.patch` modifies this file | AGPL-3.0-or-later; copyright 2023–2026 Visual Ehrmanntraut and Christian Inci. [License text](LICENSES/AGPL-3.0-or-later.txt) and [target source](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/hw/arm/apple-silicon/a13_gxf.c) |
| Combined QEMU runtime | Built separately and mounted into the container | Mixed upstream GPL/AGPL licenses; consult the pinned checkout's `LICENSE` and `COPYING*` |
| reims-vgpu and metal2vulkan | Linked by the separately built emulator | Upstream GPL-2.0-or-later / LGPL-3.0-or-later alternatives; see [runtime licensing](https://github.com/YahiaAngelo/vphone-linux/blob/888d17ca17268aaecd277c64f2d95385b184744b/docs/LICENSING.md) |
| vphone-cli and Icli | Upstream native guest tooling | Their upstream license files govern the downloaded release and source |
| Moby syscall profile | `runtime/seccomp-vsock.json` derives from the Moby profile | Apache-2.0, retained in [runtime/LICENSE.moby-profiles](runtime/LICENSE.moby-profiles) |
| sing-box | Downloaded by the optional proxy Docker build | GPL-3.0-or-later; [upstream license](https://github.com/SagerNet/sing-box/blob/v1.14.4/LICENSE) |
| Firmware and guest assets | Obtained separately by the user's setup tools | Their respective vendor licenses; not covered by this project's license |

Binary distribution has obligations separate from distributing this source
repository. Preserve the exact dependency revisions, modifications, notices
and corresponding source required by the dependencies you distribute. Do not
label the combined emulator image as MIT-only.
