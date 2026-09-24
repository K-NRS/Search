#!/usr/bin/env python3
"""Real WebKit Mini/Peek acceptance using an owned disposable debug app.

Build with ./build.sh debug, then python3 tests/previews.py. Reuses preview-harness.py's
isolated launcher; never reads or drives the installed app's session.
"""
import argparse
import html
import json
import runpy
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = runpy.run_path(str(ROOT / 'tests/preview-harness.py'))


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/preview-download.txt':
            data = b'Real preview download acceptance\n'
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="preview-download.txt"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        port = self.server.server_port
        body = f'''<!doctype html><title>Preview acceptance</title>
<style>body{{font:18px system-ui;padding:28px}}a{{display:block;margin:18px}}iframe{{height:90px}}</style>
<p id="identity">{html.escape(self.path)}</p><input id="draft" value="untouched">
<a id="same" href="/same-site">Same site</a>
<a id="cross" href="http://localhost:{port}/cross-site">Cross site</a>
<a id="next" href="/next">Next page</a>
<a id="popup" href="/popup" target="_blank">Popup</a>
<a id="opener" href="/popup" target="_blank" rel="opener">Popup with opener</a>
<a id="download" href="/preview-download.txt" download>Download</a>
<a id="frame" href="http://localhost:{port}/subframe" target="child">Subframe</a>
<iframe name="child" src="/frame-start"></iframe>
<script>window.documentIdentity=crypto.randomUUID();
addEventListener('message', event => {{ window.lastFramePath = event.data.framePath; }});</script>'''
        if self.path in ('/frame-start', '/subframe'):
            body = (f'<!doctype html><p id="identity">{self.path}</p>'
                    '<script>parent.postMessage({framePath: location.pathname}, "*")</script>')
        data = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class Previews(SUITE['Run']):
    def preview(self, action='state', **fields):
        return self.ask('preview', action=action, **fields)

    def until(self, predicate, message):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state = self.preview()
            if predicate(state):
                self.check(True, message)
                return state
            time.sleep(.1)
        raise AssertionError(message + ': ' + json.dumps(state))

    def mini(self, path, source=None):
        fields = {'url': self.origin + path}
        if source:
            fields['source'] = source
        before = {t['id'] for t in self.preview()['tabs']}
        state = self.preview('mini', **fields)
        tab = next(t['id'] for t in state['tabs'] if t['id'] not in before)
        self.page(tab, path)
        return tab

    def key(self, code, chars='', window=None, modifiers=None):
        fields = {'code': code, 'chars': chars, 'modifiers': modifiers or []}
        if window is not None:
            fields['window'] = window
        self.ask('native', action='focus', **({'window': window} if window is not None else {}))
        self.ask('native', action='key', **fields)
        time.sleep(.4)

    def window(self, tab):
        return next(w['number'] for w in self.preview()['miniWindows'] if w['id'] == tab)

    def click(self, tab, selector, modifiers=None):
        self.ask('tap', id=tab, selector=selector, modifiers=modifiers or [])

    def settings_controls(self):
        self.ask('resize', width=1180, height=780, steps=1)
        self.ask('native', action='focus')
        self.ask('ui', sidebar=False, look='light', settings=True)
        time.sleep(.5)
        # Activate the real accessible Settings control, then verify the
        # resulting preference independently of accessibility action delivery.
        controls = [('miniLinks', 'Open external links in Mini'),
                    ('peekLinks', 'Preview links from pinned tabs')]
        for field, label in controls:
            self.ask('native', action='press', label=label)
            try:
                self.until(lambda s: not s[field], 'native Settings switch disables ' + field)
            except AssertionError:
                self.ask('native', action='shot', path=str(self.directory / 'settings-failure.png'))
                raise
        path = self.directory / 'settings-disabled.png'
        self.report['settingsScreenshot'] = self.ask('native', action='shot', path=str(path))
        self.ask('ui', settings=False)

    def popup_preview(self, source, surface, shift=False):
        before = {t['id'] for t in self.preview()['tabs']}
        self.click(source, '#opener', ['shift'] if shift else [])
        state = self.until(lambda s: any(t['id'] not in before for t in s['tabs']),
                           'trusted popup creates a ' + surface + ' surface')
        created = [t for t in state['tabs'] if t['id'] not in before]
        self.check(len(created) == 1 and created[0]['surface'] == surface,
                   'target=_blank creates exactly one ' + surface)
        child = created[0]['id']
        self.page(child, '/popup')
        self.check(self.js(child, 'window.opener != null'), 'popup preserves its live window.opener')
        self.js(source, "window.popupMessage=null; addEventListener('message',event=>{if(event.data.previewPopup)window.popupMessage=event.data.previewPopup}); true")
        self.js(child, "window.opener.postMessage({previewPopup:'alive'}, '*'); true")
        deadline = time.monotonic() + 10
        while self.js(source, 'window.popupMessage') != 'alive' and time.monotonic() < deadline:
            time.sleep(.1)
        self.check(self.js(source, 'window.popupMessage') == 'alive', 'popup can send a real message to its opener')
        self.js(child, 'setTimeout(()=>window.close(),50); true')
        self.until(lambda s: not any(t['id'] == child for t in s['tabs']), 'popup window.close tears down its surface')

    def main_overlay_shortcuts(self, source):
        self.ask('native', action='focus')
        self.ask('ui', settings=True)
        before = {w['id'] for w in self.preview()['miniWindows']}
        self.ask('native', action='menu', titles=['File', 'New Mini Window'])
        state = self.until(lambda s: any(w['id'] not in before for w in s['miniWindows']),
                           'native Mini opens while main Settings remains open')
        mini = next(w['id'] for w in state['miniWindows'] if w['id'] not in before)
        self.key(13, 'w', self.window(mini), ['command'])
        self.until(lambda s: not any(w['id'] == mini for w in s['miniWindows']),
                   'Command-W closes focused Mini even while main Settings is open')
        self.check(any(t['id'] == source for t in self.preview()['tabs']),
                   'Mini shortcut with main overlay preserves underlying main tab')
        self.ask('ui', settings=False)

    def execute(self):
        self.prepare()
        self.launch()
        state = self.preview()
        self.check(state['miniLinks'] and state['peekLinks'], 'automatic Mini and Peek routing default on')
        source = self.open('/source')
        self.main_overlay_shortcuts(source)
        space = self.organize()['activeSpaceID']
        self.ask('native', action='focus')
        self.ask('native', action='menu', titles=['File', 'New Mini Window'])
        state = self.until(lambda s: len(s['miniWindows']) == 1, 'native File menu creates Mini')
        empty = state['miniWindows'][0]['id']
        self.key(13, 'w', self.window(empty), ['command'])
        self.until(lambda s: not s['miniWindows'], 'Command-W closes only focused Mini')
        self.check(self.preview()['activeID'] == source, 'closing Mini preserves main selection')

        first = self.mini('/mini-first')
        second = self.mini('/mini-second')
        self.check(len(self.preview()['miniWindows']) == 2, 'multiple Minis keep independent windows')
        self.check(first not in self.organize()['visible'] and second not in self.organize()['visible'],
                   'temporary pages are absent from sidebar tab membership')
        identity = self.js(second, "document.querySelector('#draft').value='retained'; window.documentIdentity")
        self.click(second, '#next')
        self.page(second, '/next')
        self.js(second, "history.back(); true")
        self.page(second, '/mini-second')
        self.key(31, 'o', self.window(second), ['command'])
        state = self.until(lambda s: self.tab(s, second)['surface'] == 'tab', 'Command-O promotes focused Mini')
        self.check(self.js(second, "[window.documentIdentity,document.querySelector('#draft').value]") == [identity, 'retained'],
                   'Mini promotion retains live document identity and edited form')
        self.check(self.js(second, 'history.length') >= 2, 'Mini promotion retains navigation history')
        self.js(second, 'history.forward(); true')
        self.page(second, '/next')
        self.js(second, 'history.back(); true')
        self.page(second, '/mini-second')
        self.check(self.js(second, "document.querySelector('#draft').value") == 'retained',
                   'promoted page can traverse its original forward and back entries')
        self.check(len(state['miniWindows']) == 1 and self.tab(state, second)['spaceID'] == space,
                   'promotion removes only its own window and retains space')
        self.key(37, 'l', self.window(first), ['command'])
        nodes = self.ask('native', action='nodes', window=self.window(first))['nodes']
        self.report['miniAddressNodes'] = nodes
        fields = self.ask('native', action='fields', window=self.window(first))['fields']
        self.report['miniAddressFields'] = fields
        self.check(any(f['editable'] and f['focused'] and f['value'] == self.origin + '/mini-first' for f in fields),
                   'Command-L focuses Mini address field with its current URL')
        before_reload = self.js(first, 'window.documentIdentity')
        self.key(15, 'r', self.window(first), ['command'])
        self.page(first, '/mini-first')
        self.check(self.js(first, 'window.documentIdentity') != before_reload, 'Command-R reloads focused Mini')
        self.report['miniScreenshots'] = []
        for label, width, height in [('narrow', 440, 340), ('wide', 940, 660)]:
            self.ask('native', action='resize', window=self.window(first), width=float(width), height=float(height))
            time.sleep(.3)
            path = self.directory / f'mini-{label}.png'
            self.report['miniScreenshots'].append(self.ask('native', action='shot', window=self.window(first), path=str(path)))
        self.preview('close', id=first)

        self.organize('select-tab', id=source)
        self.popup_preview(source, 'peek', shift=True)
        popup_parent = self.mini('/mini-opener')
        self.popup_preview(popup_parent, 'mini')
        self.preview('close', id=popup_parent)

        self.organize('select-tab', id=source)
        self.click(source, '#same', ['shift'])
        state = self.until(lambda s: s['peekID'] is not None, 'trusted Shift-click opens Peek')
        peek = state['peekID']
        self.page(peek, '/same-site')
        self.check(state['activeID'] == source, 'Peek keeps source tab selected')
        self.click(peek, '#next')
        self.page(peek, '/next')
        self.check(self.preview()['peekID'] == peek, 'ordinary Peek navigation reuses its own page')
        self.report['screenshots'] = []
        for sidebar in (False, True):
            for look in ('light', 'dark'):
                self.ask('ui', sidebar=sidebar, look=look)
                time.sleep(.3)
                path = self.directory / f'peek-{look}-{"sidebar" if sidebar else "strip"}.png'
                self.report['screenshots'].append(self.ask('native', action='shot', path=str(path)))
        self.ask('ui', sidebar=False, look='light')
        identity = self.js(peek, "document.querySelector('#draft').value='peek draft'; window.documentIdentity")
        self.key(31, 'o', modifiers=['command'])
        self.until(lambda s: s['peekID'] is None and self.tab(s, peek)['surface'] == 'tab', 'Command-O promotes Peek')
        self.check(self.js(peek, "[window.documentIdentity,document.querySelector('#draft').value]") == [identity, 'peek draft'],
                   'Peek promotion preserves live DOM identity and form state')

        self.organize('select-tab', id=source)
        self.organize('pin', id=source)
        self.click(source, '#cross')
        state = self.until(lambda s: s['peekID'] is not None, 'pinned cross-host link opens Peek by default')
        self.page(state['peekID'], '/cross-site')
        self.key(53)
        self.until(lambda s: s['peekID'] is None, 'Escape dismisses Peek')
        self.click(source, '#frame')
        deadline = time.monotonic() + 12
        while self.js(source, 'window.lastFramePath') != '/subframe' and time.monotonic() < deadline:
            time.sleep(.1)
        self.check(self.js(source, 'window.lastFramePath') == '/subframe', 'targeted cross-host iframe actually navigates')
        self.check(self.preview()['peekID'] is None and self.js(source, 'location.pathname') == '/source',
                   'cross-host subframe navigation does not open Peek')
        self.click(source, '#same')
        self.page(source, '/same-site')
        self.check(self.preview()['peekID'] is None, 'pinned same-host links navigate in place')
        self.preview('settings', peekLinks=False)
        self.click(source, '#cross')
        self.page(source, '/cross-site')
        self.check(self.preview()['peekID'] is None, 'disabled automatic Peek leaves pinned links in place')
        self.click(source, '#same', ['shift'])
        state = self.until(lambda s: s['peekID'] is not None, 'manual Shift-click works with automatic Peek disabled')
        self.key(13, 'w', modifiers=['command'])
        self.until(lambda s: s['peekID'] is None, 'Command-W closes Peek before underlying tab')
        self.check(any(t['id'] == source for t in self.preview()['tabs']), 'Peek close preserves pinned source')

        original_space = self.organize()['activeSpaceID']
        associated = self.mini('/space-associated')
        other_space = self.organize('create-space', name='Preview destination')['activeSpaceID']
        self.preview('promote', id=associated)
        state = self.organize()
        self.check(state['activeSpaceID'] == original_space and self.tab(state, associated)['spaceID'] == original_space,
                   'Mini promotion retains its original space after main space changes')
        self.check(other_space != original_space, 'space promotion check used distinct real spaces')

        download_dir = self.directory / 'downloads'
        download_dir.mkdir()
        self.preview('settings', downloadDirectory=str(download_dir))
        self.organize('select-tab', id=associated)
        self.organize('pin', id=associated)
        self.click(associated, '#download', ['shift'])
        download = download_dir / 'preview-download.txt'
        deadline = time.monotonic() + 12
        while not download.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        self.check(download.exists() and download.read_bytes() == b'Real preview download acceptance\n',
                   'Shift-click download saves actual response bytes')
        self.check(self.preview()['peekID'] is None and self.js(associated, 'location.pathname') == '/space-associated',
                   'download does not create Peek or replace pinned page')

        recovery = self.mini('/main-close-recovery')
        self.ask('native', action='close-window')
        self.key(31, 'o', self.window(recovery), ['command'])
        self.until(lambda s: self.tab(s, recovery)['surface'] == 'tab' and s['mainVisible']
                   and self.tab(s, recovery)['window'] == s['mainWindow'],
                   'Mini promotion restores a closed main window and attaches live page')

        before = len(self.preview()['miniWindows'])
        self.preview('external', url=self.origin + '/external-default')
        state = self.until(lambda s: len(s['miniWindows']) == before + 1, 'external delivery opens Mini by default')
        external = state['miniWindows'][-1]['id']
        self.preview('settings', miniLinks=False)
        state = self.preview('external', url=self.origin + '/external-disabled')
        self.page(state['activeID'], '/external-disabled')
        self.check(len(state['miniWindows']) == before + 1, 'disabled external Mini opens an ordinary tab')
        self.preview('close', id=external)

        private = self.organize('private-tab')['activeID']
        self.ask('go', id=private, url=self.origin + '/private')
        self.page(private, '/private')
        self.js(private, "document.cookie='preview_private=yes; path=/'; localStorage.setItem('preview_private','yes'); true")
        mini = self.mini('/private-mini', private)
        self.storage(mini, 'preview_private', 'yes')
        self.check(self.tab(self.preview(), mini)['shy'], 'Mini inherits source private browsing')
        self.preview('close', id=mini)
        state = self.preview('peek', source=private, url=self.origin + '/private-peek')
        self.page(state['peekID'], '/private-peek')
        self.storage(state['peekID'], 'preview_private', 'yes')
        self.preview('close', id=state['peekID'])
        normal = self.open('/normal-storage')
        self.storage(normal, 'preview_private', None)
        self.preview('settings', miniLinks=True, peekLinks=True)
        self.settings_controls()
        self.mini('/discard-at-restart')
        self.organize('save')
        self.stop()
        self.launch()
        state = self.preview()
        self.check(not state['miniLinks'] and not state['peekLinks'], 'Mini and Peek settings persist across restart')
        self.check(state['peekID'] is None and not state['miniWindows'] and all(t['surface'] == 'tab' for t in state['tabs']),
                   'temporary surfaces are never restored as session tabs')
        self.check(not any(t['url'] == self.origin + '/discard-at-restart' for t in state['tabs']),
                   'unpromoted temporary URL is excluded from saved session')
        restored = {t['url'] for t in state['tabs']}
        self.check({self.origin + '/mini-second', self.origin + '/next'}.issubset(restored),
                   'promoted Mini and Peek URLs survive a real app restart')
        self.preview('settings', miniLinks=True, peekLinks=True)
        self.report['finalState'] = self.preview()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default=str(ROOT / '.build/debug/Search'))
    args = parser.parse_args()
    args.world = 'previews-' + uuid.uuid4().hex[:8]
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = Previews(args, f'http://127.0.0.1:{server.server_port}')
    code = 0
    try:
        run.execute()
        run.report['ok'] = True
    except BaseException as error:
        run.report.update(ok=False, error=str(error))
        print('FAIL ' + str(error), flush=True)
        code = 1
    finally:
        run.stop()
        server.shutdown()
        server.server_close()
        report = run.directory / 'previews-report.json'
        report.write_text(json.dumps(run.report, indent=2))
        print('Preview report: ' + str(report), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
