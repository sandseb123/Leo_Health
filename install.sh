#!/bin/bash

echo "╔══════════════════════════════════════╗"
echo "║       Leo Health — Installer         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Clone check
if [ ! -f "leo_health/status.py" ]; then
  echo "Error: Run this from inside the Leo_Health folder"
  exit 1
fi

INSTALL_DIR="$(pwd)"

# Detect shell config file
detect_shell_config() {
  local shell_name
  shell_name="$(basename "$SHELL")"

  case "$shell_name" in
    zsh)  echo "$HOME/.zshrc" ;;
    bash)
      if [ -f "$HOME/.bash_profile" ]; then
        echo "$HOME/.bash_profile"
      else
        echo "$HOME/.bashrc"
      fi
      ;;
    fish) echo "$HOME/.config/fish/config.fish" ;;
    *)    echo "$HOME/.profile" ;;
  esac
}

SHELL_CONFIG="$(detect_shell_config)"
SHELL_NAME="$(basename "$SHELL")"

echo "Detected shell: $SHELL_NAME"
echo "Writing to:     $SHELL_CONFIG"
echo ""

# Fish uses a different syntax
if [ "$SHELL_NAME" = "fish" ]; then
  mkdir -p "$(dirname "$SHELL_CONFIG")"
  echo "set -x PYTHONPATH \"$INSTALL_DIR\" \$PYTHONPATH" >> "$SHELL_CONFIG"
  echo "alias leo=\"python3 -m leo_health.status\"" >> "$SHELL_CONFIG"
  echo "alias leo-watch=\"python3 -m leo_health.watcher\"" >> "$SHELL_CONFIG"
else
  echo "export PYTHONPATH=\"$INSTALL_DIR:\$PYTHONPATH\"" >> "$SHELL_CONFIG"
  echo "alias leo=\"python3 -m leo_health.status\"" >> "$SHELL_CONFIG"
  echo "alias leo-watch=\"python3 -m leo_health.watcher\"" >> "$SHELL_CONFIG"
fi

# Source the config (fish handles this differently)
if [ "$SHELL_NAME" != "fish" ]; then
  # shellcheck disable=SC1090
  source "$SHELL_CONFIG" 2>/dev/null || true
fi

echo "✓ Installed! Two commands now available:"
echo ""
echo "  leo          → view your health dashboard"
echo "  leo-watch    → start watching Downloads for exports"
echo ""
if [ "$SHELL_NAME" = "fish" ]; then
  echo "Restart your terminal or run: source $SHELL_CONFIG"
else
  echo "AirDrop your Apple Health export.zip to get started."
fi
