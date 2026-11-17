"""Select Mesa's CPU Vulkan driver for the native host architecture."""
from pathlib import Path
import platform


def select_manifest(directory=Path('/usr/share/vulkan/icd.d'), *, machine=None):
    machine = platform.machine() if machine is None else machine
    aliases = {'x86_64': ('x86_64',), 'aarch64': ('aarch64', 'arm64')}
    names = [f'lvp_icd.{arch}.json' for arch in aliases.get(machine, (machine,))]
    names.append('lvp_icd.json')
    for name in names:
        candidate = Path(directory) / name
        if candidate.is_file():
            return candidate
    raise RuntimeError(f'Mesa lavapipe for {machine} is required in {directory}; '
                       'hardware GPUs are not supported')


if __name__ == '__main__':
    try:
        print(select_manifest())
    except RuntimeError as error:
        raise SystemExit(str(error))
