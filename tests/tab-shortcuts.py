#!/usr/bin/env python3
"""Native tab-cycling acceptance in a uniquely identified disposable app/profile.

swift build && python3 tests/tab-shortcuts.py
swift build -c release && python3 tests/tab-shortcuts.py --smoke --binary .build/release/Search
The smoke run needs no debug UI probe. No installed app or personal profile is used.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / 'bench'))


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ('<!doctype html><title>Shortcut test</title><h1>Shortcut test</h1>'
                '<input id="one"><input id="two"><p>Real WebKit form</p>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Run:
    def __init__(self, binary, origin):
        self.origin = origin
        self.world = 'keys-' + uuid.uuid4().hex[:8]
        self.directory = Path(tempfile.mkdtemp(prefix='search-tab-shortcuts-'))
        self.profile = Path(BENCH['folder'](self.world))
        self.suite = 'com.officecommun.search.test.' + self.world
        self.socket = str(self.profile / 'bench.sock')
        self.process = None
        self.report = {'checks': [], 'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
        assert not self.profile.exists(), 'Refusing existing test profile'
        self.profile.mkdir(parents=True)
        self.app = self.directory / 'Search Shortcut Test.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        shutil.copy2(binary, self.app / 'Contents/MacOS/Search')
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.officecommun.search.shortcut-test.' + uuid.uuid4().hex,
            'CFBundleName': 'Search Shortcut Test', 'CFBundleExecutable': 'Search',
            'CFBundlePackageType': 'APPL', 'NSPrincipalClass': 'NSApplication',
            'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
            'NSAppTransportSecurity': {'NSAllowsArbitraryLoads': True},
        }))
        subprocess.run(['codesign', '--force', '--sign', '-', '--entitlements', str(ROOT / 'Search.entitlements'), str(self.app)],
                       check=True, capture_output=True)
        (self.profile / 'session.json').write_text(json.dumps({'tabs': [
            {'url': origin + '/' + name, 'title': name} for name in ('first', 'second', 'third', 'fourth')], 'active': 0}))
        prefs = self.directory / 'prefs.plist'
        prefs.write_bytes(plistlib.dumps({'bench': True, 'welcomed': True, 'spaces': True,
            'update.checked': datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)}))
        subprocess.run(['defaults', 'import', self.suite, str(prefs)], check=True, capture_output=True)

    def launch(self):
        with (self.directory / 'app.log').open('ab') as log:
            self.process = subprocess.Popen([str(self.app / 'Contents/MacOS/Search')],
                env={**os.environ, 'SEARCH_PROBE': self.world}, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                self.ask('tabs')
                time.sleep(1)
                return
            except (OSError, ValueError, SystemExit):
                time.sleep(.1)
        raise AssertionError('Test app never opened its socket')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=15)

    def ask(self, verb, **fields):
        result = BENCH['ask'](self.socket, {'do': verb, **fields})
        assert 'error' not in result, result
        return result

    def check(self, condition, message):
        self.report['checks'].append({'ok': bool(condition), 'message': message})
        print(('PASS ' if condition else 'FAIL ') + message, flush=True)
        assert condition, message

    def tabs(self):
        return self.ask('tabs')['tabs']

    def tab(self, name):
        return next(t['id'] for t in self.tabs() if t['url'] == self.origin + '/' + name)

    def choose(self, tab):
        self.ask('select', id=tab)

    def selected(self, tab, message):
        self.check(next(t['id'] for t in self.tabs() if t['active']) == tab, message)

    def press(self, code, chars, *mods, hold=False):
        return self.ask('press', code=code, chars=chars, mods=list(mods), release=not hold)

    def ui(self, action='nodes', **fields):
        return self.ask('tab-shortcut-ui', action=action, **fields)

    def click(self, label):
        nodes = self.ui()['nodes']
        button = next(n for n in nodes if n['role'] == 'AXButton' and label in (n['label'], n['title']))
        self.check(self.ui('press', index=button['index'])['pressed'], label)
        time.sleep(.2)

    def has(self, text):
        return any(text in str(n) for n in self.ui()['nodes'])

    def settings(self):
        if not self.ask('probe')['settings']:
            self.press(43, ',', 'cmd')
        self.click('Tabs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / '.build/debug/Search')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = Run(args.binary.resolve(), f'http://127.0.0.1:{server.server_port}')
    try:
        run.launch()
        # Restore lazily loaded tabs before reading their addresses.
        for tab in run.tabs():
            run.choose(tab['id'])
            run.ask('wait', id=tab['id'])
        first, second, third, fourth = (run.tab(name) for name in ('first', 'second', 'third', 'fourth'))
        for tab in (first, third, second, fourth, first):
            run.choose(tab)
        run.press(48, '\t', 'ctrl', hold=True)
        run.selected(fourth, 'last-used tab comes before the strip neighbor')
        run.press(48, '\t', 'ctrl', hold=True)
        run.selected(second, 'held Control walks older tabs without bouncing')
        run.press(48, '\x19', 'ctrl', 'shift', hold=True)
        run.selected(fourth, 'Shift reverses the same frozen order')
        run.press(48, '\t', 'ctrl')
        run.selected(second, 'release commits only the final selection')
        run.press(48, '\t', 'ctrl')
        run.selected(first, 'next gesture returns to the origin, skipping previews')
        run.press(48, '\t', 'ctrl')
        run.selected(second, 'quick gestures toggle the last two used tabs')
        run.press(48, '\x19', 'ctrl', 'shift')
        run.selected(third, 'reverse wraps in recent-use order')
        run.ask('key', id=third, text='')
        run.ask('eval', id=third, js="document.querySelector('#one').focus()")
        run.press(48, '\t')
        run.check(run.ask('eval', id=third, js='document.activeElement.id')['value'] == 'two', 'plain Tab advances web form focus')
        run.press(48, '\x19', 'shift')
        run.check(run.ask('eval', id=third, js='document.activeElement.id')['value'] == 'one', 'Shift-Tab reverses web form focus')
        # The upstream Spaces implementation parks entire tab rows.
        run.ask('ui', spaces=True)
        run.ask('space', action='new', name='Other')
        other = run.ask('open', url=run.origin + '/other')['id']
        run.choose(other)
        run.press(48, '\t', 'ctrl')
        run.check(all(t['id'] not in (first, second, third, fourth) for t in run.tabs()), 'cycling stays in the current space')
        run.ask('space', action='go', index=1)
        run.press(48, '\t', 'ctrl')
        run.selected(second, 'returning to a space preserves its recent-use history')

        if args.smoke:
            run.stop()
            for direction, code, key in (('next', 38, 'j'), ('previous', 40, 'k')):
                data = json.dumps({'code': code, 'key': key, 'modifiers': 1 << 18}).encode().hex()
                subprocess.run(['defaults', 'write', run.suite, 'shortcut.tab.' + direction, '-data', data], check=True)
            run.launch()
        else:
            run.settings()
            run.click('Change Next Tab shortcut')
            run.press(38, 'j', 'ctrl')
            run.check(run.has('⌃J'), 'recorder displays the new next-tab binding')
            run.click('Change Previous Tab shortcut')
            for code, key, mods, expected in ((38, 'j', ['ctrl'], 'Next Tab'), (12, 'q', ['cmd'], 'Quit'),
                    (17, 't', ['cmd'], 'New Tab'), (18, '1', ['ctrl'], 'Space selection')):
                run.press(code, key, *mods)
                run.check(run.has('Already used by ' + expected), 'conflict rejected: ' + expected)
            run.press(0, 'a')
            run.check(run.has('Include Command, Control, or Option'), 'plain typing is rejected')
            run.press(40, 'k', 'ctrl')
            run.check(run.has('⌃K'), 'recorder accepts a binding after a conflict')
            run.click('Change Next Tab shortcut')
            run.press(53, '\x1b')
            run.check(run.ask('probe')['settings'] and run.has('⌃J'), 'Escape cancels recording without closing Settings')
            for look in ('light', 'dark'):
                run.ask('ui', look=look)
                time.sleep(.3)
                run.ui('shot', path=str(run.directory / (look + '.png')))
            run.press(53, '\x1b')
            keys = {title: run.ui('menu-shortcut', titles=['Tabs', title]) for title in ('Next Tab', 'Previous Tab')}
            run.check(keys == {'Next Tab': {'key': 'j', 'modifiers': 1 << 18},
                              'Previous Tab': {'key': 'k', 'modifiers': 1 << 18}}, 'closed native menu updates immediately')

        first, second = run.tab('first'), run.tab('second')
        run.choose(second)
        run.choose(first)
        run.press(48, '\t', 'ctrl')
        run.selected(first, 'old Ctrl+Tab binding stops cycling')
        run.press(48, '\x19', 'ctrl', 'shift')
        run.selected(first, 'old reverse binding stops cycling')
        run.press(38, 'j', 'ctrl')
        run.selected(second, 'custom next binding follows recent-use order')
        run.press(38, 'j', 'ctrl', 'opt')
        run.selected(second, 'extra modifiers do not match')
        run.press(40, 'k', 'ctrl')
        run.selected(run.tab('fourth'), 'custom reverse binding wraps the recent-use order')
        if not args.smoke:
            run.stop()
            run.launch()
            first, second = run.tab('first'), run.tab('second')
            run.choose(second)
            run.choose(first)
            run.press(38, 'j', 'ctrl')
            run.selected(second, 'custom binding survives restart')
            run.settings()
            run.click('Reset Tab Shortcuts')
            run.press(53, '\x1b')
            run.press(38, 'j', 'ctrl')
            run.selected(second, 'reset releases custom shortcut')
            run.press(48, '\t', 'ctrl')
            run.selected(first, 'reset restores Ctrl+Tab')
            run.settings()
            run.click('Change Next Tab shortcut')
            run.press(30, '}', 'cmd', 'shift')
            run.check(run.has('⇧⌘]'), 'shifted punctuation records its base key')
            run.press(53, '\x1b')
            run.press(30, '}', 'cmd', 'shift')
            run.selected(second, 'shifted bracket binding cycles once')
            run.ask('ui', sidebar=True)
            run.press(30, '}', 'cmd', 'shift')
            run.selected(first, 'custom shortcut works in sidebar layout')
            run.settings()
            run.click('Change Next Tab shortcut')
            run.click('General')
            run.press(53, '\x1b')
            run.check(not run.ask('probe')['settings'], 'leaving the page cancels recording')
        run.report['ok'] = True
    except BaseException as error:
        run.report.update(ok=False, error=str(error))
        raise
    finally:
        run.stop()
        server.shutdown()
        server.server_close()
        report = run.directory / 'report.json'
        report.write_text(json.dumps(run.report, indent=2))
        print('Report:', report, flush=True)


if __name__ == '__main__':
    main()
