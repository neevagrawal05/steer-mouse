import Cocoa
import CoreGraphics

// ─── Tap disable signal constants ────────────────────────────────────────────
// macOS sends these pseudo-event-types when it auto-disables the tap.
// Without handling them the daemon silently stops working.
private let kTapDisabledByTimeout   = CGEventType(rawValue: UInt32(0xFFFFFFFE))!
private let kTapDisabledByUserInput = CGEventType(rawValue: UInt32(0xFFFFFFFF))!

// Global so the callback closure can re-enable it without a capture.
var globalTap: CFMachPort?

let config = ConfigLoader.load()
let engine = ChordEngine(config: config)

let opts = [kAXTrustedCheckOptionPrompt.takeRetainedValue() as String: true] as CFDictionary
if !AXIsProcessTrustedWithOptions(opts) {
    print("""
    [ChordDaemon] ❌  Accessibility permission not granted.
    Open: System Settings → Privacy & Security → Accessibility
    Add ChordDaemon and enable it, then relaunch.
    """)
    exit(1)
}

let mask: CGEventMask =
    (1 << CGEventType.leftMouseDown.rawValue)  |
    (1 << CGEventType.leftMouseUp.rawValue)    |
    (1 << CGEventType.rightMouseDown.rawValue) |
    (1 << CGEventType.rightMouseUp.rawValue)   |
    (1 << CGEventType.otherMouseDown.rawValue) |
    (1 << CGEventType.otherMouseUp.rawValue)   |
    (1 << CGEventType.scrollWheel.rawValue)

let enginePtr = Unmanaged.passRetained(engine).toOpaque()

let tapCallback: CGEventTapCallBack = { _, type, event, userInfo in

    // ── Re-enable the tap instead of letting it die ──────────────────────────
    // macOS disables the tap when it decides the callback is too slow.
    // We immediately re-enable it so the daemon keeps running.
    if type == kTapDisabledByTimeout || type == kTapDisabledByUserInput {
        print("[ChordDaemon] ⚠️  Event tap was disabled by system — re-enabling…")
        if let tap = globalTap { CGEvent.tapEnable(tap: tap, enable: true) }
        return nil
    }

    guard let userInfo = userInfo else { return Unmanaged.passRetained(event) }
    let eng = Unmanaged<ChordEngine>.fromOpaque(userInfo).takeUnretainedValue()
    if let out = eng.handle(event: event, type: type) {
        return Unmanaged.passRetained(out)
    }
    return nil
}

guard let tap = CGEvent.tapCreate(
    tap:              .cgSessionEventTap,
    place:            .headInsertEventTap,
    options:          .defaultTap,
    eventsOfInterest: mask,
    callback:         tapCallback,
    userInfo:         enginePtr
) else {
    print("[ChordDaemon] ❌  CGEvent.tapCreate failed — check Accessibility permission.")
    exit(1)
}

globalTap = tap   // store so the callback can re-enable it

let runLoopSrc = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSrc, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)

// ── Watchdog: poll every 5 s and re-enable if the tap went dead ──────────────
// Belt-and-suspenders on top of the callback check above.
let watchdog = DispatchSource.makeTimerSource(queue: .main)
watchdog.schedule(deadline: .now() + 5, repeating: 5)
watchdog.setEventHandler {
    guard let tap = globalTap, !CFMachPortIsValid(tap) == false else { return }
    if !CGEvent.tapIsEnabled(tap: tap) {
        print("[ChordDaemon] ⚠️  Watchdog re-enabling tap…")
        CGEvent.tapEnable(tap: tap, enable: true)
    }
}
watchdog.resume()

print("[ChordDaemon] 🚀  Daemon is running…")
RunLoop.main.run()
