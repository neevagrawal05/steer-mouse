import Cocoa
import CoreGraphics

// ── 1. Load config ─────────────────────────────────────────────────────────

let config = ConfigLoader.load()
let engine = ChordEngine(config: config)

// ── 2. Check Accessibility ──────────────────────────────────────────────────

let opts = [kAXTrustedCheckOptionPrompt.takeRetainedValue() as String: true] as CFDictionary
if !AXIsProcessTrustedWithOptions(opts) {
    print("""
    [ChordDaemon] ❌  Accessibility permission not granted.
    Open: System Settings → Privacy & Security → Accessibility
    Add ChordDaemon and enable it, then relaunch.
    """)
}

// ── 3. Install CGEventTap — buttons only ────────────────────────────────

let mask: CGEventMask =
    (1 << CGEventType.leftMouseDown.rawValue)  |
    (1 << CGEventType.leftMouseUp.rawValue)    |
    (1 << CGEventType.rightMouseDown.rawValue) |
    (1 << CGEventType.rightMouseUp.rawValue)   |
    (1 << CGEventType.otherMouseDown.rawValue) |
    (1 << CGEventType.otherMouseUp.rawValue)

let enginePtr = Unmanaged.passRetained(engine).toOpaque()

let tapCallback: CGEventTapCallBack = { _, type, event, userInfo in
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

let runLoopSrc = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSrc, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)

print("[ChordDaemon] ✅  Running — listening for button chords.")
print("[ChordDaemon]     Press Ctrl+C to stop.")

// ── 4. Run ──────────────────────────────────────────────────────────────────

RunLoop.main.run()
