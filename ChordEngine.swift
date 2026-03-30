import Foundation
import CoreGraphics
import AppKit
import IOKit

final class ChordEngine {

    private var heldButtons:     Set<Int> = []
    private var consumedButtons: Set<Int> = []
    private var chordMap:  [[Int]: Action] = [:]
    private var buttonMap: [Int: Action]   = [:]
    private var pendingTimers:   [Int: DispatchWorkItem] = [:]
    private var pressTimestamps: [Int: Date] = [:]

    private let maxHoldTime: TimeInterval = 3.0  // 3 seconds - enough time for chord detection
    private let chordWindow: Double       = 0.020

    // Scroll settings
    private let scrollReverse: Bool
    private let scrollSpeed:   Double

    // MARK: - Init

    init(config: ChordConfig) {
        for mapping in config.chords  { chordMap[mapping.buttons.sorted()] = mapping.action }
        for mapping in config.buttons { buttonMap[mapping.button] = mapping.action }

        scrollReverse = config.scroll?.reverse ?? false
        scrollSpeed   = config.scroll?.speed   ?? 1.0

        print("[ChordEngine] \(chordMap.count) chord(s), \(buttonMap.count) button(s) loaded")
        print("[ChordEngine] Chord pairs:  \(chordMap.keys.sorted(by:{ $0[0] < $1[0] }))")
        print("[ChordEngine] Individual:   \(buttonMap.keys.sorted())")
        print("[ChordEngine] Scroll reverse=\(scrollReverse) speed=\(scrollSpeed)x")
    }

    // MARK: - Event Handler

    func handle(event: CGEvent, type: CGEventType) -> CGEvent? {

        // ── Scroll wheel ──────────────────────────────────────────────────
        if type == .scrollWheel {
            return handleScroll(event)
        }

        // ── Mouse buttons ─────────────────────────────────────────────────
        let btn = buttonNumber(for: type, event: event)
        let isDown = (type == .leftMouseDown  || type == .rightMouseDown || type == .otherMouseDown)
        let isUp   = (type == .leftMouseUp    || type == .rightMouseUp   || type == .otherMouseUp)

        guard isDown || isUp else { return event }
        guard isTracked(btn)  else { return event }

        clearGhostButtons()

        if isDown {
            if heldButtons.contains(btn) { return event }

            for partner in heldButtons {
                let pair = [btn, partner].sorted()
                if let action = chordMap[pair] {
                    print("[CHORD] ✅ \(pair) → \(action.type.rawValue)")
                    perform(action)
                    if btn == 0 || btn == 1 { cancelClick(btn: btn) }
                    // Mark both buttons as consumed to suppress their individual actions on release
                    consumedButtons.insert(btn)
                    consumedButtons.insert(partner)
                    // Clear held buttons but keep consumedButtons for release event suppression
                    for heldBtn in heldButtons { cancelTimer(for: heldBtn) }
                    heldButtons.removeAll()
                    pressTimestamps.removeAll()
                    return nil
                }
            }

            heldButtons.insert(btn)
            pressTimestamps[btn] = Date()

            if btn == 0 || btn == 1 { return event }

            // Don't fire immediately — wait for release to fire the button action
            // This way we can detect chords if another button is pressed
            startTimer(for: btn)
            return nil

        } else {
            pressTimestamps.removeValue(forKey: btn)
            
            // Check if this button was consumed by a chord BEFORE checking heldButtons
            if consumedButtons.contains(btn) {
                consumedButtons.remove(btn)
                print("[SUPR ] Button \(btn) suppressed")
                return nil
            }
            
            guard heldButtons.contains(btn) else { return event }
            heldButtons.remove(btn)
            cancelTimer(for: btn)

            if let action = buttonMap[btn] {
                print("[FIRE ] Button \(btn) → \(action.type.rawValue)")
                perform(action)
                return nil
            }

            return event
        }
    }

    // MARK: - Scroll Handler

    private func handleScroll(_ event: CGEvent) -> CGEvent? {
        let flip: Double = scrollReverse ? -1.0 : 1.0
        let mul:  Double = scrollSpeed * flip

        // Only modify if something actually needs changing
        guard mul != 1.0 else { return event }

        print("[SCROLL] reverse=\(scrollReverse) mul=\(mul)")

        // Read current deltas
        let i1 = event.getIntegerValueField(.scrollWheelEventDeltaAxis1)
        let i2 = event.getIntegerValueField(.scrollWheelEventDeltaAxis2)
        let f1 = event.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1)
        let f2 = event.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2)
        let p1 = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
        let p2 = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis2)

        let newI1 = Int64((Double(i1) * mul).rounded())
        let newI2 = Int64((Double(i2) * mul).rounded())
        let newF1 = f1 * mul
        let newF2 = f2 * mul
        let newP1 = p1 * mul
        let newP2 = p2 * mul

        print("[SCROLL] [axis1] int: \(i1) → \(newI1) | fixed: \(f1) → \(newF1) | point: \(p1) → \(newP1)")
        print("[SCROLL] [axis2] int: \(i2) → \(newI2) | fixed: \(f2) → \(newF2) | point: \(p2) → \(newP2)")

        // Try modifying the original event
        event.setIntegerValueField(.scrollWheelEventDeltaAxis1, value: newI1)
        event.setIntegerValueField(.scrollWheelEventDeltaAxis2, value: newI2)
        event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1, value: newF1)
        event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2, value: newF2)
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis1, value: newP1)
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis2, value: newP2)

        return event
    }

    // MARK: - Ghost + Reset

    private func clearGhostButtons() {
        let now = Date()
        var ghosts: [Int] = []
        
        // Only clear isolated buttons (no chord in progress)
        // If multiple buttons are held, skip ghost detection (chord attempt in progress)
        if heldButtons.count <= 1 {
            for btn in heldButtons {
                guard let t = pressTimestamps[btn] else { ghosts.append(btn); continue }
                if now.timeIntervalSince(t) > maxHoldTime { ghosts.append(btn) }
            }
        }
        
        for btn in ghosts {
            print("[GHOST] ⚠️  Button \(btn) stuck (held > 3s alone) — clearing")
            heldButtons.remove(btn)
            consumedButtons.remove(btn)
            pressTimestamps.removeValue(forKey: btn)
            cancelTimer(for: btn)
        }
    }

    private func resetState() {
        for btn in heldButtons { cancelTimer(for: btn) }
        heldButtons.removeAll()
        consumedButtons.removeAll()
        pressTimestamps.removeAll()
    }

    // MARK: - Helpers

    private func cancelClick(btn: Int) {
        let upType: CGEventType = (btn == 0) ? .leftMouseUp : .rightMouseUp
        let src = CGEventSource(stateID: .combinedSessionState)
        let loc = CGEvent(source: nil)?.location ?? .zero
        let up  = CGEvent(mouseEventSource: src, mouseType: upType,
                          mouseCursorPosition: loc,
                          mouseButton: btn == 0 ? .left : .right)
        up?.post(tap: .cghidEventTap)
    }

    private func startTimer(for btn: Int) {
        guard buttonMap[btn] != nil else { return }
        let item = DispatchWorkItem { [weak self] in
            guard let self = self,
                  self.heldButtons.contains(btn),
                  !self.consumedButtons.contains(btn),
                  let action = self.buttonMap[btn] else { return }
            print("[FAST ] Button \(btn) timer → \(action.type.rawValue)")
            self.pendingTimers.removeValue(forKey: btn)
            self.consumedButtons.insert(btn)
            self.perform(action)
        }
        pendingTimers[btn] = item
        DispatchQueue.main.asyncAfter(deadline: .now() + chordWindow, execute: item)
    }

    private func cancelTimer(for btn: Int) {
        pendingTimers.removeValue(forKey: btn)?.cancel()
    }

    private func buttonNumber(for type: CGEventType, event: CGEvent) -> Int {
        switch type {
        case .leftMouseDown,  .leftMouseUp:  return 0
        case .rightMouseDown, .rightMouseUp: return 1
        default: return Int(event.getIntegerValueField(.mouseEventButtonNumber))
        }
    }

    private func isTracked(_ btn: Int) -> Bool {
        buttonMap[btn] != nil || chordMap.keys.contains(where: { $0.contains(btn) })
    }

    // MARK: - Action Dispatch

    private func perform(_ action: Action) {
        switch action.type {
        case .missionControl:
            runShell("open -a 'Mission Control'")
        case .launchpad:
            runShell("open -a Launchpad")
        case .expose:
            runAppleScript("tell application \"System Events\" to key code 101 using {control down}")
        case .playPause:
            sendMediaKeyViaAppleScript()
        case .back:
            sendKey(keyCode: 33, modifiers: .maskCommand)
        case .forward:
            sendKey(keyCode: 30, modifiers: .maskCommand)
        case .keystroke:
            guard let kc = action.keyCode else { return }
            let mods = cgFlags(from: action.modifiers)
            if mods.contains(.maskControl) && (kc == 123 || kc == 124) {
                let dir = kc == 124 ? "right →" : "left ←"
                print("[SPACE] Switching \(dir)")
                let script = kc == 124
                    ? "tell application \"System Events\" to key code 124 using {control down}"
                    : "tell application \"System Events\" to key code 123 using {control down}"
                runAppleScript(script)
            } else {
                sendKey(keyCode: CGKeyCode(kc), modifiers: mods)
            }
        case .none: break
        }
    }

    // MARK: - Senders

    private func runAppleScript(_ script: String) {
        print("[AS   ] \(script)")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        try? p.run()
    }

    private func sendKey(keyCode: CGKeyCode, modifiers: CGEventFlags) {
        print("[KEY  ] keyCode=\(keyCode) modifiers=\(modifiers.rawValue)")
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let down = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true)
        down?.flags = modifiers
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)
        up?.flags = modifiers
        up?.post(tap: .cghidEventTap)
    }

    private func sendMediaKeyViaAppleScript() {
        print("[MEDIA] Sending play/pause via native media key (original method)")
        sendMediaKey(keyType: 16)
    }

    private func sendMediaKey(keyType: Int) {
        print("[MEDIA] Sending media key \(keyType)")
        let flags = 0xa00
        
        // Press (data2 = 0xa)
        let downData1 = (keyType << 16) | (0xa << 8)
        let downEvent = NSEvent.otherEvent(
            with: .systemDefined,
            location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: UInt(flags)),
            timestamp: 0,
            windowNumber: 0,
            context: nil,
            subtype: 8,
            data1: downData1,
            data2: -1
        )
        print("[MEDIA] Posting down event: data1=\(String(format: "0x%x", downData1))")
        downEvent?.cgEvent?.post(tap: .cgSessionEventTap)
        
        usleep(10000) // 10ms delay
        
        // Release (data2 = 0xb)
        let upData1 = (keyType << 16) | (0xb << 8)
        let upEvent = NSEvent.otherEvent(
            with: .systemDefined,
            location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: UInt(flags)),
            timestamp: 0,
            windowNumber: 0,
            context: nil,
            subtype: 8,
            data1: upData1,
            data2: -1
        )
        print("[MEDIA] Posting up event: data1=\(String(format: "0x%x", upData1))")
        upEvent?.cgEvent?.post(tap: .cgSessionEventTap)
        print("[MEDIA] ✅ Media key sent")
    }

    private func runShell(_ cmd: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", cmd]
        try? p.run()
    }

    private func cgFlags(from names: [String]?) -> CGEventFlags {
        var f: CGEventFlags = []
        for name in (names ?? []) {
            switch name.lowercased() {
            case "command": f.insert(.maskCommand)
            case "shift":   f.insert(.maskShift)
            case "option":  f.insert(.maskAlternate)
            case "control": f.insert(.maskControl)
            default: break
            }
        }
        return f
    }
}
