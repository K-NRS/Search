#!/usr/bin/env python3
"""Reproduce the reviewed six-PR integration without changing any PR branch.
Pinned to the source heads inspected on 2026-09-25; unexpected conflicts abort.
"""
from pathlib import Path
import re
import subprocess
root = Path(__file__).resolve().parents[1]
pattern = re.compile(r'^<<<<<<<[^\n]*\n(.*?)^\|\|\|\|\|\|\|[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n', re.M | re.S)

def git(*args, check=True):
    return subprocess.run(['git', *args], cwd=root, check=check)

def resolve(path, choices):
    p = root / path
    text = p.read_text()
    matches = list(pattern.finditer(text))
    assert len(matches) == len(choices), (path, len(matches), len(choices))
    for m, c in reversed(list(zip(matches, choices))):
        result = m[c] if isinstance(c, int) else c(m) if callable(c) else c
        text = text[:m.start()] + result + text[m.end():]
    p.write_text(text)

def edit(path, old, new):
    p = root / path
    s = p.read_text()
    assert s.count(old) == 1, (path, old[:100], s.count(old))
    p.write_text(s.replace(old, new))

def keep_head(*paths):
    for p in paths:
        (root / p).write_bytes(subprocess.check_output(['git', 'show', 'HEAD:' + p], cwd=root))

def resolve_pr54():
    keep_head('CHANGELOG.md', 'ROADMAP.md', 'Sources/Search/ExtensionShims.swift', 'Sources/Search/Float.swift')
    resolve('Sources/Search/App.swift', [lambda m: m[3] + m[1]])
    resolve('Sources/Search/Browser.swift', [lambda m: m[1] + m[3], lambda m: '        tab.groupID = atEnd ? nil : active?.groupID\n        if foreground { revealGroup(of: tab) }\n' + m[1], lambda m: m[1] + ''.join(m[3].splitlines(keepends=True)[1:]), '        guard let source = active, let url = source.address else { return }\n        open(url, foreground: true, from: source).name = source.name\n', lambda m: m[1].replace('    private func adopt(', '    func adopt('), lambda m: m[1] + m[3]])
    resolve('Sources/Search/Side.swift', [lambda m: m[1][:m[1].index('                            // The tab you go to')] + '                            // Reveal the selected tab or its collapsed heading.\n                            .onChange(of: browser.activeID) { _, _ in reveal(proxy) }\n                            .onChange(of: browser.groups) { _, _ in reveal(proxy) }\n                            .onChange(of: browser.spaceID) { _, _ in reveal(proxy) }\n                            .onAppear { reveal(proxy) }\n', 3, '    private func row(_ tab: Tab) -> some View {\n        let peers = looseTabs.filter { $0.groupID == tab.groupID }\n        let index = peers.firstIndex { $0.id == tab.id } ?? 0\n        let step = SideBar.row + SideBar.gap\n        return SideRow(browser: browser, prefs: prefs, tab: tab,\n                       live: tab.id == browser.activeID, pill: pill,\n                       close: { browser.close(tab) })\n            .modifier(Carried(index: index, count: peers.count, step: step, vertical: true, space: "rows") { target in\n                guard peers.indices.contains(target),\n                      let canonical = browser.tabs.firstIndex(where: { $0.id == peers[target].id }) else { return }\n                browser.move(tab, to: canonical)\n            })\n    }\n\n'])
    resolve('Sources/Search/SpaceSwipe.swift', [lambda m: '        guard NSApp.modalWindow == nil, Links.window?.attachedSheet == nil else { return false }\n' + m[1], 1])
    resolve('Sources/Search/Spaces.swift', [lambda m: m[1].replace('active: activeID)', 'active: activeID, groups: groups)')])
    def tabbar0(m):
        s = m[1]
        start = s.index('                                        ForEach(')
        end = s.index('                                    }\n                                    .frame(height:', start)
        return s[:start] + '                                        ForEach(browser.presentedTabs.filter { $0.groupID == nil }) { tab in\n                                            tabItem(tab, strip: geo.size.width)\n                                        }\n                                        ForEach(browser.groups) { group in\n                                            GroupHeading(browser: browser, group: group,\n                                                         count: browser.tabs.filter { $0.pin == nil && $0.groupID == group.id }.count,\n                                                         selected: browser.active?.groupID == group.id)\n                                                .frame(width: GroupHeading.stripWidth)\n                                                .id(group.id)\n                                            if !group.collapsed {\n                                                ForEach(browser.presentedTabs.filter { $0.groupID == group.id }) { tab in\n                                                    tabItem(tab, strip: geo.size.width)\n                                                }\n                                            }\n                                        }\n' + s[end:]
    resolve('Sources/Search/TabBar.swift', [tabbar0, 1, 1, lambda m: m[1].replace('count: browser.tabs.count)', 'count: browser.presentedTabs.count, groupCount: browser.groups.count)').replace('count: Int) -> CGFloat', 'count: Int, groupCount: Int = 0) -> CGFloat'), '            + CGFloat(max(0, count + groupCount - 1)) * Metrics.tabGap\n            + CGFloat(groupCount) * GroupHeading.stripWidth\n'])
    p = root / 'Sources/Search/TabBar.swift'
    s = p.read_text()
    a = s.index('        let held = dragging == tab.id', s.index('    private func tabItem('))
    b = s.index('\n    // MARK: - the spaces', a)
    s = s[:a] + '        return TabPill(browser: browser, prefs: browser.prefs, tab: tab,\n                       live: tab.id == browser.activeID, width: width(in: strip),\n                       room: strip - Metrics.lights - 12, pill: pill,\n                       close: { browser.close(tab) })\n            .modifier(Carried(index: index, count: peers.count, step: step, vertical: false, space: "strip") { target in\n                guard peers.indices.contains(target),\n                      let canonical = browser.tabs.firstIndex(where: { $0.id == peers[target].id }) else { return }\n                browser.move(tab, to: canonical)\n            })\n            .id(tab.id)\n    }\n' + s[b:]
    p.write_text(s)
    edit('Sources/Search/TabBar.swift', '                                .onAppear { reveal(reader, in: geo.size.width) }', '                                .onAppear { reveal(reader, in: geo.size.width) }\n                                .onChange(of: geo.size.width) { _, _ in reveal(reader, in: geo.size.width) }\n                                .onChange(of: browser.groups) { _, _ in reveal(reader, in: geo.size.width) }')
    p = root / 'Sources/Search/SpaceSwipe.swift'
    s = p.read_text()
    s = s.replace('            Image(systemName: icon)\n                .font(.system(size: size, weight: .medium))', '            Group {\n                if let mark = emojiMark, mark.isValid { Text(mark.value) }\n                else { Image(systemName: icon) }\n            }\n                .font(.system(size: size, weight: .medium))')
    s = s.replace('.help(inline ? "New space — choose its icon" : "Choose an icon")', '.help(inline ? "New space — choose its emoji or icon" : "Choose an emoji or icon")')
    s = s.replace('Pill("Create", filled: true) { create() }', 'Pill("Create", filled: true) { create() }\n                            .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || emojiMark?.isValid == false)')
    p.write_text(s)
    p = root / 'Sources/Search/TabBar.swift'
    s = p.read_text()
    s = s.replace('Parked(tabs: browser.tabs, active: browser.activeID)', 'Parked(tabs: browser.tabs, active: browser.activeID, groups: browser.groups)')
    a = s.index('            let each = width(in: strip, pinned: row.tabs.filter')
    b = s.index('            .frame(height: Metrics.strip)', a)
    s = s[:a] + '            let visible = row.tabs.filter { tab in\n                guard let group = row.groups.first(where: { $0.id == tab.groupID }) else { return true }\n                return !group.collapsed\n            }\n            let each = width(in: strip, pinned: row.tabs.filter { $0.pin != nil }.count,\n                             count: visible.count, groupCount: row.groups.count)\n            HStack(spacing: Metrics.tabGap) {\n                ForEach(visible.filter { $0.groupID == nil }) { tab in\n                    previewPill(tab, active: row.active, width: each, strip: strip, pill: pill)\n                }\n                ForEach(row.groups) { group in\n                    GroupHeading(browser: browser, group: group,\n                                 count: row.tabs.filter { $0.groupID == group.id }.count,\n                                 selected: row.tabs.first(where: { $0.id == row.active })?.groupID == group.id)\n                        .frame(width: GroupHeading.stripWidth)\n                    ForEach(visible.filter { $0.groupID == group.id }) { tab in\n                        previewPill(tab, active: row.active, width: each, strip: strip, pill: pill)\n                    }\n                }\n            }\n' + s[b:]
    pos = s.index('    /// Brings the tab you are on into view')
    s = s[:pos] + '    private func previewPill(_ tab: Tab, active: UUID?, width: CGFloat, strip: CGFloat, pill: Namespace.ID) -> some View {\n        TabPill(browser: browser, prefs: browser.prefs, tab: tab,\n                live: tab.id == active, width: width, room: strip - Metrics.lights - 12,\n                pill: pill, close: {})\n    }\n\n' + s[pos:]
    p.write_text(s)

def resolve_pr192():
    resolve('Sources/Search/ExtensionShims.swift', [lambda m: m[1].replace('      if (inContent) return;\n', '') + m[3]])
    resolve('Sources/Search/Extensions.swift', [1])
    (root / 'tests/offscreen.py').write_bytes(subprocess.check_output(['git', 'show', 'fork/fix/offscreen-document-lifecycle:tests/offscreen.py'], cwd=root))
    p = root / 'Sources/Search/ExtensionShims.swift'
    s = p.read_text()
    old = '        if (offscreenCapable && window !== window.top && runtime && typeof runtime.sendMessage === "function") {'
    assert s.count(old) == 1
    s = s.replace(old, '        // Only extension-owned frame trees can contain this extension\'s\n        // offscreen document. Ordinary website subframes keep native delivery.\n        // The native relay additionally verifies the exact offscreen tab ID.\n        const ancestors = Array.from(location.ancestorOrigins || []);\n        const extensionOrigin = runtime && typeof runtime.getURL === "function"\n          ? runtime.getURL("").replace(/\\/$/, "") : "";\n        if (offscreenCapable && window !== window.top && extensionOrigin\n            && ancestors[ancestors.length - 1] === extensionOrigin\n            && runtime && typeof runtime.sendMessage === "function") {')
    p.write_text(s)
    p = root / 'Sources/Search/ExtensionOffscreen.swift'
    s = p.read_text()
    s = s.replace('tab["url"] as? String == document.url.absoluteString', 'tab["url"] as? String == document.web.url?.absoluteString')
    s = s.replace('    private var context: [String: Any] {\n        ["contextId":', '    private var context: [String: Any] {\n        let current = web.url ?? url\n        return ["contextId":')
    s = s.replace('"documentId": documentID, "documentUrl": url.absoluteString', '"documentId": documentID, "documentUrl": current.absoluteString')
    s = s.replace('"documentOrigin": "\\(url.scheme ?? "")://\\(url.host ?? "")\\(url.port.map { ":\\($0)" } ?? "")"', '"documentOrigin": "\\(current.scheme ?? "")://\\(current.host ?? "")\\(current.port.map { ":\\($0)" } ?? "")"')
    s = s.replace('        close(error: error)\n    }\n\n    func webView(_ webView: WKWebView, didFailProvisionalNavigation', '        if !ready { close(error: error) }\n    }\n\n    func webView(_ webView: WKWebView, didFailProvisionalNavigation')
    s = s.replace('        close(error: error)\n    }\n\n    func webViewWebContentProcessDidTerminate', '        if !ready { close(error: error) }\n    }\n\n    func webViewWebContentProcessDidTerminate')
    p.write_text(s)

def resolve_pr225():
    keep_head('CHANGELOG.md', 'ROADMAP.md')
    resolve('Sources/Search/App.swift', [1])
    resolve('Sources/Search/Settings.swift', [lambda m: ''.join(m[1].splitlines(keepends=True)[:2]) + m[3]])

def resolve_pr227():
    keep_head('CHANGELOG.md')
    opened = '        let destination = source.flatMap { previewSpace(for: $0) } ?? spaceID\n        if foreground, destination != spaceID { enter(destination) }\n        let anchor = source?.surface == .tab ? source?.id : source?.opener\n        if destination == spaceID {\n            let parent = tabs.first { $0.id == (anchor ?? activeID) }\n            tab.groupID = atEnd ? nil : parent?.groupID\n            if foreground { revealGroup(of: tab) }\n            let here = atEnd ? nil : tabs.firstIndex { $0.id == parent?.id }\n            let place = here.map { max($0 + 1, pinnedCount) } ?? tabs.count\n            tabs.insert(tab, at: place)\n        } else {\n            var row = parked[destination] ?? loadRow(destination)\n            let parent = row.tabs.first { $0.id == (anchor ?? row.active) }\n            tab.groupID = atEnd ? nil : parent?.groupID\n            let here = atEnd ? nil : row.tabs.firstIndex { $0.id == parent?.id }\n            let pins = row.tabs.prefix(while: { $0.pinned }).count\n            let place = here.map { max($0 + 1, pins) } ?? row.tabs.count\n            row.tabs.insert(tab, at: place)\n            parked[destination] = row\n        }\n'
    choices = {'App.swift': [lambda m: m[1] + m[3], lambda m: m[1] + m[3]], 'Bench.swift': [lambda m: m[1] + m[3]], 'Browser.swift': [lambda m: m[1].replace('tab = tabs.first', 'tab = allTabs.first'), lambda m: m[1] + m[3], lambda m: opened, lambda m: m[1].replace('source = active,', 'source = focusedTab,')], 'Links.swift': [3, 3], 'Peek.swift': [3], 'Prefs.swift': [lambda m: '        littleLinks = store.bool(forKey: "links.little")\n'], 'Spaces.swift': [lambda m: m[3] + m[1]], 'Stage.swift': [lambda m: m[3] + m[1]], 'Tab.swift': [lambda m: m[1] + m[3].replace('    let id = UUID()\n', '')]}
    for f, cs in choices.items():
        resolve('Sources/Search/' + f, cs)
    edit('Sources/Search/Links.swift', 'private static func comeForward()', 'static func comeForward()')
    edit('Sources/Search/Links.swift', 'private static func browserWindow()', 'static func browserWindow()')
    edit('Sources/Search/Links.swift', 'if browser.prefs.miniLinks { browser.receiveExternal(url) }', 'if browser.prefs.miniLinks || browser.prefs.littleLinks { browser.receiveExternal(url) }')
    edit('Sources/Search/Preview.swift', '        } else {\n            arrive(url)\n            showMainWindow()\n        }\n', '        } else if prefs.littleLinks, ["http", "https"].contains(url.scheme?.lowercased() ?? "") {\n            LittleWindow.show(url, for: self)\n        } else {\n            arrive(url)\n            showMainWindow()\n        }\n')
    p = root / 'Sources/Search/Preview.swift'
    s = p.read_text()
    start = s.index('    func showMainWindow() {')
    end = s.index('\n    }', start) + 6
    s = s[:start] + '    func showMainWindow() {\n        if let window = Links.window ?? Links.browserWindow() {\n            window.makeKeyAndOrderFront(nil)\n        } else {\n            _ = NSApp.delegate?.applicationOpenUntitledFile?(NSApp)\n        }\n        Links.comeForward()\n    }' + s[end:]
    p.write_text(s)
    edit('Sources/Search/Preview.swift', '        tab.opener = source?.id\n', '        tab.opener = source?.id\n        tab.groupID = source?.groupID\n')
    edit('Sources/Search/Preview.swift', '        let here = tabs.firstIndex { $0.id == tab.opener }\n        insert(tab, at: here.map { $0 + 1 } ?? tabs.count)\n', '        let opener = tabs.first { $0.id == tab.opener }\n        let group = opener.map { $0.groupID } ?? tab.groupID\n        tab.groupID = groups.contains(where: { $0.id == group }) ? group : nil\n        let here = tabs.firstIndex { $0.id == tab.opener }\n        insert(tab, at: here.map { max($0 + 1, pinnedCount) } ?? tabs.count)\n')

def resolve_pr231():
    keep_head('CHANGELOG.md')
    choices = {'Bench.swift': [lambda m: m[1] + m[3]], 'Side.swift': [lambda m: m[1].replace('return Metrics.strip +', 'return prefs.topBarHeight +')], 'Stage.swift': [lambda m: m[1] + m[3], lambda m: m[1].replace('allowed: { [weak tab]', 'chrome: chrome, allowed: { [weak tab]'), lambda m: m[3] + m[1], lambda m: '        view.show(page, chrome: chrome, allowed: allowed)\n', lambda m: m[1] + m[3], lambda m: '    func show(_ page: NSView?, chrome: PageChrome = PageChrome(), allowed: (() -> Bool)? = nil) {\n        self.chrome = chrome\n']}
    for f, cs in choices.items():
        resolve('Sources/Search/' + f, cs)

def finalize():
    def change(file, old, new):
        p = root / 'Sources/Search' / file
        s = p.read_text()
        if new in s:
            return
        assert s.count(old) == 1, (file, old[:80], s.count(old))
        p.write_text(s.replace(old, new))
    change('Store.swift', 'enum Store {', 'enum Store {\n    /// Personal builds name their data explicitly; never migrate the installed\n    /// upstream browser\'s profile into a fork merely because it was launched.\n    static let profileName: String = {\n        guard let name = Bundle.main.object(forInfoDictionaryKey: "SearchProfileName") as? String,\n              !name.isEmpty, name != ".", name != "..", !name.contains("/"), !name.contains(":")\n        else { return "Search" }\n        return name\n    }()\n')
    change('Store.swift', 'world.map { "Search (\\($0))" } ?? "Search", isDirectory:', 'world.map { "Search (\\($0))" } ?? profileName, isDirectory:')
    change('Store.swift', '        if !testing {\n            let old = support', '        if !testing, profileName == "Search" {\n            let old = support')
    change('Store.swift', '            carryOver(into: .standard)\n', '            if profileName == "Search" { carryOver(into: .standard) }\n')
    change('Vault.swift', 'Store.world.map { "Search (\\($0))" } ?? "Search"', 'Store.world.map { "Search (\\($0))" } ?? Store.profileName')
    change('Vault.swift', '        if let used { fields[kSecAttrComment as String] = String(used.timeIntervalSince1970) }\n', '        // The label restricts queries; the security domain also makes a fork\'s\n        // identical host/account a distinct keychain primary key on insertion.\n        if Store.profileName != "Search" { fields[kSecAttrSecurityDomain as String] = Store.profileName }\n        if let used { fields[kSecAttrComment as String] = String(used.timeIntervalSince1970) }\n')
    change('Updater.swift', '    static let shared = Updater()\n', '    static let shared = Updater()\n    nonisolated static var enabled: Bool {\n        Bundle.main.object(forInfoDictionaryKey: "SearchDisableUpdates") as? Bool != true\n    }\n')
    for signature in ['func checkIfDue(then say: @escaping (String) -> Void)', 'private func checkIfDue()', 'func install()', 'private func take(_ release: Release)']:
        change('Updater.swift', '    ' + signature + ' {\n', '    ' + signature + ' {\n        guard Self.enabled else { return }\n')
    change('Updater.swift', '    func check(then done: @escaping (Release?) -> Void) {\n', '    func check(then done: @escaping (Release?) -> Void) {\n        guard Self.enabled else { done(nil); return }\n')
    change('Prefs.swift', 'installsUpdates = store.object(forKey: Updater.installKey) as? Bool ?? true', 'installsUpdates = Updater.enabled && (store.object(forKey: Updater.installKey) as? Bool ?? true)')
    change('Settings.swift', '                    Switch(on: $prefs.installsUpdates)\n', '                    Switch(on: $prefs.installsUpdates)\n                        .disabled(!Updater.enabled)\n')
    change('Settings.swift', '    private var versionDetail: String {\n', '    private var versionDetail: String {\n        if !Updater.enabled { return "Personal fork — upstream updates are disabled to preserve your features. Install new builds from your integration branch." }\n')
    change('Settings.swift', '            .disabled(updater.checking)\n', '            .disabled(updater.checking || !Updater.enabled)\n')
    change('Settings.swift', 'Line("Open links from other apps in a small window", "To read and close, or keep with Open in Search (⌘O)")', 'Line("Use the upstream small window when Mini is off", "Mini takes priority above. With Mini off, this uses the original lightweight window; ⌘O keeps its page")')
    change('Prefs.swift', 'Extensions.shared.contexts.values.contains(where: { $0.command(for: event) != nil })', 'Extensions.shared.contexts.values.contains(where: { context in\n               context.commands.contains { ExtensionShortcut(key: $0.activationKey, flags: $0.modifierFlags) == ExtensionShortcut(event: event) }\n           })')
    change('Extensions.swift', '        if key == "\\t", flags.contains(.control), flags.intersection([.command, .option]).isEmpty {\n            return "Used by Search to switch tabs."\n        }\n', '        // Next/Previous Tab are reserved by their current native menu entries\n        // below, not by the old fixed Control-Tab defaults.\n')

def main():
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip():
        raise SystemExit('Refusing to integrate a dirty checkout')
    git('config', 'user.name', 'Search Integration')
    git('config', 'user.email', 'noreply@users.noreply.github.com')
    git('config', 'merge.conflictStyle', 'diff3')
    upstream = '491f3214063212fac176a7ad95467f0821040451'
    if git('merge-base', '--is-ancestor', upstream, 'HEAD', check=False).returncode:
        raise SystemExit('The reviewed upstream main must already be an ancestor')
    heads = [
        (54, 'knrs/extension-shortcuts', '52084d8850c58e9e0bdf92313efb956e5d19e38b'),
        (192, 'fix/offscreen-document-lifecycle', 'c650eb72dbb693c0cffd0c3361adf942a00b9a56'),
        (190, 'fix/floating-video-layout', '2755479e13bda01670f2b6863c7d2047efd01bb3'),
        (225, 'knrs/remappable-tab-cycling', 'aea59cd7adf12f70ca4fc153eead9f63f7f2585d'),
        (227, 'knrs/mini-and-peek', '19be56fc8774e300416280f331170b7495e0edef'),
        (231, 'knrs/adjustable-browser-appearance', '6a7f3f4e2b4300bec16b819383407eb78493783b'),
    ]
    for number, branch, sha in heads:
        print(f'\nIntegrating PR #{number}: {branch} at {sha}', flush=True)
        git('cat-file', '-e', sha + '^{commit}')
        git('update-ref', 'refs/remotes/fork/' + branch, sha)
        if git('merge-base', '--is-ancestor', sha, 'HEAD', check=False).returncode == 0:
            continue
        result = git('merge', '--no-ff', '--no-commit', 'fork/' + branch, check=False)
        if result.returncode not in (0, 1):
            raise SystemExit('Merge failed before conflict resolution')
        if number == 190:
            git('checkout', '--ours', 'CHANGELOG.md', 'Sources/Search/Float.swift')
        else:
            globals()['resolve_pr' + str(number)]()
        for path in subprocess.check_output(['git', 'ls-files'], cwd=root, text=True).splitlines():
            p = root / path
            if p.is_file() and p.suffix in ('.swift', '.md', '.py'):
                if re.search(r'^<<<<<<< |^>>>>>>> ', p.read_text(errors='replace'), re.M):
                    raise SystemExit('Unresolved conflict in ' + path)
        git('add', '-u')
        git('diff', '--cached', '--check')
        git('commit', '-m', f'Merge PR #{number}: reconcile with current upstream and personal features')
    finalize()
    git('add', '-u')
    if git('diff', '--cached', '--quiet', check=False).returncode:
        git('commit', '-m', 'fix: cross-feature behavior and isolated personal installation')
    git('diff', '--check')
    for _, _, sha in heads:
        git('merge-base', '--is-ancestor', sha, 'HEAD')
    print('All six PR heads and reviewed upstream are ancestors of this integration.', flush=True)

if __name__ == '__main__':
    main()
