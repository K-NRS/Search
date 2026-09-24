import SwiftUI

// Shift-click previews any link. Pinned tabs also preview links to another
// host by default; Settings can turn that automatic behavior off.
extension Browser {
    func peek(_ url: URL, from tab: Tab) { openPeek(url, source: tab) }
    func closePeek() { if let tab = peekTab { closePreview(tab) } }
    func keepPeek() { if let tab = peekTab { promotePreview(tab) } }
}

/// The peek over the page: the page dimmed around it, and the panel.
struct PeekLayer: View {
    @ObservedObject var browser: Browser

    var body: some View {
        ZStack {
            // The dimming only fades. Grown and shrunk with the panel, its
            // edges travelled across the window as it came (Drice, 24 Sep 2026).
            if browser.peekTab != nil {
                Color.black.opacity(0.22)
                    .contentShape(Rectangle())
                    .onTapGesture { browser.closePeek() }
                    .transition(.opacity)
            }
            if let tab = browser.peekTab {
                PeekPanel(browser: browser, tab: tab)
                    .transition(.opacity.combined(with: .scale(scale: 0.98)))
            }
        }
    }
}

/// The panel itself, in the middle of the page.
struct PeekPanel: View {
    @ObservedObject var browser: Browser
    @ObservedObject var tab: Tab

    var body: some View {
        GeometryReader { geo in
            ZStack {
                HStack(alignment: .top, spacing: 10) {
                    PreviewPage(browser: browser, tab: tab, surface: .peek)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1)
                        )
                        .shadow(color: .black.opacity(0.25), radius: 30, y: 10)
                    VStack(spacing: 8) {
                        Knob("xmark", help: "Close (esc)") { browser.closePeek() }
                        Knob("arrow.up.left.and.arrow.down.right", help: "Move to Tab (⌘O)") { browser.keepPeek() }
                    }
                }
                .frame(width: geo.size.width * 0.82, height: geo.size.height * 0.86)
                .offset(x: 21)
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
    }

    private struct Knob: View {
        let symbol: String
        let help: String
        let act: () -> Void
        @State private var hovering = false

        init(_ symbol: String, help: String, act: @escaping () -> Void) {
            self.symbol = symbol
            self.help = help
            self.act = act
        }

        var body: some View {
            Button(action: act) {
                Image(systemName: symbol)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .frame(width: 28, height: 28)
                    .background(hovering ? Palette.hover : Palette.ground, in: Circle())
                    .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .help(help)
            .onHover { hovering = $0 }
        }
    }
}
