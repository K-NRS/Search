#!/usr/bin/env python3
"""Apply the viewport fix once. --probes-only reproduces the original defect first.
All test controls remain DEBUG-only. Release builds contain no new instrumentation.
"""
from pathlib import Path
import argparse
ROOT = Path(__file__).resolve().parents[1]

def replace(file, old, new):
    path = ROOT / file
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected source in {file}: {old[:100]!r}")
    path.write_text(text.replace(old, new))

def probes():
    replace('Sources/Search/NativeProbe.swift', 'import AppKit\n', 'import AppKit\nimport WebKit\n')
    replace('Sources/Search/NativeProbe.swift', '        case "accent":\n', '''        case "chrome-config":
            // Guarded by Store.testing above; this entire file is DEBUG-only.
            browser.welcoming = false
            browser.tuning = false
            if let value = request["height"] as? Double { browser.prefs.topBarHeight = min(52, max(30, value)) }
            if let value = request["transparency"] as? Double { browser.prefs.chromeTransparency = min(1, max(0, value)) }
            if let value = request["blur"] as? Double { browser.prefs.chromeBlur = min(1, max(0, value)) }
            if let value = request["sidebar"] as? Bool { browser.prefs.sidebar = value }
            if let value = request["folded"] as? Bool { browser.folded = value }
            if let value = request["peeking"] as? Bool { browser.peeking = value }
            if let value = request["bookmarksBar"] as? Bool {
                if value, let url = browser.active?.address { browser.bookmarks.add(url, title: "Viewport fixture") }
                browser.prefs.bookmarksBar = value
            }
            return ["configured": true]
        case "chrome-geometry":
            guard let window, let web = browser.active?.built else { return ["error": "active view required"] }
            let frame = web.convert(web.bounds, to: nil)
            var inset = NSEdgeInsetsZero
            var supportsInsets = false
            #if compiler(>=6.2)
            if #available(macOS 26.0, *) {
                inset = web.obscuredContentInsets
                supportsInsets = true
            }
            #endif
            return ["frame": [frame.minX, window.frame.height - frame.maxY, frame.width, frame.height],
                    "window": [window.frame.width, window.frame.height],
                    "insets": [inset.top, inset.left, inset.bottom, inset.right],
                    "supportsInsets": supportsInsets,
                    "sidebar": browser.prefs.sidebar, "sideWidth": browser.prefs.sideWidth,
                    "height": browser.prefs.topBarHeight, "bookmarksHeight": BookmarksBar.height,
                    "folded": browser.folded, "peeking": browser.peeking,
                    "reducedTransparency": NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency,
                    "webID": String(describing: ObjectIdentifier(web)),
                    "stage": web.superview.map { String(describing: type(of: $0)) } ?? "none"]
        case "chrome-pointer":
            guard let window, let x = request["x"] as? Double, let y = request["y"] as? Double else {
                return ["error": "window coordinates required"]
            }
            window.makeKeyAndOrderFront(nil)
            NSApp.activate()
            let point = NSPoint(x: x, y: window.frame.height - y)
            for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                guard let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, eventNumber: 0, clickCount: 1, pressure: 1) else { continue }
                NSApp.postEvent(event, atStart: false)
            }
            return ["posted": true]
        case "accent":
''')

def fix():
    replace('Sources/Search/Chrome.swift', 'import CoreImage\n', 'import CoreImage\nimport WebKit\n')
    replace('Sources/Search/Chrome.swift', '''    var radius: Double = 0
}
''', '''    var radius: Double = 0

    /// Only overlay real web content when WebKit can reserve its layout viewport.
    /// Older SDKs/OS versions use an ordinary, unobscured view frame instead.
    static var supportsViewportInsets: Bool {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) { return true }
        #endif
        return false
    }

    @MainActor
    func apply(to web: WKWebView) {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            let value = NSEdgeInsets(top: max(0, min(top, web.bounds.height)),
                                    left: max(0, min(side, web.bounds.width)), bottom: 0, right: 0)
            let old = web.obscuredContentInsets
            // Avoid a new WebKit layout on every native layout pass.
            if old.top != value.top || old.left != value.left || old.bottom != 0 || old.right != 0 {
                web.obscuredContentInsets = value
            }
        }
        #endif
    }
}
''')
    replace('Sources/Search/App.swift', '''            // strip by default. Transparency lets the page extend beneath them.
''', '''            // strip by default. On supported WebKit versions only its painted
            // background extends under transparent chrome; its layout viewport
            // is inset so fixed/sticky site headers and page controls stay visible.
''')
    replace('Sources/Search/App.swift', '''                top: overlayChrome && !browser.prefs.sidebar ? visibleTop : 0,
                side: overlayChrome && browser.prefs.sidebar && (sidebar || browser.peeking) ? browser.prefs.sideWidth : 0,
''', '''                top: overlayChrome ? visibleTop : 0,
                side: overlayChrome ? visibleSide : 0,
''')
    replace('Sources/Search/App.swift', '''        browser.prefs.chromeTransparency > 0 && !reduceTransparency
''', '''        PageChrome.supportsViewportInsets && browser.prefs.chromeTransparency > 0 && !reduceTransparency
''')
    replace('Sources/Search/App.swift', '''        return browser.folded && browser.peeking ? browser.prefs.topBarHeight : chrome.height
    }
''', '''        return !browser.prefs.sidebar && browser.folded && browser.peeking
            ? browser.prefs.topBarHeight : chrome.height
    }

    private var visibleSide: CGFloat {
        guard browser.active?.immersed != true else { return 0 }
        return browser.prefs.sidebar && (sidebar || browser.peeking) ? browser.prefs.sideWidth : 0
    }
''')
    replace('Sources/Search/Stage.swift', '''    private let blur = BackgroundBlurView(frame: .zero)
''', '''    private let topBlur = BackgroundBlurView(frame: .zero)
    private let sideBlur = BackgroundBlurView(frame: .zero)

    override func willRemoveSubview(_ subview: NSView) {
        // Mini, Peek, Float and fullscreen reuse the very same web view. A
        // departing page must not carry this window's chrome into that surface.
        if let web = subview as? WKWebView { PageChrome().apply(to: web) }
        super.willRemoveSubview(subview)
    }
''')
    replace('Sources/Search/Stage.swift', 'view !== wanted && view !== blur &&', 'view !== wanted && view !== topBlur && view !== sideBlur &&')
    replace('Sources/Search/Stage.swift', '''        guard let wanted, window != nil else { blur.removeFromSuperview(); return }
''', '''        guard let wanted, window != nil else {
            topBlur.removeFromSuperview()
            sideBlur.removeFromSuperview()
            return
        }
''')
    replace('Sources/Search/Stage.swift', '''        if chrome.radius > 0, chrome.top > 0 || chrome.side > 0 {
            if subviews.last !== blur { addSubview(blur, positioned: .above, relativeTo: wanted) }
            blur.frame = chrome.side > 0
                ? NSRect(x: 0, y: 0, width: min(bounds.width, chrome.side), height: bounds.height)
                : NSRect(x: 0, y: bounds.height - chrome.top, width: bounds.width, height: chrome.top)
            blur.setRadius(chrome.radius)
        } else {
            blur.removeFromSuperview()
        }
    }
''', '''        if let web = wanted as? WKWebView { chrome.apply(to: web) }
        let side = min(bounds.width, max(0, chrome.side))
        let top = min(bounds.height, max(0, chrome.top))
        // In sidebar mode a bookmarks bar can obscure the top as well. Use
        // disjoint rectangles: their corner must not receive Gaussian blur twice.
        place(sideBlur, in: NSRect(x: 0, y: 0, width: side, height: bounds.height), above: wanted)
        place(topBlur, in: NSRect(x: side, y: bounds.height - top,
                                  width: bounds.width - side, height: top), above: wanted)
    }

    private func place(_ blur: BackgroundBlurView, in frame: NSRect, above page: NSView) {
        guard chrome.radius > 0, frame.width > 0, frame.height > 0 else {
            blur.removeFromSuperview()
            return
        }
        if blur.superview !== self { addSubview(blur, positioned: .above, relativeTo: page) }
        blur.frame = frame
        blur.setRadius(chrome.radius)
    }
''')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probes-only', action='store_true')
    args = parser.parse_args()
    probes()
    if not args.probes_only:
        fix()
    print('Viewport observations installed' if args.probes_only else 'Viewport fix applied (or already present)')
