# Extension keyboard shortcuts

## Approved scope

Settings → Extensions lists the commands supplied by each enabled extension.
Each command has a shortcut recorder, Clear, and Reset. Changes take effect
immediately and survive extension reload, disable/enable, and browser restart.
No dependency or system-wide hotkey registration is needed.

## Implementation plan

1. Exercise a real extension and native key events in an isolated bench world;
   first reproduce a manifest shortcut taking Search's Command-L binding.
2. Save optional command overrides on the existing installed-extension record.
   Preserve the manifest defaults separately in memory for Reset. An explicit
   empty binding means cleared; a missing override means use the manifest.
3. Capture shortcuts before normal key handling while recording. Escape cancels,
   Tab leaves recording, and leaving Extensions settings cancels capture.
   Use the existing settings typography, colors and native buttons.
4. Reject unsupported WebKit keys, modifier-free typing, Search/menu/space shortcuts,
   and duplicate bindings in enabled extensions. Show errors at the command.
   Existing conflicting defaults are displayed but cannot intercept Search.
5. Dispatch only exact modifier matches. Respect private tabs and reuse the
   extension action/popup path. Test changes through real WebKit commands and
   review the native Settings layout.

## API constraints

WebKit requires the embedding app to save and restore `activationKey` and
`modifierFlags`. Changes are visible to `chrome.commands.getAll()`.
Only Control, Option, Shift and Command modifier bits are accepted. The key
setter asserts on unsupported characters; validate before setting it.

Conflicts are checked against enabled extensions. If two extension defaults
collide after enabling or installing, neither ambiguous binding is dispatched;
the Settings rows identify the conflict so it can be reassigned or cleared.

## Verification

Use a dedicated `SEARCH_PROBE` world with its bench enabled. Run the regression
with `python3 tests/extension-shortcuts.py --world <world>` after building.
Tests use temporary real WebKit extensions and the existing native `press`
event queue. Never run shortcut recording against a personal browser profile.

The test-only `ext-shortcuts` bench request exposes command state and invokes
the same record/clear/reset operations as Settings. It does not simulate or
replace WebKit command delivery.

### Observed results (23 September 2026)

- Before implementation, the native Command-L regression failed: the extension
  worker received `address-conflict` and Search's address field stayed closed.
- Initial debug and packaged release builds: all 105 integration checks passed in each
  with no cleanup errors. The release run used `SEARCH_MEASURE=1` to retain
  production scheduling and App Nap policy.
  Coverage includes real worker delivery, `commands.getAll`, popup actions,
  clear/reset, duplicate defaults, reset conflicts, private tabs, capture
  cancellation, and reload/disable/enable persistence.
- A separate restart restored both an edited binding and an explicitly cleared
  binding from `installed.json` into the newly created WebKit commands.
- A Turkish-layout regression initially recorded `ö` as comma at hardware code
  43. Capture now prefers the event's actual characters; the native regression
  and worker delivery pass. Hardware codes are a fallback for missing characters.
- The actual native Settings window was captured and visually inspected. The
  recorder, Clear and Reset controls fit the existing panel and scroll view.
- After integration with current upstream main, the packaged release passed
  all 110 checks, including rejection of the new Control-1–9 space shortcuts.
  The added space-shortcut regression failed before the reservation was added.
- The packaged release app built successfully and its ad-hoc signature verified.
  Existing compiler warnings are inherited from upstream.
- Independent code review found no actionable issues. The personal profile and
  installed application were not modified.

## References

- [Apple command activation key](https://developer.apple.com/documentation/webkit/wkwebextension/command/activationkey)
- [WebKit key validation and matching](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/Extensions/Cocoa/WebExtensionCommandCocoa.mm)
