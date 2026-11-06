# Third-party notices

The root MIT license covers this project's original Python, shell, C and
diagnostic-page code and documentation. It does not replace the licenses below.
No emulator, renderer, proxy executable, firmware or prepared VM is committed.

| Component | Relationship | License source |
| --- | --- | --- |
| pymobiledevice3 | Optional Python Inspector protocol dependency, installed separately at commit `776d601cec83b894c3ad0eeee8ab4db375a46e4a`; no upstream source is vendored | GPL-3.0; [pinned upstream license](https://github.com/doronz88/pymobiledevice3/blob/776d601cec83b894c3ad0eeee8ab4db375a46e4a/LICENSE) |
| Pillow | Optional Python dependency for native selector-highlight image analysis; installed separately, no source vendored | MIT-CMU; [upstream license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| vphone-linux | Downloaded runtime glue; USB and setup patches modify its files | MIT; copyright 2026 Yahia, retained in [LICENSES/MIT-vphone-linux.txt](LICENSES/MIT-vphone-linux.txt) |
| qemu-vphone `a13_gxf.c` | `patches/qemu-sprr-user-mask.patch` modifies this file | AGPL-3.0-or-later; copyright 2023–2026 Visual Ehrmanntraut and Christian Inci. [License text](LICENSES/AGPL-3.0-or-later.txt) and [target source](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/hw/arm/apple-silicon/a13_gxf.c) |
| Combined QEMU runtime | Built separately and mounted into the container | Mixed upstream GPL/AGPL licenses; consult the pinned checkout's `LICENSE` and `COPYING*` |
| QEMU display diagnostics | Optional `patches/qemu-display-trace.patch` and `patches/qemu-display-refresh-toggle.patch` modify the Apple graphics implementation and shared header | [GPL-2.0-or-later](LICENSES/GPL-2.0-or-later.txt); `apple-gfx.c` retains copyright 2023 Amazon.com, Inc. or its affiliates. The combined fork's license still applies. |
| QEMU native FP boundary experiment | Optional `patches/qemu-native-fp-boundary.patch`; see [evaluation](docs/native-fp.md) | `accel/tcg/cpu-exec.c` retains LGPL-2.1-or-later and Fabrice Bellard's notice. `include/accel/tcg/cpu-ops.h`, `target/arm/cpu.c` and `target/arm/tcg/host-fpcr.h` retain GPL-2.0-or-later, SUSE notices and the upstream Inferno-port attribution. License texts are in `LICENSES/`; the combined fork's license still applies. |
| QEMU ARM WFE experiment | Optional `patches/qemu-wfe-backoff.patch` modifies ARM CPU and translator files | `cpu.c`, `tcg/helper.h` and the header without a file-specific notice use [GPL-2.0-or-later](LICENSES/GPL-2.0-or-later.txt); `cpu.h`, `tcg/op_helper.c` and `tcg/translate-a64.c` retain [LGPL-2.1-or-later](LICENSES/LGPL-2.1-or-later.txt). Upstream notices credit SUSE LINUX Products GmbH, Fabrice Bellard, CodeSourcery, LLC and Alexander Graf. The combined fork's [license notice](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/LICENSE) still applies. |
| reims-vgpu and metal2vulkan | Linked by the separately built emulator | Upstream GPL-2.0-or-later / LGPL-3.0-or-later alternatives; see [runtime licensing](https://github.com/YahiaAngelo/vphone-linux/blob/888d17ca17268aaecd277c64f2d95385b184744b/docs/LICENSING.md) |
| metal2vulkan placement logging diagnostic | Optional `patches/metal2vulkan-log-all-placements.patch` modifies `memory.rs` and `memory_log.rs` | Distributed under the upstream [GPL-2.0-or-later](LICENSES/GPL-2.0-or-later.txt) option; the [pinned dual-license notice](https://github.com/YahiaAngelo/metal2vulkan/blob/cd1a08ae5442065a11af0fb79d1bdc35c1f0e1b9/LICENSE) also offers LGPL-3.0-or-later. The original analyzer is covered by this repository's MIT license. |
| vphone-cli and Icli | Upstream native guest tooling | Their upstream license files govern the downloaded release and source |
| Moby syscall profile | `runtime/seccomp-vsock.json` derives from the Moby profile | Apache-2.0, retained in [runtime/LICENSE.moby-profiles](runtime/LICENSE.moby-profiles) |
| Mesa llvmpipe `lp_texture.c` | Optional `patches/lavapipe-release-backing.patch` modifies this file | MIT-style license; copyright 2006 VMware, retained in [LICENSES/MIT-Mesa-lp_texture.txt](LICENSES/MIT-Mesa-lp_texture.txt). Other downloaded Mesa files retain their own notices. |
| sing-box | Downloaded by the optional proxy Docker build | GPL-3.0-or-later; [upstream license](https://github.com/SagerNet/sing-box/blob/v1.14.4/LICENSE) |
| Firmware and guest assets | Obtained separately by the user's setup tools | Their respective vendor licenses; not covered by this project's license |

Binary distribution has obligations separate from distributing this source
repository. Preserve the exact dependency revisions, modifications, notices
and corresponding source required by the dependencies you distribute. Do not
label the combined emulator image as MIT-only.
