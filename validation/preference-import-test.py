#!/usr/bin/env python3
"""Exercise the exact shell/JXA/defaults pipeline in disposable macOS domains."""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/import-official-preferences.sh'
DEFAULTS = '/usr/bin/defaults'
checks = 0

def check(value, label):
    global checks
    if not value:
        raise AssertionError(label)
    checks += 1
    print('PASS ' + label, flush=True)

with tempfile.TemporaryDirectory(prefix='search-pref-tests-') as tmp:
    home = Path(tmp)
    prefix = 'tech.noras.search.preferences-test.' + uuid.uuid4().hex
    source = prefix + '.official'
    target = prefix + '.personal'
    backups = home / 'Preference Backups'
    script = home / 'import.sh'
    text = SCRIPT.read_text()
    # Only the domain constants and backup location change; the actual macOS
    # export, merge, validation, import and restore commands run unchanged.
    assert text.count('SOURCE_DOMAIN="com.officecommun.search"') == 1
    assert text.count('TARGET_DOMAIN="tech.noras.search.personal"') == 1
    text = text.replace('SOURCE_DOMAIN="com.officecommun.search"', f'SOURCE_DOMAIN="{source}"')
    text = text.replace('TARGET_DOMAIN="tech.noras.search.personal"', f'TARGET_DOMAIN="{target}"')
    text = text.replace('BACKUP_ROOT="$HOME/Library/Application Support/Search Personal Preference Backups"',
                        f'BACKUP_ROOT="{backups}"')
    script.write_text(text)
    subprocess.run(['/bin/bash', '-n', str(script)], check=True)

    def write(domain, values):
        path = home / 'seed.plist'
        path.write_bytes(plistlib.dumps(values))
        subprocess.run([DEFAULTS, 'import', domain, str(path)], check=True, capture_output=True)

    def read(domain):
        result = subprocess.run([DEFAULTS, 'export', domain, '-'], capture_output=True)
        return plistlib.loads(result.stdout) if result.returncode == 0 else None

    def execute(*args, success=True):
        result = subprocess.run(['/bin/bash', str(script), *args], text=True, capture_output=True)
        print(result.stdout, end='', flush=True)
        if success and result.returncode != 0:
            raise AssertionError(result.stderr)
        if not success and result.returncode == 0:
            raise AssertionError('Expected refusal, got success')
        return result

    source_data = {
        'look': 'dark', 'sidebar': False, 'sidebar.hides': False,
        'sidebar.width': 288.5, 'glyph': 'icons', 'tabs.sleep': False,
        'chrome.blur': 0.0, 'search.custom': 'https://example.test/search?q=%s',
        'shortcut.tab.next': b'{"code":48,"modifiers":262144}',
        'downloads': '/tmp/downloads with spaces', 'downloads.ask': True,
        'bench': True, 'spaces': True, 'passkeys': True, 'passkeys.entitled': True,
        'extensions.private': True, 'update.install': True,
        'unknown.secret': b'never-copied', 'welcomed': True,
        'NSWindow Frame search': 'not-copied',
    }
    original = {
        'look': 'light', 'sidebar': True, 'sidebar.hides': True,
        'chrome.transparency': 0.83, 'chrome.blur': 0.65,
        'bench': False, 'spaces': False, 'update.install': False,
        'personal.only': ['preserve', 23, False], 'NSWindow Frame search': 'keep-frame',
    }
    allowed = {'look', 'sidebar', 'sidebar.hides', 'sidebar.width', 'glyph', 'tabs.sleep',
               'chrome.blur', 'search.custom', 'shortcut.tab.next', 'downloads', 'downloads.ask'}
    expected = {**original, **{k: v for k, v in source_data.items() if k in allowed}}
    try:
        check(read(source) is None and read(target) is None, 'unique domains begin empty')
        write(source, source_data)
        write(target, original)
        execute('--dry-run')
        check(read(source) == source_data and read(target) == original, 'dry-run never changes preferences')
        check(not backups.exists(), 'dry-run creates no persistent backup')
        execute()
        check(read(target) == expected, 'only saved allowlisted preferences are merged, including false and zero')
        check(read(source) == source_data, 'official source is unchanged')
        check(isinstance(read(target)['shortcut.tab.next'], bytes), 'Data shortcuts retain their property-list type')
        first = next(backups.iterdir())
        check(plistlib.loads((first / 'before.plist').read_bytes()) == original, 'full Personal backup predates changes')
        check(first.stat().st_mode & 0o777 == 0o700 and (first / 'before.plist').stat().st_mode & 0o777 == 0o600,
              'backup directory and preferences are private to the user')
        execute()
        check(read(target) == expected and read(source) == source_data, 'repeat import is idempotent')
        subprocess.run(['/bin/bash', str(first / 'restore.sh')], check=True)
        check(read(target) == original and read(source) == source_data, 'restore exactly recovers Personal preferences, removing added keys')
        write(source, {'look': ['bad-type']})
        execute(success=False)
        check(read(target) == original, 'invalid preference type refuses without a write')
        write(source, {'welcomed': True, 'bench': True})
        execute(success=False)
        check(read(target) == original, 'source with no compatible saved preferences refuses without a write')
        write(source, {'manner': 'side', 'bars.compact': True})
        execute()
        check(read(target) == {**original, 'sidebar': True, 'bars.height': 30}, 'legacy saved sidebar and compact choices are translated')
        subprocess.run([DEFAULTS, 'delete', source], check=True, capture_output=True)
        before_missing = read(target)
        execute(success=False)
        check(read(target) == before_missing and read(source) is None, 'missing source fails closed')
        write(source, source_data)
        subprocess.run([DEFAULTS, 'delete', target], check=True, capture_output=True)
        execute(success=False)
        check(read(target) is None and read(source) == source_data, 'missing Personal domain fails closed')
    finally:
        for domain in (source, target):
            subprocess.run([DEFAULTS, 'delete', domain], capture_output=True)
print(f'All {checks} preference-import checks passed.', flush=True)
