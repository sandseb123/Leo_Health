#!/bin/bash

echo "╔══════════════════════════════════════╗"
echo "║       Leo Health — Installer         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Clone check
if [ ! -f "leo_health/status.py" ]; then
  echo "Error: Run this from inside the leo-health folder"
  exit 1
fi

# Add to shell config
INSTALL_DIR="$(pwd)"

echo 'export PYTHONPATH="'"$INSTALL_DIR"':$PYTHONPATH"' >> ~/.zshrc
echo 'alias leo="python3 -m leo_health.status"' >> ~/.zshrc
echo 'alias leo-watch="python3 -m leo_health.watcher"' >> ~/.zshrc

source ~/.zshrc

echo ""
echo "✓ Installed! Two commands now available:"
echo ""
echo "  leo          → view your health dashboard"
echo "  leo-watch    → start watching Downloads for exports"
echo ""
echo "AirDrop your Apple Health export.zip to get started."
