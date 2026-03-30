#!/bin/bash
# Completely removes ChordDaemon from your system

PLIST="$HOME/Library/LaunchAgents/com.chorddaemon.plist"
INSTALL_DIR="$HOME/.chorddaemon"

echo "▶ Stopping ChordDaemon..."
launchctl unload "$PLIST" 2>/dev/null && echo "  ✅ Stopped" || echo "  (was not running)"

echo "▶ Removing files..."
rm -f "$PLIST"
rm -rf "$INSTALL_DIR"
echo "  ✅ All files removed"

echo ""
echo "✅ ChordDaemon fully uninstalled."
echo "   Remove it from System Settings → Privacy & Security → Accessibility too."
