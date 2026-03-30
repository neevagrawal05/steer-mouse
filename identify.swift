import Cocoa

let mask: CGEventMask =
    (1 << CGEventType.otherMouseDown.rawValue) |
    (1 << CGEventType.leftMouseDown.rawValue)  |
    (1 << CGEventType.rightMouseDown.rawValue)

let cb: CGEventTapCallBack = { _, type, event, _ in
    let btn = event.getIntegerValueField(.mouseEventButtonNumber)
    print("🖱 Button number: \(btn)  (type: \(type.rawValue))")
    return Unmanaged.passRetained(event)
}

let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
    options: .listenOnly, eventsOfInterest: mask, callback: cb, userInfo: nil)!
CFRunLoopAddSource(CFRunLoopGetMain(),
    CFMachPortCreateRunLoopSource(nil, tap, 0), .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
print("Click each mouse button to see its number. Ctrl+C to stop.")
RunLoop.main.run()
