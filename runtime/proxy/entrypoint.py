#!/usr/bin/env python3
"""Fail-closed TCP proxy routing in a dedicated container network namespace."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess

MARK = '0x5454'
TUN = 'ttt0'


def run(*args):
    subprocess.run(args, check=True)


def main():
    secret = json.loads(Path('/run/secrets/proxy.json').read_text())
    address = str(ipaddress.IPv4Address(secret['address']))
    port = int(secret['port'])
    if not 1 <= port <= 65535 or secret['scheme'] not in {'http', 'https', 'socks5'}:
        raise ValueError('Unsupported proxy protocol or port')
    outbound = {'type': 'socks' if secret['scheme'] == 'socks5' else 'http',
                'tag': 'upstream', 'server': address, 'server_port': port,
                'username': secret['username'], 'password': secret['password'],
                'routing_mark': int(MARK, 16), 'bind_interface': 'eth0'}
    if secret['scheme'] == 'socks5':
        outbound['version'] = '5'
    if secret['scheme'] == 'https':
        outbound['tls'] = {'enabled': True, 'server_name': secret['host']}
    config = {
        'log': {'level': 'warn', 'timestamp': True},
        'dns': {'servers': [{'type': 'https', 'tag': 'remote-dns', 'server': '1.1.1.1',
                            'tls': {'enabled': True, 'server_name': 'cloudflare-dns.com'},
                            'detour': 'upstream'}], 'final': 'remote-dns', 'strategy': 'ipv4_only'},
        'inbounds': [{'type': 'tun', 'tag': 'device', 'interface_name': TUN,
                      'address': ['100.64.0.1/30'], 'mtu': 1500, 'auto_route': True,
                      'strict_route': True, 'dns_mode': 'disabled', 'stack': 'mixed',
                      'route_exclude_address': [address + '/32']}],
        'outbounds': [outbound],
        'route': {'rules': [{'port': 53, 'action': 'hijack-dns'},
                            {'network': 'udp', 'action': 'reject'}],
                  'final': 'upstream', 'default_domain_resolver': 'remote-dns'},
    }
    path = Path('/run/proxy-config.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream)
    run('sing-box', 'check', '-c', str(path))
    # Never modify host networking: this process runs in the proxy's namespace.
    # DROP precedes every rule change, including a restart in an existing namespace.
    for family in ('iptables', 'ip6tables'):
        for chain in ('INPUT', 'OUTPUT', 'FORWARD'):
            run(family, '-w', '-P', chain, 'DROP')
            run(family, '-w', '-F', chain)
        for chain, direction in (('INPUT', '-i'), ('OUTPUT', '-o')):
            # Docker's embedded resolver is deliberately unavailable to the guest.
            if family == 'iptables' and chain == 'OUTPUT':
                run(family, '-w', '-A', chain, '-d', '127.0.0.11/32', '-j', 'REJECT')
            run(family, '-w', '-A', chain, direction, 'lo', '-j', 'ACCEPT')
    run('iptables', '-w', '-A', 'INPUT', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT')
    run('iptables', '-w', '-A', 'INPUT', '-i', TUN, '-j', 'ACCEPT')
    run('iptables', '-w', '-A', 'OUTPUT', '-o', TUN, '-j', 'ACCEPT')
    run('iptables', '-w', '-A', 'OUTPUT', '-p', 'tcp', '-d', address + '/32', '--dport', str(port),
        '-m', 'mark', '--mark', MARK + '/0xffffffff', '-j', 'ACCEPT')
    # Replies from local diagnostic services remain local; no general direct-egress rule.
    subprocess.run(['ip', 'rule', 'del', 'pref', '100', 'fwmark', MARK, 'lookup', 'main'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run('ip', 'rule', 'add', 'pref', '100', 'fwmark', MARK, 'lookup', 'main')
    print(json.dumps({'proxy_protocol': secret['scheme'], 'country': secret.get('country'),
                      'direct_egress': False, 'dns': 'DoH through proxy', 'udp': 'blocked except DNS'}), flush=True)
    os.execvp('sing-box', ['sing-box', 'run', '-c', str(path)])


if __name__ == '__main__':
    main()
