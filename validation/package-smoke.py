#!/usr/bin/env python3
"""CI-only validation of the downloaded app in normal, non-probe operation.
A saved local page reports its real DOM/navigation back to our local HTTP server.
The app's user-consent gate for scripted control is neither enabled nor bypassed.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OBSERVATIONS = []
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<!doctype html><title>Personal package check</title>
<meta name="theme-color" content="#357953"><h1 id="check">Local WebKit works</h1><input id="input">
<script>
const first = location.pathname === '/first';
if (first) { document.querySelector('#input').value = 'kept'; localStorage.setItem('package-check', 'kept'); }
requestAnimationFrame(() => {
  fetch('/record', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    path:location.pathname, text:document.querySelector('#check').textContent,
    width:document.querySelector('#check').getBoundingClientRect().width,
    form:document.querySelector('#input').value, stored:localStorage.getItem('package-check')
  })}).then(() => { if (first) setTimeout(() => location.href='/next', 250); });
});
</script>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if self.path != '/record' or not 0 < length < 65536:
            self.send_error(400)
            return
        OBSERVATIONS.append(json.loads(self.rfile.read(length)))
        self.send_response(204)
        self.end_headers()
    def log_message(self, *_):
        pass

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
              'test_mode': False, 'scripted_control': False, 'checks': []}
    def check(value, name):
        report['checks'].append({'ok': bool(value), 'message': name})
        print(('PASS ' if value else 'FAIL ') + name, flush=True)
        if not value:
            raise AssertionError(name)
    support = Path.home() / 'Library/Application Support'
    personal, old, legacy = (support / n for n in ('Search Personal', 'Search', 'Office Browser'))
    process = server = log = None
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
        prefs_path.write_bytes(plistlib.dumps({'bench': False, 'welcomed': True,
            'update.checked': datetime.datetime(2001, 1, 1), 'update.install': True}))
        subprocess.run(['defaults', 'import', identifier, str(prefs_path)], check=True)
        before_update = subprocess.check_output(['defaults', 'read', identifier, 'update.checked'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        personal.mkdir(parents=True)
        (personal / 'session.json').write_text(json.dumps({'tabs': [
            {'url': f'http://127.0.0.1:{server.server_port}/first', 'title': 'Local validation'}], 'active': 0}))
        env = {k: v for k, v in os.environ.items() if k not in ('SEARCH_PROBE', 'SEARCH_MEASURE', 'SEARCH_FEED')}
        log = (args.output / 'application.log').open('w')
        process = subprocess.Popen([str(binary)], env=env, stdout=log, stderr=log)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None:
            if any(row.get('path') == '/next' for row in OBSERVATIONS):
                break
            time.sleep(.2)
        check(process.poll() is None, 'normal production application stays running')
        first = next((row for row in OBSERVATIONS if row.get('path') == '/first'), {})
        second = next((row for row in OBSERVATIONS if row.get('path') == '/next'), {})
        check(first.get('text') == 'Local WebKit works' and first.get('width', 0) > 0,
              'normal app restores its personal session and renders a real webpage')
        check(first.get('form') == 'kept', 'real page form state is retained')
        check(second.get('text') == 'Local WebKit works', 'normal app navigates to the second real page')
        check(second.get('stored') == 'kept', 'website storage survives navigation')
        check(subprocess.check_output(['defaults', 'read', identifier, 'update.checked']) == before_update,
              'startup does not run the overdue upstream update check')
        check(all(Path(p).read_bytes() == data for p, data in snapshots.items()),
              'original and legacy browser data remain unchanged')
        check(not any((p / 'bench.sock').exists() for p in (personal, old, legacy)),
              'normal app does not enable scripted control without user consent')
        report['ok'] = True
    except BaseException as error:
        report.update(ok=False, error=str(error))
        if process and process.poll() is None:
            try:
                subprocess.run(['sample', str(process.pid), '1', '-file', str(args.output / 'owned-process-sample.txt')], timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
    finally:
        report['observations'] = OBSERVATIONS
        if process:
            report['process_returncode_before_cleanup'] = process.poll()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if server:
            server.shutdown()
            server.server_close()
        if log:
            log.close()
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
