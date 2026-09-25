#!/usr/bin/env python3
"""CI-only real WebKit profile/cookie roundtrip with disposable bundle IDs."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('Only run on disposable GitHub Actions machines.')
p = argparse.ArgumentParser()
p.add_argument('--app', type=Path, required=True)
p.add_argument('--script', type=Path, required=True)
a = p.parse_args()
spec = importlib.util.spec_from_file_location('migration', a.script)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
os.umask(0o077)
checks = []
def check(value, label):
    checks.append({'ok': bool(value), 'label': label})
    print(('PASS ' if value else 'FAIL ')+label, flush=True)
    if not value:
        raise AssertionError(label)

tag = uuid.uuid4().hex[:12]
m.SOURCE_ID = 'tech.noras.search.profile-test.'+tag+'.source'
m.TARGET_ID = 'tech.noras.search.profile-test.'+tag+'.target'
m.SOURCE_NAME = 'Search Copy Test '+tag+' Source'
m.TARGET_NAME = 'Search Copy Test '+tag+' Target'
space = str(uuid.uuid4()).upper()
records = []
seed = True
phase = 'source'
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        category = 'named' if self.path.startswith('/named') else 'prior' if self.path.startswith('/prior') else 'default'
        html = '''<!doctype html><title>Copy fixture</title><h1>Profile copy fixture</h1>
<script>
(async () => {
 const category=CATEGORY, seed=SEED;
 if(seed){localStorage.setItem('copy-check',category);document.cookie='copy_pref='+category+';Max-Age=604800;Path=/';}
 const db=await new Promise((res,rej)=>{const r=indexedDB.open('copy-fixture',1);r.onupgradeneeded=()=>r.result.createObjectStore('values');r.onsuccess=()=>res(r.result);r.onerror=()=>rej('db');});
 if(seed) await new Promise((res,rej)=>{let t=db.transaction('values','readwrite');t.objectStore('values').put(category,'check');t.oncomplete=res;t.onerror=rej;});
 const idb=await new Promise((res,rej)=>{const r=db.transaction('values').objectStore('values').get('check');r.onsuccess=()=>res(r.result);r.onerror=rej;});
 db.close();
 await fetch('/record',{method:'POST',body:JSON.stringify({category:category,local:localStorage.getItem('copy-check'),idb:idb,jsCookie:document.cookie.includes('copy_pref='+category)})});
})().catch(e=>fetch('/record',{method:'POST',body:JSON.stringify({error:String(e)})}));
</script>'''.replace('CATEGORY', json.dumps(category)).replace('SEED', str(seed).lower())
        body=html.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        if seed:
            self.send_header('Set-Cookie','copy_session='+category+';Max-Age=604800;Path=/;HttpOnly;SameSite=Lax')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        if self.path!='/record' or not 0<size<4096:
            self.send_error(400); return
        data=json.loads(self.rfile.read(size))
        data['phase']=phase
        data['httpOnly']=('copy_session='+data.get('category','??')) in self.headers.get('Cookie','')
        records.append(data)
        self.send_response(204);self.end_headers()
    def log_message(self,*_): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Page)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin='http://127.0.0.1:'+str(server.server_port)
source_profile=m.mappings()[0][0]
target_profile=m.mappings()[0][1]
owned=[]
logs=[]
process=None

def launch(app):
    global process
    log=(app.parent/(app.name+'.log')).open('ab'); logs.append(log)
    process=subprocess.Popen([str(app/'Contents/MacOS/Search')],stdout=log,stderr=log,
                             env={k:v for k,v in os.environ.items() if k not in ('SEARCH_PROBE','SEARCH_MEASURE')})
    owned.append(process)

def stop():
    global process
    if process and process.poll() is None:
        m.native('''ObjC.import('AppKit');function run(a){var p=$.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(a[0])); if(p) p.terminate;}''', process.pid)
        process.wait(timeout=20)
    process=None
    time.sleep(3)

def await_record(category, label):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        matches=[r for r in records if r.get('phase')==phase and r.get('category')==category]
        if matches:
            r=matches[-1]
            check(r.get('httpOnly') is True,label+': HTTP-only persistent login cookie reaches the server')
            check(r.get('jsCookie') is True,label+': script-readable persistent cookie restored')
            check(r.get('local')==category,label+': localStorage restored')
            check(r.get('idb')==category,label+': IndexedDB restored')
            return
        if process and process.poll() is not None:
            break
        time.sleep(.2)
    print('Fixture observations:', records, flush=True)
    raise AssertionError('Page did not load for '+label)

def session(category, pinned=False):
    tabs=[{'url':origin+'/'+category,'title':'Fixture '+category}]
    if pinned: tabs[0]['pin']='Fixture'
    return {'tabs':tabs,'active':0}

try:
    with tempfile.TemporaryDirectory(prefix='search-copy-') as t:
        apps=[]
        for identifier,name in [(m.SOURCE_ID,m.SOURCE_NAME),(m.TARGET_ID,m.TARGET_NAME)]:
            app=Path(t)/(name+'.app')
            subprocess.run(['/usr/bin/ditto',str(a.app),str(app)],check=True)
            path=app/'Contents/Info.plist'; info=plistlib.loads(path.read_bytes())
            info.update(CFBundleIdentifier=identifier,CFBundleName=name,SearchProfileName=name,SearchDisableUpdates=True)
            info.pop('CFBundleURLTypes',None);info.pop('CFBundleDocumentTypes',None)
            path.write_bytes(plistlib.dumps(info))
            subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True,capture_output=True)
            apps.append(app)
        check(all(not a.exists() and not b.exists() for a,b,_ in m.mappings()),'all test profile paths start empty')
        source_profile.mkdir(parents=True)
        (source_profile/'session.json').write_text(json.dumps(session('default',True)))
        (source_profile/('session-'+space+'.json')).write_text(json.dumps(session('named')))
        (source_profile/'spaces.json').write_text(json.dumps([
            dict(id=m.FIRST_SPACE,name='Home',colour=0),dict(id=space,name='Work',colour=1,sharesSignIns=False)]))
        (source_profile/'history.json').write_text(json.dumps([dict(url=origin+'/historic',key='historic',title='History fixture',count=6,last=800000000)]))
        (source_profile/'bookmarks.json').write_text(json.dumps([dict(id=str(uuid.uuid4()),title='Bookmark fixture',url=origin+'/bookmark')]))
        # Real installed extension content/state copied as data; runtime extension behavior is not asserted.
        (source_profile/'Extensions').mkdir()
        (source_profile/'Extensions/installed.json').write_text('[]')
        (source_profile/'Extensions/migration-sentinel.txt').write_text('synthetic extension data')
        m.set_domain(m.SOURCE_ID,dict(welcomed=True,spaces=True,**{'space.current':m.FIRST_SPACE,'bench':False}))
        launch(apps[0]);await_record('default','source default store seeded');stop()
        d=m.domain(m.SOURCE_ID);d['space.current']=space;m.set_domain(m.SOURCE_ID,d)
        phase='source-named';launch(apps[0]);await_record('named','source named store seeded');stop()
        target_profile.mkdir(parents=True)
        (target_profile/'session.json').write_text(json.dumps(session('prior')))
        (target_profile/'personal-only.txt').write_text('must survive rollback')
        m.set_domain(m.TARGET_ID,dict(welcomed=True,spaces=False,bench=False))
        phase='target-before';launch(apps[1]);await_record('prior','old target store seeded');stop()
        seed=False
        pairs=m.mappings()
        src=[m.snapshot(x) for x,_,_ in pairs]
        old=[m.snapshot(x) for _,x,_ in pairs]
        source_prefs=m.domain(m.SOURCE_ID); old_prefs=m.domain(m.TARGET_ID)
        m.import_profile(False)
        check([m.snapshot(x) for x,_,_ in pairs]==src and [m.snapshot(x) for _,x,_ in pairs]==old,'preview preserves all real profile bytes')
        check(not m.backup_root().exists(),'preview creates no persistent backup')
        with m.lock(): backup=m.import_profile(True)
        check([m.snapshot(x) for x,_,_ in pairs]==src and m.domain(m.SOURCE_ID)==source_prefs,'source profile and defaults remain unchanged')
        check([m.snapshot(x) for _,x,_ in pairs]==src,'all known data roots copied byte-for-byte')
        check((source_profile/'session.json').read_bytes()==(target_profile/'session.json').read_bytes(),'open and pinned tabs copied')
        check((source_profile/'history.json').read_bytes()==(target_profile/'history.json').read_bytes(),'history copied')
        check((source_profile/'bookmarks.json').read_bytes()==(target_profile/'bookmarks.json').read_bytes(),'bookmarks copied')
        check((target_profile/'Extensions/migration-sentinel.txt').exists(),'extension directory copied')
        check(m.domain(m.TARGET_ID)['space.current']==space and m.domain(m.TARGET_ID)['spaces'],'Spaces activation and current selection copied')
        check(backup.stat().st_mode & 0o777 == 0o700,'backup is private to current user')
        phase='target-import-named';launch(apps[1]);await_record('named','imported named Space');stop()
        d=m.domain(m.TARGET_ID);d['space.current']=m.FIRST_SPACE;m.set_domain(m.TARGET_ID,d)
        phase='target-import-default';launch(apps[1]);await_record('default','imported default Space');stop()
        check([m.snapshot(x) for x,_,_ in pairs]==src,'running copied profile does not modify source WebKit stores')
        with m.lock():m.restore(backup)
        check([m.snapshot(x) for _,x,_ in pairs]==old and m.domain(m.TARGET_ID)==old_prefs,'rollback restores original target bytes and preference domain')
        phase='target-restored';launch(apps[1]);await_record('prior','restored old target');stop()
        # Fault injection after file swaps and preference write: exact rollback must still work.
        old2=[m.snapshot(x) for _,x,_ in pairs];prefs2=m.domain(m.TARGET_ID)
        real_set=m.set_domain
        calls=[0]
        def fail_once(identifier,values):
            real_set(identifier,values)
            if identifier==m.TARGET_ID and calls[0]==0:
                calls[0]+=1
                raise m.Refusal('injected failure after preferences write')
        m.set_domain=fail_once
        try:
            with m.lock():m.import_profile(True)
            raise AssertionError('fault injection was ignored')
        except m.Refusal:
            pass
        finally:m.set_domain=real_set
        check([m.snapshot(x) for _,x,_ in pairs]==old2 and m.domain(m.TARGET_ID)==prefs2,'mid-transaction failure automatically restores original profile and defaults')
        sentinel=Path(t)/'outside.txt';sentinel.write_text('untouched')
        link=source_profile/'unsafe-link';link.symlink_to(sentinel)
        try:
            try:m.import_profile(False);raise AssertionError('symlink was accepted')
            except m.Refusal:pass
        finally:link.unlink()
        check(sentinel.read_text()=='untouched','symlinked source refuses without touching external data')
        phase='app-open';launch(apps[1]);await_record('prior','running-app precondition')
        try:m.import_profile(False);raise AssertionError('running app was accepted')
        except m.Refusal:check(True,'running application refuses migration')
        stop()
        check([m.snapshot(x) for x,_,_ in pairs]==src,'all tests leave synthetic official profile unchanged')
        print('All',len(checks),'profile-copy checks passed.',flush=True)
finally:
    if process:
        try:stop()
        except Exception:pass
    for proc in owned:
        if proc.poll() is None:
            proc.terminate();proc.wait(timeout=15)
    for log in logs:log.close()
    server.shutdown();server.server_close()
    # Only these unique, test-owned paths are eligible for removal.
    for x,y,_ in m.mappings():
        for path in (x,y):
            if path.is_dir():shutil.rmtree(path)
            elif path.exists():path.unlink()
    if m.backup_root().exists():shutil.rmtree(m.backup_root())
    for identifier in (m.SOURCE_ID,m.TARGET_ID):
        subprocess.run(['/usr/bin/defaults','delete',identifier],capture_output=True)
    Path('profile-copy-test-report.json').write_text(json.dumps({'checks':checks,'passed':sum(x['ok'] for x in checks)},indent=2))
