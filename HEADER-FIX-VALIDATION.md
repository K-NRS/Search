# Search Personal: website header / viewport fix

Validated on 2026-09-25. This supersedes the initial downloadable package for the header-overlap defect; the original PR histories and upstream integration remain intact.

## Exact application

- Source commit: `b09432de2f076fc0a6fa9d44714f4cc59ee42d61`.
- Branch: `knrs/integrated-2026-09-25`.
- Version: 1.0.3, build `202609251409`.
- Universal executable: arm64 and x86_64; minimum deployment target macOS 14.
- Build and validation: https://github.com/K-NRS/Search/actions/runs/36145098698
- Artifact: `Search-Personal-viewport-fix-universal`.
- Downloadable ZIP: 3,991,609 bytes. The conversation attachment is named `Search-Personal-header-fixed.zip`; its bytes are identical to the CI-produced inner `Search-Personal-universal.zip`.

ZIP SHA-256:

```text
5547a7027c446d6ccff2473a3c9ea50cf9e988688ec9c1e90acdf99ae83879b0
```

`Search Personal.app/Contents/MacOS/Search` SHA-256:

```text
15648978994813da7c5922c66707c59647f4bba84b79412f46e1d08c58925b82
```

## Cause and repair

The original transparent layout extended the WKWebView beneath the browser's tab bar and sidebar without reserving that obscured area in its layout viewport. Fixed and sticky website headers were consequently painted behind browser controls.

On macOS 26 and newer, `PageChrome` now supplies WebKit's public `obscuredContentInsets`. The page can still paint a backdrop under transparent chrome, while its actual layout viewport excludes the browser controls. Height changes, sidebars and the bookmarks bar are accounted for together. Top and side blur regions are disjoint, avoiding a double-blurred corner.

Older operating systems and toolchains use a physically inset web-view frame instead. They do not render live webpage content under transparent chrome; they do keep the page unobscured. System Reduce Transparency remains respected. No website CSS, DOM padding, reload, or replacement WKWebView is used for this repair.

Insets are cleared when a web view leaves its stage, so another hosting surface does not inherit the original window's chrome dimensions. Changes are limited to the native layout and DEBUG-only test observations; test observations do not ship in release builds.

## Completed checks

Both jobs in the linked build completed successfully:

| Environment | Baseline reproduction | Fixed viewport checks |
| --- | --- | --- |
| macOS 15.7.9, Xcode 16.4, Apple Silicon | 2/2 | 137/137 |
| macOS 26.6.2, Xcode 26.6, Apple Silicon | 2/2 | 137/137 |

The 137 checks in each environment cover 19 layout cases: transparent and opaque top bars, 30/42/52-point heights, blur off/max, scrolling, fixed/sticky website navigation, top and side layouts with/without bookmarks, folded layouts, window resizing, repeated appearance changes, tab changes and restart with saved settings. Native hit testing and application-queue mouse events confirm the website's top button receives clicks. Form state and the original WebKit page identity survive layout changes. Composited screenshots document the clipped original and the unobscured fixed layout.

The tests use uniquely identified disposable apps/profiles and a local HTTP fixture. The runner's Reduce Transparency setting was disabled before these tests, because otherwise the original defect is masked by the opaque fallback. No accessibility setting on the user's Mac was changed. Switching layouts intentionally resets transient fold state; the fixture explicitly folds the new layout afterward.

Debug compilation succeeded on both SDK generations. Release compilation succeeded for both architectures on macOS 26. The universal bundle passed strict code-signature verification. The exact release ZIP was extracted, then its actual production app passed **14/14** normal-startup checks without SEARCH_PROBE: startup, session restoration, real page rendering, form state, navigation, website storage, update suppression, profile isolation and scripted-control consent. The downloaded artifact checksum, embedded source commit, bundle identity and Mach-O architectures were independently checked again after retrieval.

This is targeted coverage for this fix, not a claim that every original PR test suite or every website was retested. Runtime checks were on Apple Silicon; Intel was compiled and its slice verified, not runtime-tested. macOS 14 was not runtime-tested. The website fixture is not the live authenticated Noras website. No system-wide pointer injection is claimed.

## Replacement and data safety

Quit Search Personal before replacing the old application in `/Applications` or `~/Applications` with the new **Search Personal.app**. Do not delete its Application Support folder.

The existing personal identity is unchanged: bundle ID `tech.noras.search.personal`, profile `~/Library/Application Support/Search Personal/`. This is an update to the same personal app, not a new empty profile. No profile migration, reset, or deletion was added. Upstream self-updates remain disabled.

The application remains ad-hoc signed, not Developer-ID signed or Apple-notarized. Signature verification proves integrity, not Apple approval. Internet-download quarantine may still require approval on the receiving Mac. Any quarantine exception should be limited to this exact verified application, never a global Gatekeeper disable.
