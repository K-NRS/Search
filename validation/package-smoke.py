#!/usr/bin/env python3
"""CI-only smoke of the exact downloadable app, without a test-mode environment."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Personal package check</title><meta name="theme-color" content="#357953"><h1 id="check">Local WebKit works</h1><input id="input"><a href="/next">Next</a>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass

def ask(path, verb, **fields):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(20)
        s.connect(str(path))
        s.sendall((json.dumps({'do': verb, **fields}) + '\n').encode())
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
        result = json.loads(b''.join(chunks).split(b'\n')[0])
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result

def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('This fixture runs only on a disposable GitHub Actions machine.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = args.app.resolve()
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    binary = app / 'Contents/MacOS/Search'
    report = {'source_commit': info.get('SearchIntegrationCommit'),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'test_mode': False, 'checks': []}
    def check(value, name):
        report['checks'].append({'ok': bool(value), 'message': name})
        print(('PASS ' if value else 'FAIL ') + name, flush=True)
        if not value:
            raise AssertionError(name)
    support = Path.home() / 'Library/Application Support'
    personal = support / 'Search Personal'
    old = support / 'Search'
    legacy = support / 'Office Browser'
    owned_process = None
    server = None
    log = None
    try:
        check(info['CFBundleIdentifier'] == 'tech.noras.search.personal', 'distinct production bundle ID')
        check(info.get('SearchProfileName') == 'Search Personal', 'distinct production profile configured')
        check(info.get('SearchDisableUpdates') is True, 'upstream updater disabled in packaged app')
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        check(True, 'downloaded application signature verifies')
        subprocess.run(['lipo', str(binary), '-verify_arch', 'arm64', 'x86_64'], check=True)
        check(True, 'downloaded executable contains both Mac architectures')
        check(not any(p.exists() for p in (personal, old, legacy)), 'all fixture profile paths are fresh')
        snapshots = {}
        for path in (old, legacy):
            path.mkdir(parents=True)
            sentinel = path / 'do-not-change.txt'
            sentinel.write_text('Synthetic original-browser data. Preserve exactly.\n')
            snapshots[str(sentinel)] = sentinel.read_bytes()
        identifier = info['CFBundleIdentifier']
        prefs_path = args.output / 'fixture-preferences.plist'
        prefs_path.write_bytes(plistlib.dumps({'bench': True, 'welcomed': True,
            'update.checked': datetime.datetime(2001, 1, 1), 'update.install': True}))
        subprocess.run(['defaults', 'import', identifier, str(prefs_path)], check=True)
        before_update = subprocess.check_output(['defaults', 'read', identifier, 'update.checked'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {k: v for k, v in os.environ.items() if k not in ('SEARCH_PROBE', 'SEARCH_MEASURE', 'SEARCH_FEED')}
        log = (args.output / 'application.log').open('w')
        owned_process = subprocess.Popen([str(binary)], env=env, stdout=log, stderr=log)
        socket_path = personal / 'bench.sock'
        deadline = time.monotonic() + 40
        ready = False
        while time.monotonic() < deadline and owned_process.poll() is None:
            try:
                ask(socket_path, 'tabs')
                ready = True
                break
            except (OSError, ValueError, RuntimeError):
                time.sleep(.2)
        check(ready, 'real production app launches and listens only in its personal profile')
        opened = ask(socket_path, 'open', url=f'http://127.0.0.1:{server.server_port}/first')
        tab = opened['id']
        ask(socket_path, 'wait', id=tab, seconds=20)
        value = ask(socket_path, 'eval', id=tab, js='document.querySelector("#check").textContent').get('value')
        check(value == 'Local WebKit works', 'packaged release renders and evaluates a real local webpage')
        value = ask(socket_path, 'eval', id=tab, js='document.querySelector("#input").value="kept"; document.querySelector("#input").value').get('value')
        check(value == 'kept', 'packaged release retains live form state')
        ask(socket_path, 'go', id=tab, url=f'http://127.0.0.1:{server.server_port}/next')
        ask(socket_path, 'wait', id=tab, seconds=20)
        value = ask(socket_path, 'eval', id=tab, js='location.pathname').get('value')
        check(value == '/next', 'packaged release navigates successfully')
        ask(socket_path, 'close', id=tab)
        check(all(t['id'] != tab for t in ask(socket_path, 'tabs')['tabs']), 'packaged release closes the owned tab')
        check(subprocess.check_output(['defaults', 'read', identifier, 'update.checked']) == before_update,
              'startup does not run the overdue upstream update check')
        check(all(Path(p).read_bytes() == contents for p, contents in snapshots.items()),
              'original and legacy browser data remain unchanged')
        check(not (old / 'bench.sock').exists() and not (legacy / 'bench.sock').exists(),
              'production automation never uses the original browser socket')
        report['ok'] = True
    except BaseException as error:
        report.update(ok=False, error=str(error))
        raise
    finally:
        if owned_process and owned_process.poll() is None:
            owned_process.terminate()
            try:
                owned_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                owned_process.kill()
                owned_process.wait()
        if server:
            server.shutdown()
            server.server_close()
        if log:
            log.close()
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
