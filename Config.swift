import Foundation

enum ActionType: String, Codable {
    case back
    case forward
    case missionControl
    case launchpad
    case expose
    case playPause
    case keystroke
    case none
}

struct Action: Codable {
    var type:      ActionType
    var keyCode:   UInt16?
    var modifiers: [String]?
}

struct ChordMapping: Codable {
    var buttons: [Int]
    var action:  Action
}

struct ButtonMapping: Codable {
    var button: Int
    var action: Action
}

// NEW: Scroll settings block in config.json
struct ScrollSettings: Codable {
    var reverse: Bool?      // true = reverse scroll direction
    var speed:   Double?    // 1.0 = normal, 2.0 = 2x faster, 0.5 = slower
}

struct ChordConfig: Codable {
    var chords:  [ChordMapping]
    var buttons: [ButtonMapping]
    var scroll:  ScrollSettings?   // optional — defaults to normal if missing
}

struct ConfigLoader {

    static func load() -> ChordConfig {
        let locations: [URL] = [
            executableDir().appendingPathComponent("config.json"),
            homeDir().appendingPathComponent(".config/chorddaemon/config.json")
        ]
        for url in locations {
            guard let data = try? Data(contentsOf: url) else { continue }
            if let config = try? JSONDecoder().decode(ChordConfig.self, from: data) {
                print("[ChordDaemon] ✅ Config loaded from \(url.path)")
                return config
            } else {
                print("[ChordDaemon] ⚠️  Parse error in \(url.path)")
            }
        }
        print("[ChordDaemon] ⚠️  Using built-in defaults")
        return ChordConfig(chords: [], buttons: [], scroll: nil)
    }

    private static func executableDir() -> URL {
        URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    }
    private static func homeDir() -> URL {
        URL(fileURLWithPath: NSHomeDirectory())
    }
}
