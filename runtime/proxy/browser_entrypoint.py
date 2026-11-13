#!/usr/bin/env python3
"""Loopback HTTP relay for Safari; other guest traffic keeps normal routing."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess


def configuration(secret):
    address = str(ipaddress.IPv4Address(secret['address']))
    port = secret['port']
    if type(port) is not int or not 1 <= port <= 65535 or secret['scheme'] not in {'http', 'https', 'socks5'}:
        raise ValueError('Unsupported proxy protocol or port')
    for key in ('username', 'password'):
        if not isinstance(secret[key], str) or any(ord(c) < 32 for c in secret[key]):
            raise ValueError('Invalid proxy credential')
    outbound = {'type': 'socks' if secret['scheme'] == 'socks5' else 'http',
                'tag': 'upstream', 'server': address, 'server_port': port,
                'username': secret['username'], 'password': secret['password']}
    if secret['scheme'] == 'socks5':
        outbound['version'] = '5'
    if secret['scheme'] == 'https':
        if not isinstance(secret.get('host'), str) or not secret['host']:
            raise ValueError('HTTPS proxy requires its TLS server name')
        outbound['tls'] = {'enabled': True, 'server_name': secret['host']}
    return {
        'log': {'level': 'warn', 'timestamp': True},
        'inbounds': [{'type': 'http', 'tag': 'safari', 'listen': '127.0.0.1', 'listen_port': 1080}],
        'outbounds': [outbound],
        'route': {'final': 'upstream'},
    }


def main():
    config = configuration(json.loads(Path('/run/secrets/proxy.json').read_text()))
    path = Path('/run/browser-proxy-config.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream)
    subprocess.run(['sing-box', 'check', '-c', str(path)], check=True)
    print(json.dumps({'scope': 'safari-http-https', 'guest_proxy': 'http://10.0.2.2:1080',
                      'other_traffic': 'direct', 'guest_preferences_required': True}), flush=True)
    os.execvp('sing-box', ['sing-box', 'run', '-c', str(path)])


if __name__ == '__main__':
    main()
