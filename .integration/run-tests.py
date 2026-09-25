#!/usr/bin/env python3
"""Run the existing PR suites sequentially and keep every real result.
Only uniquely named disposable apps/profiles are used. No installed browser is
closed, no personal session is imported, and no default-browser setting changes.
"""
import datetime
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('SEARCH_TEST_OUTPUT', tempfile.mkdtemp(prefix='search-ci-results-')))
OUT.mkdir(parents=True, exist_ok=True)
BENCH = runpy.run_path(str(ROOT / 'bench'))
RESULTS = []
START = time.time()

def execute(name, args, timeout=300):
    path = OUT / (name + '.log')
    began = time.monotonic()
    print('Running ' + name, flush=True)
    with path.open('w') as log:
        process = subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            code = process.wait(timeout=timeout)
            status = 'passed' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code, status = 124, 'timeout'
    result = dict(name=name, status=status, exit_code=code,
                  seconds=round(time.monotonic()-began, 2), log=path.name)
    RESULTS.append(result)
    print(json.dumps(result), flush=True)
    if code:
        print('\n'.join(path.read_text(errors='replace').splitlines()[-18:]), flush=True)
    return code

def external_suite(name):
    world = 'ci-' + uuid.uuid4().hex[:10]
    directory = Path(tempfile.mkdtemp(prefix='search-ci-owned-'))
    profile = Path(BENCH['folder'](world))
    if profile.exists():
        raise RuntimeError('Refusing existing profile: ' + str(profile))
    profile.mkdir(parents=True)
    app = directory / 'Search CI.app'
    (app / 'Contents/MacOS').mkdir(parents=True)
    shutil.copy2(ROOT / '.build/release/Search', app / 'Contents/MacOS/Search')
    info = dict(CFBundleIdentifier='tech.noras.search.ci.' + uuid.uuid4().hex,
                CFBundleName='Search CI', CFBundleExecutable='Search',
                CFBundlePackageType='APPL', NSPrincipalClass='NSApplication',
                LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True,
                NSAppTransportSecurity={'NSAllowsArbitraryLoads': True})
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--sign', '-', '--entitlements', str(ROOT / 'Search.entitlements'), str(app)], check=True)
    prefs = directory / 'prefs.plist'
    prefs.write_bytes(plistlib.dumps({'bench': True, 'welcomed': True, 'spaces': True,
                                     'links.mini': False,
                                     'update.checked': datetime.datetime.now()}))
    subprocess.run(['defaults', 'import', 'com.officecommun.search.test.' + world, str(prefs)], check=True)
    log = (OUT / (name + '-app.log')).open('w')
    process = subprocess.Popen([str(app / 'Contents/MacOS/Search')], stdout=log, stderr=log,
                               env={**os.environ, 'SEARCH_PROBE': world, 'SEARCH_MEASURE': '1'},
                               start_new_session=True)
    try:
        deadline = time.monotonic() + 35
        ready = False
        while time.monotonic() < deadline and process.poll() is None:
            try:
                BENCH['ask'](str(profile / 'bench.sock'), {'do': 'tabs'})
                ready = True
                break
            except (OSError, ValueError, SystemExit):
                time.sleep(.2)
        if not ready:
            raise RuntimeError('Isolated test app did not open its bench socket')
        execute(name, ['tests/' + name + '.py', '--world', world], timeout=360)
    except Exception as error:
        RESULTS.append(dict(name=name, status='harness-error', error=str(error)))
        print(name + ': ' + str(error), flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log.close()

def main():
    suites = [
        ('tab-groups', []), ('native-tab-groups', []),
        ('tab-shortcuts', []),
        ('tab-shortcuts-release', ['tests/tab-shortcuts.py', '--smoke', '--binary', '.build/release/Search']),
        ('previews', []), ('preview-external', []),
        ('preview-extensions', ['tests/preview-extensions.py', '--binary', '.build/release/Search']),
        ('compact-bars', []), ('chrome-page-accent', []),
        ('chrome-blur', []), ('chrome-blur-opacity', []),
    ]
    for name, args in suites:
        execute(name, args or ['tests/' + name + '.py'])
    for name in ['extension-shortcuts', 'offscreen', 'background-tabs', 'float-video']:
        external_suite(name)
    # Retain the actual structured reports produced by the source suites.
    reports = OUT / 'reports'
    reports.mkdir(exist_ok=True)
    for directory in Path(tempfile.gettempdir()).glob('search-*'):
        if not directory.is_dir() or directory == OUT or directory.stat().st_mtime < START - 2:
            continue
        for path in directory.glob('*.json'):
            try:
                data = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            if isinstance(data, dict) and ('checks' in data or 'ok' in data):
                shutil.copy2(path, reports / (directory.name + '-' + path.name))
    summary = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'suites': RESULTS, 'passed': sum(r['status'] == 'passed' for r in RESULTS),
               'total': len(RESULTS), 'all_passed': all(r['status'] == 'passed' for r in RESULTS)}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary['all_passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
