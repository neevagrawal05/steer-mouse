# ChordDaemon 🖱

A tiny macOS daemon that adds **button chording**, **scroll gestures**, and **custom actions** to any mouse — no hardware changes, no driver installation.

Press and hold one button, click another → chord action fires.  
Release without a second button → individual button action fires.  
Hold a button while scrolling → scroll gesture action fires.

No timers. No guessing. Dead simple state machine.

---

## Features

- **Button chords** — any two-button combo fires a custom action
- **Individual button actions** — single buttons map to keystrokes or system actions
- **Scroll gestures** — hold a button while scrolling to trigger zoom, volume, or media control
- **Horizontal scroll pivot** — hold two buttons while scrolling to redirect vertical scroll to horizontal
- **Scroll speed & reverse** — global multiplier and natural/standard scroll direction toggle
- **Auto-restart** — watchdog re-enables the event tap if macOS suspends it
- **launchd install script** — runs silently at login, no Terminal needed after setup

---

## Files

```
ChordDaemon/
├── main.swift          ← entry point, CGEventTap, watchdog
├── ChordEngine.swift   ← state machine (the core)
├── Config.swift        ← config model + loader
├── config.json         ← YOUR chord/button/scroll mappings (edit this!)
├── identify.swift      ← one-shot tool to identify button numbers
├── install.sh          ← compiles + installs as a login daemon
├── uninstall.sh        ← fully removes ChordDaemon from system
└── README.md
```

---

## Quick install (recommended)

This compiles ChordDaemon and registers it as a launchd agent so it starts automatically at every login, silently in the background.

```bash
bash install.sh
```

Then grant Accessibility permission when prompted (one time only). That's it — no Terminal needed again.

To stop: `launchctl unload ~/Library/LaunchAgents/com.chorddaemon.plist`  
To start: `launchctl load ~/Library/LaunchAgents/com.chorddaemon.plist`  
To uninstall: `bash uninstall.sh`

Logs are written to `~/.chorddaemon/chorddaemon.log`.

---

## Manual build (Xcode)

### Step 1 — Create Xcode project

1. Open Xcode → **File → New → Project**
2. Choose **macOS → Command Line Tool** → Next
3. Fill in:
   - Product Name: `ChordDaemon`
   - Language: `Swift`
4. Click **Next**, choose a folder → **Create**

### Step 2 — Add source files

1. Delete the auto-generated `main.swift` Xcode created
2. Right-click the `ChordDaemon` group in the Navigator → **Add Files to "ChordDaemon"…**
3. Select all three `.swift` files:
   - `main.swift`
   - `ChordEngine.swift`
   - `Config.swift`
4. Make sure **"Add to target: ChordDaemon"** is checked → **Add**

### Step 3 — Disable App Sandbox

CGEventTap does not work inside a sandboxed app.

1. Click your project in the Navigator → select the **ChordDaemon** target
2. Go to **Signing & Capabilities** tab
3. If "App Sandbox" appears → click it and press the **–** (minus) button to remove it

### Step 4 — Build & run

Press **⌘B** to build, then run from Terminal:

```bash
./ChordDaemon
```

Grant **Accessibility permission** when macOS prompts:
- Open **System Settings → Privacy & Security → Accessibility**
- Click **+**, add `ChordDaemon`, enable its toggle
- Run `./ChordDaemon` again

You should see:
```
[ChordDaemon] ✅ Config loaded from .../config.json
[ChordDaemon] 🚀 Daemon is running…
```

---

## Identifying button numbers

Run the included identifier tool to see which number each button reports:

```bash
swiftc identify.swift -framework Cocoa -o identify && ./identify
```

Click each mouse button — the button number prints in Terminal. Use these numbers in `config.json`.

```
Button 0 = Left click         ← tracked for chords only
Button 1 = Right click        ← tracked for chords only
Button 2 = Middle click
Button 3 = Side button (back)
Button 4 = Side button (forward)
Button 5 = Extra button 1
Button 6 = Extra button 2
Button 7 = Extra button 3
```

---

## Editing config.json

### Scroll settings

```json
"scroll": {
  "reverse": false,
  "speed": 1.0
}
```

`reverse: true` switches to natural (Apple-style) scrolling. `speed` is a multiplier — `2.0` doubles scroll distance, `0.5` halves it.

### Button actions

Map individual buttons to actions. These fire when a button is released without any chord having triggered.

```json
"buttons": [
  { "button": 3, "action": { "type": "keystroke", "keyCode": 123, "modifiers": ["control"] } },
  { "button": 4, "action": { "type": "keystroke", "keyCode": 124, "modifiers": ["control"] } }
]
```

### Chord actions

Map two-button combinations to actions. The chord fires the moment the second button is pressed.

```json
"chords": [
  { "buttons": [0, 1], "action": { "type": "playPause" } },
  { "buttons": [3, 4], "action": { "type": "missionControl" } },
  {
    "buttons": [0, 3],
    "action": { "type": "keystroke", "keyCode": 13, "modifiers": ["command"] }
  }
]
```

### Available action types

| type | What it does |
|---|---|
| `back` | ⌘[ (browser/Finder back) |
| `forward` | ⌘] (browser/Finder forward) |
| `missionControl` | Mission Control |
| `launchpad` | Launchpad |
| `expose` | App Exposé |
| `playPause` | Play / pause media |
| `previousTrack` | Previous track |
| `nextTrack` | Next track |
| `keystroke` | Custom key combo — add `keyCode` + `modifiers` |
| `none` | Suppress the button — do nothing |

**Modifiers:** `"command"`, `"shift"`, `"option"`, `"control"`

**Common key codes:** A=0, S=1, D=2, F=3, H=4, G=5, Z=6, X=7, C=8, V=9, B=11, Q=12, W=13, E=14, R=15, Y=16, T=17, 1=18, 2=19, 3=20, 4=21, 6=22, 5=23, `=`=24, 9=25, 7=26, `-`=27, 8=28, 0=29, `]`=30, O=31, U=32, `[`=33, I=34, P=35, Return=36, L=37, J=38, `'`=39, K=40, `;`=41, `\`=42, `,`=43, `/`=44, N=45, M=46, `.`=47, Tab=48, Space=49, `` ` ``=50, Delete=51, Esc=53, Left=123, Right=124, Down=125, Up=126

### All 10 chord pairs for a 5-button mouse

```
[3,4]  [3,5]  [3,6]  [3,7]
       [4,5]  [4,6]  [4,7]
              [5,6]  [5,7]
                     [6,7]
```

---

## Scroll gesture behaviors

These are hardcoded in `ChordEngine.swift` and extend the config-driven system without requiring config changes.

### Volume scroll

Hold **button 4** while scrolling → adjusts system volume. Scroll up = volume up, scroll down = volume down. Button 4's individual action (if configured) is preserved — it only fires if no scrolling occurred while it was held.

### Zoom scroll

Hold **button 3** while scrolling → sends `⌘+` (zoom in) or `⌘–` (zoom out) to the active app. Works in browsers, Finder, maps, PDFs, and most macOS apps. Button 3's individual action is preserved identically.

### Horizontal scroll pivot

Hold **buttons 3 + 4** simultaneously while scrolling → redirects vertical scroll deltas to horizontal axis. Useful for navigating timelines, wide spreadsheets, or any horizontal content. If buttons 3 and 4 are held but no scrolling occurs, the configured chord action for `[3, 4]` fires on release — existing behavior is fully preserved.

---

## How it works

### Button + chord state machine

```
Button A DOWN  →  held: {A}
Button B DOWN  →  chord {A,B} found → fire action → consumed: {A,B}
Button A UP    →  in consumed → suppress → consumed: {B}
Button B UP    →  in consumed → suppress → consumed: {}

Button A DOWN  →  held: {A}
Button A UP    →  not consumed → fire individual action for A
```

### Scroll gesture logic

```
Button held + scroll event arrives
  → mark button as consumed (suppress its standalone action)
  → intercept scroll, apply gesture behavior
  → return nil (swallow original scroll event)

Button held + no scroll occurred
  → on release: not consumed → fire individual or chord action as normal
```

No timers. No race conditions. Every case is deterministic.

### Event tap resilience

macOS can auto-disable a CGEventTap if the callback is too slow. ChordDaemon handles this two ways:

- **Inline recovery** — the tap callback detects the disable signal and immediately re-enables itself
- **Watchdog** — a background timer polls every 5 seconds and re-enables the tap if it has gone dead

This means ChordDaemon keeps working even after long periods of inactivity or system load spikes.
