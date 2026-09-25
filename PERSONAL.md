# Search Personal

Personal integration for K-NRS. Original Search is by Office Commun; the upstream license and authorship are retained. This is a personal fork, not an official Office Commun release.

## Included source

The integration branch `knrs/integrated-2026-09-25` retains the complete histories of PRs #54, #190, #192, #225, #227 and #231, on upstream `491f3214063212fac176a7ad95467f0821040451` (2026-09-24).

Features: tab groups and space emoji markers; extension command recording; owned offscreen extension documents; MRU tab cycling with configurable bindings; live Mini and Peek windows; adjustable bar height, transparency, blur and page accents. The improved upstream Netflix caption rule is retained instead of replacing it with the older PR version. Upstream Little windows remain available when Mini is off.

## Install the built application

Download the `Search-Personal-universal-macOS` artifact from a completed **Build Search Personal** run. Extract the artifact, then extract `Search-Personal-universal.zip`. Move **Search Personal.app** to `/Applications` or `~/Applications` and open it.

The binary contains both Apple Silicon and Intel architectures. The minimum deployment target is macOS 14; extension features require macOS 15.4 or later. The application is ad-hoc signed, not Developer-ID-signed or Apple-notarized. macOS may require allowing this specific app in System Settings > Privacy & Security > Open Anyway. Do not disable Gatekeeper globally.

Check the archive against the included SHA256SUMS before installation. The app's Info.plist records the exact source commit in `SearchIntegrationCommit`.

## Data and update safety

This package uses bundle identifier `tech.noras.search.personal`, profile folder `~/Library/Application Support/Search Personal/`, and its own keychain label/security domain. It does not automatically import, move, delete or reuse the installed upstream Search profile. Existing history, cookies, tabs and passwords remain with the old app. Test runs use separate temporary profiles.

The upstream updater is disabled by the `SearchDisableUpdates` bundle flag at every check/install entry point. Install subsequent personal builds manually from this branch. Installation does not change your default browser.

## Build from source

With Xcode selected on a Mac:

```sh
bash build-personal.sh
```

Output: `build/Search-Personal-universal.zip`. This script builds both architectures, packages the app, applies its personal identity, signs it ad hoc and verifies the signature/architectures.

## Validation

Read the structured `summary.json` and individual logs inside the corresponding `Search-Personal-build-and-tests` artifact. Successful compilation is not a claim that all GUI, compositor or live-site tests passed. The workflow records failures and permission/environment limitations explicitly. No historical PR test count is presented as a result for this combined build.

`.integration/reconcile.py` contains the reviewed conflict resolutions and aborts on unexpected source changes. Original `main` and PR branches are not rewritten; merge commits preserve their ancestry.
