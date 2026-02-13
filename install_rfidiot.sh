#!/bin/bash
# install_rfidiot.sh — RFIDIOt Installer for macOS and Linux
# Usage: bash install_rfidiot.sh

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        RFIDIOt Installer — macOS / Linux             ║"
echo "║        github.com/AdamLaurie/RFIDIOt                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Find Python 3
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
    PYTHON=python
else
    echo "❌  Python 3 not found."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "    Install via Homebrew: brew install python3"
        echo "    Or download from: https://python.org"
    else
        echo "    Install: sudo apt install python3  (Ubuntu/Debian)"
        echo "         or: sudo dnf install python3  (Fedora)"
    fi
    exit 1
fi

echo "✔  Python: $($PYTHON --version)"
echo ""

# On macOS, warn if not running with enough permissions
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "ℹ️  macOS: some steps may ask for your password (sudo)"
    echo ""
fi

# On Linux, check if running as root (not recommended) or regular user
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if [[ $EUID -eq 0 ]]; then
        echo "⚠️  Running as root — system packages will install without sudo"
        echo ""
    else
        echo "ℹ️  Linux: some steps require sudo (will prompt for password)"
        echo ""
    fi
fi

# Run the Python installer
echo "Starting RFIDIOt installer..."
echo ""
$PYTHON install_rfidiot.py

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✔  Installation complete!"
    echo "   Run the app: python3 rfid_manager.py"
else
    echo "⚠️  Installer finished with some issues."
    echo "   Check the output above."
    echo ""
    echo "   Common Linux fix:"
    echo "   sudo apt install libpcsclite-dev swig python3-dev"
    echo "   pip3 install pyscard pyserial pycryptodome"
fi
echo ""
