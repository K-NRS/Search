import SwiftUI
import AppKit

struct GroupMenu: View {
    @ObservedObject var browser: Browser
    let group: TabGroup

    var body: some View {
        Button("New Tab in Group") { browser.newTab(inGroup: group.id) }
        Button(group.collapsed ? "Expand Group" : "Collapse Group") { browser.toggleGroup(group.id) }
        Button("Rename Group…") { browser.askToRenameGroup(group) }
        Divider()
        Button("Ungroup Tabs") { browser.removeGroup(group.id) }
    }
}

/// Used in both layouts; neighbouring swipe pages supply their own count and
/// selection and render a noninteractive copy of the same heading.
struct GroupHeading: View {
    @ObservedObject var browser: Browser
    let group: TabGroup
    let count: Int
    var selected = false
    var interactive = true
    static let stripWidth: CGFloat = 124

    private var current: Bool { group.collapsed && selected }

    var body: some View {
        HStack(spacing: 0) {
            if interactive {
                Button { browser.toggleGroup(group.id) } label: { title }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(group.name), \(count) tabs")
                    .accessibilityValue(group.collapsed ? "Collapsed" : "Expanded")
                    .accessibilityAddTraits(current ? .isSelected : [])
                    .accessibilityHint(group.collapsed ? "Expand group" : "Collapse group")
                Menu {
                    GroupMenu(browser: browser, group: group)
                } label: { more }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .accessibilityLabel("\(group.name) group actions")
            } else {
                title
                more
            }
        }
        .foregroundStyle(Palette.muted)
        .background(current ? Palette.wash : .clear, in: RoundedRectangle(cornerRadius: 9))
        .contextMenu { if interactive { GroupMenu(browser: browser, group: group) } }
        .help("\(group.name) · \(count) tabs")
        .accessibilityHidden(!interactive)
    }

    private var title: some View {
        HStack(spacing: 5) {
            Image(systemName: group.collapsed ? "chevron.right" : "chevron.down")
                .font(.system(size: 8, weight: .semibold))
                .frame(width: 9)
            if let mark = group.mark {
                switch mark.kind {
                case .emoji: Text(mark.value).font(.system(size: 12)).accessibilityHidden(true)
                case .symbol: Image(systemName: mark.value).font(.system(size: 10)).accessibilityHidden(true)
                }
            }
            Text(group.name)
                .font(.system(size: 11.5, weight: .medium))
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 2)
            Text("\(count)").font(.system(size: 10)).monospacedDigit()
        }
        .padding(.leading, 6)
        .frame(height: 28)
        .contentShape(Rectangle())
    }

    private var more: some View {
        Image(systemName: "ellipsis")
            .font(.system(size: 10))
            .frame(width: 22, height: 28)
    }
}

struct TabGroupingMenu: View {
    @ObservedObject var browser: Browser
    @ObservedObject var tab: Tab

    var body: some View {
        if tab.pin == nil {
            Menu("Move to Group") {
                Button("New Group…") { browser.askForGroup(tab: tab) }
                ForEach(browser.groups) { group in
                    Button { _ = browser.assignGroup(tab, group: group.id) } label: {
                        // A native Menu flattens its label: the emoji and name
                        // must be one Text or the emoji can replace the name.
                        Label(
                            (group.mark?.kind == .emoji ? "\(group.mark!.value) " : "") + group.name,
                            systemImage: group.mark?.kind == .symbol ? group.mark!.value : "folder"
                        )
                    }
                    .disabled(tab.groupID == group.id)
                }
                if tab.groupID != nil {
                    Divider()
                    Button("Remove from Group") { _ = browser.assignGroup(tab, group: nil) }
                }
            }
        }
    }
}

extension Browser {
    func askForGroup(tab: Tab? = nil) {
        GroupEditor.ask(title: "New Tab Group", name: "", mark: nil) { name, mark, _ in
            if self.createGroup(name: name, tab: tab, mark: mark) == nil {
                self.announce("Couldn't create the group.")
            }
        }
    }

    func askToRenameGroup(_ group: TabGroup) {
        GroupEditor.ask(title: "Rename Group", name: group.name, mark: group.mark) { name, mark, _ in
            if !self.renameGroup(group.id, name: name) || !self.setGroupMark(group.id, mark: mark) {
                self.announce("Couldn't update the group.")
            }
        }
    }
}

/// One native sheet for group naming and space customization. The optional
/// checkbox starts unchecked, so new spaces share sign-ins unless requested.
@MainActor
enum GroupEditor {
    static func ask(
        title: String, name: String, mark: TabMark?, separateOption: Bool = false,
        then: @escaping (String, TabMark?, Bool) -> Void
    ) {
        guard let window = Links.window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = separateOption
            ? "Its own tabs. Share existing website sign-ins, or keep this space's sign-ins separate."
            : "Choose a name and an optional emoji or icon."
        alert.addButton(withTitle: name.isEmpty ? "Create" : "Save")
        alert.addButton(withTitle: "Cancel")
        let form = GroupEditorForm(name: name, mark: mark, separate: separateOption, save: alert.buttons[0])
        alert.accessoryView = form.view
        alert.window.initialFirstResponder = form.name
        let observer = NotificationCenter.default.addObserver(
            forName: NSControl.textDidChangeNotification, object: nil, queue: .main
        ) { note in
            MainActor.assumeIsolated {
                if let field = note.object as? NSTextField, field === form.name || field === form.emoji {
                    form.changed()
                }
            }
        }
        alert.beginSheetModal(for: window) { response in
            NotificationCenter.default.removeObserver(observer)
            guard response == .alertFirstButtonReturn, form.valid else { return }
            then(form.name.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
                 form.mark, form.separate.state == .on)
        }
    }
}

@MainActor
private final class GroupEditorForm: NSObject {
    let view: NSView
    let name: NSTextField
    let emoji = NSTextField(string: "")
    let mode = NSPopUpButton(frame: .zero, pullsDown: false)
    let icon = NSPopUpButton(frame: .zero, pullsDown: false)
    let separate = NSButton(checkboxWithTitle: "Separate sign-ins", target: nil, action: nil)
    let save: NSButton

    private static func iconName(_ symbol: String) -> String {
        if let index = Spaces.icons.firstIndex(of: symbol), Spaces.iconNames.indices.contains(index) {
            return Spaces.iconNames[index]
        }
        return ["square.stack": "Spaces", "folder": "Folder", "bookmark": "Bookmark",
                "globe": "Globe", "star": "Star", "bolt": "Lightning", "paintbrush": "Paintbrush"][symbol] ?? symbol
    }

    init(name initialName: String, mark: TabMark?, separate offersSeparate: Bool, save: NSButton) {
        self.name = NSTextField(string: initialName)
        self.save = save
        let base: CGFloat = offersSeparate ? 34 : 0
        view = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: base + 98))
        super.init()
        name.placeholderString = "Name"
        name.setAccessibilityLabel("Name")
        name.identifier = .init("organization-name")
        name.frame = NSRect(x: 0, y: base + 72, width: 300, height: 24)
        view.addSubview(name)
        let label = NSTextField(labelWithString: "Marker")
        label.font = .systemFont(ofSize: 11)
        label.textColor = .secondaryLabelColor
        label.frame = NSRect(x: 0, y: base + 43, width: 300, height: 18)
        view.addSubview(label)
        mode.addItems(withTitles: ["Default", "Emoji", "Icon"])
        mode.frame = NSRect(x: 0, y: base + 10, width: 100, height: 26)
        mode.setAccessibilityLabel("Marker type")
        mode.identifier = .init("organization-marker-type")
        mode.target = self
        mode.action = #selector(changed)
        view.addSubview(mode)
        emoji.placeholderString = "One emoji"
        emoji.setAccessibilityLabel("Emoji")
        emoji.identifier = .init("organization-emoji")
        emoji.frame = NSRect(x: 110, y: base + 11, width: 190, height: 24)
        view.addSubview(emoji)
        icon.frame = NSRect(x: 110, y: base + 10, width: 190, height: 26)
        icon.setAccessibilityLabel("Icon")
        icon.identifier = .init("organization-icon")
        for symbol in TabMark.symbols {
            icon.addItem(withTitle: Self.iconName(symbol))
            icon.lastItem?.representedObject = symbol
            icon.lastItem?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        }
        view.addSubview(icon)
        separate.frame = NSRect(x: 0, y: 0, width: 300, height: 24)
        separate.state = .off
        if offersSeparate { view.addSubview(separate) }
        if let mark {
            mode.selectItem(at: mark.kind == .emoji ? 1 : 2)
            if mark.kind == .emoji { emoji.stringValue = mark.value }
            else if let index = TabMark.symbols.firstIndex(of: mark.value) { icon.selectItem(at: index) }
        }
        changed()
    }

    var mark: TabMark? {
        switch mode.indexOfSelectedItem {
        case 1: return TabMark(kind: .emoji, value: emoji.stringValue.trimmingCharacters(in: .whitespacesAndNewlines))
        case 2: return (icon.selectedItem?.representedObject as? String).map { TabMark(kind: .symbol, value: $0) }
        default: return nil
        }
    }

    var valid: Bool {
        !name.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && (mark?.isValid ?? true)
    }

    @objc func changed() {
        emoji.isHidden = mode.indexOfSelectedItem != 1
        icon.isHidden = mode.indexOfSelectedItem != 2
        save.isEnabled = valid
    }
}
