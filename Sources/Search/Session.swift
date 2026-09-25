import Foundation

struct TabMark: Codable, Equatable {
    enum Kind: String, Codable { case emoji, symbol }
    var kind: Kind
    var value: String

    static let symbols = Spaces.icons + ["square.stack", "folder", "bookmark",
                          "globe", "star", "bolt", "paintbrush"]

    var isValid: Bool {
        switch kind {
        case .symbol: return Self.symbols.contains(value)
        case .emoji:
            // Character counts extended graphemes: families, flags, skin tones,
            // and joined professions each occupy one marker, not several slots.
            guard value.count == 1 else { return false }
            let scalars = value.unicodeScalars
            return scalars.contains { $0.properties.isEmoji }
                && (scalars.contains { $0.properties.isEmojiPresentation || $0.value > 127 && $0.properties.isEmoji }
                    || scalars.contains { $0.value == 0xFE0F || $0.value == 0x20E3 })
        }
    }

    init(kind: Kind, value: String) { self.kind = kind; self.value = value }

    private enum CodingKeys: String, CodingKey { case kind, value }

    init(from decoder: Decoder) throws {
        // A damaged cosmetic marker must not quarantine otherwise usable tabs.
        let fields = try? decoder.container(keyedBy: CodingKeys.self)
        kind = (try? fields?.decode(Kind.self, forKey: .kind)) ?? .symbol
        value = (try? fields?.decode(String.self, forKey: .value)) ?? ""
    }
}

struct TabGroup: Codable, Identifiable, Equatable {
    var id: UUID
    var name: String
    var collapsed: Bool = false
    var mark: TabMark?
}


// What was open last time. A list of addresses and their names, and which one
// you were looking at — nothing else, because everything else is either on the
// page or in the history file next door.

enum Session {
    struct Entry: Codable {
        var url: String
        var title: String
        var pin: String?
        /// The name you gave the tab, when you gave it one.
        var name: String?
        var id: UUID?
        var groupID: UUID?
    }

    struct Shape: Codable {
        var tabs: [Entry]
        var active: Int
        var groups: [TabGroup]?
    }

    /// The first space's is the session there always was; each other space
    /// keeps its own beside it.
    private static func file(_ space: UUID) -> URL {
        Store.file(space == Space.firstID ? "session.json" : "session-\(space.uuidString).json")
    }

    private static let writer = DispatchQueue(label: "search.session", qos: .utility)

    static func erase(space: UUID) {
        guard space != Space.firstID else { return }
        writer.sync { try? FileManager.default.removeItem(at: file(space)) }
    }

    static func read(space: UUID = Space.firstID) -> Shape {
        let file = file(space)
        guard let data = try? Data(contentsOf: file) else { return Shape(tabs: [], active: 0) }
        guard let shape = try? JSONDecoder().decode(Shape.self, from: data) else {
            // A file that's there but won't decode is not the same as no
            // file: something wrote it, and overwriting it on the next save
            // without a trace is how yesterday's tabs actually disappear.
            Store.quarantine(file)
            return Shape(tabs: [], active: 0)
        }
        return shape
    }

    /// `now` writes on the calling thread. Quitting doesn't wait for a
    /// background queue, and a session handed to one on the way out is a
    /// session that may never reach the disk.
    static func write(now: Bool = false, space: UUID = Space.firstID, _ shape: Shape) {
        let file = file(space)
        let put = {
            guard let data = try? JSONEncoder().encode(shape) else { return }
            try? FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true
            )
            try? data.write(to: file, options: .atomic)
        }
        if now {
            writer.sync(execute: put)
        } else {
            writer.async(execute: put)
        }
    }
}
