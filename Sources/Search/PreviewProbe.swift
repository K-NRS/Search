import AppKit

/// Integration operations are available only in an isolated SEARCH_PROBE run.
@MainActor
enum PreviewProbe {
    static func modifiers(_ request: [String: Any]) -> NSEvent.ModifierFlags {
        var result: NSEvent.ModifierFlags = []
        for name in (request["modifiers"] ?? request["mods"]) as? [String] ?? [] {
            switch name {
            case "command", "cmd": result.insert(.command)
            case "option", "opt": result.insert(.option)
            case "control", "ctrl": result.insert(.control)
            case "shift": result.insert(.shift)
            default: break
            }
        }
        return result
    }

    static func run(_ request: [String: Any], browser: Browser) -> [String: Any] {
        guard Store.testing else { return ["error": "test process required"] }
        func pages() -> [Tab] { browser.tabs + browser.parkedTabs + browser.previewTabs }
        func tab(_ key: String) -> Tab? {
            guard let raw = request[key] as? String, let id = UUID(uuidString: raw) else { return nil }
            return pages().first { $0.id == id }
        }
        let url = (request["url"] as? String).flatMap(URL.init(string:))
        switch request["action"] as? String ?? "state" {
        case "controllers":
            guard let source = tab("source"), let child = tab("id") else { return ["error": "source and child required"] }
            return ["shared": source.web.configuration.userContentController === child.web.configuration.userContentController]
        case "state", "main-state": break
        case "mini": _ = browser.openMini(url, source: tab("source"))
        case "peek":
            guard let source = tab("source"), let url else { return ["error": "source and url required"] }
            _ = browser.openPeek(url, source: source)
        case "promote":
            guard let page = tab("id") else { return ["error": "page required"] }
            browser.promotePreview(page)
        case "close":
            guard let page = tab("id"), page.surface != .tab else { return ["error": "preview required"] }
            browser.closePreview(page)
        case "external":
            guard let url else { return ["error": "url required"] }
            browser.receiveExternal(url)
        case "settings":
            if let on = request["extensionsInPrivate"] as? Bool { browser.prefs.extensionsInPrivate = on }
            if let on = request["miniLinks"] as? Bool { browser.prefs.miniLinks = on }
            if let on = request["peekLinks"] as? Bool { browser.prefs.peeksLinks = on }
            if let path = request["downloadDirectory"] as? String {
                browser.prefs.downloads = URL(fileURLWithPath: path, isDirectory: true)
                browser.prefs.asksWhereToSave = false
            }
        case "main-open":
            guard let url else { return ["error": "url required"] }
            _ = browser.open(url, foreground: true)
        case "main-new-tab": browser.newTab()
        case "main-private-tab": browser.newShyTab()
        case "main-select-tab", "main-close-tab", "main-pin", "main-unpin":
            guard let page = tab("id") else { return ["error": "page required"] }
            switch request["action"] as? String {
            case "main-select-tab": browser.select(page)
            case "main-close-tab": browser.close(page)
            case "main-pin": browser.pin(page)
            default: browser.unpin(page)
            }
        case "main-create-space":
            guard let name = request["name"] as? String else { return ["error": "name required"] }
            browser.prefs.usesSpaces = true
            browser.addSpace(named: name, sharesSignIns: !(request["separate"] as? Bool ?? false))
        case "main-save": browser.flushSession()
        case "main-select-space":
            guard let id = (request["space"] as? String).flatMap(UUID.init(uuidString:)) else { return ["error": "space required"] }
            browser.switchSpace(to: id)
        default: return ["error": "unknown preview action"]
        }
        func optional(_ id: UUID?) -> Any { id.map { $0.uuidString as Any } ?? NSNull() }
        func space(of page: Tab) -> UUID {
            page.previewSpaceID ?? browser.parked.first { $0.value.tabs.contains { $0 === page } }?.key ?? browser.spaceID
        }
        return [
            "activeID": optional(browser.activeID), "activeSpaceID": browser.spaceID.uuidString,
            "peekID": optional(browser.peekTab?.id), "mainVisible": Links.window?.isVisible ?? false,
            "mainWindow": Links.window?.windowNumber as Any? ?? NSNull(),
            "miniLinks": browser.prefs.miniLinks, "peekLinks": browser.prefs.peeksLinks,
            "visible": browser.tabs.map { $0.id.uuidString },
            "spaces": browser.spaces.map { ["id": $0.id.uuidString, "name": $0.name] },
            "tabs": pages().map { page -> [String: Any] in
                ["id": page.id.uuidString, "surface": page.surface.rawValue,
                 "spaceID": space(of: page).uuidString, "shy": page.shy,
                 "pin": page.pin as Any? ?? NSNull(),
                 "url": (page.pending ?? page.address)?.absoluteString ?? "",
                 "window": page.built?.window?.windowNumber as Any? ?? NSNull()]
            },
            "miniWindows": browser.miniWindows.map { id, mini -> [String: Any] in
                ["id": id.uuidString, "number": mini.window.windowNumber,
                 "visible": mini.window.isVisible, "key": mini.window.isKeyWindow]
            }.sorted { ($0["number"] as? Int ?? 0) < ($1["number"] as? Int ?? 0) },
        ]
    }
}
