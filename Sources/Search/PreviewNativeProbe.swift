#if DEBUG
import AppKit

/// Native acceptance for a disposable SEARCH_PROBE process only. Every control,
/// menu, and pixel comes from this process; this never uses cross-app AX access.
@MainActor
enum PreviewNativeProbe {
    static func run(_ request: [String: Any]) -> [String: Any] {
        guard Store.testing else { return ["error": "test process required"] }
        let window: NSWindow?
        if let number = request["window"] as? Int {
            guard let own = NSApp.windows.first(where: { $0.windowNumber == number }) else {
                return ["error": "owned window missing"]
            }
            window = own
        } else {
            window = NSApp.modalWindow ?? Links.window
        }
        switch request["action"] as? String ?? "nodes" {
        case "close-window":
            guard let window else { return ["error": "no window"] }
            window.performClose(nil)
            return ["closed": !window.isVisible]
        case "focus":
            guard let window else { return ["error": "no window"] }
            NSApp.activate()
            window.makeKeyAndOrderFront(nil)
            return ["number": window.windowNumber, "key": window.isKeyWindow]
        case "fields":
            guard let window, let content = window.contentView else { return ["error": "no window"] }
            return ["fields": views(content).compactMap { $0 as? NSTextField }.map { field in
                ["value": field.stringValue, "editable": field.isEditable,
                 "focused": field.currentEditor() != nil && window.firstResponder === field.currentEditor(),
                 "identifier": field.identifier?.rawValue ?? ""] as [String: Any]
            }]
        case "nodes":
            guard let window else { return ["error": "no window"] }
            return ["modal": NSApp.modalWindow != nil, "nodes": nodes(window).enumerated().map { index, node in
                let frame = (property(node, "accessibilityFrame") as? NSValue)?.rectValue ?? .zero
                return ["index": index, "role": text(node, "accessibilityRole"),
                        "label": text(node, "accessibilityLabel"), "title": text(node, "accessibilityTitle"),
                        "value": String(describing: property(node, "accessibilityValue") ?? ""),
                        "enabled": property(node, "isAccessibilityEnabled") as? Bool ?? false,
                        "focused": property(node, "isAccessibilityFocused") as? Bool ?? false,
                        "frame": [frame.minX, frame.minY, frame.width, frame.height]] as [String: Any]
            }]
        case "press":
            guard let window else { return ["error": "window required"] }
            let elements = nodes(window)
            let index: Int?
            if let label = request["label"] as? String {
                index = elements.firstIndex {
                    text($0, "accessibilityRole") != "AXStaticText"
                        && [text($0, "accessibilityLabel"), text($0, "accessibilityTitle")].contains(label)
                }
            } else { index = request["index"] as? Int }
            guard let index else { return ["error": "control required"] }
            guard elements.indices.contains(index) else { return ["error": "node missing"] }
            return ["pressed": property(elements[index], "accessibilityPerformPress") as? Bool ?? false]
        case "menu":
            guard let path = request["titles"] as? [String], !path.isEmpty,
                  let main = NSApp.mainMenu else { return ["error": "menu titles required"] }
            var menu = main
            for (depth, title) in path.enumerated() {
                if request["action"] as? String == "menu" {
                    menu.delegate?.menuNeedsUpdate?(menu)
                    menu.update()
                }
                guard let item = menu.items.first(where: { $0.title == title }) else {
                    return ["error": "menu item missing: \(title)", "available": menu.items.map(\.title)]
                }
                if depth == path.count - 1 {
                    guard item.isEnabled else { return ["error": "menu item disabled"] }
                    menu.performActionForItem(at: menu.index(of: item))
                    return ["invoked": title]
                }
                guard let child = item.submenu else { return ["error": "submenu missing"] }
                menu = child
            }
            return ["error": "empty menu path"]
        case "click":
            guard let window, let x = request["x"] as? Double, let y = request["y"] as? Double else { return ["error": "point required"] }
            let point = NSPoint(x: x, y: window.frame.height - y)
            for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                if let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: PreviewProbe.modifiers(request),
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, eventNumber: 0, clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0) {
                    window.sendEvent(event)
                }
            }
            return ["posted": true]
        case "key":
            guard let window, let code = request["code"] as? Int else { return ["error": "key code required"] }
            let chars = request["chars"] as? String ?? ""
            for type in [NSEvent.EventType.keyDown, .keyUp] {
                if let event = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: PreviewProbe.modifiers(request),
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, characters: chars, charactersIgnoringModifiers: chars,
                    isARepeat: false, keyCode: UInt16(clamping: code)) {
                    NSApp.postEvent(event, atStart: false)
                }
            }
            return ["posted": true]
        case "hit-test":
            guard let window, let root = window.contentView?.superview,
                  let x = request["x"] as? Double, let y = request["y"] as? Double
            else { return ["error": "window and point required"] }
            let point = NSPoint(x: x, y: window.frame.height - y)
            let hit = root.hitTest(root.convert(point, from: nil))
            return ["hit": hit.map { String(describing: type(of: $0)) } ?? "none",
                    "frame": hit.map { NSStringFromRect($0.frame) } ?? "",
                    "key": window.isKeyWindow, "visible": window.isVisible,
                    "active": NSApp.isActive,
                    "backdrops": views(root).filter { $0.identifier?.rawValue == "page-chrome-backdrop" }.map {
                        ["alpha": $0.alphaValue, "filters": $0.backgroundFilters.map {
                            ["name": $0.name, "radius": $0.value(forKey: "inputRadius") ?? 0] as [String: Any]
                        }] as [String: Any]
                    },
                    "effects": views(root).compactMap { $0 as? NSVisualEffectView }.map {
                        ["frame": NSStringFromRect($0.frame), "alpha": $0.alphaValue,
                         "identifier": $0.identifier?.rawValue ?? "", "hidden": $0.isHidden,
                         "mode": $0.blendingMode.rawValue, "material": $0.material.rawValue] as [String: Any]
                    }]
        case "resize":
            guard let window, let width = request["width"] as? Double,
                  let height = request["height"] as? Double else { return ["error": "size required"] }
            window.setContentSize(NSSize(width: width, height: height))
            window.displayIfNeeded()
            return ["width": window.frame.width, "height": window.frame.height]
        case "shot":
            guard let view = window?.contentView, let path = request["path"] as? String,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
            else { return ["error": "window or path missing"] }
            view.layoutSubtreeIfNeeded()
            view.cacheDisplay(in: view.bounds, to: bitmap)
            guard let data = bitmap.representation(using: .png, properties: [:]) else { return ["error": "PNG failed"] }
            do { try data.write(to: URL(fileURLWithPath: path), options: .atomic) }
            catch { return ["error": error.localizedDescription] }
            return ["path": path, "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh]
        default:
            return ["error": "unknown native action"]
        }
    }

    private static func views(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(views)
    }

    private static func property(_ node: NSObject, _ getter: String) -> Any? {
        guard node.responds(to: NSSelectorFromString(getter)) else { return nil }
        return node.value(forKey: getter)
    }

    private static func text(_ node: NSObject, _ getter: String) -> String {
        // SwiftUI can return attributed labels despite AppKit's NSString type.
        guard let value = property(node, getter) else { return "" }
        if let attributed = value as? NSAttributedString { return attributed.string }
        return value as? String ?? ""
    }

    private static func nodes(_ root: NSObject) -> [NSObject] {
        var seen = Set<ObjectIdentifier>()
        func walk(_ node: NSObject, depth: Int) -> [NSObject] {
            guard depth < 30, seen.insert(ObjectIdentifier(node as AnyObject)).inserted else { return [] }
            // SwiftUI's accessibility objects expose these Objective-C getters
            // without declaring NSAccessibilityProtocol conformance.
            return [node] + (property(node, "accessibilityChildren") as? [NSObject] ?? [])
                .flatMap { walk($0, depth: depth + 1) }
        }
        return walk(root, depth: 0)
    }
}

#endif
