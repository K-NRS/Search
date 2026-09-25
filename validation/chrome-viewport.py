#!/usr/bin/env python3
"""Real-browser fixed/sticky header regression in an owned, disposable profile.
Native window coordinates and pointer events; no DOM padding or script clicks.
--expect-overlap proves the original sources exhibit the reported regression.
"""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = Path(__file__).resolve().parents[1]
SUPPORT = runpy.run_path(str(ROOT / 'tests/chrome_support.py'))

class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        html = '''<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#163a58"><title>Visible website header</title>
<style>
* { box-sizing: border-box } html,body { margin:0; padding:0 }
body { font: 18px system-ui; min-height:2400px; background:repeating-linear-gradient(90deg,#163a58 0 32px,#397187 32px 64px) }
#fixed { position:fixed; z-index:8; top:0; left:0; right:0; height:64px; background:#f1d659; color:#152332; display:flex; align-items:center; justify-content:space-between; padding:0 16px }
#fixed-button { position:absolute; top:0; left:0; width:220px; height:40px; border:0; background:#e05536; color:white; font:600 16px system-ui }
#fixed span { margin-left:244px }
#normal { margin-top:64px; height:64px; background:#cce8ed; padding:18px }
#sticky { position:sticky; top:64px; height:40px; background:#55dfb0; color:#132b27; padding:7px 20px; z-index:3 }
main { margin:30px; padding:30px; border-radius:18px; background:white; height:360px }
input { padding:12px; font:18px system-ui; width:75% }
#right { position:fixed; right:0; bottom:0; width:18px; height:18px; background:#f8adff }
</style>
<header id="fixed"><button id="fixed-button" onclick="window.headerClicks++">Website menu — clickable</button><span>THE ENTIRE WEBSITE HEADER IS VISIBLE</span><b>Contact</b></header>
<div id="normal">Normal document content begins below the website header.</div>
<nav id="sticky">Sticky navigation stays below the website header when scrolling.</nav>
<main><h1>Viewport regression fixture</h1><p id="identity">PATH</p><input id="form" value="unchanged"><p>The browser tab bar must not hide any of the orange button, yellow header or green sticky navigation.</p></main><div id="right"></div>
<script>window.headerClicks=0;window.pageMarker=crypto.randomUUID();</script>'''.replace('PATH', self.path)
        body = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default=str(ROOT / '.build/debug/Search'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect-overlap', action='store_true')
    args = parser.parse_args()
    args.world = 'vp-' + uuid.uuid4().hex[:8]
    server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = SUPPORT['Run'](args, f'http://127.0.0.1:{server.server_port}')
    cases = []
    def config(**values):
        run.ask('native', action='chrome-config', **values)
        time.sleep(.8)
    def data(tab):
        return run.js(tab, '''({width:innerWidth,height:innerHeight,scroll:scrollY,
fixed: (()=>{let r=document.querySelector('#fixed').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})(),
sticky:(()=>{let r=document.querySelector('#sticky').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})(),
marker:window.pageMarker,form:document.querySelector('#form').value,clicks:window.headerClicks})''')
    def shot(name):
        path = run.directory / (name + '.png')
        run.ask('native', action='composited-shot', path=str(path))
        status = Path(str(path) + '.json')
        deadline = time.monotonic() + 15
        while not status.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        result = json.loads(status.read_text()) if status.exists() else {'error': 'capture timed out'}
        run.report.setdefault('screenshots', {})[name] = result
        return result
    def verify(name, tab, *, sidebar=False, height=52, bookmarks=False, folded=False):
        g = run.ask('native', action='chrome-geometry')
        dom = data(tab)
        cases.append({'name': name, 'geometry': g, 'dom': dom})
        left = g['sideWidth'] if sidebar and not folded else 0
        top = (0 if sidebar or folded else height) + (g['bookmarksHeight'] if bookmarks and not folded else 0)
        actual_left = g['frame'][0] + g['insets'][1]
        actual_top = g['frame'][1] + g['insets'][0]
        run.check(abs(actual_top - top) < 1, f'{name}: visible viewport starts below {top:g}pt of browser chrome (actual {actual_top:g})')
        run.check(abs(actual_left - left) < 1, f'{name}: visible viewport clears {left:g}pt of sidebar (actual {actual_left:g})')
        run.check(abs(dom['width'] - (g['window'][0] - left)) <= 1, f'{name}: CSS viewport width fits the unobscured area')
        run.check(abs(dom['height'] - (g['window'][1] - top)) <= 1, f'{name}: CSS viewport height fits the unobscured area')
        before = dom['clicks']
        hit = run.ask('native', action='hit-test', x=left+110, y=top+8)
        run.check(hit['hit'] == 'PageView', f'{name}: header is the native pointer target')
        run.ask('native', action='chrome-pointer', x=left+110, y=top+8)
        time.sleep(.6)
        run.check(data(tab)['clicks'] > before, f'{name}: top website button receives a native click')
        run.check(data(tab)['form'] == 'kept-through-layout', f'{name}: form state survives layout')
        return g
    try:
        run.prepare()
        run.launch()
        config(height=52, transparency=.8, blur=.65, sidebar=False, folded=False, peeking=False, bookmarksBar=False)
        run.ask('resize', width=1280, height=820)
        tab = run.open('/fixed-header')
        run.js(tab, "document.querySelector('#form').value='kept-through-layout'")
        time.sleep(.8)
        if args.expect_overlap:
            geometry = run.ask('native', action='chrome-geometry')
            dom = data(tab)
            run.report['baseline'] = {'geometry': geometry, 'dom': dom}
            run.check(geometry['frame'][1] + geometry['insets'][0] < 51,
                      'Original transparent layout reproduces the reported top-chrome overlap')
            shot('before-overlap')
            run.report['ok'] = True
            return
        original = verify('transparent-top-52', tab)
        marker = data(tab)['marker']
        shot('after-top-52')
        for height in (30, 42, 52):
            config(height=height)
            verify(f'top-height-{height}', tab, height=height)
        config(blur=0)
        verify('blur-off', tab)
        config(blur=1)
        verify('blur-max', tab)
        run.js(tab, 'scrollTo(0,350)')
        time.sleep(.5)
        verify('scrolled-fixed-and-sticky', tab)
        shot('after-scrolled')
        config(bookmarksBar=True)
        verify('top-with-bookmarks', tab, bookmarks=True)
        config(sidebar=True)
        verify('sidebar-with-bookmarks', tab, sidebar=True, bookmarks=True)
        shot('after-sidebar-bookmarks')
        config(bookmarksBar=False)
        verify('sidebar-without-bookmarks', tab, sidebar=True)
        config(transparency=0)
        verify('opaque-sidebar', tab, sidebar=True)
        config(transparency=.8, folded=True)
        verify('folded-sidebar', tab, sidebar=True, folded=True)
        config(sidebar=False)
        # Switching layouts intentionally resets the transient fold state.
        # Fold the new layout only after that onChange has completed.
        config(folded=True, peeking=False)
        verify('folded-top', tab, folded=True)
        config(folded=False)
        run.ask('resize', width=980, height=700)
        time.sleep(.8)
        verify('resized-top', tab)
        config(transparency=0)
        verify('opaque-top', tab)
        config(transparency=.8)
        last = verify('transparent-restored', tab)
        run.check(last['webID'] == original['webID'] and data(tab)['marker'] == marker,
                  'Appearance changes never recreate or reload the original WebKit page')
        other = run.open('/second-header')
        run.js(other, "document.querySelector('#form').value='kept-through-layout'")
        verify('second-tab', other)
        run.ask('select', id=tab)
        time.sleep(.8)
        verify('return-to-original-tab', tab)
        run.check(data(tab)['marker'] == marker, 'Switching tabs retains the original page state')
        config(height=42)
        run.organize('save')
        run.stop()
        run.launch()
        run.ask('ui', welcome=False, settings=False)
        time.sleep(.8)
        tab = run.restored('/fixed-header')
        run.ask('select', id=tab)
        run.ask('wait', id=tab)
        run.js(tab, "document.querySelector('#form').value='kept-through-layout'")
        verify('restart-saved-transparency', tab, height=42)
        shot('after-restart')
        run.report['ok'] = True
    except BaseException as error:
        run.report.update(ok=False, error=str(error))
        try:
            shot('failure')
        except Exception:
            pass
        raise
    finally:
        run.report['cases'] = cases
        run.stop()
        server.shutdown()
        server.server_close()
        (run.directory / 'report.json').write_text(json.dumps(run.report, indent=2))
        args.output.mkdir(parents=True, exist_ok=True)
        for path in run.directory.iterdir():
            if path.is_file():
                shutil.copy2(path, args.output / path.name)
        print('Report:', args.output / 'report.json', flush=True)

if __name__ == '__main__':
    main()
