# Invisible extension work

## Scope

The requested outcome is dictionary translation without a temporary tab in
the tab strip. The agreed implementation fixes Search's existing offscreen
support. Ordinary inactive tabs retain their browser semantics. No Lokal
changes or new dependency are needed.

## Reproduced failures

Lokal 1.5.9 creates its offscreen document during startup, then calls
`runtime.getContexts({contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [...]})`
before each lookup. Search returned `[]` unconditionally even while
`offscreen.hasDocument()` returned `true`. The duplicate create failed;
Lokal surfaced `Failed to fetch translations`.

Native regressions also reproduced early creation completion, stale documents
after reload/disable, and premature discovery during initial load. Real Lokal
then exposed `Invalid call to runtime.sendMessage(). Tab not found.` from its
iframe content script: WebKit needs a registered page adapter for the sender.

A separate regression reproduced a reply lost at ten seconds: Search's old
message shim sent an explicit empty response from uninterested extension
pages. That response won before a slower worker could finish. The shim now
declines normally, allowing WebKit to wait for the context handling the request.

The initial investigation forced `tabScrape: true`, bypassing the broken
default path. A successful fallback lookup did not prove offscreen support.

## Implementation

`ExtensionOffscreen.swift` owns each extension's document, initial load and
discovery metadata in one record. It reserves the single-document slot before
awaiting, publishes a context only after initial load, and releases it on
close, failure, process termination or extension unload. Recreated documents
get fresh IDs. Context filters apply only to the requesting extension.

The invisible document uses WebKit's `.throttle` policy, allowing detached
DOM work and replies while retaining background CPU throttling. Ordinary
browser-tab scheduling remains unchanged.

Its `WKWebExtensionTab` adapter is registered only with the owning extension
context, with no window/index and no entry in `Browser.tabs`. This supports
iframe messages without adding to the strip, sidebar, session, history or
`tabs.query()`. The public WebKit registration API can still emit internal
`tabs.onCreated/onRemoved` events to the owner; other extensions are not notified.

WebKit also excludes every frame of the sender's page from runtime delivery,
so iframe-to-parent messages require a relay through another owner context.
Only extensions declaring offscreen capability get this compatibility path.
Native delivery verifies the actual offscreen tab ID and URL, preserves the
message and sender, and deduplicates deliveries across receivers. Pending
replies are rejected on close; only completed replies enter the bounded cache.

`runtime.getContexts` remains limited to Search-managed offscreen documents.
Other context types were already unsupported. The current WebKit runtime has
no native `getContexts`; a future native implementation would need its results
composed with Search-managed contexts.

## Verification

Launch an isolated world with `SEARCH_PROBE=<world>` and `SEARCH_MEASURE=1`,
with its bench enabled. Ordinary test mode overrides scheduling and App Nap;
measurement mode preserves production behavior.

```sh
python3 tests/offscreen.py --world background-tabs
python3 tests/background-tabs.py --world background-tabs
```

The offscreen regression uses real WebKit APIs, two temporary extensions and
a local HTTP server. It checks immediate messages, context filters, duplicate
creation, pending-load cancellation, invalid URLs, absolute own URLs, iframe
content-script messages and replies, worker/workerless delivery, deduplication,
sender preservation, pending-message teardown, isolation, recreation, reload/disable cleanup, and
unchanged native tabs/selection. It also checks a real worker reply delayed
twelve seconds and completion of genuinely unanswered requests. Browser APIs
and dictionary data are not mocked.

Real acceptance uses unchanged `../modern-tureng/extension/dist` (Lokal 1.5.9),
default Offscreen mode and uncached lookups without `tabScrape: true`. Verify
actual dictionary rows, repeat with another word, reload and repeat, and compare
visible tabs. Cached results and forced tab fallback do not establish acceptance.

### Observed results (23 September 2026)

- Debug and packaged release builds: 315 offscreen checks passed in each;
  the slow worker returned its nonce at 12.002 and 12.004 seconds respectively.
  The previous shim lost it at 10.002 seconds.
- Ordinary background tabs: all 13 checks passed, including autonomous work
  with the controller closed and unchanged foreground selection.
- Unchanged Lokal, default Offscreen, after reload: uncached `resilient`
  returned 43 Tureng rows, including `dirençli`, with identical visible tabs.
  Earlier popup UI acceptance showed 46 rows for `durable`, and a second
  post-reload lookup returned 19 rows for `steadfast`.
- Packaged release, fresh extension storage: typing `tenacious` into the
  actual Lokal popup returned 29 Tureng rows, including `azimli` and `inatçı`.
  The tab strip retained its single blank tab. Popup screenshot inspected.
- Cambridge returned an intact response after 10.6 seconds but zero rows.
  This establishes message delivery, not Cambridge dictionary acceptance.
- One debug run timed out during a post-reload/re-enable create operation;
  the next complete run passed. No cause was established. The harness now
  records the operation and last completed assertion on controller timeouts.
- Release bundle built at `build/Search.app`; its ad-hoc signature verified.
  The installed application and personal profile were not modified.

### PR #54 integration (24 September 2026)

The implementation was ported onto the current extension-shortcut and floating
video changes, preserving upstream embedded-frame API routing and shortcut
teardown. The packaged release passed all 454 native checks: 315 offscreen,
13 background-tab, 110 shortcut and 16 floating-video checks. Its ad-hoc
signature and the integration diff were verified.

Unchanged Lokal 1.5.9 returned 43 Tureng rows for an uncached `resilient` lookup
and 46 rows for an uncached `durable` lookup after extension reload. Each lookup
reported one offscreen document; visible tab IDs and foreground selection
remained unchanged throughout polling. Tests used the isolated
`offscreen-pr54-20260924` world with production scheduling enabled.

## References

- [Chrome offscreen lifecycle](https://developer.chrome.com/docs/extensions/reference/api/offscreen)
- [Chrome context filters](https://developer.chrome.com/docs/extensions/reference/api/runtime#type-ContextFilter)
- [Apple inactive scheduling](https://developer.apple.com/documentation/webkit/wkpreferences/inactiveschedulingpolicy-swift.property)
- [WebKit sender lookup](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/Extensions/Cocoa/API/WebExtensionContextAPIRuntimeCocoa.mm)
- [WebKit tab queries](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/Extensions/Cocoa/API/WebExtensionContextAPITabsCocoa.mm)
