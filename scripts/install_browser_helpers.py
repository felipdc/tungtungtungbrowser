#!/usr/bin/env python3
"""Install a verified helper bundle once and write an experimental API profile."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest
import install_touch_helper as touch
import install_keyboard_helper as keyboard
import install_focused_element as focus
import install_inspector_bridge as bridge


def install(build_directory, output, *, cid=3):
    if type(cid) is not int or not 3 <= cid < 2**32 - 1:
        raise ValueError('Invalid guest CID')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the repository')
    # Check every local binary before the first guest mutation.
    builds = [(name, module, module.read_build(Path(build_directory) / source))
              for name, source, module in (
                  ('touch', 'touch_helper', touch), ('keyboard', 'keyboard_helper', keyboard),
                  ('focus', 'focused_element', focus), ('bridge', 'inspector_bridge', bridge))]
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'version': 1, 'name': 'api-experimental', 'status': 'installing', 'cid': cid,
              'completed': [], 'native_input_sent': False}

    def save():
        (output / 'profile.json').write_text(json.dumps(report, indent=2) + '\n')

    save()
    try:
        for name, module, (data, digest) in builds:
            report['installing'] = name
            save()
            if name == 'bridge':
                module.install(Guest(cid=cid, timeout=bridge.RPC_TIMEOUT_SECONDS), data, digest,
                               output / name, lambda: asyncio.run(bridge.health(cid)), launch_mode='process')
            else:
                module.install(Guest(cid=cid, timeout=60), data, digest, output / name)
            report['completed'].append(name)
        report['status'] = 'complete'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args()
    try:
        result = install(args.build_directory, args.output, cid=args.cid)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'Installation stopped: {error}. Evidence and guest files retained; do not replay an uncertain installation.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
