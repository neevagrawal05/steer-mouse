import Foundation

enum ActionType: String, Codable {
    case back
    case forward
    case missionControl
    case launchpad
    case expose
    case playPause
    case previousTrack
    case nextTrack
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

struct ScrollSettings: Codable {
    var reverse: Bool?      
    var speed:   Double?    
}

struct ChordConfig: Codable {
    var chords:  [ChordMapping]
    var buttons: [ButtonMapping]
    var scroll:  ScrollSettings?   
}

struct ConfigLoader {

    static func load() -> ChordConfig {
        let locations: [URL] = [
            executableDir().appendingPathComponent("config.json"),
            homeDir().appendingPathComponent(".config/chorddaemon/config.json"),
            homeDir().appendingPathComponent(".chorddaemon/config.json")
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