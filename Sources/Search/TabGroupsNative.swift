import AppKit

/// Native acceptance confined to this disposable process and its own window.
/// No system Accessibility APIs, global events, or other applications are used.
extension Browser {
    func groupingNative(_ request: [String: Any]) -> [String: Any] {
        guard Store.testing else { return ["error": "Native grouping checks require a test run"] }
        guard let main = Links.window else { return ["error": "No window"] }
        let window = main.attachedSheet ?? main
        guard let content = window.contentView else { return ["error": "No content"] }
        let views = GroupNative.views(content)
        switch request["action"] as? String ?? "state" {
        case "new-group":
            askForGroup()
            return ["opened": true]
        case "rename-group":
            guard let raw = request["group"] as? String,
                  let group = groups.first(where: { $0.id.uuidString == raw }) else { return ["error": "Group missing"] }
            askToRenameGroup(group)
            return ["opened": true]
        case "field":
            guard let identifier = request["identifier"] as? String, let value = request["value"] as? String,
                  let field = views.compactMap({ $0 as? NSTextField }).first(where: { $0.identifier?.rawValue == identifier && !$0.isHidden })
            else { return ["error": "Field missing"] }
            field.stringValue = value
            NotificationCenter.default.post(name: NSControl.textDidChangeNotification, object: field)
            return ["value": value]
        case "choose":
            guard let identifier = request["identifier"] as? String, let title = request["title"] as? String,
                  let popup = views.compactMap({ $0 as? NSPopUpButton }).first(where: { $0.identifier?.rawValue == identifier && !$0.isHidden }),
                  popup.itemTitles.contains(title) else { return ["error": "Choice missing"] }
            popup.selectItem(withTitle: title)
            if let action = popup.action { popup.sendAction(action, to: popup.target) }
            return ["selected": popup.titleOfSelectedItem ?? ""]
        case "button":
            guard let title = request["title"] as? String,
                  let button = views.compactMap({ $0 as? NSButton }).first(where: { $0.title == title })
            else { return ["error": "Button missing"] }
            guard button.isEnabled else { return ["error": "Button disabled"] }
            button.performClick(nil)
            return ["pressed": title]
        case "click-group":
            guard main.attachedSheet == nil, let raw = request["group"] as? String,
                  let group = groups.first(where: { $0.id.uuidString == raw }),
                  let menu = GroupNative.nodes(main).first(where: { GroupNative.text($0, "accessibilityTitle") == "\(group.name) group actions" })
            else { return ["error": "Visible group heading missing"] }
            let frame = GroupNative.frame(menu)
            let point = main.convertPoint(fromScreen: NSPoint(x: frame.minX - 40, y: frame.midY))
            guard main.contentView?.bounds.contains(point) == true else { return ["error": "Group heading is outside the viewport"] }
            for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                if let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: main.windowNumber,
                    context: nil, eventNumber: 0, clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0) {
                    NSApp.postEvent(event, atStart: false)
                }
            }
            return ["posted": true]
        case "shot":
            guard let path = request["path"] as? String,
                  let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds)
            else { return ["error": "Snapshot path missing"] }
            content.layoutSubtreeIfNeeded()
            content.cacheDisplay(in: content.bounds, to: bitmap)
            guard let data = bitmap.representation(using: .png, properties: [:]) else { return ["error": "PNG failed"] }
            do { try data.write(to: URL(fileURLWithPath: path), options: .atomic) }
            catch { return ["error": error.localizedDescription] }
            return ["path": path, "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh]
        case "state":
            let controls: [[String: Any]] = views.compactMap { view in
                guard let control = view as? NSControl, !control.isHidden else { return nil }
                return ["identifier": control.identifier?.rawValue ?? "", "value": control.stringValue,
                        "title": (control as? NSButton)?.title ?? "", "enabled": control.isEnabled,
                        "focused": (control as? NSTextField).map { window.firstResponder === $0.currentEditor() && $0.currentEditor() != nil } ?? false]
            }
            return ["sheet": main.attachedSheet != nil, "controls": controls,
                    "nodes": GroupNative.nodes(window).map { node in
                        ["role": GroupNative.text(node, "accessibilityRole"),
                         "title": GroupNative.text(node, "accessibilityTitle"),
                         "label": GroupNative.text(node, "accessibilityLabel"),
                         "frame": GroupNative.rect(GroupNative.frame(node))] as [String: Any]
                    },
                    "scrolls": views.compactMap { $0 as? NSScrollView }.map {
                        ["frame": GroupNative.rect($0.frame), "clip": GroupNative.rect($0.contentView.bounds),
                         "document": GroupNative.rect($0.documentView?.bounds ?? .zero),
                         "flipped": $0.documentView?.isFlipped ?? false] as [String: Any]
                    },
                    "missingSymbols": TabMark.symbols.filter { NSImage(systemSymbolName: $0, accessibilityDescription: nil) == nil }]
        default:
            return ["error": "Unknown native grouping check"]
        }
    }
}

@MainActor
private enum GroupNative {
    static func views(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(views) }
    static func rect(_ rect: NSRect) -> [CGFloat] { [rect.minX, rect.minY, rect.width, rect.height] }
    static func frame(_ object: NSObject) -> NSRect {
        if let element = object as? NSAccessibilityProtocol { return element.accessibilityFrame() }
        if let element = object as? NSAccessibilityElementProtocol { return element.accessibilityFrame() }
        return .zero
    }
    static func text(_ object: NSObject, _ getter: String) -> String {
        guard object.responds(to: NSSelectorFromString(getter)),
              let value = object.perform(NSSelectorFromString(getter))?.takeUnretainedValue() else { return "" }
        // SwiftUI sometimes supplies attributed text despite the NSString API.
        if let attributed = value as? NSAttributedString { return attributed.string }
        return value as? String ?? ""
    }
    static func nodes(_ root: NSObject) -> [NSObject] {
        var seen = Set<ObjectIdentifier>()
        func walk(_ object: NSObject, depth: Int) -> [NSObject] {
            guard depth < 30, seen.insert(ObjectIdentifier(object)).inserted else { return [] }
            let selector = NSSelectorFromString("accessibilityChildren")
            let children = object.responds(to: selector)
                ? object.perform(selector)?.takeUnretainedValue() as? [NSObject] ?? [] : []
            return [object] + children.flatMap { walk($0, depth: depth + 1) }
        }
        return walk(root, depth: 0)
    }
}
