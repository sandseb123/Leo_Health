#!/bin/bash

echo "╔══════════════════════════════════════╗"
echo "║       Leo Health — Installer         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Clone check
if [ ! -f "leo_health/status.py" ]; then
  echo "Error: Run this from inside the Leo-Health-Core folder"
  exit 1
fi

# Check pip is available
if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
  echo "Error: pip not found. Install Python 3.9+ from python.org and try again."
  exit 1
fi

PIP="pip3"
command -v pip3 &>/dev/null || PIP="pip"

echo "Installing Leo Health..."
echo ""

# Install in editable mode — creates leo, leo-watch, leo-dash commands
# Uses pyproject.toml entry points, no PYTHONPATH changes
$PIP install -e . --quiet

if [ $? -ne 0 ]; then
  echo ""
  echo "pip install failed. Try:"
  echo "  pip3 install -e ."
  exit 1
fi

echo ""
echo "✓ Installed! The following commands are now available:"
echo ""
echo "  leo          → print health stats in the terminal"
echo "  leo-watch    → watch Downloads folder for new exports"
echo "  leo-dash     → open the web dashboard in your browser"
echo ""
echo "If the commands aren't found, try opening a new terminal tab."
echo ""
echo "AirDrop your Apple Health export.zip to ~/Downloads, then run: leo-watch"
