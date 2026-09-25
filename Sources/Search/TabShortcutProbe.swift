#if DEBUG
import AppKit

/// Actual native controls and menu equivalents, exposed only in a disposable
/// test process. SwiftUI AX objects need not declare NSAccessibilityProtocol.
@MainActor
enum TabShortcutProbe {
    static func run(_ request: [String: Any]) -> [String: Any] {
        guard Store.testing else { return ["error": "test process required"] }
        let window = NSApp.modalWindow ?? Links.window
        switch request["action"] as? String ?? "nodes" {
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
            guard let window, let index = request["index"] as? Int else { return ["error": "index required"] }
            let elements = nodes(window)
            guard elements.indices.contains(index) else { return ["error": "node missing"] }
            return ["pressed": property(elements[index], "accessibilityPerformPress") as? Bool ?? false]
        case "menu", "menu-shortcut":
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
                    if request["action"] as? String == "menu-shortcut" {
                        return ["key": item.keyEquivalent, "modifiers": item.keyEquivalentModifierMask.rawValue]
                    }
                    guard item.isEnabled else { return ["error": "menu item disabled"] }
                    menu.performActionForItem(at: menu.index(of: item))
                    return ["invoked": title]
                }
                guard let child = item.submenu else { return ["error": "submenu missing"] }
                menu = child
            }
            return ["error": "empty menu path"]
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
        default: return ["error": "unknown shortcut UI action"]
        }
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
