import Foundation
import CoreGraphics
import AppKit
import IOKit

final class ChordEngine {

    private struct ResolvedAction {
        let type: ActionType
        let keyCode: CGKeyCode?
        let flags: CGEventFlags
    }

    private var heldButtons:     Set<Int> = []
    private var consumedButtons: Set<Int> = []
    private var pendingTimers:   [Int: DispatchWorkItem] = [:]
    private var pressTimestamps: [Int: Date] = [:]

    private var chordMap:  [Int: ResolvedAction] = [:]
    private var buttonMap: [Int: ResolvedAction] = [:]

    private let maxHoldTime: TimeInterval = 3.0 
    private let chordWindow: Double       = 0.020 

    private let scrollReverse: Bool
    private let scrollSpeed: Double
    
    // 🎛️ Volume Modifier explicitly set to Button 4
    private let volumeScrollButtons: Set<Int> = [4] 
    private var accumulatedScroll: Double = 0.0

    init(config: ChordConfig) {
        self.scrollReverse = config.scroll?.reverse ?? false
        self.scrollSpeed   = config.scroll?.speed ?? 1.0

        for mapping in config.chords {
            let mask = mapping.buttons.reduce(0) { $0 | (1 << $1) }
            chordMap[mask] = resolve(mapping.action)
        }
        for mapping in config.buttons { 
            buttonMap[mapping.button] = resolve(mapping.action) 
        }

        print("[ChordEngine] \(chordMap.count) chord(s), \(buttonMap.count) button(s) loaded")
        print("[ChordEngine] Volume Scroll enabled for buttons: \(volumeScrollButtons)")
    }

    private func resolve(_ action: Action) -> ResolvedAction {
        let flags = cgFlags(from: action.modifiers)
        return ResolvedAction(
            type: action.type,
            keyCode: action.keyCode != nil ? CGKeyCode(action.keyCode!) : nil,
            flags: flags
        )
    }

    func handle(event: CGEvent, type: CGEventType) -> CGEvent? {
        if type == .scrollWheel { return handleScroll(event) }

        let isDown = (type == .leftMouseDown  || type == .rightMouseDown || type == .otherMouseDown)
        let isUp   = (type == .leftMouseUp    || type == .rightMouseUp   || type == .otherMouseUp)

        guard isDown || isUp else { return event }
        
        let btn = buttonNumber(for: type, event: event)
        guard isTracked(btn) else { return event }

        clearGhostButtons()

        if isDown {
            if heldButtons.contains(btn) { return event }

            for partner in heldButtons {
                let mask = (1 << btn) | (1 << partner)
                if let action = chordMap[mask] {
                    perform(action)
                    if btn == 0 || btn == 1 { cancelClick(btn: btn) }
                    
                    consumedButtons.insert(btn)
                    consumedButtons.insert(partner)
                    
                    for heldBtn in heldButtons { cancelTimer(for: heldBtn) }
                    heldButtons.removeAll()
                    pressTimestamps.removeAll()
                    return nil
                }
            }

            heldButtons.insert(btn)
            pressTimestamps[btn] = Date()

            if btn == 0 || btn == 1 { return event }
            
            if volumeScrollButtons.contains(btn) { 
                accumulatedScroll = 0.0 
                return nil 
            }
            
            startTimer(for: btn)
            return nil

        } else {
            pressTimestamps.removeValue(forKey: btn)
            heldButtons.remove(btn)
            cancelTimer(for: btn)
            
            if consumedButtons.contains(btn) {
                consumedButtons.remove(btn)
                return nil
            }

            if let action = buttonMap[btn] {
                perform(action)
                return nil
            } else if volumeScrollButtons.contains(btn) {
                synthesizeClick(btn: btn)
                return nil
            }
            
            return event
        }
    }

    private func handleScroll(_ event: CGEvent) -> CGEvent? {
        
        // ── Volume Control via Button 4 + Scroll ───────────────────────
        let activeVolModifiers = heldButtons.intersection(volumeScrollButtons)
        
        if !activeVolModifiers.isEmpty {
            for btn in activeVolModifiers {
                consumedButtons.insert(btn)
                cancelTimer(for: btn)
            }
            
            let rawDelta = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
            accumulatedScroll += rawDelta
            
            if accumulatedScroll >= 1.0 {
                sendMediaKey(keyType: 1) // Sound Down
                accumulatedScroll = 0.0
            } else if accumulatedScroll <= -1.0 {
                sendMediaKey(keyType: 0) // Sound Up
                accumulatedScroll = 0.0
            }
            
            return nil // Consume scroll event so the page doesn't scroll
        }
        // ────────────────────────────────────────────────────────────────

        // If no modifier is held, check if we need to apply config math
        let flip: Double = scrollReverse ? -1.0 : 1.0
        let mul:  Double = scrollSpeed * flip

        // If math isn't changing anything, instantly return the raw event to preserve native trackpad momentum
        guard mul != 1.0 else { return event }

        // Only tamper with the scroll if config explicitly asks for it
        let i1 = event.getIntegerValueField(.scrollWheelEventDeltaAxis1)
        let i2 = event.getIntegerValueField(.scrollWheelEventDeltaAxis2)
        let f1 = event.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1)
        let f2 = event.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2)
        let p1 = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
        let p2 = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis2)

        event.setIntegerValueField(.scrollWheelEventDeltaAxis1, value: Int64((Double(i1) * mul).rounded()))
        event.setIntegerValueField(.scrollWheelEventDeltaAxis2, value: Int64((Double(i2) * mul).rounded()))
        event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1, value: f1 * mul)
        event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2, value: f2 * mul)
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis1, value: p1 * mul)
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis2, value: p2 * mul)

        return event
    }

    private func clearGhostButtons() {
        let now = Date()
        var ghosts: [Int] = []
        
        if heldButtons.count <= 1 {
            for btn in heldButtons {
                guard let t = pressTimestamps[btn] else { ghosts.append(btn); continue }
                if now.timeIntervalSince(t) > maxHoldTime && !volumeScrollButtons.contains(btn) { 
                    ghosts.append(btn) 
                }
            }
        }
        
        for btn in ghosts {
            heldButtons.remove(btn)
            consumedButtons.remove(btn)
            pressTimestamps.removeValue(forKey: btn)
            cancelTimer(for: btn)
        }
    }

    private func cancelClick(btn: Int) {
        let upType: CGEventType = (btn == 0) ? .leftMouseUp : .rightMouseUp
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let loc = CGEvent(source: nil)?.location ?? .zero
        let up  = CGEvent(mouseEventSource: src, mouseType: upType,
                          mouseCursorPosition: loc,
                          mouseButton: btn == 0 ? .left : .right)
        up?.post(tap: .cghidEventTap)
    }
    
    private func synthesizeClick(btn: Int) {
        if btn == 0 || btn == 1 { return }
        
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let loc = CGEvent(source: nil)?.location ?? .zero
        let mappedBtn = CGMouseButton(rawValue: UInt32(btn)) ?? .center
        
        let down = CGEvent(mouseEventSource: src, mouseType: .otherMouseDown, mouseCursorPosition: loc, mouseButton: mappedBtn)
        down?.post(tap: .cghidEventTap)
        
        let up = CGEvent(mouseEventSource: src, mouseType: .otherMouseUp, mouseCursorPosition: loc, mouseButton: mappedBtn)
        up?.post(tap: .cghidEventTap)
    }

    private func startTimer(for btn: Int) {
        guard buttonMap[btn] != nil else { return }
        let item = DispatchWorkItem { [weak self] in
            guard let self = self,
                  self.heldButtons.contains(btn), 
                  !self.consumedButtons.contains(btn),
                  let action = self.buttonMap[btn] else { return }
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
        if volumeScrollButtons.contains(btn) { return true }
        if buttonMap[btn] != nil { return true }
        let btnMask = 1 << btn
        return chordMap.keys.contains { ($0 & btnMask) != 0 }
    }

    // MARK: - Action Dispatch

    private func perform(_ action: ResolvedAction) {
        switch action.type {
        case .missionControl:
            runShellAsync("open -a 'Mission Control'")
        case .launchpad:
            runShellAsync("open -a Launchpad")
        case .expose:
            runAppleScriptAsync("tell application \"System Events\" to key code 101 using {control down}")
        case .playPause:
            sendMediaKey(keyType: 16) 
        case .previousTrack:
            sendMediaKey(keyType: 18) 
        case .nextTrack:
            sendMediaKey(keyType: 17) 
        case .back:
            sendKey(keyCode: 33, modifiers: .maskCommand)
        case .forward:
            sendKey(keyCode: 30, modifiers: .maskCommand)
        case .keystroke:
            guard let kc = action.keyCode else { return }
            if action.flags.contains(.maskControl) && (kc == 123 || kc == 124) {
                let script = kc == 124
                    ? "tell application \"System Events\" to key code 124 using {control down}"
                    : "tell application \"System Events\" to key code 123 using {control down}"
                runAppleScriptAsync(script)
            } else {
                sendKey(keyCode: kc, modifiers: action.flags)
            }
        case .none: break
        }
    }

    // MARK: - Senders (Async Execution)

    private func runAppleScriptAsync(_ script: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            p.arguments = ["-e", script]
            try? p.run()
        }
    }

    private func runShellAsync(_ cmd: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = ["-c", cmd]
            try? p.run()
        }
    }

    private func sendKey(keyCode: CGKeyCode, modifiers: CGEventFlags) {
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let down = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true)
        down?.flags = modifiers
        down?.post(tap: .cghidEventTap)
        
        let up = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)
        up?.flags = modifiers
        up?.post(tap: .cghidEventTap)
    }

    private func sendMediaKey(keyType: Int) {
        let flags: UInt = 0xa00
        let downData1 = (keyType << 16) | (0xa << 8)
        if let downEvent = NSEvent.otherEvent(
            with: .systemDefined, location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: flags),
            timestamp: 0, windowNumber: 0, context: nil,
            subtype: 8, data1: downData1, data2: -1
        )?.cgEvent {
            downEvent.post(tap: .cgSessionEventTap)
        }
        
        let upData1 = (keyType << 16) | (0xb << 8)
        if let upEvent = NSEvent.otherEvent(
            with: .systemDefined, location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: flags),
            timestamp: 0, windowNumber: 0, context: nil,
            subtype: 8, data1: upData1, data2: -1
        )?.cgEvent {
            upEvent.post(tap: .cgSessionEventTap)
        }
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