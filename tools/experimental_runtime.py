"""Launch a verified experimental candidate without touching existing processes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from tools.experimental_snapshot import PYTHON, verify


BOOT = '''import sys, socket, uvicorn
from pathlib import Path
from app.experimental import create_experimental_app
app = create_experimental_app(state_directory=Path(sys.argv[1]), build_commit=sys.argv[2])
server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning"))
server.run(sockets=[socket.socket(fileno=int(sys.argv[3]))])
'''
MODE = 'EXPERIMENTAL / PAPER ONLY'


def private_directory(path):
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError('absolute non-symlink runtime directory required')
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('runtime directory must be owned and private')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("runtime redirects are forbidden")


def http_gate(port, commit):
    if type(port) is not int or port == 8000 or not 1 <= port <= 65535:
        raise ValueError('non-production port required')
    # Never inherit proxy configuration for local requests.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for route in ('/health', '/api/today', '/', '/performance'):
        with opener.open(f'http://127.0.0.1:{port}{route}', timeout=2) as response:
            body = response.read(1024 * 1024).decode('utf-8')
            if response.status != 200:
                raise ValueError('HTTP status gate failed')
        if route in ('/health', '/api/today'):
            value = json.loads(body)
            if (value.get('build_commit') != commit or value.get('mode') != MODE
                    or value.get('empirical_validation') != 'NOT YET VALIDATED'):
                raise ValueError('HTTP identity gate failed')
            if route == '/health' and value.get('status') != 'healthy':
                raise ValueError('HTTP health gate failed')
        elif commit not in body or MODE not in body:
            raise ValueError('HTML identity gate failed')


def current_status(runtime_root: Path):
    """Observe reachability in this network context, never infer it from a PID.

    A successful promotion receipt is historical, not a liveness guarantee.
    This check never mutates the pointer, starts a process, or signals a PID.
    """
    result = {'mode': MODE, 'available': False,
              'empirical_validation': 'NOT YET VALIDATED',
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'current network context'}
    try:
        private_directory(runtime_root)
        pointer = runtime_root / 'current.json'
        if pointer.is_symlink() or not pointer.is_file():
            raise ValueError('missing or unsafe runtime pointer')
        record = json.loads(pointer.read_text())
        if not isinstance(record, dict):
            raise ValueError('invalid runtime record')
        commit = record.get('commit')
        url = record.get('url')
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', url or '')
        if (record.get('schema') != 'experimental-runtime-v1'
                or record.get('mode') != MODE or record.get('http_gate') != 'passed'
                or not isinstance(commit, str)
                or re.fullmatch('[0-9a-f]{40}', commit) is None or match is None):
            raise ValueError('invalid runtime record')
        port = int(match.group(1))
        if port == 8000 or not 1 <= port <= 65535:
            raise ValueError('non-production port required')
    except (OSError, ValueError, TypeError):
        return {**result, 'reason': 'MISSING_OR_INVALID_RUNTIME_RECORD'}
    try:
        http_gate(port, commit)
    except (OSError, ValueError, TypeError, AttributeError):
        return {**result, 'reason': 'HTTP_GATE_UNAVAILABLE_OR_MISMATCHED'}
    return {**result, 'available': True, 'reason': 'HTTP_GATE_PASSED',
            'build_commit': commit, 'url': url}


def launch(repository: Path, candidate: Path, runtime_root: Path, port: int = 0):
    if type(port) is not int or port == 8000 or not 0 <= port <= 65535:
        raise ValueError('non-production port required')
    private_directory(runtime_root)
    receipt = verify(repository, candidate)
    # Reserve before spawning: never probe or reuse another process's listener.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    process = None
    promoted = False
    try:
        listener.bind(('127.0.0.1', port))
        actual_port = listener.getsockname()[1]
        if actual_port == 8000:
            raise ValueError('reserved production port')
        listener.listen(128)
        run = Path(tempfile.mkdtemp(prefix='run-', dir=runtime_root))
        state = run / 'state'
        state.mkdir(mode=0o700)
        # Fresh empty state only; no production/configuration copying.
        environment = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
                       'TMPDIR': str(run)}
        with (run / 'runtime.log').open('xb') as log:
            process = subprocess.Popen(
                [PYTHON, '-c', BOOT, str(state), receipt['commit'], str(listener.fileno())],
                cwd=candidate / 'source', env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, pass_fds=(listener.fileno(),),
                start_new_session=True,
            )
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise ValueError('candidate runtime exited before promotion')
            try:
                http_gate(actual_port, receipt['commit'])
                break
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise ValueError('candidate HTTP gate timed out') from None
                time.sleep(0.1)
        verify(repository, candidate)
        if process.poll() is not None:
            raise ValueError('candidate runtime exited during gate')
        record = {'schema': 'experimental-runtime-v1', 'commit': receipt['commit'],
                  'candidate': str(candidate), 'state': str(state), 'pid': process.pid,
                  'url': f'http://127.0.0.1:{actual_port}', 'mode': MODE,
                  'http_gate': 'passed', 'started_at': datetime.now(timezone.utc).isoformat()}
        encoded = json.dumps(record, indent=2) + '\n'
        (run / 'runtime.json').write_text(encoded)
        # Unique temporary file; failure never truncates the previous pointer.
        pointer = run / 'current.tmp'
        pointer.write_text(encoded)
        pointer.replace(runtime_root / 'current.json')
        promoted = True
        return record
    finally:
        listener.close()
        if process is not None and not promoted and process.poll() is None:
            # Only the child created in this attempt may be terminated.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('runtime_root', type=Path)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(launch(args.repository, args.candidate, args.runtime_root, args.port)))
