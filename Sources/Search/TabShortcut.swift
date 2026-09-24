import AppKit
import SwiftUI

/// The two configurable tab commands. Ordinary Tab always belongs to the page.
enum TabDirection: String, CaseIterable, Identifiable {
    case next, previous
    var id: String { rawValue }
    var title: String { self == .next ? "Next Tab" : "Previous Tab" }
    var step: Int { self == .next ? 1 : -1 }
    var standard: TabShortcut {
        TabShortcut(code: 48, key: "\t", modifiers:
            (self == .next ? NSEvent.ModifierFlags.control : [.control, .shift]).rawValue)
    }
}

struct TabShortcut: Codable, Equatable {
    let code: UInt16
    let key: String
    let modifiers: UInt

    static let modifierMask: NSEvent.ModifierFlags = [.command, .control, .option, .shift]
    var flags: NSEvent.ModifierFlags { NSEvent.ModifierFlags(rawValue: modifiers) }
    var valid: Bool {
        key.count == 1 && code < 128 && code != 53
            && !flags.intersection([.command, .control, .option]).isEmpty
            && flags.subtracting(Self.modifierMask).isEmpty
    }

    init(code: UInt16, key: String, modifiers: UInt) {
        self.code = code
        self.key = key
        self.modifiers = modifiers
    }

    init?(_ event: NSEvent) {
        let key: String
        switch event.keyCode {
        case 48: key = "\t" // Shift-Tab may arrive as the backtab character.
        case 36, 76: key = "\r"
        case 49: key = " "
        case 51: key = "\u{8}"
        case 117: key = String(UnicodeScalar(NSDeleteFunctionKey)!)
        case 123: key = String(UnicodeScalar(NSLeftArrowFunctionKey)!)
        case 124: key = String(UnicodeScalar(NSRightArrowFunctionKey)!)
        case 125: key = String(UnicodeScalar(NSDownArrowFunctionKey)!)
        case 126: key = String(UnicodeScalar(NSUpArrowFunctionKey)!)
        default: key = (event.characters(byApplyingModifiers: []) ?? event.charactersIgnoringModifiers ?? "").lowercased()
        }
        self.init(code: event.keyCode, key: key,
                  modifiers: event.modifierFlags.intersection(Self.modifierMask).rawValue)
        guard valid else { return nil }
    }

    func matches(_ event: NSEvent) -> Bool {
        code == event.keyCode && modifiers == event.modifierFlags.intersection(Self.modifierMask).rawValue
    }

    func overlaps(_ other: TabShortcut) -> Bool {
        modifiers == other.modifiers && (code == other.code || key == other.key)
    }

    var equivalent: KeyEquivalent { KeyEquivalent(key.first ?? "\t") }
    var eventModifiers: EventModifiers {
        var result: EventModifiers = []
        if flags.contains(.command) { result.insert(.command) }
        if flags.contains(.control) { result.insert(.control) }
        if flags.contains(.option) { result.insert(.option) }
        if flags.contains(.shift) { result.insert(.shift) }
        return result
    }

    var label: String {
        var result = ""
        if flags.contains(.control) { result += "⌃" }
        if flags.contains(.option) { result += "⌥" }
        if flags.contains(.shift) { result += "⇧" }
        if flags.contains(.command) { result += "⌘" }
        let names: [UInt16: String] = [48: "Tab", 36: "Return", 76: "Enter", 49: "Space",
            51: "Delete", 117: "Forward Delete", 123: "←", 124: "→", 125: "↓", 126: "↑",
            115: "Home", 119: "End", 116: "Page Up", 121: "Page Down"]
        if let scalar = key.unicodeScalars.first, (NSF1FunctionKey...NSF35FunctionKey).contains(Int(scalar.value)) {
            return result + "F\(Int(scalar.value) - NSF1FunctionKey + 1)"
        }
        return result + (names[code] ?? key.uppercased())
    }

    static func load(_ data: Data?, fallback: TabShortcut) -> TabShortcut {
        guard let data, let shortcut = try? JSONDecoder().decode(Self.self, from: data), shortcut.valid else { return fallback }
        return shortcut
    }

    /// SwiftUI refreshes closed menus lazily. Update their native equivalents
    /// immediately too, so a removed binding stops firing without opening a menu.
    @MainActor
    func updateMenu(in menu: NSMenu?, title: String) {
        // A bookmark can have the same title; only touch these two commands.
        guard let tabs = menu?.items.first(where: { $0.title == "Tabs" })?.submenu,
              let item = tabs.items.first(where: { $0.title == title }) else { return }
        item.keyEquivalent = key
        item.keyEquivalentModifierMask = flags
    }

    /// Include disabled menu items: their keys are still reserved when enabled.
    @MainActor
    func menuConflict(in menu: NSMenu?) -> String? {
        guard let menu else { return nil }
        for item in menu.items {
            if let conflict = menuConflict(in: item.submenu) { return conflict }
            if menu === NSApp.mainMenu?.items.first(where: { $0.title == "Tabs" })?.submenu,
               ["Next Tab", "Previous Tab"].contains(item.title) { continue }
            if !item.keyEquivalent.isEmpty,
               item.keyEquivalent.lowercased() == key,
               item.keyEquivalentModifierMask.intersection(Self.modifierMask) == flags {
                return item.title
            }
        }
        return nil
    }
}
