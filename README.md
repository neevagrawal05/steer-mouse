# ChordDaemon 🖱

A tiny macOS daemon that adds **button chording** to any mouse.
Press and hold one button, click another → chord action fires.
Release without a second button → individual button action fires.

No timers. No guessing. Dead simple state machine.

---

## Files

```
ChordDaemon/
├── main.swift          ← entry point + CGEventTap
├── ChordEngine.swift   ← state machine (the core)
├── Config.swift        ← config model + loader
├── config.json         ← YOUR chord/button mappings (edit this!)
└── README.md
```

---

## Step 1 — Create Xcode Project

1. Open Xcode → **File → New → Project**
2. Choose **macOS → Command Line Tool** → Next
3. Fill in:
   - Product Name: `ChordDaemon`
   - Language: `Swift`
4. Click **Next**, choose a folder → **Create**

---

## Step 2 — Add Source Files

1. Delete the auto-generated `main.swift` Xcode created
2. Right-click the `ChordDaemon` group in the Navigator → **Add Files to "ChordDaemon"…**
3. Select all three `.swift` files:
   - `main.swift`
   - `ChordEngine.swift`
   - `Config.swift`
4. Make sure **"Add to target: ChordDaemon"** is checked → **Add**

---

## Step 3 — Disable App Sandbox

CGEventTap **does not work** inside a sandboxed app.

1. Click your project in the Navigator → select the **ChordDaemon** target
2. Go to **Signing & Capabilities** tab
3. If "App Sandbox" appears → click it and press the **–** (minus) button to remove it

---

## Step 4 — Build

Press **⌘B** to build.

Find the built binary:
- In Xcode menu: **Product → Show Build Folder in Finder**
- Or navigate to: `~/Library/Developer/Xcode/DerivedData/ChordDaemon-.../Build/Products/Debug/ChordDaemon`

---

## Step 5 — Place config.json next to the binary

Copy `config.json` into the same folder as the `ChordDaemon` binary.

---

## Step 6 — Run & Grant Permission

Open Terminal, navigate to the folder containing `ChordDaemon` and run:

```bash
./ChordDaemon
```

macOS will ask for **Accessibility permission**:
- Open **System Settings → Privacy & Security → Accessibility**
- Click **+** and add `ChordDaemon`
- Enable its toggle
- Run `./ChordDaemon` again

You should see:
```
[ChordDaemon] ✅ Config loaded from .../config.json
[ChordDaemon] ✅ Running — listening for button chords.
```

---

## Editing config.json

### Button numbers (typical 5-button mouse)
```
Button 0 = Left click       ← not tracked
Button 1 = Right click      ← not tracked
Button 2 = Middle click     ← not tracked
Button 3 = Side button (back)
Button 4 = Side button (forward)
Button 5 = Extra button 1
Button 6 = Extra button 2
Button 7 = Extra button 3
```
> Tip: Run `ChordDaemon` and click each button to see its number printed in Terminal.

### Available action types
| type | What it does |
|---|---|
| `back` | ⌘[ (browser/Finder back) |
| `forward` | ⌘] (browser/Finder forward) |
| `missionControl` | Mission Control |
| `launchpad` | Launchpad |
| `expose` | App Exposé |
| `keystroke` | Custom key combo (add `keyCode` + `modifiers`) |
| `none` | Block the button / do nothing |

### Custom keystroke example
```json
{
  "buttons": [3, 4],
  "action": {
    "type": "keystroke",
    "keyCode": 3,
    "modifiers": ["command", "shift"]
  }
}
```
`keyCode` values follow macOS virtual key codes (e.g. 0=A, 1=S, 3=F, 13=W).

### All 10 chord pairs for a 5-button mouse
```
[3,4]  [3,5]  [3,6]  [3,7]
       [4,5]  [4,6]  [4,7]
              [5,6]  [5,7]
                     [6,7]
```

---

## Run at Login (optional)

Once you're happy with it, run it automatically at login:

1. Copy `ChordDaemon` binary and `config.json` to `/usr/local/bin/`
2. Open **System Settings → General → Login Items**
3. Click **+** and add the `ChordDaemon` binary

Or create a launchd plist in `~/Library/LaunchAgents/` for more control.

---

## How It Works

```
Button A DOWN  →  held set: {A},  do nothing
Button B DOWN  →  chord {A,B} found! → fire action → consumed: {A,B}
Button A UP    →  in consumed → suppress → consumed: {B}
Button B UP    →  in consumed → suppress → consumed: {}

Button A DOWN  →  held set: {A}
Button A UP    →  not consumed → fire individual action for A
```

No timers. No race conditions. Every case is deterministic.
