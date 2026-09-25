import Foundation

/// Probe-only access to the same grouping operations used by native menus.
extension Browser {
    func groupingCommand(_ request: [String: Any]) -> [String: Any] {
        let invalid: [String: Any] = ["error": "Invalid grouping operation"]
        guard Store.testing else { return ["error": "Grouping commands require an isolated test run"] }
        func uuid(_ key: String) -> UUID? {
            (request[key] as? String).flatMap(UUID.init(uuidString:))
        }
        let tab = uuid("id").flatMap { id in tabs.first { $0.id == id } }
        let group = uuid("group")
        let space = uuid("space")
        let name = request["name"] as? String ?? ""

        // Malformed values must fail before creating a space/group or clearing
        // an existing marker. Decoding persisted cosmetics is intentionally
        // tolerant, so input validation here uses the actual enum and fields.
        var mark: TabMark?
        if let raw = request["mark"], !(raw is NSNull) {
            guard let fields = raw as? [String: Any],
                  let kindName = fields["kind"] as? String,
                  let kind = TabMark.Kind(rawValue: kindName),
                  let value = fields["value"] as? String else { return invalid }
            let candidate = TabMark(kind: kind, value: value)
            guard candidate.isValid else { return invalid }
            mark = candidate
        }

        switch request["action"] as? String ?? "state" {
        case "state": break
        case "enable-spaces":
            guard let enabled = request["enabled"] as? Bool else { return invalid }
            prefs.usesSpaces = enabled
        case "create-space":
            guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return invalid }
            addSpace(named: name, sharesSignIns: !(request["separate"] as? Bool ?? false), mark: mark)
        case "select-space":
            guard let space, spaces.contains(where: { $0.id == space }), prefs.usesSpaces else { return invalid }
            switchSpace(to: space)
        case "set-space-mark":
            guard request["mark"] != nil, let space, setSpaceMark(space, mark: mark) else { return invalid }
        case "open":
            guard let raw = request["url"] as? String, let url = URL(string: raw) else { return invalid }
            open(url, foreground: true)
        case "new-tab": newTab()
        case "private-tab": newShyTab()
        case "create-group":
            guard request["id"] == nil || tab != nil,
                  createGroup(name: name, tab: tab, mark: mark) != nil else { return invalid }
        case "rename-group":
            guard let group, renameGroup(group, name: name) else { return invalid }
        case "set-group-mark":
            guard request["mark"] != nil, let group, setGroupMark(group, mark: mark) else { return invalid }
        case "assign-group":
            if let raw = request["group"], !(raw is NSNull), group == nil { return invalid }
            guard let tab, assignGroup(tab, group: group) else { return invalid }
        case "toggle-group":
            guard let group, groups.contains(where: { $0.id == group }) else { return invalid }
            toggleGroup(group)
        case "remove-group":
            guard let group, groups.contains(where: { $0.id == group }) else { return invalid }
            removeGroup(group)
        case "group-tab":
            guard let group, groups.contains(where: { $0.id == group }) else { return invalid }
            newTab(inGroup: group)
        case "pin":
            guard let tab else { return invalid }
            pin(tab)
        case "unpin":
            guard let tab else { return invalid }
            unpin(tab)
        case "select-tab":
            guard let tab else { return invalid }
            select(tab)
        case "close-tab":
            guard let tab else { return invalid }
            close(tab)
        case "reorder":
            guard let tab, let index = request["index"] as? Int, tabs.indices.contains(index) else { return invalid }
            move(tab, to: index)
        case "reopen": reopen()
        case "duplicate": duplicate()
        case "replace":
            guard let tab, let raw = request["url"] as? String, let url = URL(string: raw) else { return invalid }
            replace(tab, going: url)
        case "save": flushSession()
        case "sidebar":
            guard let enabled = request["enabled"] as? Bool else { return invalid }
            prefs.sidebar = enabled
        case "rename-tab":
            guard let tab, request["name"] is String else { return invalid }
            beginTabRename(tab)
            tabDraft = name
            commitTabEdit()
        default: return invalid
        }
        return groupingState
    }

    var groupingState: [String: Any] {
        func optional<T>(_ value: T?) -> Any { value.map { $0 as Any } ?? NSNull() }
        func marker(_ value: TabMark?) -> Any {
            guard let value else { return NSNull() }
            return ["kind": value.kind.rawValue, "value": value.value]
        }
        let spaceRows: [[String: Any]] = spaces.map {
            ["id": $0.id.uuidString, "name": $0.name, "icon": optional($0.icon),
             "sharesSignIns": optional($0.sharesSignIns), "downloads": optional($0.downloads),
             "mark": marker($0.mark)]
        }
        let groupRows: [[String: Any]] = groups.map {
            ["id": $0.id.uuidString, "name": $0.name, "collapsed": $0.collapsed, "mark": marker($0.mark)]
        }
        let tabRows: [[String: Any]] = tabs.map {
            ["id": $0.id.uuidString, "url": ($0.pending ?? $0.address)?.absoluteString ?? "",
             "name": optional($0.name), "pin": optional($0.pin), "groupID": optional($0.groupID?.uuidString),
             "shy": $0.shy, "bench": $0.bench]
        }
        return ["spaceID": spaceID.uuidString, "spaces": spaceRows, "groups": groupRows,
                "tabs": tabRows, "presented": presentedTabs.map { $0.id.uuidString },
                "activeID": optional(activeID?.uuidString)]
    }
}
