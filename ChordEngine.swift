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
    private let scrollSpeed:   Double

    private let volumeScrollButtons: Set<Int> = [4]
    private var accumulatedScroll: Double = 0.0

    private let zoomScrollButtons: Set<Int> = [3]
    private var accumulatedZoom: Double = 0.0

    private let hScrollButtons: Set<Int> = [3, 4]   // hold both → scroll becomes horizontal
    private var hScrollActive = false
    private var hScrollUsed   = false

    private enum SystemKey {
        static let missionControl: CGKeyCode = 160  // F3
        static let launchpad:      CGKeyCode = 131  // F4
        static let expose:         CGKeyCode = 109  // F10
    }

    init(config: ChordConfig) {
        self.scrollReverse = config.scroll?.reverse ?? false
        self.scrollSpeed   = config.scroll?.speed   ?? 1.0

        for mapping in config.chords {
            let mask = mapping.buttons.reduce(0) { $0 | (1 << $1) }
            chordMap[mask] = resolve(mapping.action)
        }
        for mapping in config.buttons {
            buttonMap[mapping.button] = resolve(mapping.action)
        }

        print("[ChordEngine] \(chordMap.count) chord(s), \(buttonMap.count) button(s) loaded")
    }

    private func resolve(_ action: Action) -> ResolvedAction {
        ResolvedAction(
            type:    action.type,
            keyCode: action.keyCode.map { CGKeyCode($0) },
            flags:   cgFlags(from: action.modifiers)
        )
    }

    // MARK: - Main event handler

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

            // hScroll combo: when this button completes the {3,4} set,
            // enter hScroll mode instead of firing the chord immediately.
            if hScrollButtons.contains(btn) {
                let afterInsert = heldButtons.union([btn])
                if hScrollButtons.isSubset(of: afterInsert) {
                    heldButtons.insert(btn)
                    pressTimestamps[btn] = Date()
                    for b in hScrollButtons { consumedButtons.insert(b); cancelTimer(for: b) }
                    hScrollActive = true
                    hScrollUsed   = false
                    return nil
                }
            }

            for partner in heldButtons {
                let mask = (1 << btn) | (1 << partner)
                if let action = chordMap[mask] {
                    perform(action)

                    if btn     == 0 || btn     == 1 { cancelClick(btn: btn) }
                    if partner == 0 || partner == 1 { cancelClick(btn: partner) }

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

            if zoomScrollButtons.contains(btn) {
                accumulatedZoom = 0.0
                return nil
            }

            startTimer(for: btn)
            return nil

        } else {
            pressTimestamps.removeValue(forKey: btn)
            heldButtons.remove(btn)
            cancelTimer(for: btn)

            // hScroll cleanup — must run before the generic consumedButtons check.
            if hScrollActive && hScrollButtons.contains(btn) {
                hScrollActive = false
                consumedButtons.remove(btn)   // leave the OTHER button consumed so its release is suppressed
                if !hScrollUsed {
                    // No scroll happened → honour the original chord action.
                    let mask = hScrollButtons.reduce(0) { $0 | (1 << $1) }
                    if let action = chordMap[mask] { perform(action) }
                }
                hScrollUsed = false
                return nil
            }

            if consumedButtons.contains(btn) {
                consumedButtons.remove(btn)
                if btn == 0 || btn == 1 { return event }
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

    // MARK: - Scroll

    private func handleScroll(_ event: CGEvent) -> CGEvent? {

        // hScroll MUST be checked first — button 4 is also in volumeScrollButtons,
        // so the volume block would fire incorrectly when 3+4 are both held.
        if hScrollActive {
            let vInt   = event.getIntegerValueField(.scrollWheelEventDeltaAxis1)
            let vFixed = event.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1)
            let vPoint = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)

            event.setIntegerValueField(.scrollWheelEventDeltaAxis1,       value: 0)
            event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1, value: 0)
            event.setDoubleValueField(.scrollWheelEventPointDeltaAxis1,   value: 0)

            event.setIntegerValueField(.scrollWheelEventDeltaAxis2,       value: vInt)
            event.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2, value: vFixed)
            event.setDoubleValueField(.scrollWheelEventPointDeltaAxis2,   value: vPoint)

            hScrollUsed = true
            return event
        }

        let activeVolModifiers = heldButtons.intersection(volumeScrollButtons)

        if !activeVolModifiers.isEmpty {
            for btn in activeVolModifiers {
                consumedButtons.insert(btn)
                cancelTimer(for: btn)
            }

            let rawDelta = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
            accumulatedScroll += rawDelta

            if accumulatedScroll >= 1.0 {
                sendMediaKey(keyType: 1)
                accumulatedScroll = 0.0
            } else if accumulatedScroll <= -1.0 {
                sendMediaKey(keyType: 0)
                accumulatedScroll = 0.0
            }
            return nil
        }

        let activeZoomModifiers = heldButtons.intersection(zoomScrollButtons)

        if !activeZoomModifiers.isEmpty {
            for btn in activeZoomModifiers {
                consumedButtons.insert(btn)
                cancelTimer(for: btn)
            }

            let rawDelta = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
            accumulatedZoom += rawDelta

            if accumulatedZoom >= 1.0 {
                sendKey(keyCode: 24, modifiers: .maskCommand) // Cmd+=  (zoom in)
                accumulatedZoom = 0.0
            } else if accumulatedZoom <= -1.0 {
                sendKey(keyCode: 27, modifiers: .maskCommand) // Cmd+-  (zoom out)
                accumulatedZoom = 0.0
            }
            return nil
        }

        let flip: Double = scrollReverse ? -1.0 : 1.0
        let mul:  Double = scrollSpeed * flip

        guard mul != 1.0 else { return event }

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
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis1,   value: p1 * mul)
        event.setDoubleValueField(.scrollWheelEventPointDeltaAxis2,   value: p2 * mul)

        return event
    }

    // MARK: - Ghost cleanup

    private func clearGhostButtons() {
        let now    = Date()
        var ghosts = [Int]()

        if heldButtons.count <= 1 {
            for btn in heldButtons {
                guard let t = pressTimestamps[btn] else { ghosts.append(btn); continue }
                if now.timeIntervalSince(t) > maxHoldTime,
                   !volumeScrollButtons.contains(btn) { ghosts.append(btn) }
            }
        }

        for btn in ghosts {
            heldButtons.remove(btn)
            consumedButtons.remove(btn)
            pressTimestamps.removeValue(forKey: btn)
            cancelTimer(for: btn)
        }
    }

    // MARK: - Click synthesis

    private func cancelClick(btn: Int) {
        let upType: CGEventType = (btn == 0) ? .leftMouseUp : .rightMouseUp
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let loc = CGEvent(source: nil)?.location ?? .zero
        CGEvent(mouseEventSource: src, mouseType: upType,
                mouseCursorPosition: loc,
                mouseButton: btn == 0 ? .left : .right)?
            .post(tap: .cghidEventTap)
    }

    private func synthesizeClick(btn: Int) {
        guard btn != 0, btn != 1 else { return }
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let loc       = CGEvent(source: nil)?.location ?? .zero
        let mappedBtn = CGMouseButton(rawValue: UInt32(btn)) ?? .center

        CGEvent(mouseEventSource: src, mouseType: .otherMouseDown,
                mouseCursorPosition: loc, mouseButton: mappedBtn)?
            .post(tap: .cghidEventTap)
        CGEvent(mouseEventSource: src, mouseType: .otherMouseUp,
                mouseCursorPosition: loc, mouseButton: mappedBtn)?
            .post(tap: .cghidEventTap)
    }

    // MARK: - Timers

    private func startTimer(for btn: Int) {
        guard buttonMap[btn] != nil else { return }
        let item = DispatchWorkItem { [weak self] in
            guard let self,
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

    // MARK: - Action dispatch

    private func perform(_ action: ResolvedAction) {
        switch action.type {

        case .missionControl:
            sendSystemKey(SystemKey.missionControl)

        case .launchpad:
            sendSystemKey(SystemKey.launchpad)

        case .expose:
            sendKey(keyCode: SystemKey.expose, modifiers: .maskControl)

        case .playPause:     sendMediaKey(keyType: 16)
        case .previousTrack: sendMediaKey(keyType: 18)
        case .nextTrack:     sendMediaKey(keyType: 17)

        case .back:    sendKey(keyCode: 33, modifiers: .maskCommand)
        case .forward: sendKey(keyCode: 30, modifiers: .maskCommand)

        case .keystroke:
            guard let kc = action.keyCode else { return }

            // Ctrl+Left (123) / Ctrl+Right (124) for Space switching.
            //
            // WHY NOT CGEvent: macOS's SkyLight window server validates the
            // Spaces shortcut by checking for a real flagsChanged event in the
            // HID stream — a modifier flag on a keyboard event alone is not
            // enough. Synthesizing flagsChanged via CGEvent from inside or near
            // a tap callback is unreliable across macOS versions.
            //
            // WHY NOT osascript subprocess: spawning a process costs 150-300 ms.
            //
            // SOLUTION: NSAppleScript in-process on a background thread.
            // Same mechanism as System Events, zero subprocess overhead, ~15-30 ms.
            // Thread-safe as long as each call creates its own NSAppleScript instance.
            if action.flags.contains(.maskControl) && (kc == 123 || kc == 124) {
                let script = "tell application \"System Events\" to key code \(kc) using {control down}"
                runAppleScriptInProcess(script)
            } else {
                sendKey(keyCode: kc, modifiers: action.flags)
            }

        case .none: break
        }
    }

    // MARK: - Key senders

    private func sendSystemKey(_ keyCode: CGKeyCode) {
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true)?
            .post(tap: .cghidEventTap)
        CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)?
            .post(tap: .cghidEventTap)
    }

    private func sendKey(keyCode: CGKeyCode, modifiers: CGEventFlags) {
        guard let src = CGEventSource(stateID: .combinedSessionState) else { return }
        let down = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true)
        down?.flags = modifiers
        let up = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)
        up?.flags = modifiers
        // Async: posting synchronously inside a tap callback can be silently
        // swallowed by macOS's recursive-event protection.
        DispatchQueue.main.async {
            down?.post(tap: .cgSessionEventTap)
            up?.post(tap: .cgSessionEventTap)
        }
    }

    // In-process AppleScript — no subprocess, ~15-30 ms.
    // NSAppleScript is thread-safe when each call uses its own instance.
    private func runAppleScriptInProcess(_ source: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            var err: NSDictionary?
            NSAppleScript(source: source)?.executeAndReturnError(&err)
            if let err = err {
                print("[ChordEngine] AppleScript error: \(err)")
            }
        }
    }

    private func sendMediaKey(keyType: Int) {
        let flags: UInt = 0xa00
        let downData1 = (keyType << 16) | (0xa << 8)
        if let evt = NSEvent.otherEvent(
            with: .systemDefined, location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: flags),
            timestamp: 0, windowNumber: 0, context: nil,
            subtype: 8, data1: downData1, data2: -1
        )?.cgEvent { evt.post(tap: .cgSessionEventTap) }

        let upData1 = (keyType << 16) | (0xb << 8)
        if let evt = NSEvent.otherEvent(
            with: .systemDefined, location: .zero,
            modifierFlags: NSEvent.ModifierFlags(rawValue: flags),
            timestamp: 0, windowNumber: 0, context: nil,
            subtype: 8, data1: upData1, data2: -1
        )?.cgEvent { evt.post(tap: .cgSessionEventTap) }
    }

    // MARK: - Helpers

    private func buttonNumber(for type: CGEventType, event: CGEvent) -> Int {
        switch type {
        case .leftMouseDown,  .leftMouseUp:  return 0
        case .rightMouseDown, .rightMouseUp: return 1
        default: return Int(event.getIntegerValueField(.mouseEventButtonNumber))
        }
    }

    private func isTracked(_ btn: Int) -> Bool {
        if volumeScrollButtons.contains(btn) { return true }
        if zoomScrollButtons.contains(btn)   { return true }
        if buttonMap[btn] != nil { return true }
        let btnMask = 1 << btn
        return chordMap.keys.contains { ($0 & btnMask) != 0 }
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