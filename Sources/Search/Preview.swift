import AppKit
import Carbon
import SwiftUI
import WebKit

enum TabSurface: String { case tab, mini, peek }

extension Browser {
    /// Main selection remains stable while a temporary page owns the keyboard.
    var focusedTab: Tab? {
        if let window = NSApp.keyWindow,
           let id = miniWindows.first(where: { $0.value.window === window })?.key {
            return miniWindows[id]?.tab
        }
        if NSApp.keyWindow === Links.window, !mainOverlayShowing, let peekTab { return peekTab }
        return active
    }

    var mainOverlayShowing: Bool {
        tuning || welcoming || bookmarking || managing || recalling || hoarding || reviewing || editing
    }

    var previewsInOtherSpaces: Bool {
        previewTabs.contains { $0.previewSpaceID != nil && $0.previewSpaceID != Space.firstID }
    }

    var previewTabs: [Tab] { miniWindows.values.map(\.tab) + (peekTab.map { [$0] } ?? []) }
    var allTabs: [Tab] { tabs + parkedTabs + previewTabs }

    func previewSpace(for tab: Tab) -> UUID? {
        if let space = tab.previewSpaceID { return space }
        if tabs.contains(where: { $0 === tab }) { return spaceID }
        return parked.first { $0.value.tabs.contains(where: { $0 === tab }) }?.key
    }

    func receiveExternal(_ url: URL) {
        if prefs.miniLinks {
            openMini(url)
        } else {
            arrive(url)
            showMainWindow()
        }
    }

    func showMainWindow() {
        if Links.window == nil { _ = NSApp.delegate?.applicationOpenUntitledFile?(NSApp) }
        Links.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func previewTab(_ url: URL?, source: Tab?, surface: TabSurface,
                            configuration: WKWebViewConfiguration? = nil) -> Tab {
        let space = source.flatMap { previewSpace(for: $0) } ?? spaceID
        let config = configuration ?? Web.configuration(shy: source?.shy ?? false,
            space: space, store: source?.store)
        let tab = Tab(shy: source?.shy ?? false, configuration: config)
        tab.previewSpaceID = space
        tab.surface = surface
        tab.opener = source?.id
        prepare(tab)
        return tab
    }

    @discardableResult
    func openMini(_ url: URL? = nil, source: Tab? = nil) -> Tab {
        let tab = previewTab(url, source: source, surface: .mini)
        if let url { tab.setAddressOptimistically(url) }
        presentMini(tab)
        if let url { tab.go(to: url) }
        return tab
    }

    /// A popup keeps WebKit's exact configuration and opener relationship.
    func openPreviewPopup(configuration: WKWebViewConfiguration, source: Tab,
                          url: URL?) -> Tab {
        let tab = previewTab(url, source: source, surface: .mini, configuration: configuration)
        if let url { tab.setAddressOptimistically(url) }
        presentMini(tab)
        return tab
    }

    private func presentMini(_ tab: Tab) {
        let controller = MiniWindow(tab: tab, browser: self)
        miniWindows[tab.id] = controller
        controller.window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        previewsChanged()
        focusPreview(tab)
    }

    @discardableResult
    func openPeek(_ url: URL, source: Tab, configuration: WKWebViewConfiguration? = nil) -> Tab {
        // Peeking within a preview navigates that page; no nested overlays.
        if source.surface != .tab { source.go(to: url); return source }
        if let peekTab { closePreview(peekTab) }
        let tab = previewTab(url, source: source, surface: .peek, configuration: configuration)
        withAnimation(Motion.settle) { peekTab = tab }
        editing = false
        // A new-window navigation is loaded by WebKit into the exact supplied
        // configuration; loading it ourselves would sever window.opener.
        if configuration != nil { tab.setAddressOptimistically(url) }
        else { tab.go(to: url) }
        previewsChanged()
        focusPreview(tab)
        return tab
    }

    func focusPreview(_ tab: Tab) {
        let window = tab.surface == .mini ? miniWindows[tab.id]?.window : Links.window
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        tab.touch()
        if tab.stale { tab.revive() }
        if tab.isBlank { tab.previewFocus += 1 }
        else {
            DispatchQueue.main.async { [weak self, weak tab, weak window] in
                guard let self, let tab, self.previewTabs.contains(where: { $0 === tab }),
                      tab.surface != .tab, tab.built?.window === window else { return }
                window?.makeFirstResponder(tab.built)
            }
        }
        objectWillChange.send()
    }

    func closePreview(_ tab: Tab) {
        guard tab.surface != .tab, previewTabs.contains(where: { $0 === tab }) else { return }
        let wasFocused = focusedTab === tab
        let source = allTabs.first { $0.id == tab.opener }
        cancelPageRequests(tab)
        detachPreview(tab)
        tab.close()
        previewsChanged()
        if wasFocused {
            if let source, source.surface != .tab { focusPreview(source) }
            else if let active { Links.window?.makeFirstResponder(active.built) }
        }
    }

    func promotePreview(_ tab: Tab) {
        guard tab.surface != .tab, previewTabs.contains(where: { $0 === tab }) else { return }
        // Detach the old host before the main stage can request the live view.
        detachPreview(tab)
        tab.surface = .tab
        tab.previewFinding = false
        if let destination = tab.previewSpaceID, destination != spaceID {
            enter(destination)
        }
        let here = tabs.firstIndex { $0.id == tab.opener }
        insert(tab, at: here.map { $0 + 1 } ?? tabs.count)
        tab.previewSpaceID = nil
        previewsChanged()
        select(tab)
        showMainWindow()
        DispatchQueue.main.async { [weak tab] in
            guard let tab, tab.surface == .tab else { return }
            Links.window?.makeFirstResponder(tab.built)
        }
        rememberSession()
    }

    private func detachPreview(_ tab: Tab) {
        if let controller = miniWindows.removeValue(forKey: tab.id) {
            controller.window.delegate = nil
            controller.window.contentView = nil
            controller.window.close()
        }
        if peekTab === tab { withAnimation(Motion.quick) { peekTab = nil } }
        tab.built?.removeFromSuperview()
        if suggesting?.tab == tab.id { dropChoice() }
    }

    func previewsChanged() {
        objectWillChange.send()
        // Native window callbacks and a space change can run inside promotion.
        // Reconcile after the same page has reached its final owner.
        DispatchQueue.main.async {
            if #available(macOS 15.4, *) { Extensions.shared.previewSurfacesChanged() }
        }
    }

    func closeFocusedPage() {
        if let tab = focusedTab { close(tab) }
    }

    /// Only top-level user navigation from the main tab may create a Peek.
    func shouldPeek(_ action: WKNavigationAction, from source: Tab, to url: URL) -> Bool {
        guard source.surface == .tab, source.id == activeID,
              action.navigationType == .linkActivated, action.sourceFrame.isMainFrame,
              action.targetFrame?.isMainFrame ?? true,
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              !action.shouldPerformDownload, action.buttonNumber <= 1 else { return false }
        let flags = action.modifierFlags.intersection([.command, .option, .control, .shift])
        if flags == [.shift] { return true }
        guard flags.isEmpty, prefs.peeksLinks, source.pin != nil,
              let host = source.address?.host?.lowercased(), let target = url.host?.lowercased() else { return false }
        // WebKit does not expose a registrable-domain API; host boundaries are
        // explicit rather than guessing public suffixes such as co.uk.
        return host != target
    }

    /// Preview-specific commands never fall through to main-tab shortcuts.
    func previewKey(_ event: NSEvent, tab: Tab) -> Bool {
        let flags = event.modifierFlags.intersection([.command, .option, .control, .shift])
        let key = event.charactersIgnoringModifiers?.lowercased() ?? ""
        if event.keyCode == 53 {
            if tab.previewFinding { tab.previewFinding = false }
            else if tab.surface == .peek { closePreview(tab) }
            else { return false }
            return true
        }
        if #available(macOS 15.4, *), Extensions.shared.take(event) { return true }
        guard flags.contains(.command) else { return false }
        switch (key, flags) {
        case ("w", [.command]): closePreview(tab)
        case ("o", [.command]): promotePreview(tab)
        case ("l", [.command]): tab.previewFocus += 1
        case ("r", [.command]): tab.reload()
        case ("r", [.command, .shift]): tab.toggleReader { _ in }
        case ("[", [.command]): tab.back()
        case ("]", [.command]): tab.forward()
        case ("f", [.command]): tab.previewFinding = true
        case ("g", [.command]), ("g", [.command, .shift]): previewFind(tab, forward: !flags.contains(.shift))
        case ("+", [.command]), ("=", [.command]), ("+", [.command, .shift]): tab.magnify(by: 1.1)
        case ("-", [.command]): tab.magnify(by: 1 / 1.1)
        case ("0", [.command]): tab.resetZoom()
        case ("p", [.command]): printPage()
        case ("c", [.command, .shift]): copyAddress()
        case ("v", [.command, .shift]): pasteAndGo()
        case ("b", [.command, .shift]): bookmarkCurrent()
        case ("d", [.command]): duplicate()
        case ("i", [.command, .option]): toggleInspector()
        case ("j", [.command, .option]): showConsole()
        case ("c", [.command, .option]): inspectElement()
        case ("t", [.command]):
            let privatePage = tab.shy
            showMainWindow()
            privatePage ? newShyTab() : newTab()
        case (",", [.command]): showMainWindow(); tuning = true
        default: return false
        }
        return true
    }

    func previewFind(_ tab: Tab, forward: Bool = true) {
        guard !tab.previewNeedle.isEmpty else { return }
        let config = WKFindConfiguration()
        config.backwards = !forward
        config.wraps = true
        tab.web.find(tab.previewNeedle, configuration: config) { _ in }
    }
}

@MainActor
final class MiniWindow: NSObject, NSWindowDelegate {
    let window: NSWindow
    private weak var browser: Browser?
    let tab: Tab

    init(tab: Tab, browser: Browser) {
        self.tab = tab
        self.browser = browser
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 560),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "Mini — Search"
        window.identifier = NSUserInterfaceItemIdentifier("mini-" + tab.id.uuidString)
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 440, height: 320)
        window.backgroundColor = Palette.NS.ground
        window.titlebarAppearsTransparent = true
        window.delegate = self
        window.contentView = NSHostingView(rootView: PreviewPage(browser: browser, tab: tab, surface: .mini))
        window.center()
        let offset = CGFloat(browser.miniWindows.count % 8) * 22
        window.setFrameOrigin(NSPoint(x: window.frame.minX + offset, y: window.frame.minY - offset))
    }

    func windowWillClose(_ notification: Notification) {
        browser?.closePreview(tab)
    }

    func windowDidBecomeKey(_ notification: Notification) { browser?.previewsChanged() }
    func windowDidResignKey(_ notification: Notification) { browser?.objectWillChange.send() }
}

struct PreviewPage: View {
    @ObservedObject var browser: Browser
    @ObservedObject var tab: Tab
    let surface: TabSurface
    @State private var address = ""
    @State private var addressFocused = false
    @FocusState private var findFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                button("chevron.left", "Back", disabled: !tab.canGoBack) { tab.back() }
                button("chevron.right", "Forward", disabled: !tab.canGoForward) { tab.forward() }
                button(tab.loading ? "xmark" : "arrow.clockwise", tab.loading ? "Stop" : "Reload") {
                    tab.loading ? tab.stop() : tab.reload()
                }
                PreviewAddressField(text: $address, editing: $addressFocused, tab: tab) {
                        guard let url = browser.destination(for: address) else { return }
                        tab.go(to: url)
                        DispatchQueue.main.async { tab.built?.window?.makeFirstResponder(tab.built) }
                    }
                    .frame(height: 18)
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .background(Palette.wash, in: RoundedRectangle(cornerRadius: 6))
                if surface == .mini {
                    Button("Move to Tab") { browser.promotePreview(tab) }
                        .font(.system(size: 12))
                        .buttonStyle(.plain)
                        .help("Move to Tab (⌘O)")
                }
            }
            .padding(10)
            Rectangle().fill(Palette.hairline).frame(height: 1)
            Page(tab: tab, surface: surface)
                .overlay(alignment: .topLeading) {
                    if let asked = browser.suggesting, asked.tab == tab.id {
                        AccountList(browser: browser, asked: asked)
                    }
                }
                .overlay(alignment: .bottom) {
                    PageNotices(browser: browser, tab: tab).padding(12)
                }
                .overlay(alignment: .top) {
                    if tab.loading {
                        GeometryReader { geometry in
                            Palette.ink.opacity(0.3).frame(width: geometry.size.width * tab.progress, height: 2)
                        }.frame(height: 2).allowsHitTesting(false)
                    }
                }
                .overlay(alignment: .topTrailing) {
                    if tab.previewFinding {
                        HStack(spacing: 8) {
                            TextField("Find on page", text: $tab.previewNeedle)
                                .textFieldStyle(.plain).frame(width: 160)
                                .focused($findFocused)
                                .onSubmit { browser.previewFind(tab) }
                                .onChange(of: tab.previewNeedle) { _, _ in browser.previewFind(tab) }
                            button("chevron.up", "Previous match") { browser.previewFind(tab, forward: false) }
                            button("chevron.down", "Next match") { browser.previewFind(tab) }
                            button("xmark", "Close Find") { tab.previewFinding = false }
                        }
                        .padding(10).background(Palette.ground, in: RoundedRectangle(cornerRadius: 8))
                        .padding(10)
                    }
                }
        }
        .background(Palette.ground)
        .foregroundStyle(Palette.ink)
        .onAppear {
            address = tab.address?.absoluteString ?? ""
        }
        .onChange(of: tab.address) { _, value in
            if !addressFocused { address = value?.absoluteString ?? "" }
        }
        .onChange(of: tab.previewFinding) { _, value in findFocused = value }
    }

    private func button(_ icon: String, _ label: String, disabled: Bool = false,
                        action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 12))
                .frame(width: 24, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(disabled).help(label).accessibilityLabel(label)
    }
}

/// Like the main address field, a focus request selects all using AppKit.
/// A SwiftUI focus flag can stay true after WebKit takes first responder.
struct PreviewAddressField: NSViewRepresentable {
    @Binding var text: String
    @Binding var editing: Bool
    @ObservedObject var tab: Tab
    var submit: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSTextField {
        let field = NSTextField()
        field.identifier = NSUserInterfaceItemIdentifier("preview-address")
        field.setAccessibilityLabel("Preview address")
        field.placeholderString = "Search or enter address"
        field.isBordered = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.font = .systemFont(ofSize: 12)
        field.textColor = Palette.NS.ink
        field.cell?.usesSingleLineMode = true
        field.cell?.wraps = false
        field.delegate = context.coordinator
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        let coordinator = context.coordinator
        coordinator.owner = self
        if coordinator.synced != text {
            coordinator.synced = text
            field.stringValue = text
        }
        if coordinator.answered != tab.previewFocus {
            coordinator.answered = tab.previewFocus
            DispatchQueue.main.async { [weak field, weak tab] in
                guard let field, let tab, tab.surface != .tab,
                      let window = field.window, window.isKeyWindow else { return }
                field.stringValue = tab.address?.absoluteString ?? ""
                coordinator.synced = field.stringValue
                coordinator.owner.text = field.stringValue
                window.makeFirstResponder(field)
                field.currentEditor()?.selectAll(nil)
            }
        }
    }

    @MainActor
    final class Coordinator: NSObject, NSTextFieldDelegate {
        var owner: PreviewAddressField
        var synced = ""
        var answered: Int
        init(_ owner: PreviewAddressField) {
            self.owner = owner
            answered = owner.tab.isBlank ? -1 : owner.tab.previewFocus
        }
        func controlTextDidChange(_ note: Notification) {
            guard let field = note.object as? NSTextField else { return }
            synced = field.stringValue
            owner.text = field.stringValue
        }
        func controlTextDidBeginEditing(_ note: Notification) { owner.editing = true }
        func controlTextDidEndEditing(_ note: Notification) { owner.editing = false }
        func control(_ control: NSControl, textView: NSTextView, doCommandBy command: Selector) -> Bool {
            guard command == #selector(NSResponder.insertNewline(_:)) else { return false }
            owner.submit()
            return true
        }
    }
}

/// Carbon registers a real global shortcut without Accessibility permission.
@MainActor
final class MiniShortcut {
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private var action: (() -> Void)?

    func register(_ action: @escaping () -> Void) -> Bool {
        guard !Store.testing else { return true }
        if hotKey != nil { return true }
        self.action = action
        var kind = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let installed = InstallEventHandler(GetApplicationEventTarget(), { _, event, user in
            guard let event, let user else { return OSStatus(eventNotHandledErr) }
            var id = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID), nil, MemoryLayout<EventHotKeyID>.size, nil, &id) == noErr,
                id.signature == 0x534D494E else { return OSStatus(eventNotHandledErr) }
            MainActor.assumeIsolated {
                Unmanaged<MiniShortcut>.fromOpaque(user).takeUnretainedValue().action?()
            }
            return noErr
        }, 1, &kind, Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard installed == noErr else { return false }
        let status = RegisterEventHotKey(UInt32(kVK_ANSI_N), UInt32(cmdKey | optionKey),
                                        EventHotKeyID(signature: 0x534D494E, id: 1),
                                        GetApplicationEventTarget(), 0, &hotKey)
        if status != noErr, let handler {
            RemoveEventHandler(handler)
            self.handler = nil
            self.action = nil
        }
        return status == noErr
    }

    deinit {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let handler { RemoveEventHandler(handler) }
    }
}

/// Requests belong to the page that made them, including a temporary page.
struct PageNotices: View {
    @ObservedObject var browser: Browser
    var tab: Tab?

    var body: some View {
        VStack(spacing: 8) {
            if let ask = browser.asking, browser.askingTab == tab?.id {
                captureAsking(ask)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            if let offer = browser.offering, browser.offeringTab == tab?.id {
                keepAsking(offer)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
    }

    /// A page asking to see or hear you. Named by the site, in its own words,
    /// with the answer remembered so it is asked once and not every call.
    private func captureAsking(_ ask: Browser.CaptureAsk) -> some View {
        HStack(spacing: 12) {
            Image(systemName: ask.wants == "microphone" ? "mic" : "video")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Palette.muted)
            Text("\(ask.host) wants to use your \(ask.wants)")
                .font(.system(size: 12.5))
                .foregroundStyle(Palette.ink)
            Button { browser.allowCapture() } label: {
                Text("Allow")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ground)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 5)
                    .background(Palette.ink, in: Capsule())
            }
            .buttonStyle(.plain)
            Button { browser.denyCapture() } label: {
                Text("Don't allow")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.muted)
            }
            .buttonStyle(.plain)
        }
        .padding(.leading, 16)
        .padding(.trailing, 10)
        .padding(.vertical, 9)
        .background(Palette.ground, in: Capsule())
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: .black.opacity(0.12), radius: 20, y: 6)
    }

    /// Offered once, answered once. The password is never shown back to you —
    /// there is nothing to be learned from reading your own password.
    private func keepAsking(_ offer: Browser.Offer) -> some View {
        let login = offer.login
        return HStack(spacing: 12) {
            Text(offer.changed
                 ? "Update the password for \(login.user) on \(login.host)?"
                 : (login.user.isEmpty
                    ? "Save this password for \(login.host)?"
                    : "Save the password for \(login.user) on \(login.host)?"))
                .font(.system(size: 12.5))
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
            Button(offer.changed ? "Update" : "Save") { browser.keepOffer() }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundStyle(Palette.ground)
                .padding(.horizontal, 11)
                .padding(.vertical, 5)
                .background(Palette.ink, in: Capsule())
            Button("Not now") { browser.dropOffer() }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundStyle(Palette.muted)
            if !offer.changed {
                Button("Never here") { browser.neverOffer() }
                    .buttonStyle(.plain)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.muted)
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 12)
        .padding(.vertical, 9)
        .background(Palette.ground, in: Capsule())
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: .black.opacity(0.12), radius: 20, y: 6)
    }


}
