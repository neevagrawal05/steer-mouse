#!/bin/bash
# ─────────────────────────────────────────────────────────────
# ChordDaemon Installer
# Installs the daemon so it runs automatically at login,
# silently in the background — no Terminal needed.
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.chorddaemon"
PLIST="$HOME/Library/LaunchAgents/com.chorddaemon.plist"

echo "──────────────────────────────────────"
echo " ChordDaemon Installer"
echo "──────────────────────────────────────"

# 1. Compile
echo "▶ Compiling..."
cd "$SCRIPT_DIR"
swiftc main.swift ChordEngine.swift Config.swift \
    -framework Cocoa -o ChordDaemon
echo "  ✅ Compiled"

# 2. Copy files to install dir
echo "▶ Installing to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp ChordDaemon "$INSTALL_DIR/ChordDaemon"
cp config.json "$INSTALL_DIR/config.json"
echo "  ✅ Files copied"

# 3. Write launchd plist
echo "▶ Creating launch agent..."
cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.chorddaemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/ChordDaemon</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>

    <!-- Start at login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart automatically if it crashes -->
    <key>KeepAlive</key>
    <true/>

    <!-- Log output (check these if something is wrong) -->
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/chorddaemon.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/chorddaemon.error.log</string>
</dict>
</plist>
EOF
echo "  ✅ Launch agent created"

# 4. Load it right now (no reboot needed)
echo "▶ Starting ChordDaemon..."
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "  ✅ ChordDaemon is now running in the background!"

echo ""
echo "──────────────────────────────────────"
echo " ✅  Done! ChordDaemon will now:"
echo "    • Run silently in the background"
echo "    • Start automatically at every login"
echo "    • No Terminal needed"
echo ""
echo " 📄 Logs:  $INSTALL_DIR/chorddaemon.log"
echo " ⚙️  Config: $INSTALL_DIR/config.json"
echo ""
echo " To stop:      launchctl unload ~/Library/LaunchAgents/com.chorddaemon.plist"
echo " To start:     launchctl load   ~/Library/LaunchAgents/com.chorddaemon.plist"
echo " To uninstall: bash uninstall.sh"
echo "──────────────────────────────────────"
