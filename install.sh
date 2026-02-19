#!/bin/bash

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         Leo Health — Installer           ║"
echo "║   macOS + Linux                          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Check Python ──────────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "   Install it: brew install python3"
    else
        echo "   Install it: sudo apt install python3  (Ubuntu/Debian)"
        echo "               sudo dnf install python3  (Fedora)"
    fi
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 9 ]; then
    echo "❌ Python 3.9+ required. You have Python 3.$PYTHON_VERSION"
    exit 1
fi

echo "✓ Python 3.$PYTHON_VERSION found"

# ── Detect OS ─────────────────────────────────────────────────────
INSTALL_DIR="$(pwd)"
OS="unknown"

if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    SHELL_CONFIG="$HOME/.zshrc"
    echo "✓ macOS detected"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    SHELL_CONFIG="$HOME/.bashrc"
    echo "✓ Linux detected"
else
    echo "⚠️  Unknown OS: $OSTYPE — trying Linux defaults"
    OS="linux"
    SHELL_CONFIG="$HOME/.bashrc"
fi

# ── Create leo-health directory ───────────────────────────────────
mkdir -p "$HOME/.leo-health"
echo "✓ Created ~/.leo-health/"

# ── Add to shell config ───────────────────────────────────────────

# Remove any old Leo entries first (clean reinstall)
if [ -f "$SHELL_CONFIG" ]; then
    grep -v "leo_health" "$SHELL_CONFIG" | grep -v "LEO_HEALTH" > /tmp/shell_config_clean
    mv /tmp/shell_config_clean "$SHELL_CONFIG"
fi

# Add fresh entries
echo "" >> "$SHELL_CONFIG"
echo "# Leo Health" >> "$SHELL_CONFIG"
echo "export PYTHONPATH=\"$INSTALL_DIR:\$PYTHONPATH\"" >> "$SHELL_CONFIG"
echo "alias leo=\"python3 -m leo_health.status\"" >> "$SHELL_CONFIG"
echo "alias leo-watch=\"python3 -m leo_health.watcher\"" >> "$SHELL_CONFIG"

echo "✓ Added leo and leo-watch commands to $SHELL_CONFIG"

# ── Linux: optional systemd auto-start ───────────────────────────
if [[ "$OS" == "linux" ]]; then
    echo ""
    echo "  Optional: Run leo-watch automatically on login?"
    echo "  This watches your Downloads folder for health exports."
    read -p "  Set up auto-start? (y/n): " AUTO_START

    if [[ "$AUTO_START" == "y" || "$AUTO_START" == "Y" ]]; then
        # Create systemd user service
        mkdir -p "$HOME/.config/systemd/user"
        cat > "$HOME/.config/systemd/user/leo-health.service" << EOF
[Unit]
Description=Leo Health Watcher
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m leo_health.watcher
Environment=PYTHONPATH=$INSTALL_DIR
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable leo-health.service
        systemctl --user start leo-health.service
        echo "✓ Leo watcher enabled — starts automatically on login"
    fi
fi

# ── macOS: optional launchd auto-start ───────────────────────────
if [[ "$OS" == "macos" ]]; then
    echo ""
    echo "  Optional: Run leo-watch automatically on login?"
    read -p "  Set up auto-start? (y/n): " AUTO_START

    if [[ "$AUTO_START" == "y" || "$AUTO_START" == "Y" ]]; then
        PLIST="$HOME/Library/LaunchAgents/com.leohealth.watcher.plist"
        cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.leohealth.watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>-m</string>
        <string>leo_health.watcher</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$INSTALL_DIR</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$HOME/.leo-health/watcher.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.leo-health/watcher-error.log</string>
</dict>
</plist>
EOF
        launchctl load "$PLIST" 2>/dev/null
        echo "✓ Leo watcher enabled — starts automatically on login"
    fi
fi

# ── Reload shell ──────────────────────────────────────────────────
source "$SHELL_CONFIG" 2>/dev/null || true

# ── Done ──────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║           Installation Complete!         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Two commands now available:"
echo ""
echo "    leo          → view your health dashboard"
echo "    leo-watch    → watch Downloads for new exports"
echo ""

if [[ "$OS" == "macos" ]]; then
echo "  To get your data:"
echo "    iPhone → Health app → profile → Export All Health Data"
echo "    AirDrop export.zip to your Mac → Leo auto-ingests it"
fi

if [[ "$OS" == "linux" ]]; then
echo "  To get your data:"
echo "    Copy your Apple Health export.zip to ~/Downloads/"
echo "    Run: leo-watch"
echo "    Leo will detect and ingest it automatically"
fi

echo ""
echo "  ⭐ Leo Pro coming soon — AI coach that learns from"
echo "     PubMed weekly. 100% local, zero cloud."
echo "     Join the waitlist: https://leoheath.beehiiv.com/subscribe"
echo ""

# ── Reload reminder ───────────────────────────────────────────────
echo "  ℹ️  Run this to activate in your current terminal:"
if [[ "$OS" == "macos" ]]; then
echo "     source ~/.zshrc"
else
echo "     source ~/.bashrc"
fi
echo ""
