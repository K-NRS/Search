#!/bin/bash
# Search Personal: import explicitly saved, compatible preferences only.
# Does not share a profile, copy credentials, or write to the source domain.
# Source schema: K-NRS/Search, 94ac53c644be4e4ab90b172d3b144fcaabda166b.
# Usage: bash import-official-preferences.sh [--dry-run]
set -euo pipefail
umask 077
SOURCE_DOMAIN="com.officecommun.search"
TARGET_DOMAIN="tech.noras.search.personal"
BACKUP_ROOT="$HOME/Library/Application Support/Search Personal Preference Backups"

fail() { printf '\nHata: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Darwin ]] || fail 'Bu betik macOS icindir.'
[[ "$EUID" -ne 0 ]] || fail 'sudo kullanmayin; kendi kullanici hesabinizda calistirin.'
MODE="${1:-}"
[[ "$#" -le 1 && ( -z "$MODE" || "$MODE" == --dry-run ) ]] || fail 'Kullanim: bash import-official-preferences.sh [--dry-run]'

WORK="$(mktemp -d "${TMPDIR:-/tmp}/search-preferences.XXXXXX")"
BACKUP=""
ATTEMPTED=0
SUCCESS=0
# defaults import merges keys; use the replacement API for exact rollback.
replace_domain() {
    /usr/bin/osascript -l JavaScript - "$1" "$2" <<'JS'
ObjC.import('Foundation');
function run(argv) {
    var values = $.NSDictionary.alloc.initWithContentsOfFile(argv[1]);
    var prefs = $.NSUserDefaults.standardUserDefaults;
    prefs.setPersistentDomainForName(values, argv[0]);
    if (!prefs.synchronize()) throw new Error('Tercihler kalici depoya yazilamadi.');
}
JS
}
finish() {
    local status=$?
    trap - EXIT
    if [[ "$ATTEMPTED" == 1 && "$SUCCESS" != 1 ]]; then
        printf '\nAktarim dogrulanamadi; onceki Personal tercihleri geri yukleniyor.\n' >&2
        if ! replace_domain "$TARGET_DOMAIN" "$BACKUP/before.plist"; then
            printf 'Otomatik geri alma basarisiz. Yedek: %s\n' "$BACKUP" >&2
        fi
        [[ "$status" -ne 0 ]] || status=1
    fi
    /bin/rm -rf "$WORK"
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# AppKit inspection only: no Apple Events, forced quits or Automation grants.
closed() {
    /usr/bin/osascript -l JavaScript - "$SOURCE_DOMAIN" "$TARGET_DOMAIN" <<'JS'
ObjC.import('AppKit');
function run(argv) {
    argv.forEach(function (id) {
        if ($.NSRunningApplication.runningApplicationsWithBundleIdentifier(id).count > 0)
            throw new Error('Search ve Search Personal uygulamalarini Command-Q ile tamamen kapatin: ' + id);
    });
}
JS
}
same() {
    /usr/bin/osascript -l JavaScript - "$1" "$2" <<'JS'
ObjC.import('Foundation');
function run(argv) {
    var a = $.NSDictionary.alloc.initWithContentsOfFile(argv[0]);
    var b = $.NSDictionary.alloc.initWithContentsOfFile(argv[1]);
    if (!a.isEqualToDictionary(b)) throw new Error('Tercih dosyalari eslesmiyor; islem durduruldu.');
}
JS
}
closed
/usr/bin/defaults export "$SOURCE_DOMAIN" "$WORK/source.plist" 2>"$WORK/source-error" || fail 'Resmi Search tercihleri okunamadi. Resmi Search uygulamasini bir kez acip Command-Q ile kapatin; hicbir ayar degistirilmedi.'
/usr/bin/defaults export "$TARGET_DOMAIN" "$WORK/before.plist" 2>"$WORK/target-error" || fail 'Personal tercihleri okunamadi. Search Personal uygulamasini bir kez acip Command-Q ile kapatin; hicbir ayar degistirilmedi.'
/usr/bin/plutil -lint "$WORK/source.plist" "$WORK/before.plist" >/dev/null

/usr/bin/osascript -l JavaScript - "$WORK/source.plist" "$WORK/before.plist" "$WORK/after.plist" >"$WORK/summary.txt" <<'JS'
ObjC.import('Foundation');
function run(argv) {
    var source = $.NSDictionary.alloc.initWithContentsOfFile(argv[0]);
    var before = $.NSDictionary.alloc.initWithContentsOfFile(argv[1]);
    var after = before.mutableCopy;
    // Exact allowlist. Never import bench consent, passkeys, updater state,
    // spaces, extension permissions, site data, or unknown application keys.
    var schema = {
        'look':'string', 'sidebar':'bool', 'sidebar.hides':'bool',
        'sidebar.width':'number', 'glyph':'string',
        'bars.height':'number', 'chrome.transparency':'number',
        'chrome.blur':'number', 'chrome.accent':'string',
        'search.engine':'string', 'search.custom':'string',
        'tabs.sleep':'bool', 'tabs.reading':'bool',
        'shortcut.tab.next':'data', 'shortcut.tab.previous':'data',
        'shield':'bool', 'downloads':'string', 'downloads.ask':'bool',
        'passwords.save':'bool', 'passwords.fill':'bool',
        'autocorrect':'bool', 'autoscroll':'bool', 'pages.120':'bool',
        'links.mini':'bool', 'links.peek':'bool', 'links.little':'bool',
        'links.show':'bool', 'bookmarks.bar':'bool',
        'float.flicks':'bool', 'float.away':'bool', 'float.leave':'bool'
    };
    var imported = [];
    function has(key) { return source.allKeys.containsObject(key); }
    function copy(key, value, kind) {
        var v = ObjC.unwrap(value);
        var valid = kind === 'data' ? value.isKindOfClass($.NSData) :
            kind === 'string' ? (typeof v === 'string') :
            kind === 'number' ? (typeof v === 'number' && isFinite(v)) :
            (typeof v === 'boolean' || (typeof v === 'number' && (v === 0 || v === 1)));
        if (!valid) throw new Error('Beklenmeyen tercih turu: ' + key + '. Hicbir ayar degistirilmedi.');
        if (key === 'downloads' && (v.charAt(0) !== '/' || v.indexOf('\u0000') !== -1))
            throw new Error('Gecersiz indirme klasoru. Hicbir ayar degistirilmedi.');
        after.setObjectForKey(value, key);
        imported.push(key);
    }
    Object.keys(schema).forEach(function (key) {
        if (has(key)) copy(key, source.objectForKey(key), schema[key]);
    });
    // Translate older saved choices without inventing values for absent keys.
    if (!has('sidebar') && has('manner'))
        copy('sidebar', $(ObjC.unwrap(source.objectForKey('manner')) === 'side'), 'bool');
    if (!has('bars.height') && has('bars.compact'))
        copy('bars.height', $(ObjC.unwrap(source.objectForKey('bars.compact')) ? 30 : 52), 'number');
    if (imported.length === 0)
        throw new Error('Resmi Search alaninda aktarilabilir kaydedilmis tercih bulunamadi. Hicbir ayar degistirilmedi.');
    if (!after.writeToFileAtomically(argv[2], true)) throw new Error('Hazirlanan tercihler yazilamadi.');
    return 'Aktarilacak kaydedilmis tercihler (' + imported.length + '):\n' + imported.sort().join('\n');
}
JS
/bin/cat "$WORK/summary.txt"
/usr/bin/plutil -lint "$WORK/after.plist" >/dev/null
if [[ "$MODE" == --dry-run ]]; then
    printf '\nOnizleme tamamlandi. Kalici tercihler degistirilmedi.\n'
    exit 0
fi

# Recheck immediately before the write to avoid overwriting concurrent edits.
closed
/usr/bin/defaults export "$TARGET_DOMAIN" "$WORK/current.plist"
same "$WORK/before.plist" "$WORK/current.plist"
/usr/bin/defaults export "$SOURCE_DOMAIN" "$WORK/source-current.plist"
same "$WORK/source.plist" "$WORK/source-current.plist"
/bin/mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S).XXXXXX")"
/bin/cp "$WORK/before.plist" "$BACKUP/before.plist"
/bin/cp "$WORK/after.plist" "$BACKUP/after.plist"
/bin/cp "$WORK/summary.txt" "$BACKUP/imported-keys.txt"
# The rollback helper refers to its own backup directory, not a guessed path.
{
    printf '#!/bin/bash\nset -euo pipefail\numask 077\n'
    printf 'TARGET_DOMAIN=%q\n' "$TARGET_DOMAIN"
    cat <<'ROLLBACK'
[[ "$EUID" -ne 0 ]] || { echo 'sudo kullanmayin.' >&2; exit 1; }
/usr/bin/osascript -l JavaScript - "$TARGET_DOMAIN" <<'JS'
ObjC.import('AppKit');
function run(argv) {
    if ($.NSRunningApplication.runningApplicationsWithBundleIdentifier(argv[0]).count > 0)
        throw new Error('Once Search Personal uygulamasini Command-Q ile kapatin.');
}
JS
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
/usr/bin/plutil -lint "$HERE/before.plist" >/dev/null
/usr/bin/osascript -l JavaScript - "$TARGET_DOMAIN" "$HERE/before.plist" <<'JS'
ObjC.import('Foundation');
function run(argv) {
    var values = $.NSDictionary.alloc.initWithContentsOfFile(argv[1]);
    var prefs = $.NSUserDefaults.standardUserDefaults;
    prefs.setPersistentDomainForName(values, argv[0]);
    if (!prefs.synchronize()) throw new Error('Geri alma kalici depoya yazilamadi.');
}
JS
printf 'Aktarimdan onceki Personal tercihleri geri yuklendi. Search Personal uygulamasini acabilirsiniz.\n'
ROLLBACK
} > "$BACKUP/restore.sh"
ATTEMPTED=1
replace_domain "$TARGET_DOMAIN" "$BACKUP/after.plist"
/usr/bin/defaults export "$TARGET_DOMAIN" "$WORK/verified.plist"
same "$BACKUP/after.plist" "$WORK/verified.plist"
/usr/bin/defaults export "$SOURCE_DOMAIN" "$WORK/source-after.plist"
same "$WORK/source.plist" "$WORK/source-after.plist"
SUCCESS=1
printf '\nAktarim tamamlandi ve dogrulandi. Resmi Search tercihleri degismedi.\n'
printf 'Yedek: %s\n' "$BACKUP"
printf 'Geri alma: /bin/bash %q\n' "$BACKUP/restore.sh"
printf 'Search Personal uygulamasini yeniden acabilirsiniz.\n'
