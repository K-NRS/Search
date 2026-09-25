#!/usr/bin/env python3
"""Offline, same-Mac Search -> Search Personal profile copy (Python 3.9+).

Default: preview then ask for AKTAR. --dry-run never changes persistent data.
--restore BACKUP restores the pre-import Personal data and retains displaced data.
No network calls, Keychain access, profile sharing, app resigning or forced quits.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

SOURCE_ID = 'com.officecommun.search'
TARGET_ID = 'tech.noras.search.personal'
SOURCE_NAME = 'Search'
TARGET_NAME = 'Search Personal'
HOME = Path.home().resolve()
FIRST_SPACE = '00000000-0000-0000-0000-000000000001'

class Refusal(RuntimeError):
    pass

def native(script, *args):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-', *map(str, args)],
                            input=script, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise Refusal(result.stderr.strip() or 'macOS islemi basarisiz.')
    return result.stdout.strip()

def closed_apps():
    native('''ObjC.import('AppKit');
function run(ids) { ids.forEach(function(id) {
 if ($.NSRunningApplication.runningApplicationsWithBundleIdentifier(id).count > 0)
  throw new Error('Search ve Search Personal uygulamalarini once Command-Q ile kapatin.');
}); }''', SOURCE_ID, TARGET_ID)

def safe_path(path):
    """Never follow symlinked profile paths or parents."""
    path = Path(path)
    for part in [path, *path.parents]:
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise Refusal('Sembolik baglantili yol desteklenmiyor: ' + str(part))
    return path

def mappings():
    lib = HOME / 'Library'
    return [
        (lib/'Application Support'/SOURCE_NAME, lib/'Application Support'/TARGET_NAME, 'Sekmeler, gecmis, yer imleri, Spaces, eklentiler'),
        (lib/'WebKit'/SOURCE_ID, lib/'WebKit'/TARGET_ID, 'WebKit: site verileri ve Spaces depolari'),
        (lib/'HTTPStorages'/(SOURCE_ID+'.binarycookies'), lib/'HTTPStorages'/(TARGET_ID+'.binarycookies'), 'Kalici HTTP cookieleri'),
        (lib/'Cookies'/(SOURCE_ID+'.binarycookies'), lib/'Cookies'/(TARGET_ID+'.binarycookies'), 'Eski konumdaki uygulama cookieleri'),
        (lib/'HTTPStorages'/SOURCE_ID, lib/'HTTPStorages'/TARGET_ID, 'HTTP depolari'),
        (lib/'Caches'/SOURCE_ID/'WebKit', lib/'Caches'/TARGET_ID/'WebKit', 'WebKit cache / Service Worker verileri'),
        (lib/'Application Support'/SOURCE_ID/'WebExtensions', lib/'Application Support'/TARGET_ID/'WebExtensions', 'WebExtension depolari'),
    ]

def backup_root():
    return HOME/'Library/Application Support'/(TARGET_NAME+' Profile Backups')

def check_layout():
    # Sandboxed variants need different paths. Refuse rather than report a partial import.
    for identifier in (SOURCE_ID, TARGET_ID):
        c = safe_path(HOME/'Library/Containers'/identifier/'Data/Library')
        if c.exists():
            raise Refusal('Sandbox profili bulundu; bu arac bu farkli dizilimi desteklemiyor. Hicbir veri degistirilmedi.')
    for source, target, _ in mappings():
        safe_path(source); safe_path(target)
        if source == target or source.resolve() == target.resolve():
            raise Refusal('Kaynak ve hedef ayni olamaz.')

def domain(identifier):
    r = subprocess.run(['/usr/bin/defaults', 'export', identifier, '-'], capture_output=True, timeout=30)
    if r.returncode:
        raise Refusal('Tercihler okunamadi: ' + identifier)
    result = plistlib.loads(r.stdout)
    if not isinstance(result, dict):
        raise Refusal('Gecersiz tercihler: ' + identifier)
    return result

def set_domain(identifier, values):
    with tempfile.TemporaryDirectory(prefix='search-prefs-') as t:
        path = Path(t)/'prefs.plist'
        path.write_bytes(plistlib.dumps(values))
        native('''ObjC.import('Foundation');
function run(a) { var d=$.NSDictionary.alloc.initWithContentsOfFile(a[1]);
 var p=$.NSUserDefaults.standardUserDefaults;
 p.setPersistentDomainForName(d,a[0]);
 if (!p.synchronize) throw new Error('Tercihler kaydedilemedi.'); }''', identifier, path)
    if domain(identifier) != values:
        raise Refusal('Tercihler yazildiktan sonra dogrulanamadi.')

def merged_preferences(source, before):
    # Preserve personal-only appearance/features. Profile selectors must match copied data.
    result = dict(before)
    allowed = '''look sidebar sidebar.hides sidebar.width glyph bars.height bars.compact
 chrome.transparency chrome.blur chrome.accent search.engine search.custom tabs.sleep
 tabs.reading shortcut.tab.next shortcut.tab.previous shield shield.paused downloads
 downloads.ask passwords.save passwords.fill autocorrect autoscroll pages.120
 links.mini links.peek links.little links.show bookmarks.bar float.flicks float.away
 float.leave manner'''.split()
    for key in allowed:
        if key in source:
            result[key] = source[key]
    if 'sidebar' not in source and 'manner' in source:
        result['sidebar'] = source['manner'] == 'side'
    if 'bars.height' not in source and 'bars.compact' in source:
        result['bars.height'] = 30 if source['bars.compact'] else 52
    for key in list(result):
        if key.startswith(('extensions.settings.', 'extensions.newtab.', 'extensions.granted.', 'capture.')):
            result.pop(key)
    for key, value in source.items():
        if key.startswith('extensions.settings.'):
            result[key] = value
    result['spaces'] = bool(source.get('spaces', False))
    result['space.current'] = source.get('space.current', FIRST_SPACE)
    result['spaces.erasing'] = []  # Never import or keep a pending store-deletion queue.
    result['welcomed'] = True
    result['bench'] = False       # Copying a profile is not consent to scripted control.
    result['extensions.private'] = False
    result['passkeys'] = False    # The personal build has no browser passkey entitlement.
    result['passkeys.entitled'] = False
    result['update.install'] = False
    return result

def snapshot(root):
    """Hash regular file bytes and directory names; sockets are ephemeral, never copied."""
    root = safe_path(root)
    if not root.exists():
        return None
    digest = hashlib.sha256()
    total = files = cookies = 0
    def walk(path, rel):
        nonlocal total, files, cookies
        st = path.lstat()
        if stat.S_ISSOCK(st.st_mode):
            return
        if stat.S_ISDIR(st.st_mode):
            digest.update(b'D'+rel.encode('utf-8')+b'\0')
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                walk(child, rel+'/'+child.name)
        elif stat.S_ISREG(st.st_mode):
            digest.update(b'F'+rel.encode('utf-8')+b'\0')
            h = hashlib.sha256()
            head = b''
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(1024*1024), b''):
                    if not head: head = chunk[:4]
                    h.update(chunk)
            digest.update(h.digest())
            total += st.st_size; files += 1
            cookies += int(head == b'cook')  # Independent of staged/backup file names.
        else:
            raise Refusal('Sembolik baglanti veya desteklenmeyen dosya: '+str(path))
    walk(root, '')
    return dict(sha256=digest.hexdigest(), bytes=total, files=files, cookieFiles=cookies)

def copy_data(source, target):
    """Fresh independent copies; hardlinks and symlinks are not used."""
    st = source.lstat()
    if stat.S_ISDIR(st.st_mode):
        target.mkdir(mode=0o700)
        for child in source.iterdir():
            if stat.S_ISSOCK(child.lstat().st_mode):
                continue
            copy_data(child, target/child.name)
    elif stat.S_ISREG(st.st_mode):
        shutil.copy2(source, target, follow_symlinks=False)
    else:
        raise Refusal('Desteklenmeyen kaynak dosya: '+str(source))

def profile_counts(source):
    counts = dict(tabs=0, pinned=0, history=0, spaces=0, extensions=0)
    sessions = sorted(source.glob('session*.json'))
    if not sessions:
        raise Refusal('Resmi Search session dosyasi bulunamadi. Resmi uygulamayi acip Command-Q ile kapatin.')
    for path in sessions:
        if path.name != 'session.json' and not path.name.startswith('session-'):
            continue
        # Quarantined backups are not live sessions.
        if '.unreadable-' in path.name:
            continue
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('tabs'), list) or not isinstance(data.get('active'), int):
            raise Refusal('Okunamayan kaynak session dosyasi: '+path.name)
        for tab in data['tabs']:
            if not isinstance(tab, dict) or not isinstance(tab.get('url'), str) or not isinstance(tab.get('title'), str):
                raise Refusal('Desteklenmeyen kaynak sekme kaydi.')
        counts['tabs'] += len(data['tabs'])
        counts['pinned'] += sum(t.get('pin') is not None for t in data['tabs'])
    for file, key in [('history.json','history'), ('spaces.json','spaces'), ('Extensions/installed.json','extensions')]:
        p = source/file
        if p.exists():
            data = json.loads(p.read_text())
            if not isinstance(data, list):
                raise Refusal('Desteklenmeyen kaynak dosya: '+file)
            counts[key] = len(data)
    return counts

def write_journal(backup, data):
    tmp = backup/'journal.tmp'
    with tmp.open('w') as f:
        json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
    tmp.replace(backup/'journal.json')

@contextlib.contextmanager
def lock():
    root = safe_path(backup_root())
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = safe_path(root/'.import.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)

def restore(backup):
    backup = safe_path(Path(backup).absolute())
    if backup.parent != backup_root() or not backup.is_dir():
        raise Refusal('Bu kullanicinin profil yedek klasorunu secin.')
    closed_apps(); check_layout()
    j = json.loads(safe_path(backup/'journal.json').read_text())
    if j.get('home') != str(HOME) or j.get('target') != TARGET_ID or j.get('version') != 1:
        raise Refusal('Yedek bu kullanici/uygulamaya ait degil.')
    if j['state'] == 'restored':
        raise Refusal('Bu yedek zaten geri yuklenmis; yeniden islem yapilmadi.')
    pairs = mappings()
    before = plistlib.loads(safe_path(backup/'before.plist').read_bytes())
    discarded = backup/('displaced-'+uuid.uuid4().hex[:8])
    discarded.mkdir(mode=0o700)
    for i in reversed(j['started']):
        if type(i) is not int or not 0 <= i < len(pairs):
            raise Refusal('Gecersiz geri alma kaydi.')
        target = pairs[i][1]
        old = safe_path(backup/'before'/str(i))
        if old.exists():
            # Refuse modified backup data; preserve it for manual recovery.
            if snapshot(old) != j['before'][i]:
                raise Refusal('Yedek dogrulanamadi; otomatik geri alma durduruldu.')
            if target.exists():
                target.rename(discarded/str(i))
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            old.rename(target)
        elif j['before'][i] is None and target.exists():
            target.rename(discarded/str(i))
    set_domain(TARGET_ID, before)
    j['state'] = 'restored'; write_journal(backup, j)
    print('Personal aktarim oncesine dondu. Sonradan olusan veriler de yedekte saklandi.')

def import_profile(apply=False):
    closed_apps(); check_layout()
    pairs = mappings()
    src = [snapshot(a) for a,_,_ in pairs]
    old = [snapshot(b) for _,b,_ in pairs]
    if src[0] is None:
        raise Refusal('Resmi Search profili bulunamadi; hicbir veri degistirilmedi.')
    counts = profile_counts(pairs[0][0])
    s_pref, t_pref = domain(SOURCE_ID), domain(TARGET_ID)
    if not any(src[1:]):
        raise Refusal('Resmi Search WebKit/cookie deposu bulunamadi. Eksik profil aktarilmayacak.')
    print('Resmi Search kayitlari: {tabs} sekme, {pinned} sabit sekme, {history} gecmis kaydi, '
          '{spaces} Space, {extensions} eklenti.'.format(**counts), flush=True)
    for row, (_,_,label) in zip(src, pairs):
        if row:
            print('  '+label+': '+str(row['files'])+' dosya, '+str(round(row['bytes']/1048576, 1))+' MiB')
    n_cookies = sum(r['cookieFiles'] for r in src if r)
    print('Bulunan kalici cookie dosyasi: '+str(n_cookies))
    if not n_cookies:
        print('UYARI: Kalici cookie dosyasi yok; bu kaynakta cookie aktarimi yapilamaz.')
    print('Personal profili BIRLESTIRILMEYECEK; mevcut kopya yedeklenip resmi profilin kopyasi yerlestirilecek.')
    if not apply:
        return None
    new_pref = merged_preferences(s_pref, t_pref)
    required = sum(r['bytes'] for r in src if r) + 128*1024*1024
    if shutil.disk_usage(HOME).free < required:
        raise Refusal('Kaynak profilin bagimsiz kopyasi icin yeterli bos alan yok.')
    root = backup_root()
    backup = root/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(mode=0o700)
    (backup/'before').mkdir(mode=0o700)
    (backup/'staging').mkdir(mode=0o700)
    (backup/'before.plist').write_bytes(plistlib.dumps(t_pref))
    (backup/'after.plist').write_bytes(plistlib.dumps(new_pref))
    j = dict(version=1, home=str(HOME), target=TARGET_ID, state='staging', started=[], before=old)
    write_journal(backup, j)
    print('Yedek / kurtarma klasoru: '+str(backup), flush=True)
    try:
        for i,(a,b,_) in enumerate(pairs):
            if src[i] is not None:
                staged = backup/'staging'/str(i)
                copy_data(a, staged)
                if snapshot(staged) != src[i]:
                    raise Refusal('Hazirlanan veri kopyasi dogrulanamadi: '+pairs[i][2])
        closed_apps()
        if [snapshot(a) for a,_,_ in pairs] != src or [snapshot(b) for _,b,_ in pairs] != old:
            raise Refusal('Kopyalama sirasinda profil degisti; iki uygulama da kapali kalmali.')
        if domain(SOURCE_ID) != s_pref or domain(TARGET_ID) != t_pref:
            raise Refusal('Kopyalama sirasinda tercihler degisti.')
        j['state']='installing'; write_journal(backup,j)
        for i,(_,target,_) in enumerate(pairs):
            closed_apps()
            j['started'].append(i); write_journal(backup,j)
            if target.exists():
                target.rename(backup/'before'/str(i))
            if src[i] is not None:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                (backup/'staging'/str(i)).rename(target)
        set_domain(TARGET_ID, new_pref)
        if [snapshot(b) for _,b,_ in pairs] != src or [snapshot(a) for a,_,_ in pairs] != src:
            raise Refusal('Aktarim sonrasi dosya dogrulamasi basarisiz.')
        if domain(SOURCE_ID) != s_pref:
            raise Refusal('Resmi tercihler islem sirasinda degisti.')
        j['state']='complete'; write_journal(backup,j)
        print('Aktarim tamamlandi ve dosyalar dogrulandi. Resmi Search verileri degistirilmedi.')
        print('Geri alma: python3 '+shlex.quote(str(Path(__file__).resolve()))+' --restore '+shlex.quote(str(backup)))
        print('Simdi Search Personal uygulamasini acabilirsiniz. Yedegi kimseyle paylasmayin; oturum verileri icerir.')
        return backup
    except BaseException:
        if j['state'] == 'installing':
            print('Hata: Onceki Personal profili geri yukleniyor.', file=sys.stderr)
            try:
                restore(backup)
            except BaseException:
                print('Otomatik geri alma bitmedi. Uygulamalari kapali tutun; yedek: '+str(backup), file=sys.stderr)
        raise

def main():
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise Refusal('macOS uzerinde, kendi kullanicinizla ve sudo OLMADAN calistirin.')
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group()
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--restore', type=Path)
    a=p.parse_args()
    if a.restore:
        with lock():
            restore(a.restore)
        return
    import_profile(False)
    if a.dry_run:
        print('Onizleme bitti. Kalici veriler degismedi.'); return
    if input('\nYedekleyip aktarmak icin AKTAR yazin: ').strip() != 'AKTAR':
        print('Iptal edildi; kalici veriler degismedi.'); return
    with lock():
        import_profile(True)

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        main()
    except (Refusal, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as e:
        print('\nIslem durdu: '+str(e)+'\nProfilleri/yedekleri silmeyin. sudo veya izin atlatma komutlari kullanmayin.', file=sys.stderr)
        sys.exit(1)
