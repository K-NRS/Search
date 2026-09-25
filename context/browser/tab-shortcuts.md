# Remappable recent-use tab cycling

Settings → Tabs provides independent Next Tab and Previous Tab recorders and
Reset. Defaults are Control-Tab and Control-Shift-Tab. Escape cancels recording;
leaving the page or application cancels it too. Shortcuts are saved immediately.
Only these two browser commands are configurable.

Cycling uses last-used order within the current space and session. Holding the
binding's Command, Control, or Option modifiers freezes that order; Shift can
reverse direction. Releasing a required modifier commits only the final choice.
Separate gestures therefore toggle the two most recently used tabs. Tab-strip
order and numeric tab selection remain unchanged. Restored tabs not visited this
session follow visited tabs in strip order. Spaces retain their recent-use order
while their tab rows are parked.

Duplicate bindings, existing menu/extension shortcuts, tab-number and space-number
commands, and navigation/zoom aliases are rejected. The former fixed
Command-Shift-bracket aliases can be chosen as replacements. Ordinary Tab and
Shift-Tab continue moving focus through webpage forms.

SwiftUI refreshes closed menu equivalents lazily. Preference setters update just
the two native Tabs-menu items immediately; the command view also observes
preferences for subsequent menu rebuilds. A same-named bookmark is unaffected.
Recent-use history is separate from Tab.touched, which is also sleep accounting.

## Verification

```sh
swift build
python3 tests/tab-shortcuts.py
swift build -c release
python3 tests/tab-shortcuts.py --smoke --binary .build/release/Search
```

The test creates a uniquely identified ad-hoc app and disposable SEARCH_PROBE
profile, serves real local pages, and stops only its own process. The debug suite
uses actual native controls, menu equivalents, and application-queue events to
exercise recording, conflicts, cancellation, reset, persistence, modifier release,
recent-use order, space boundaries, layouts, and WebKit form focus. The release
smoke checks cycling and persisted remaps without debug-only UI instrumentation.
Reports and light/dark snapshots are saved in the printed temporary directory.

Native tests require uninterrupted focus. They do not claim system-wide input
injection or coverage of chords intercepted by macOS before reaching the app.
