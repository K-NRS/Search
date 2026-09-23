# Tab groups and emoji markers

## Integration scope

Extend the upstream spaces implementation. Keep `spaces.json`, each space's
session file, website-store identifiers, shared sign-ins, download folders,
Control-1–9 shortcuts, and sidebar swiping. Preserve the extension-shortcut
recorder already in this pull request.

Add optional group metadata and stable tab identifiers to each session. Groups
belong to one space, may collapse, and may have an emoji or native icon. Pinning
removes group membership; removing a group keeps its tabs. Selection reveals a
collapsed member. Parked spaces retain their groups and restore them on return.

Space emoji markers extend the existing icon controls. Missing marker fields
keep the old icons. Invalid cosmetic marker metadata must not discard tabs.

## Verification plan

1. Reproduce missing group/marker behavior in a disposable real-WebKit app.
2. Extend session data and tab lifecycle without migrating upstream storage.
3. Integrate group UI in both tab layouts and parked sidebar previews.
4. Verify legacy sessions, named tabs, groups, markers, shared/separate website
   storage, private-tab exclusion, and restart persistence.
5. Run the existing extension-shortcut suite on the combined packaged build.
6. Review the combined diff, then update the existing PR branch and description.

## Behavior

Use File › New Tab Group, or a tab's Move to Group › New Group action. The group
heading expands or collapses its members; its menu creates a tab, edits its name
and marker, or ungroups the tabs. Groups are available without enabling spaces.
Within each space, pins come first, then ungrouped tabs, then groups in creation
order. Keyboard selection reveals collapsed members. Dragging reorders peers;
extension-driven moves retain the existing canonical tab indices.

Space creation retains the sidebar card and sign-in choice. Its icon popover
also accepts one emoji. The space menu's Emoji or Icon action customizes an
existing space; clearing a marker returns to that space's existing icon.
Family, skin-tone and flag emoji are accepted as single grapheme clusters.

## Running the checks

Build a packaged app with `./build.sh release app`, then run:

```sh
SEARCH_MEASURE=1 python3 tests/tab-groups.py --binary build/Search.app/Contents/MacOS/Search
SEARCH_MEASURE=1 python3 tests/native-tab-groups.py --binary build/Search.app/Contents/MacOS/Search
```

Each driver copies the bundle, assigns it a unique identifier, and uses a fresh
`SEARCH_PROBE` world. It never opens the installed browser or personal profile.
The tests use the native app, local HTTP pages, and actual WebKit storage.
`organize` and `organize-ui` bench commands are restricted to test runs.

The model suite first failed against the unmodified PR build. Focused tests
also reproduced cross-group extension move rejection, unavailable-space ghosts
blocking reopen, and close selection using backing rather than displayed order.

## Verified on 24 September 2026

- The packaged release passed 111 group/session checks with production scheduling
  (`SEARCH_MEASURE=1`): legacy metadata, per-space persistence, shared and isolated
  cookies/local storage, privacy filtering, markers and tab lifecycle.
- 37 native checks passed against the same release. Actual heading mouse events
  collapsed/expanded groups in both layouts; native sheet controls validated
  emoji and saved icon choices. The first grouped tab's pin action immediately
  rendered the updated header count. Native scroll bounds and screenshots verified
  the selected last tab after switching layouts and narrowing the strip to 640px.
- The narrow-strip regression failed before observing width changes. Merely
  observing the overflow Boolean missed resizes that stayed overflowing.
- Native pointer dragging was not automated; canonical reorder behavior is covered
  by the integration suite and the drag handlers were reviewed.
- The same final packaged binary also passed the existing 454 checks: 110 extension
  shortcuts, 13 background tabs, 315 offscreen lifecycle/messaging, and 16 floating
  video/subtitle checks. Total: 602 successful checks. The video visibility check
  requires the disposable app to be visible; activate that owned bundle before
  running `tests/float-video.py` after a background-only launch.
- The final release built successfully and its ad-hoc signature verified.
