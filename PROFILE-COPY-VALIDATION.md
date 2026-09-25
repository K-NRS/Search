# Search Personal: offline profile-copy validation

Validated 2026-09-25. This is a data-copy helper, not another application build. The previous preferences-only helper intentionally did not copy browsing data.

## Exact helper and successful run

- Script: `tools/import-search-profile.py`.
- Tested source commit: `3dc3342172f28bb1d8649691127990398e640194`.
- Git blob: `fb9d4503229a55cf08c6ef4d8e6fb3c403087976`.
- Script SHA-256: `dd577ea5229eb44a303f91c24a60bd40a4c6f6cc40beba05670af9da9bd50b69`.
- Successful run: https://github.com/K-NRS/Search/actions/runs/36166816927
- macOS 15: **45/45 checks passed**.
- macOS 26: **45/45 checks passed**.
- Both runtime environments used Apple Silicon. No macOS 14 or Intel runtime claim is made.
- The script retrieved from both CI artifacts was compared byte-for-byte with the downloadable conversation attachment; all three are identical.

## Operation

Run with Python 3.9 or newer, under the same macOS user who uses both browsers, without sudo. The script uses only the Python standard library and macOS's existing defaults/osascript tools. No browser rebuild is required. It does not modify or re-sign either installed application, so the header-fixed Search Personal application remains unchanged.

Close both Search and Search Personal with Command-Q. Run `python3 import-search-profile.py`. It first prints source counts and then requires the exact confirmation `AKTAR`. A standalone `--dry-run` only previews. Keep both applications closed until the operation finishes.

Source identity: `com.officecommun.search`, profile `~/Library/Application Support/Search`.
Target identity: `tech.noras.search.personal`, profile `~/Library/Application Support/Search Personal`.

The helper copies persisted open/pinned-tab lists, browsing history, bookmarks, Spaces and their session files, extension installation files, and other profile-folder data. It also independently copies bundle-scoped WebKit website stores, named Space stores, persistent HTTP cookies, app-specific legacy cookie files when present, HTTP stores, and WebKit caches/Service Worker data. Compatible saved preferences are merged; Spaces activation and current selection follow the copied profile. File counts and SHA-256 fingerprints are verified before and after replacement. Cookie values and browsing URLs are not printed.

This is a **one-time replacement, not a merge of two browsing profiles and not ongoing synchronization**. Existing Personal data in the mapped roots is moved into a private backup before replacement. Source data is copied, never moved or intentionally written. All copied regular files are independent, not symlinks or hardlinks to the source. A file permission error, symlinked profile layout, unsupported sandbox container, incompatible session shape, or changed source/target data stops the operation rather than silently claiming a complete import.

## Backup and recovery

Backups are stored under `~/Library/Application Support/Search Personal Profile Backups/`, in a timestamped directory with user-only permissions. The exact `--restore` command is printed after success. The helper can also recover from its journal after an interrupted installation; if automatic recovery cannot finish, keep both apps closed and retain the reported backup.

Manual rollback restores pre-import Personal data and preferences. Data displaced by that rollback is kept inside the backup rather than discarded. The fault-injection test verifies automatic rollback after file replacement and a preference write. This is not a guarantee of filesystem-wide atomicity under power loss or simultaneous external modification.

Backups contain sensitive browsing and session data. Do not upload or share them. The helper makes no network requests, accesses no Keychain items, sends no browser-automation events and does not change default-browser registration or macOS security settings.

## What was tested

The test uses two copies of the actual shipped header-fixed application under unique disposable source/target bundle IDs and profile names. It runs the ordinary application without SEARCH_PROBE, using real WebKit default and UUID-named stores. It does not use the user's installed official app or actual accounts.

A local HTTP fixture seeds a persistent HttpOnly cookie, a script-readable cookie, localStorage and IndexedDB in the source default store and a separate Space. Another independent profile receives its own prior data. After migration, the target application is launched: the server receives the copied HttpOnly cookie and the page reports the other three persisted values, in both default and named stores. Rollback is then tested by launching the target with its original cookie and storage state again.

Other checks cover tab/history/bookmark byte preservation, extension-directory copying, Space activation, unchanged source data even after the copied profile runs, private backups, dry-run behavior, injected write failure recovery, rejecting symlinked sources and refusing migration while an application is running.

## Limits

- Only data that the official application has persisted to disk is available. Private sessions, unsaved in-page state and memory-only/session cookies may not survive quitting and cannot be reconstructed by this offline helper.
- Copying persisted cookies and storage does not guarantee every real service will retain authentication; expiration or server-side/device-bound authentication can still require login. No real external account was used to validate this.
- Keychain passwords and passkeys are not migrated. The personal ad-hoc application still lacks Apple's browser passkey entitlement.
- Extension files are copied, but individual extension login state, Keychain integration, external native hosts and runtime behavior were not exercised. Some extensions may require reauthorization.
- The helper leaves scripted control, extensions in private windows and passkeys off, does not transfer capture permissions, clears pending Space deletion requests, and keeps upstream automatic installation disabled.
- Only the current non-sandboxed Search layout and compatible persisted session formats are supported. Old Office Browser and sandboxed variants require separate handling instead of guessed paths.
