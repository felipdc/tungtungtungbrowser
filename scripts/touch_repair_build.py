"""Build metadata and C recipe for the native cache installer."""
import hashlib
import json
from pathlib import Path

from prepare_touch_repair import CACHE_NAME, RECIPE, REPAIR_ID

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('native/touch_repair.c', 'native/touch_repair_darwin.h',
           'native/touch_repair_libSystem.tbd', 'native/touch_repair_entitlements.plist',
           'scripts/prepare_touch_repair.py', 'scripts/touch_repair_build.py',
           'scripts/build_touch_repair.sh')


def recipe_header(recipe=RECIPE, *, cache_directory='/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld',
                  backup_root='/private/var/root/ttt-touch-repair-v1'):
    """Paths/alternate recipes are for host tests; the build CLI has no overrides."""
    def string(value):
        return json.dumps(value, ensure_ascii=True)

    def array(value):
        return '{' + ','.join(f'0x{x:02x}' for x in value) + '}'

    lines = [f'#define REPAIR_SIZE {recipe.size}LL',
             f'static const char original_hash[] = {string(recipe.original_sha256)};',
             f'static const char repaired_hash[] = {string(recipe.repaired_sha256)};',
             f'static const char cache_directory[] = {string(cache_directory)};',
             f'static const char cache_path[] = {string(cache_directory + "/" + CACHE_NAME)};',
             f'static const char backup_root[] = {string(backup_root)};',
             f'static const char lock_path[] = {string(backup_root + ".lock")};',
             'struct RepairSpan { long long offset; unsigned long size; unsigned char original[32], repaired[32]; };',
             'static const struct RepairSpan repair_spans[] = {']
    for span in recipe.spans:
        if not 0 < len(span.original) == len(span.repaired) <= 32:
            raise ValueError('Unsupported native repair span')
        lines.append(f'    {{{span.offset}, {len(span.original)}, {array(span.original)}, {array(span.repaired)}}},')
    return '\n'.join(lines + ['};', ''])


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    import sys
    action, directory, *tools = sys.argv[1:]
    directory = Path(directory)
    if directory.resolve().is_relative_to(ROOT):
        raise ValueError('Keep generated files outside the source checkout')
    if action == 'recipe' and not tools:
        with (directory / 'touch_repair_recipe.h').open('x') as output:
            output.write(recipe_header())
    elif action == 'manifest' and len(tools) == 3:
        binary = directory / 'touch-repair'
        report = {'protocol': 1, 'binary': binary.name, 'repair_id': REPAIR_ID,
                  'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                  'sources': source_hashes(), 'recipe_sha256': hashlib.sha256(recipe_header().encode()).hexdigest(),
                  'tools': dict(zip(('compiler', 'linker', 'signer'), tools)),
                  'installed': False, 'guest_cache_modified': False}
        with (directory / 'build.json').open('x') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps(report, indent=2))
    else:
        raise ValueError('Expected recipe DIRECTORY or manifest DIRECTORY COMPILER LINKER SIGNER')


if __name__ == '__main__':
    main()
