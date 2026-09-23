import Foundation

extension Browser {
    func validGroups(_ saved: [TabGroup]) -> [TabGroup] {
        var seen = Set<UUID>()
        return saved.filter { seen.insert($0.id).inserted && !$0.name.isEmpty }.map { group in
            var group = group
            if group.mark?.isValid == false { group.mark = nil }
            return group
        }
    }

    var orderedTabs: [Tab] {
        tabs.filter { $0.pin != nil }
            + tabs.filter { $0.pin == nil && $0.groupID == nil }
            + groups.flatMap { group in tabs.filter { $0.pin == nil && $0.groupID == group.id } }
    }

    var presentedTabs: [Tab] {
        let collapsed = Set(groups.filter(\.collapsed).map(\.id))
        return orderedTabs.filter { $0.groupID.map { !collapsed.contains($0) } ?? true }
    }

    @discardableResult
    func createGroup(name: String, tab: Tab? = nil, mark: TabMark? = nil) -> TabGroup? {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, mark?.isValid != false,
              tab == nil || tabs.contains(where: { $0.id == tab?.id }) else { return nil }
        let group = TabGroup(id: UUID(), name: name, mark: mark)
        groups.append(group)
        if let tab { _ = assignGroup(tab, group: group.id) }
        writeSession(now: true)
        return group
    }

    @discardableResult
    func renameGroup(_ id: UUID, name: String) -> Bool {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, let index = groups.firstIndex(where: { $0.id == id }) else { return false }
        groups[index].name = name
        writeSession(now: true)
        return true
    }

    @discardableResult
    func setGroupMark(_ id: UUID, mark: TabMark?) -> Bool {
        guard mark?.isValid != false, let index = groups.firstIndex(where: { $0.id == id }) else { return false }
        groups[index].mark = mark
        writeSession(now: true)
        return true
    }

    @discardableResult
    func setSpaceMark(_ id: UUID, mark: TabMark?) -> Bool {
        guard mark?.isValid != false, let index = spaces.firstIndex(where: { $0.id == id }) else { return false }
        spaces[index].mark = mark
        Spaces.write(spaces)
        return true
    }

    @discardableResult
    func assignGroup(_ tab: Tab, group id: UUID?) -> Bool {
        guard tabs.contains(where: { $0.id == tab.id }),
              id == nil || groups.contains(where: { $0.id == id }) else { return false }
        if id != nil, tab.pin != nil { unpin(tab) }
        tab.groupID = id
        objectWillChange.send()
        writeSession(now: true)
        return true
    }

    func toggleGroup(_ id: UUID) {
        guard let index = groups.firstIndex(where: { $0.id == id }) else { return }
        cancelTabEdit()
        groups[index].collapsed.toggle()
        writeSession(now: true)
    }

    func revealGroup(of tab: Tab) {
        guard let index = groups.firstIndex(where: { $0.id == tab.groupID }), groups[index].collapsed else { return }
        groups[index].collapsed = false
        writeSession(now: true)
    }

    func removeGroup(_ id: UUID) {
        for tab in tabs where tab.groupID == id { tab.groupID = nil }
        groups.removeAll { $0.id == id }
        writeSession(now: true)
    }

    func newTab(inGroup id: UUID) {
        guard groups.contains(where: { $0.id == id }) else { return }
        if let blank = tabs.first(where: { $0.groupID == id && $0.isBlank && !$0.shy && !$0.bench }) {
            select(blank)
        } else {
            let tab = Tab()
            tab.groupID = id
            adopt(tab)
            select(tab)
            if #available(macOS 15.4, *) { Extensions.shared.offerNewTabPage(into: tab) }
        }
        typed = ""
        askFocus()
        writeSession(now: true)
    }
}
