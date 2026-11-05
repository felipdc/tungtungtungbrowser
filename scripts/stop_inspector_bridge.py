#!/usr/bin/env python3
"""Stop an owned Inspector bridge using its recorded process and guest identity."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.inspector_transport import InspectorTransport


async def stop(deployment, output, *, cid=3, bridge_socket=None, timeout=5):
    deployment, output = Path(deployment), Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the repository')
    if deployment.stat().st_size > 512 * 1024:
        raise ValueError('Deployment record exceeds 512 KiB')
    record = json.loads(deployment.read_text())
    if record.get('status') != 'complete':
        raise ValueError('A verified deployment record is required')
    expected = record['health']
    if (not isinstance(expected, dict) or expected.get('protocol') != 2 or expected.get('status') != 'ready'
            or type(expected.get('pid')) is not int or not 1 < expected['pid'] <= 2147483647
            or expected.get('port') != 1343 or not isinstance(expected.get('boot_uuid'), str)
            or str(uuid.UUID(expected['boot_uuid'])).lower() != expected['boot_uuid'].lower()):
        raise ValueError('Invalid deployment process identity')
    report = {'status': 'checking', 'identity': expected, 'stop_requested': False}
    # Claim the result file before any request. An uncertain stop is retained,
    # and this invocation never repeats it or signals a PID directly.
    with output.open('x') as stream:
        json.dump(report, stream, indent=2)

    def save():
        output.write_text(json.dumps(report, indent=2) + '\n')

    async def exchange(command, status):
        transport = InspectorTransport(cid=cid, timeout=timeout, bridge_socket=bridge_socket)
        transport.identity = expected
        try:
            async with asyncio.timeout(timeout):
                sock = await transport._bridge_socket()
                try:
                    transport.reader, transport.writer = await asyncio.open_connection(sock=sock, limit=1024)
                except BaseException:
                    sock.close()
                    raise
                return await transport._control(command, status)
        finally:
            await transport.close()

    try:
        report['health'] = await exchange('health', 'ready')
        report['stop_requested'] = True
        save()
        # The bridge checks this identity before stopping. Checking a previous
        # health response alone would leave a replacement-process race.
        command = 'stop ' + str(expected['pid']) + ' ' + expected['boot_uuid']
        report['response'] = await exchange(command, 'stopping')
        report['status'] = 'stop_acknowledged'
        return report
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('deployment', type=Path, help='Verified deploy.json from this guest boot')
    parser.add_argument('--output', type=Path, required=True, help='New external JSON evidence file')
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args()
    try:
        result = asyncio.run(stop(args.deployment, args.output, cid=args.cid))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'Inspector stop failed: {error}. No automatic retry was made.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
