"""Read-only WSGI routing consumer for protected host installation.

No listener or installation defaults. Host policy supplies exact public paths and
release ports; requests cannot select an upstream. Run behind the host's existing
authentication boundary. This component does not authorize trading mutations.
"""
import json
import os
from pathlib import Path
import stat
import time
from urllib.request import Request, build_opener, ProxyHandler

from tools.delivery_runner import SHA, acceptance, require
from tools.experimental_runtime import NoRedirect


class ReportingRouter:
    def __init__(self, pointer, ports, paths, verify_service, *, owner=0):
        self.pointer = Path(pointer)
        self.ports = dict(ports)
        self.paths = frozenset(paths)
        self.verify_service = verify_service
        self.owner = owner
        require(self.pointer.is_absolute() and self.pointer.parent.resolve() == self.pointer.parent,
                'unsafe routing pointer path')
        require(self.ports and all(isinstance(k, str) and SHA.fullmatch(k)
                and type(v) is int and 1024 <= v <= 65535 for k, v in self.ports.items())
                and len(set(self.ports.values())) == len(self.ports), 'invalid routing targets')
        require(self.paths and all(isinstance(p, str) and p.startswith('/')
                and not p.startswith('//') and all(32 < ord(c) < 127 for c in p)
                and not any(c in p for c in ('?', '#', '\\', '%'))
                and '..' not in p.split('/') for p in self.paths), 'invalid public routes')

    def _target(self):
        require(self.pointer.parent.resolve() == self.pointer.parent,
                'routing ancestor changed')
        # Protected ancestors prevent candidate writers replacing the pointer.
        for parent in self.pointer.parents:
            info = parent.stat()
            require(info.st_uid == self.owner and not info.st_mode & 0o022,
                    'unprotected routing ancestor')
        fd = os.open(self.pointer, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == self.owner
                    and not info.st_mode & 0o022 and info.st_size <= 4096,
                    'unsafe routing pointer')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(4097)
            require(len(raw) <= 4096, 'oversized routing pointer')
        finally:
            os.close(fd)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'duplicate routing field')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        require(isinstance(value, dict) and set(value) == {'commit', 'live_money'}
                and value['live_money'] is False and isinstance(value['commit'], str)
                and value['commit'] in self.ports, 'unapproved routing identity')
        return value['commit']

    def _fetch(self, sha, path):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        request = Request(f'http://127.0.0.1:{self.ports[sha]}{path}',
                          headers={'Accept-Encoding': 'identity'}, method='GET')
        deadline = time.monotonic() + 5
        with opener.open(request, timeout=2) as response:
            require(response.status == 200, 'upstream response rejected')
            chunks, size = [], 0
            while size <= 4 * 1024 * 1024:
                require(time.monotonic() < deadline, 'routing deadline exceeded')
                chunk = response.read1(min(65536, 4 * 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            require(time.monotonic() < deadline and size <= 4 * 1024 * 1024,
                    'routing response limit exceeded')
            content_type = response.headers.get('Content-Type', 'application/octet-stream')
            require(content_type and len(content_type) <= 256
                    and all(32 <= ord(c) < 127 for c in content_type), 'unsafe content type')
            return b''.join(chunks), content_type

    def __call__(self, environ, start_response):
        status, body, content_type = '503 Service Unavailable', b'Release unavailable\n', 'text/plain'
        try:
            require(environ.get('REQUEST_METHOD') in ('GET', 'HEAD'), 'read-only route')
            path = environ.get('PATH_INFO')
            require(path in self.paths and not environ.get('QUERY_STRING')
                    and environ.get('CONTENT_LENGTH', '') in ('', '0')
                    and not environ.get('HTTP_TRANSFER_ENCODING'), 'unapproved request')
            sha = self._target()
            acceptance(self.verify_service(sha), sha, require_sig=False)
            body, content_type = self._fetch(sha, path)
            # Never return a buffered response from a release removed during fetch.
            require(self._target() == sha, 'route changed during request')
            acceptance(self.verify_service(sha), sha, require_sig=False)
            status = '200 OK'
        except Exception:
            # Do not disclose upstream errors, host policy or paths to clients.
            body, content_type = b'Release unavailable\n', 'text/plain'
        start_response(status, [('Content-Type', content_type), ('Content-Length', str(len(body))),
                                ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')])
        return [b'' if environ.get('REQUEST_METHOD') == 'HEAD' else body]
