#!/bin/bash
# Build both Mac architectures and package a personal fork without touching an
# installed Search or its data. Run: bash build-personal.sh
set -euo pipefail
cd "$(dirname "$0")"
[[ "$(uname -s)" == Darwin ]] || { echo 'A Mac with Xcode is required to build this application.' >&2; exit 1; }
command -v swift >/dev/null
xcrun --find swift >/dev/null

# Reuse the upstream bundle/resources builder, then change the fork's identity.
bash build.sh release
APP='build/Search Personal.app'
[[ ! -L "$APP" ]] || { echo 'Refusing a symlink at the output app path.' >&2; exit 1; }
rm -rf "$APP"
ditto build/Search.app "$APP"
NATIVE="$(uname -m)"
case "$NATIVE" in
  arm64) OTHER=x86_64 ;;
  x86_64) OTHER=arm64 ;;
  *) echo "Unsupported architecture: $NATIVE" >&2; exit 1 ;;
esac
swift build -c release --arch "$OTHER" --scratch-path .build-personal-cross
CROSS="$(swift build -c release --arch "$OTHER" --scratch-path .build-personal-cross --show-bin-path)"
lipo -create .build/release/Search "$CROSS/Search" -output "$APP/Contents/MacOS/Search"
strip -x "$APP/Contents/MacOS/Search"

python3 - <<'PY'
from pathlib import Path
import plistlib
import subprocess
path = Path('build/Search Personal.app/Contents/Info.plist')
info = plistlib.loads(path.read_bytes())
info.update(CFBundleName='Search Personal', CFBundleDisplayName='Search Personal',
            CFBundleIdentifier='tech.noras.search.personal',
            SearchProfileName='Search Personal', SearchDisableUpdates=True,
            SearchIntegrationCommit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
path.write_bytes(plistlib.dumps(info, sort_keys=False))
PY
# No Developer ID is impersonated and no notarization is claimed.
codesign --force --sign - --entitlements Search.entitlements "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
lipo "$APP/Contents/MacOS/Search" -verify_arch arm64 x86_64
file "$APP/Contents/MacOS/Search"
ZIP='build/Search-Personal-universal.zip'
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
(cd build && shasum -a 256 Search-Personal-universal.zip > SHA256SUMS)
echo "Built: $ZIP"
echo 'Install by copying Search Personal.app to Applications or ~/Applications.'
echo 'The app has its own profile, preferences and keychain label; upstream updates are disabled.'
